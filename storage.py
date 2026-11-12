"""SQLite storage for listings seen by the scanner and for scanner runs.

One ``listings`` row per (mode, item id) holds the deduplication state and the
structured content of the alert; ``runs`` with ``run_modes`` and
``run_issues`` mirror the end-of-run report. All timestamps are UTC ISO-8601
strings ("2026-11-03T20:00:05Z"), so they sort and compare as text.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

MODE_LABELS = {"mini_pc": "Mini PC (Proxmox)", "ram": "RAM Hunt", "cpu": "CPU Hunt"}
MODE_BY_LABEL = {label: mode for mode, label in MODE_LABELS.items()}

# Statuses that keep a listing out of later runs. "failed" (undelivered alert)
# is retried; "filtered" is skipped only under the same filter-rule version.
SEEN_STATUSES = ("notified", "dry_run", "ignored", "legacy")

LISTING_FIELDS = (
    "title", "price_eur", "url", "image_url", "matched_query", "description",
    "score", "upgrade_score", "verdict", "tier", "model", "platform", "generation",
    "cpu", "cores", "threads", "ram_gb", "storage", "gpu",
    "total_16_eur", "total_32_eur", "total_16_cpu_eur", "total_32_cpu_eur",
    "build_estimate_eur", "sections", "score_version",
)
# Columns derived from the hardware analysis; rescore.py rewrites only these,
# never the listing identity (title, price, links, query, description) or status.
ANALYSIS_FIELDS = tuple(
    field for field in LISTING_FIELDS
    if field not in ("title", "price_eur", "url", "image_url", "matched_query", "description")
)
RUN_MODE_FIELDS = (
    "searches", "failed_searches", "skipped_searches", "listings", "new", "matches", "notified",
)

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    duration_s REAL,
    dry_run INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'running',
    source TEXT NOT NULL DEFAULT 'scanner',
    summary TEXT
);
CREATE TABLE IF NOT EXISTS run_modes (
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    mode TEXT NOT NULL,
    {", ".join(f"{field} INTEGER NOT NULL DEFAULT 0" for field in RUN_MODE_FIELDS)},
    PRIMARY KEY (run_id, mode)
);
CREATE TABLE IF NOT EXISTS run_issues (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    message TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS listings (
    mode TEXT NOT NULL,
    item_id TEXT NOT NULL,
    status TEXT NOT NULL,
    filter_version TEXT,
    source TEXT NOT NULL DEFAULT 'scanner',
    first_seen TEXT NOT NULL,
    notified_at TEXT,
    run_id INTEGER REFERENCES runs(id) ON DELETE SET NULL,
    title TEXT, price_eur REAL, url TEXT, image_url TEXT, matched_query TEXT, description TEXT,
    score INTEGER, upgrade_score INTEGER, verdict TEXT, tier TEXT,
    model TEXT, platform TEXT, generation INTEGER,
    cpu TEXT, cores INTEGER, threads INTEGER, ram_gb INTEGER, storage TEXT, gpu TEXT,
    total_16_eur REAL, total_32_eur REAL, total_16_cpu_eur REAL, total_32_cpu_eur REAL,
    build_estimate_eur REAL,
    sections TEXT,
    score_version TEXT,
    PRIMARY KEY (mode, item_id)
);
CREATE INDEX IF NOT EXISTS listings_status ON listings (mode, status);
CREATE INDEX IF NOT EXISTS listings_notified ON listings (notified_at);
CREATE INDEX IF NOT EXISTS runs_started ON runs (source, started_at);
"""
SCHEMA_VERSION = 2


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(path: Path | str, readonly: bool = False) -> sqlite3.Connection:
    """Open the database; read-only connections never create or change the file."""
    if readonly:
        connection = sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True, check_same_thread=False)
    else:
        connection = sqlite3.connect(path)
        connection.execute("PRAGMA journal_mode=WAL")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


class Store:
    """Read/write access used by the scanner and the Telegram importer."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.db = connect(self.path)
        with self.db:
            self.db.executescript(SCHEMA)
            self._migrate()
            self.db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def _migrate(self) -> None:
        """Bring a database created by an older release up to SCHEMA_VERSION."""
        columns = {row["name"] for row in self.db.execute("PRAGMA table_info(listings)")}
        if "score_version" not in columns:
            # Version 2: which hardware_enricher.SCORING_VERSION produced the score.
            # Older rows stay NULL until rescore.py recalculates them.
            self.db.execute("ALTER TABLE listings ADD COLUMN score_version TEXT")

    def close(self) -> None:
        self.db.close()

    # -- metadata -----------------------------------------------------------
    def get_meta(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))

    # -- listings -----------------------------------------------------------
    def seen_ids(self, mode: str, filter_version: str | None = None) -> set[str]:
        """IDs that must not be processed again in ``mode``."""
        placeholders = ", ".join("?" for _ in SEEN_STATUSES)
        rows = self.db.execute(
            f"SELECT item_id FROM listings WHERE mode = ? AND (status IN ({placeholders})"
            " OR (status = 'filtered' AND filter_version IS ?))",
            (mode, *SEEN_STATUSES, filter_version),
        )
        return {row["item_id"] for row in rows}

    def record_listing(
        self,
        mode: str,
        item_id: str,
        status: str,
        *,
        run_id: int | None = None,
        source: str = "scanner",
        seen_at: str | None = None,
        filter_version: str | None = None,
        **fields: Any,
    ) -> None:
        """Insert or update a listing; a notified listing is never downgraded.

        ``fields`` are LISTING_FIELDS; ``sections`` may be a list (stored as JSON).
        A None field keeps the stored value, so later partial data never erases it.
        """
        unknown = set(fields) - set(LISTING_FIELDS)
        if unknown:
            raise ValueError(f"unknown listing fields: {sorted(unknown)}")
        if isinstance(fields.get("sections"), (list, dict)):
            fields["sections"] = json.dumps(fields["sections"], ensure_ascii=False)
        seen_at = seen_at or utc_now()
        notified_at = seen_at if status in ("notified", "dry_run") else None
        values = {
            "mode": mode, "item_id": str(item_id), "status": status,
            "filter_version": filter_version, "source": source, "first_seen": seen_at,
            "notified_at": notified_at, "run_id": run_id,
            **{field: fields.get(field) for field in LISTING_FIELDS},
        }
        kept = "listings.status = 'notified'"
        updates = [
            f"status = CASE WHEN {kept} THEN listings.status ELSE excluded.status END",
            f"filter_version = CASE WHEN {kept} THEN listings.filter_version ELSE excluded.filter_version END",
            f"run_id = CASE WHEN {kept} THEN listings.run_id ELSE COALESCE(excluded.run_id, listings.run_id) END",
            "notified_at = COALESCE(listings.notified_at, excluded.notified_at)",
            "source = CASE WHEN listings.source = 'legacy' THEN excluded.source ELSE listings.source END",
            "first_seen = MIN(listings.first_seen, excluded.first_seen)",
            *(f"{field} = COALESCE(excluded.{field}, listings.{field})" for field in LISTING_FIELDS),
        ]
        columns = ", ".join(values)
        params = ", ".join(f":{column}" for column in values)
        with self.db:
            self.db.execute(
                f"INSERT INTO listings ({columns}) VALUES ({params}) "
                f"ON CONFLICT (mode, item_id) DO UPDATE SET {', '.join(updates)}",
                values,
            )

    def replace_analysis(self, mode: str, item_id: str, fields: dict[str, Any]) -> bool:
        """Overwrite the analysis columns of one listing, NULLs included.

        Unlike record_listing, a None value clears the stored one, so a value the
        old parser misread (for example 500 GB of RAM) does not survive a rescore.
        """
        unknown = set(fields) - set(ANALYSIS_FIELDS)
        if unknown:
            raise ValueError(f"not analysis fields: {sorted(unknown)}")
        values = dict(fields)
        if isinstance(values.get("sections"), (list, dict)):
            values["sections"] = json.dumps(values["sections"], ensure_ascii=False)
        assignments = ", ".join(f"{field} = :{field}" for field in values)
        with self.db:
            cursor = self.db.execute(
                f"UPDATE listings SET {assignments} WHERE mode = :_mode AND item_id = :_item_id",
                {**values, "_mode": mode, "_item_id": str(item_id)},
            )
        return cursor.rowcount == 1

    def migrate_legacy_ledgers(self, ledgers: dict[str, Path], filtered: Path | None = None) -> int:
        """Import the old .txt ID ledgers once; the files are left untouched."""
        if self.get_meta("legacy_ledgers_imported"):
            return 0
        now = utc_now()
        rows = []
        for mode, path in ledgers.items():
            for line in _read_lines(path):
                rows.append((mode, line, "legacy", None))
        if filtered is not None:
            for line in _read_lines(filtered):
                version, _, item_id = line.rpartition(":")
                if item_id:
                    rows.append(("mini_pc", item_id, "filtered", version or None))
        with self.db:
            self.db.executemany(
                "INSERT OR IGNORE INTO listings (mode, item_id, status, filter_version, source, first_seen)"
                " VALUES (?, ?, ?, ?, 'legacy', ?)",
                [(*row, now) for row in rows],
            )
            self.db.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('legacy_ledgers_imported', ?)", (now,)
            )
        return len(rows)

    # -- runs ---------------------------------------------------------------
    def start_run(self, dry_run: bool = False, started_at: str | None = None) -> int:
        with self.db:
            cursor = self.db.execute(
                "INSERT INTO runs (started_at, dry_run) VALUES (?, ?)",
                (started_at or utc_now(), int(dry_run)),
            )
        return cursor.lastrowid

    def finish_run(self, run_id: int, report: Any, summary: str | None = None) -> None:
        """Store the outcome of a ``run_report.RunReport``."""
        status = "failed" if report.fatal else "problems" if report.issues else "ok"
        modes = {
            MODE_BY_LABEL.get(label, label): {field: getattr(stats, field) for field in RUN_MODE_FIELDS}
            for label, stats in report.modes.items()
        }
        issues = [(kind, message) for kind, messages in report.issues.items() for message in messages]
        with self.db:
            self.db.execute(
                "UPDATE runs SET duration_s = ?, status = ?, summary = ? WHERE id = ?",
                (round(report.elapsed_seconds(), 1), status, summary, run_id),
            )
            self._write_run_details(run_id, modes, issues)

    def save_run(
        self,
        *,
        started_at: str,
        source: str,
        status: str,
        duration_s: float | None = None,
        dry_run: bool = False,
        summary: str | None = None,
        modes: dict[str, dict[str, int]] | None = None,
        issues: Iterable[tuple[str, str]] = (),
    ) -> tuple[int, bool]:
        """Insert a complete run once per (source, started_at); return (id, created)."""
        existing = self.db.execute(
            "SELECT id FROM runs WHERE source = ? AND started_at = ?", (source, started_at)
        ).fetchone()
        if existing:
            return existing["id"], False
        with self.db:
            cursor = self.db.execute(
                "INSERT INTO runs (started_at, duration_s, dry_run, status, source, summary)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (started_at, duration_s, int(dry_run), status, source, summary),
            )
            self._write_run_details(cursor.lastrowid, modes or {}, list(issues))
        return cursor.lastrowid, True

    def _write_run_details(self, run_id, modes, issues) -> None:
        columns = ", ".join(RUN_MODE_FIELDS)
        params = ", ".join("?" for _ in RUN_MODE_FIELDS)
        self.db.executemany(
            f"INSERT OR REPLACE INTO run_modes (run_id, mode, {columns}) VALUES (?, ?, {params})",
            [
                (run_id, mode, *(int(stats.get(field) or 0) for field in RUN_MODE_FIELDS))
                for mode, stats in modes.items()
            ],
        )
        self.db.executemany(
            "INSERT INTO run_issues (run_id, kind, message) VALUES (?, ?, ?)",
            [(run_id, kind, message) for kind, message in issues],
        )


def _read_lines(path: Path) -> list[str]:
    try:
        with Path(path).open(encoding="utf-8", errors="ignore") as handle:
            return list(dict.fromkeys(line.strip() for line in handle if line.strip()))
    except FileNotFoundError:
        return []
