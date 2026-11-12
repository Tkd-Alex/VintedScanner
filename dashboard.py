#!/usr/bin/env python3
"""Local web dashboard for the scanner database (read-only).

    python3 dashboard.py [--host 127.0.0.1] [--port 8000] [--db vinted_scanner.db]

Serves the static UI in ``dashboard/`` and a small JSON API:

    GET /api/overview                      market overview and counters
    GET /api/facets?mode=mini_pc           values for the filter sidebar
    GET /api/listings?mode=&status=&q=&platform=&verdict=&tier=&generation=
                     &min_price=&max_price=&sort=&limit=&offset=
    GET /api/listings/<mode>/<item_id>     one listing with every alert section
    GET /api/runs?limit=&offset=           scanner runs with counters and issues
    GET /api/references                    reference CPUs for benchmark comparisons

Benchmarks are not stored in the database: SQL functions look them up in
hardware_enricher.CPU_CATALOG by CPU name, so every listing, including history
restored from Telegram, gets them without a migration.
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import statistics
from functools import lru_cache
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import hardware_enricher
import storage

PROJECT_DIR = Path(__file__).resolve().parent
STATIC_DIR = PROJECT_DIR / "dashboard"
STATIC_FILES = {"/": "index.html", "/app.css": "app.css", "/app.js": "app.js"}
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
                 ".js": "text/javascript; charset=utf-8"}

STATUS_GROUPS = {
    "alerts": ("notified", "dry_run"),
    "failed": ("failed",),
    "filtered": ("filtered",),
    "ignored": ("ignored",),
    "all": ("notified", "dry_run", "failed", "filtered", "ignored", "legacy"),
}
ALERT_STATUSES = STATUS_GROUPS["alerts"]
SEEN_AT = "COALESCE(notified_at, first_seen)"
SORTS = {
    "newest": f"{SEEN_AT} DESC",
    "price": "price_eur IS NULL, price_eur ASC",
    "price_desc": "price_eur DESC",
    "score": "score IS NULL, score DESC, price_eur ASC",
    # Score points per euro of the 16 GB build: cheap, capable machines first.
    "value": "score IS NULL, CAST(score AS REAL) / COALESCE(total_16_eur, price_eur, 1e9) DESC",
    # PassMark multithread points per euro of the 16 GB build.
    "perf_value": "cpu_bench_mt(cpu) IS NULL,"
                  " CAST(cpu_bench_mt(cpu) AS REAL) / COALESCE(total_16_eur, price_eur, 1e9) DESC",
    "fastest": "cpu_bench_mt(cpu) IS NULL, cpu_bench_mt(cpu) DESC, price_eur ASC",
}
CARD_COLUMNS = (
    "mode, item_id, status, source, first_seen, notified_at, run_id, title, price_eur, url, image_url, "
    "matched_query, score, upgrade_score, verdict, tier, model, platform, generation, cpu, cores, "
    "threads, ram_gb, storage, gpu, total_16_eur, total_32_eur, total_16_cpu_eur, total_32_cpu_eur, "
    "build_estimate_eur, cpu_bench_mt(cpu) AS bench_mt, cpu_bench_st(cpu) AS bench_st"
)
MAX_LIMIT = 200


@lru_cache(maxsize=512)
def _benchmark(cpu_name):
    return hardware_enricher.benchmark_for_name(cpu_name) or {}


def register_functions(db: sqlite3.Connection) -> sqlite3.Connection:
    """Expose catalog benchmarks to SQL as cpu_bench_mt(cpu) and cpu_bench_st(cpu)."""
    db.create_function("cpu_bench_mt", 1, lambda cpu: _benchmark(cpu).get("mt"), deterministic=True)
    db.create_function("cpu_bench_st", 1, lambda cpu: _benchmark(cpu).get("st"), deterministic=True)
    return db


def references() -> dict:
    """Reference CPUs (Config.hardware_reference_cpus, else the defaults)."""
    try:
        import Config
        keys = getattr(Config, "hardware_reference_cpus", None)
    except ImportError:
        keys = None
    return {
        "items": hardware_enricher.reference_cpus(keys),
        "source": f"{hardware_enricher.BENCHMARK_SOURCE} ({hardware_enricher.BENCHMARK_VERSION})",
    }


def _build_cost(listing: dict):
    """16 GB build cost, else the asking price."""
    return listing.get("total_16_eur") if listing.get("total_16_eur") is not None else listing.get("price_eur")


def value_frontier(listings: list[dict]) -> list[str]:
    """Item ids no other listing beats on both build cost and multithread benchmark."""
    points = sorted(
        (listing for listing in listings if listing.get("bench_mt") and _build_cost(listing) is not None),
        key=lambda listing: (_build_cost(listing), -listing["bench_mt"]),
    )
    frontier, best = [], 0
    for listing in points:
        if listing["bench_mt"] > best:
            frontier.append(listing["item_id"])
            best = listing["bench_mt"]
    return frontier


def _placeholders(values) -> str:
    return ", ".join("?" for _ in values)


def _median(values):
    values = [value for value in values if value is not None]
    return round(statistics.median(values), 2) if values else None


def _number(value, cast=float):
    try:
        return cast(value) if value not in (None, "") else None
    except ValueError:
        return None


# -- queries -----------------------------------------------------------------
def overview(db: sqlite3.Connection) -> dict:
    register_functions(db)
    alerts = _placeholders(ALERT_STATUSES)
    modes = {}
    for mode in storage.MODE_LABELS:
        row = db.execute(
            f"SELECT COUNT(*) AS seen, SUM(status IN ({alerts})) AS alerts,"
            " SUM(status = 'filtered') AS filtered, SUM(status = 'ignored') AS ignored,"
            f" MAX(CASE WHEN status IN ({alerts}) THEN {SEEN_AT} END) AS last_alert"
            " FROM listings WHERE mode = ?",
            (*ALERT_STATUSES, *ALERT_STATUSES, mode),
        ).fetchone()
        modes[mode] = {key: row[key] or 0 for key in ("seen", "alerts", "filtered", "ignored")}
        modes[mode]["last_alert"] = row["last_alert"]

    market = [dict(row) for row in db.execute(
        f"SELECT {CARD_COLUMNS} FROM listings WHERE mode = 'mini_pc' AND status IN ({alerts})",
        ALERT_STATUSES,
    )]
    runs = db.execute(
        "SELECT COUNT(*) AS total, SUM(status = 'ok') AS ok, SUM(status = 'problems') AS problems,"
        " SUM(status = 'failed') AS failed FROM runs WHERE status != 'running'"
    ).fetchone()
    last_run = db.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT 1").fetchone()
    daily = db.execute(
        f"SELECT substr({SEEN_AT}, 1, 10) AS day, mode, COUNT(*) AS count FROM listings"
        f" WHERE status IN ({alerts}) GROUP BY day, mode ORDER BY day",
        ALERT_STATUSES,
    ).fetchall()
    return {
        "modes": modes,
        "runs": {key: runs[key] or 0 for key in ("total", "ok", "problems", "failed")},
        "last_run": dict(last_run) if last_run else None,
        "market": market_stats(market),
        "references": references(),
        "daily": [dict(row) for row in daily],
    }


def market_stats(listings: list[dict]) -> dict:
    """Price and score statistics over the mini-PC alerts."""
    def group(key):
        groups: dict = {}
        for listing in listings:
            groups.setdefault(listing.get(key), []).append(listing)
        rows = [
            {
                "name": name,
                "count": len(items),
                "min_price": min((i["price_eur"] for i in items if i["price_eur"] is not None), default=None),
                "median_price": _median(i["price_eur"] for i in items),
                "median_total_32": _median(i["total_32_eur"] for i in items),
                "median_score": _median(i["score"] for i in items),
                "best_score": max((i["score"] for i in items if i["score"] is not None), default=None),
                "median_bench_mt": _median(i.get("bench_mt") for i in items),
                # Median euros of the 16 GB build per 1,000 PassMark multithread points.
                "eur_per_1k_mt": _median(
                    _build_cost(i) / i["bench_mt"] * 1000
                    for i in items if i.get("bench_mt") and _build_cost(i) is not None
                ),
            }
            for name, items in groups.items() if name is not None
        ]
        return sorted(rows, key=lambda row: (-row["count"], row["median_price"] or 0))

    verdicts: dict = {}
    for listing in listings:
        if listing["verdict"]:
            verdicts[listing["verdict"]] = verdicts.get(listing["verdict"], 0) + 1
    scored = [listing for listing in listings if listing["score"] is not None]
    with_32 = [listing for listing in listings if listing["total_32_eur"] is not None]
    value_key = lambda i: i["score"] / (i["total_16_eur"] or i["price_eur"] or 1e9)  # noqa: E731
    benchmarked = [i for i in listings if i.get("bench_mt") and _build_cost(i)]
    return {
        "count": len(listings),
        "median_price": _median(i["price_eur"] for i in listings),
        "median_total_32": _median(i["total_32_eur"] for i in listings),
        "median_score": _median(i["score"] for i in listings),
        "by_platform": group("platform"),
        "by_generation": group("generation"),
        "by_cpu": group("cpu"),
        "verdicts": verdicts,
        "best_value": sorted(scored, key=value_key, reverse=True)[:6],
        "cheapest_32": sorted(with_32, key=lambda i: i["total_32_eur"])[:6],
        "top_score": sorted(scored, key=lambda i: (-i["score"], i["price_eur"] or 0))[:6],
        "best_perf_value": sorted(benchmarked, key=lambda i: i["bench_mt"] / _build_cost(i), reverse=True)[:6],
        "value_frontier": value_frontier(listings),
        "benchmarked": len(benchmarked),
    }


def facets(db: sqlite3.Connection, mode: str) -> dict:
    alerts = _placeholders(ALERT_STATUSES)

    def values(column):
        rows = db.execute(
            f"SELECT {column} AS value, COUNT(*) AS count FROM listings WHERE mode = ?"
            f" AND status IN ({alerts}) AND {column} IS NOT NULL GROUP BY {column} ORDER BY count DESC",
            (mode, *ALERT_STATUSES),
        )
        return [dict(row) for row in rows]

    prices = db.execute(
        f"SELECT MIN(price_eur) AS min, MAX(price_eur) AS max FROM listings WHERE mode = ? AND status IN ({alerts})",
        (mode, *ALERT_STATUSES),
    ).fetchone()
    return {
        "platform": values("platform"), "verdict": values("verdict"), "tier": values("tier"),
        "generation": values("generation"), "cpu": values("cpu"), "price": dict(prices),
    }


def listings(db: sqlite3.Connection, params: dict[str, str]) -> dict:
    register_functions(db)
    mode = params.get("mode", "mini_pc")
    statuses = STATUS_GROUPS.get(params.get("status", "alerts"), ALERT_STATUSES)
    where = [f"mode = ?", f"status IN ({_placeholders(statuses)})"]
    args: list = [mode, *statuses]
    if params.get("q"):
        where.append("(title LIKE ? OR model LIKE ? OR cpu LIKE ? OR matched_query LIKE ?)")
        args += [f"%{params['q']}%"] * 4
    for column in ("platform", "verdict", "tier", "cpu"):
        if params.get(column):
            where.append(f"{column} = ?")
            args.append(params[column])
    if _number(params.get("generation"), int) is not None:
        where.append("generation = ?")
        args.append(int(params["generation"]))
    if _number(params.get("min_price")) is not None:
        where.append("price_eur >= ?")
        args.append(float(params["min_price"]))
    if _number(params.get("max_price")) is not None:
        where.append("price_eur <= ?")
        args.append(float(params["max_price"]))
    for param, column in (("min_threads", "threads"), ("min_ram", "ram_gb"), ("min_mt", "cpu_bench_mt(cpu)")):
        if _number(params.get(param)) is not None:
            where.append(f"{column} >= ?")
            args.append(float(params[param]))
    if _number(params.get("run_id"), int) is not None:
        where.append("run_id = ?")
        args.append(int(params["run_id"]))
    clause = " AND ".join(where)
    order = SORTS.get(params.get("sort", "newest"), SORTS["newest"])
    limit = min(_number(params.get("limit"), int) or 48, MAX_LIMIT)
    offset = max(_number(params.get("offset"), int) or 0, 0)
    total = db.execute(f"SELECT COUNT(*) FROM listings WHERE {clause}", args).fetchone()[0]
    rows = db.execute(
        f"SELECT {CARD_COLUMNS} FROM listings WHERE {clause} ORDER BY {order}, item_id LIMIT ? OFFSET ?",
        (*args, limit, offset),
    )
    return {"total": total, "limit": limit, "offset": offset, "items": [dict(row) for row in rows]}


def listing_detail(db: sqlite3.Connection, mode: str, item_id: str) -> dict | None:
    row = db.execute("SELECT * FROM listings WHERE mode = ? AND item_id = ?", (mode, item_id)).fetchone()
    if not row:
        return None
    data = dict(row)
    data["sections"] = json.loads(data["sections"]) if data["sections"] else []
    # False for scores from older rules (not yet recalculated by rescore.py).
    data["score_current"] = data.get("score_version") == hardware_enricher.SCORING_VERSION
    benchmark = _benchmark(data.get("cpu"))
    data["bench_mt"], data["bench_st"] = benchmark.get("mt"), benchmark.get("st")
    data["vs_reference"] = hardware_enricher.compare_to_references(
        benchmark or None, references()["items"]) if benchmark else []
    return data


def runs(db: sqlite3.Connection, params: dict[str, str]) -> dict:
    limit = min(_number(params.get("limit"), int) or 50, MAX_LIMIT)
    offset = max(_number(params.get("offset"), int) or 0, 0)
    total = db.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    rows = [dict(row) for row in db.execute(
        "SELECT runs.*, (SELECT COUNT(*) FROM listings WHERE listings.run_id = runs.id"
        f" AND listings.status IN ({_placeholders(ALERT_STATUSES)})) AS alerts"
        " FROM runs ORDER BY started_at DESC LIMIT ? OFFSET ?",
        (*ALERT_STATUSES, limit, offset),
    )]
    ids = [row["id"] for row in rows]
    modes: dict = {}
    issues: dict = {}
    if ids:
        marks = _placeholders(ids)
        for row in db.execute(f"SELECT * FROM run_modes WHERE run_id IN ({marks})", ids):
            modes.setdefault(row["run_id"], {})[row["mode"]] = {
                key: row[key] for key in storage.RUN_MODE_FIELDS
            }
        for row in db.execute(f"SELECT * FROM run_issues WHERE run_id IN ({marks}) ORDER BY id", ids):
            issues.setdefault(row["run_id"], []).append({"kind": row["kind"], "message": row["message"]})
    for row in rows:
        row["modes"] = modes.get(row["id"], {})
        row["issues"] = issues.get(row["id"], [])
    return {"total": total, "items": rows}


# -- HTTP --------------------------------------------------------------------
class DashboardHandler(BaseHTTPRequestHandler):
    database_path: Path = PROJECT_DIR / "vinted_scanner.db"

    def do_GET(self):  # noqa: N802 (http.server naming)
        url = urlsplit(self.path)
        if url.path in STATIC_FILES:
            return self.send_static(STATIC_FILES[url.path])
        if not url.path.startswith("/api/"):
            return self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        params = {key: values[-1] for key, values in parse_qs(url.query).items()}
        if not self.database_path.exists():
            return self.send_json(
                {"error": "Database not found: run the scanner or telegram_import.py first."},
                HTTPStatus.SERVICE_UNAVAILABLE,
            )
        db = storage.connect(self.database_path, readonly=True)
        try:
            result = self.route(db, url.path, params)
        except sqlite3.Error as error:
            logging.exception("Dashboard query failed")
            return self.send_json({"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)
        finally:
            db.close()
        if result is None:
            return self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        self.send_json(result)

    @staticmethod
    def route(db, path, params):
        parts = path.strip("/").split("/")[1:]
        if parts == ["overview"]:
            return overview(db)
        if parts == ["facets"]:
            return facets(db, params.get("mode", "mini_pc"))
        if parts == ["listings"]:
            return listings(db, params)
        if len(parts) == 3 and parts[0] == "listings":
            return listing_detail(db, parts[1], parts[2])
        if parts == ["runs"]:
            return runs(db, params)
        if parts == ["references"]:
            return references()
        return None

    def send_static(self, name):
        path = STATIC_DIR / name
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", CONTENT_TYPES[path.suffix])
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, data, status=HTTPStatus.OK):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # noqa: A002 (http.server signature)
        logging.debug("%s - %s", self.address_string(), format % args)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Serve the scanner dashboard.")
    parser.add_argument("--host", default="127.0.0.1", help="interface to bind (default: localhost only)")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--db", type=Path, default=DashboardHandler.database_path)
    arguments = parser.parse_args(argv)
    DashboardHandler.database_path = arguments.db
    server = ThreadingHTTPServer((arguments.host, arguments.port), DashboardHandler)
    print(f"Dashboard on http://{arguments.host}:{arguments.port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
