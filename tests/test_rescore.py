import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import hardware_enricher
import rescore
import storage


class RescoreTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.dir = Path(directory.name)
        self.path = self.dir / "vinted_scanner.db"
        store = storage.Store(self.path)
        # Scanner row with the old 500 GB RAM misreading.
        store.record_listing(
            "mini_pc", "1", "notified", title="Unité centrale HP EliteDesk 705 G5 Desktop Mini Ryzen 3 Pro SSD 500GO",
            price_eur=100.0, url="https://www.vinted.it/items/1", description="RAM : 8 Go",
            ram_gb=500, score=54, verdict="Weak for a Proxmox node", seen_at="2026-11-11T12:00:00Z",
        )
        # Telegram import: the excerpt lacks the RAM, the old alert's Hardware rows have it.
        store.record_listing(
            "mini_pc", "2", "notified", source="telegram", title="HP EliteDesk 800 G3 Desktop Mini PC",
            price_eur=175.0, url="https://www.vinted.it/items/2", description="Vendo mini PC aziendale …",
            score=60, seen_at="2026-11-05T12:00:00Z",
            sections=[{"title": "🖥 Hardware", "rows": [
                "CPU: Intel Core i5-7500T · 4 core / 4 thread · nominal TDP 35 W",
                "RAM: 8 GB · max 32 GB", "Disk: ❓ not stated"]}],
        )
        store.record_listing("mini_pc", "3", "filtered", filter_version="2", title="Hp 400 G4 mini pc")
        store.record_listing("ram", "4", "notified", title="Laptop DDR4")
        store.close()

    def run_rescore(self, **options):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            code = rescore.rescore(self.path, **options)
        return code, output.getvalue()

    def rows(self):
        db = storage.connect(self.path, readonly=True)
        rows = {row["item_id"]: dict(row) for row in db.execute("SELECT * FROM listings")}
        db.close()
        return rows

    def test_dry_run_reports_without_writing(self):
        before = self.rows()
        code, output = self.run_rescore(dry_run=True)
        self.assertEqual(code, 0)
        self.assertIn("ram_gb 500 → 8", output)
        self.assertIn("2 listings analysed (1 from Telegram excerpts)", output)
        self.assertEqual(self.rows(), before)
        self.assertEqual(list(self.dir.glob("*.bak-*")), [])

    def test_rescore_updates_analysis_only_and_backs_up(self):
        code, _output = self.run_rescore()
        self.assertEqual(code, 0)
        rows = self.rows()
        self.assertEqual(rows["1"]["ram_gb"], 8)
        self.assertEqual(rows["1"]["score_version"], hardware_enricher.SCORING_VERSION)
        self.assertEqual((rows["1"]["status"], rows["1"]["notified_at"], rows["1"]["description"]),
                         ("notified", "2026-11-11T12:00:00Z", "RAM : 8 Go"))
        self.assertTrue(json.loads(rows["1"]["sections"]))
        # Carried-over rows keep the Telegram listing's CPU and RAM; ❓ rows are not carried.
        self.assertEqual((rows["2"]["cpu"], rows["2"]["ram_gb"]), ("Intel Core i5-7500T", 8))
        self.assertEqual(rows["2"]["source"], "telegram")
        # Filtered and other-mode rows are untouched.
        self.assertIsNone(rows["3"]["score_version"])
        self.assertIsNone(rows["4"]["score_version"])
        backups = list(self.dir.glob("vinted_scanner.db.bak-*"))
        self.assertEqual(len(backups), 1)
        old = storage.connect(backups[0], readonly=True)
        self.assertEqual(old.execute("SELECT ram_gb FROM listings WHERE item_id = '1'").fetchone()[0], 500)
        old.close()

    def test_second_run_has_nothing_to_do_unless_all(self):
        self.run_rescore()
        _code, output = self.run_rescore(dry_run=True)
        self.assertIn("Nothing to rescore", output)
        _code, output = self.run_rescore(dry_run=True, include_current=True)
        self.assertIn("2 listings analysed", output)

    def test_unknown_rows_are_not_carried_over(self):
        sections = json.dumps([{"title": "🖥 Hardware", "rows": ["CPU: ❓ not identified", "RAM: 16 GB"]},
                               {"title": "📊 Score breakdown", "rows": ["CPU: 10/40"]}])
        self.assertEqual(rescore.earlier_hardware_rows(sections), ["RAM: 16 GB"])
        self.assertEqual(rescore.earlier_hardware_rows("not json"), [])


if __name__ == "__main__":
    unittest.main()
