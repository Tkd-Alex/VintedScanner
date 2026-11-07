import json
import tempfile
import unittest
from pathlib import Path

import run_report
import storage


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.dir = Path(directory.name)
        self.store = storage.Store(self.dir / "test.db")
        self.addCleanup(self.store.close)


class ListingTests(StoreTestCase):
    def test_seen_ids_follow_status_and_mode(self):
        for item_id, status in [("1", "notified"), ("2", "dry_run"), ("3", "ignored"), ("4", "failed")]:
            self.store.record_listing("ram", item_id, status)
        self.store.record_listing("cpu", "5", "notified")
        self.assertEqual(self.store.seen_ids("ram"), {"1", "2", "3"})
        self.assertEqual(self.store.seen_ids("cpu"), {"5"})

    def test_filtered_ids_are_seen_only_under_the_same_rule_version(self):
        self.store.record_listing("mini_pc", "7", "filtered", filter_version="2")
        self.assertEqual(self.store.seen_ids("mini_pc", "2"), {"7"})
        self.assertEqual(self.store.seen_ids("mini_pc", "3"), set())

    def test_notified_listing_is_never_downgraded_and_keeps_data(self):
        self.store.record_listing(
            "mini_pc", "9", "notified", title="EliteDesk 800 G4", price_eur=150.0,
            sections=[{"title": "Hardware", "rows": ["RAM: 8 GB"]}],
        )
        self.store.record_listing("mini_pc", "9", "filtered", filter_version="2", score=40)
        row = self.store.db.execute("SELECT * FROM listings WHERE item_id = '9'").fetchone()
        self.assertEqual(row["status"], "notified")
        self.assertEqual(row["title"], "EliteDesk 800 G4")
        self.assertEqual(row["score"], 40)
        self.assertEqual(json.loads(row["sections"])[0]["rows"], ["RAM: 8 GB"])

    def test_failed_listing_becomes_notified_on_retry(self):
        first = self.store.start_run()
        second = self.store.start_run(started_at="2030-01-01T00:00:00Z")
        self.store.record_listing("mini_pc", "1", "failed", run_id=first)
        self.store.record_listing("mini_pc", "1", "notified", run_id=second)
        row = self.store.db.execute("SELECT status, run_id, notified_at FROM listings").fetchone()
        self.assertEqual((row["status"], row["run_id"]), ("notified", second))
        self.assertIsNotNone(row["notified_at"])

    def test_unknown_field_is_rejected(self):
        with self.assertRaises(ValueError):
            self.store.record_listing("ram", "1", "notified", colour="red")


class LegacyMigrationTests(StoreTestCase):
    def test_txt_ledgers_are_imported_once_and_left_on_disk(self):
        items = self.dir / "vinted_items.txt"
        items.write_text("1\n2\n2\n", encoding="utf-8")
        filtered = self.dir / "vinted_filtered_items.txt"
        filtered.write_text("2:3\n1:4\n", encoding="utf-8")
        ledgers = {"mini_pc": items, "ram": self.dir / "missing.txt"}

        self.assertEqual(self.store.migrate_legacy_ledgers(ledgers, filtered), 4)
        self.assertEqual(self.store.migrate_legacy_ledgers(ledgers, filtered), 0)
        self.assertEqual(self.store.seen_ids("mini_pc", "2"), {"1", "2", "3"})
        self.assertTrue(items.exists())

    def test_imported_alert_enriches_a_legacy_row(self):
        items = self.dir / "items.txt"
        items.write_text("10\n", encoding="utf-8")
        self.store.migrate_legacy_ledgers({"mini_pc": items})
        self.store.record_listing(
            "mini_pc", "10", "notified", source="telegram", seen_at="2026-11-03T20:00:05Z", title="M720q",
        )
        row = self.store.db.execute("SELECT * FROM listings").fetchone()
        self.assertEqual((row["status"], row["source"], row["title"]), ("notified", "telegram", "M720q"))
        self.assertEqual(row["first_seen"], "2026-11-03T20:00:05Z")


class RunTests(StoreTestCase):
    def test_finish_run_stores_modes_issues_and_status(self):
        report = run_report.RunReport(clock=lambda: 0.0)
        report.mode("Mini PC (Proxmox)").searches = 12
        report.mode("RAM Hunt").matches = 1
        report.add_issue("rate_limit", "429 on search")
        run_id = self.store.start_run(dry_run=True)
        self.store.finish_run(run_id, report, summary="text")

        run = self.store.db.execute("SELECT * FROM runs").fetchone()
        self.assertEqual((run["status"], run["dry_run"], run["summary"]), ("problems", 1, "text"))
        modes = {row["mode"]: row for row in self.store.db.execute("SELECT * FROM run_modes")}
        self.assertEqual(modes["mini_pc"]["searches"], 12)
        self.assertEqual(modes["ram"]["matches"], 1)
        issue = self.store.db.execute("SELECT kind, message FROM run_issues").fetchone()
        self.assertEqual(tuple(issue), ("rate_limit", "429 on search"))

    def test_save_run_is_idempotent(self):
        kwargs = dict(started_at="2026-11-03T20:00:00Z", source="telegram", status="ok",
                      modes={"mini_pc": {"searches": 3}}, issues=[("http", "403")])
        first, created = self.store.save_run(**kwargs)
        second, created_again = self.store.save_run(**kwargs)
        self.assertEqual((first, created, created_again), (second, True, False))
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM run_issues").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
