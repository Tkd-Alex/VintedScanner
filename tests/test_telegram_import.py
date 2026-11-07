import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import storage
import telegram_import

ALERT = [
    {"type": "plain", "text": "🟡 "}, {"type": "bold", "text": "65/100"},
    {"type": "plain", "text": " · Worth evaluating\n"},
    {"type": "bold", "text": "HP ProDesk 400 G4 mini 8500T"},
    {"type": "plain", "text": "\n💶 "}, {"type": "bold", "text": "€150"},
    {"type": "plain", "text": "  ·  🔗 "},
    {"type": "text_link", "text": "Open listing", "href": "https://www.vinted.it/items/10654669373-hp"},
    {"type": "plain", "text": "  ·  📷 "},
    {"type": "text_link", "text": "Photo", "href": "https://images1.vinted.net/p.webp"},
    {"type": "plain", "text": "\n🔍 "}, {"type": "italic", "text": "ProDesk Mini"},
    {"type": "plain", "text": "\n\n"}, {"type": "bold", "text": "🖥 Hardware"},
    {"type": "plain", "text": "\nCPU: Intel Core i5-8500T · 6 core / 6 thread · nominal TDP 35 W\n"
                              "RAM: 8 GB · max 32 GB\n"
                              "Base: HP ProDesk 400 G4 Mini · 8th gen Intel · no PCIe · CPU ≤35 W"},
]
SUMMARY = ("✅ Vinted scan finished · 1 match notified\n"
           "• Mini PC (Proxmox): 1 match · 12 searches, 144 listings, 3 new\n"
           "• RAM Hunt: nothing matched · 7 searches, 127 listings, 14 new\n\n"
           "🕒 2026-11-03 20:00 · took 14s")
LATE = ("📬 Delivered late · 1 earlier report could not be sent (Telegram or the network was unreachable)"
        "\n\n———\n🔴 Vinted scan failed\n\n🌐 Network error\n• Could not open https://www.vinted.it\n\n"
        "🕒 2026-11-03 18:00 · took 2s")


def message(text, unixtime, entities=None):
    return {"id": unixtime, "type": "message", "date": "2026-11-03T21:00:00",
            "date_unixtime": str(unixtime), "text": text, "text_entities": entities or []}


EXPORT = {"messages": [
    message(ALERT, 1793736010, ALERT),                    # 2026-11-03T20:00:10Z
    message(SUMMARY, 1793736016),                         # 2026-11-03T20:00:16Z
    message(LATE, 1793739000),
    message("HP EliteDesk 800 G3 Mini\n🏷️ 100.00 EUR", 1793700000),   # old format
    {"id": 1, "type": "service", "action": "pin_message"},
]}


class ImportTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.dir = Path(directory.name)
        self.export_path = self.dir / "result.json"
        self.export_path.write_text(json.dumps(EXPORT, ensure_ascii=False), encoding="utf-8")
        self.db_path = self.dir / "test.db"

    def run_import(self, *extra):
        output = io.StringIO()
        with redirect_stdout(output):
            code = telegram_import.main([str(self.export_path), "--db", str(self.db_path), *extra])
        self.assertEqual(code, 0)
        return output.getvalue()

    def rows(self, sql):
        store = storage.Store(self.db_path)
        try:
            return [dict(row) for row in store.db.execute(sql)]
        finally:
            store.close()

    def test_alerts_and_runs_are_imported_and_linked(self):
        output = self.run_import()
        self.assertIn("Mini PC (Proxmox): 1 alerts", output)
        self.assertIn("Runs: 2 new", output)
        self.assertIn("Skipped messages (older format or not an alert): 1", output)

        [listing] = self.rows("SELECT * FROM listings")
        self.assertEqual((listing["mode"], listing["item_id"]), ("mini_pc", "10654669373"))
        self.assertEqual((listing["status"], listing["source"]), ("notified", "telegram"))
        self.assertEqual((listing["score"], listing["price_eur"], listing["cpu"]), (65, 150.0, "Intel Core i5-8500T"))
        self.assertEqual((listing["platform"], listing["generation"]), ("HP ProDesk 400 G4 Mini", 8))
        self.assertEqual(listing["image_url"], "https://images1.vinted.net/p.webp")
        self.assertEqual(listing["notified_at"], "2026-11-03T20:00:10Z")

        runs = self.rows("SELECT * FROM runs ORDER BY started_at")
        self.assertEqual([(r["started_at"], r["status"]) for r in runs],
                         [("2026-11-03T18:00:00Z", "failed"), ("2026-11-03T20:00:02Z", "ok")])
        self.assertEqual(listing["run_id"], runs[1]["id"])
        issues = self.rows("SELECT kind FROM run_issues")
        self.assertEqual(issues, [{"kind": "network"}])

    def test_import_is_idempotent(self):
        self.run_import()
        output = self.run_import()
        self.assertIn("Runs: 0 new, 2 already imported", output)
        self.assertEqual(len(self.rows("SELECT * FROM runs")), 2)
        self.assertEqual(len(self.rows("SELECT * FROM listings")), 1)

    def test_dry_run_saves_nothing(self):
        output = self.run_import("--dry-run")
        self.assertIn("nothing was saved", output)
        self.assertFalse(self.db_path.exists())


if __name__ == "__main__":
    unittest.main()
