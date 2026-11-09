import json
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

import dashboard
import run_report
import storage


def seed(store):
    run_id = store.start_run(started_at="2026-11-05T10:00:00Z")
    report = run_report.RunReport(clock=lambda: 0.0)
    report.mode("Mini PC (Proxmox)").searches = 12
    report.add_issue("http", "HTTP 403")
    store.finish_run(run_id, report, "summary")
    listings = [
        ("1", 120.0, 55, "Lenovo ThinkCentre M720q Tiny", 8, 170.0, "Worth evaluating"),
        ("2", 200.0, 77, "HP EliteDesk 800 G5 Mini", 9, 250.0, "Strong candidate"),
        ("3", 90.0, 40, "Lenovo ThinkCentre M720q Tiny", 8, 140.0, "Weak for a Proxmox node"),
    ]
    for item_id, price, score, platform, generation, total_16, verdict in listings:
        store.record_listing(
            "mini_pc", item_id, "notified", run_id=run_id, seen_at=f"2026-11-0{item_id}T10:00:00Z",
            title=f"{platform} #{item_id}", price_eur=price, score=score, platform=platform,
            generation=generation, total_16_eur=total_16, total_32_eur=total_16 + 80, verdict=verdict,
            sections=[{"title": "🖥 Hardware", "rows": ["RAM: 8 GB"]}],
        )
    store.record_listing("mini_pc", "4", "filtered", filter_version="2", title="GEEKOM", price_eur=300.0)
    store.record_listing("ram", "5", "notified", title="ThinkPad", price_eur=60.0, tier="HIGH — 32 GB stated")


class QueryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        store = storage.Store(Path(directory.name) / "test.db")
        seed(store)
        store.close()
        self.db = storage.connect(Path(directory.name) / "test.db", readonly=True)
        self.addCleanup(self.db.close)

    def test_overview_counts_and_market(self):
        data = dashboard.overview(self.db)
        self.assertEqual(data["modes"]["mini_pc"]["alerts"], 3)
        self.assertEqual(data["modes"]["mini_pc"]["filtered"], 1)
        self.assertEqual(data["modes"]["ram"]["alerts"], 1)
        self.assertEqual(data["runs"]["problems"], 1)
        market = data["market"]
        self.assertEqual((market["count"], market["median_price"]), (3, 120.0))
        m720q = market["by_platform"][0]
        self.assertEqual((m720q["name"], m720q["count"], m720q["min_price"]), ("Lenovo ThinkCentre M720q Tiny", 2, 90.0))
        self.assertEqual(market["top_score"][0]["item_id"], "2")
        self.assertEqual(market["cheapest_32"][0]["item_id"], "3")

    def test_listings_filter_and_sort(self):
        ids = lambda params: [i["item_id"] for i in dashboard.listings(self.db, params)["items"]]  # noqa: E731
        self.assertEqual(ids({}), ["3", "2", "1"])
        self.assertEqual(ids({"sort": "price"}), ["3", "1", "2"])
        self.assertEqual(ids({"sort": "score"}), ["2", "1", "3"])
        self.assertEqual(ids({"platform": "Lenovo ThinkCentre M720q Tiny", "max_price": "100"}), ["3"])
        self.assertEqual(ids({"q": "EliteDesk"}), ["2"])
        self.assertEqual(ids({"status": "filtered"}), ["4"])
        self.assertEqual(ids({"mode": "ram"}), ["5"])
        self.assertEqual(ids({"generation": "x", "min_price": "abc"}), ["3", "2", "1"])

    def test_detail_runs_and_facets(self):
        detail = dashboard.listing_detail(self.db, "mini_pc", "2")
        self.assertEqual(detail["sections"][0]["rows"], ["RAM: 8 GB"])
        self.assertIsNone(dashboard.listing_detail(self.db, "mini_pc", "404"))
        [run] = dashboard.runs(self.db, {})["items"]
        self.assertEqual((run["alerts"], run["modes"]["mini_pc"]["searches"]), (3, 12))
        self.assertEqual(run["issues"], [{"kind": "http", "message": "HTTP 403"}])
        facets = dashboard.facets(self.db, "mini_pc")
        self.assertEqual(facets["platform"][0], {"value": "Lenovo ThinkCentre M720q Tiny", "count": 2})
        self.assertEqual(facets["price"], {"min": 90.0, "max": 200.0})


class ServerTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "test.db"
        store = storage.Store(path)
        seed(store)
        store.close()
        static = Path(directory.name) / "static"
        static.mkdir()
        (static / "index.html").write_text('<script src="app.js"></script>', encoding="utf-8")
        patcher = patch.object(dashboard, "STATIC_DIR", static)
        patcher.start()
        self.addCleanup(patcher.stop)
        handler = type("Handler", (dashboard.DashboardHandler,), {"database_path": path})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def get(self, path):
        with urlopen(self.base + path) as response:
            return response.headers["Content-Type"], response.read()

    def test_static_files_and_api(self):
        content_type, body = self.get("/")
        self.assertTrue(content_type.startswith("text/html"))
        self.assertIn(b"app.js", body)
        content_type, body = self.get("/api/listings?mode=mini_pc&sort=price&limit=1")
        self.assertEqual(content_type, "application/json; charset=utf-8")
        self.assertEqual(json.loads(body)["items"][0]["item_id"], "3")

    def test_unknown_paths_are_404(self):
        for path in ("/api/nope", "/../storage.py", "/api/listings/mini_pc/404"):
            with self.subTest(path=path), self.assertRaises(HTTPError) as raised:
                self.get(path)
            self.assertEqual(raised.exception.code, 404)
            raised.exception.close()


if __name__ == "__main__":
    unittest.main()
