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


# Alert layout since the benchmark phase: Bench row, architecture, RAM layout.
NEW_ALERT = (
    "🟢 74/100 · Strong candidate\n"
    "Lenovo ThinkCentre M70q Gen 3 Tiny i5-12400T 16GB 2x8GB\n"
    "💶 €290  ·  🔗 Open listing  ·  📷 Photo\n"
    "🔍 ThinkCentre Tiny\n\n"
    "🖥 Hardware\n"
    "CPU: Intel Core i5-12400T · 6 core / 12 thread · nominal TDP 35 W · Alder Lake, 2022\n"
    "Bench: ST 3,331 · MT 15,575 · ×2.9 N100 · ×1.0 i5-12500T\n"
    "RAM: 16 GB (2×8 GB) · max 64 GB\n"
    "Base: Lenovo ThinkCentre M70q Gen 3 Tiny · 12th gen Intel · no PCIe · CPU ≤35 W\n\n"
    "📊 Score breakdown\n"
    "CPU 30/40 · Price 20/25 · RAM 8/10 · Disk 3/10 · Platform 13/15"
)
NEW_LINKS = [
    {"type": "text_link", "text": "Open listing", "href": "https://www.vinted.it/items/20000000001-m70q"},
]


class MixedLayoutImportTests(unittest.TestCase):
    def test_old_and_new_alert_layouts_import_side_by_side(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        export_path = Path(directory.name) / "result.json"
        db_path = Path(directory.name) / "test.db"
        export = {"messages": [message(ALERT, 1793736010, ALERT),
                               message(NEW_ALERT, 1793736100, NEW_LINKS)]}
        export_path.write_text(json.dumps(export, ensure_ascii=False), encoding="utf-8")

        def run():
            with redirect_stdout(io.StringIO()):
                self.assertEqual(telegram_import.main([str(export_path), "--db", str(db_path)]), 0)
            store = storage.Store(db_path)
            try:
                return {row["item_id"]: dict(row) for row in store.db.execute("SELECT * FROM listings")}
            finally:
                store.close()

        first = run()
        old, new = first["10654669373"], first["20000000001"]
        self.assertEqual((old["cpu"], old["cores"], old["threads"], old["ram_gb"]), ("Intel Core i5-8500T", 6, 6, 8))
        self.assertEqual((new["cpu"], new["cores"], new["threads"], new["ram_gb"]), ("Intel Core i5-12400T", 6, 12, 16))
        self.assertEqual((new["score"], new["platform"], new["generation"]),
                         (74, "Lenovo ThinkCentre M70q Gen 3 Tiny", 12))
        self.assertIn("Bench: ST 3,331 · MT 15,575 · ×2.9 N100 · ×1.0 i5-12500T", new["sections"])
        second = run()
        self.assertEqual(first, second)


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
