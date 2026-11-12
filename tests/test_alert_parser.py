"""The parser is fed with text produced by the real message builders, so a change
to the alert layout breaks these tests instead of silently breaking the import."""
import unittest
from unittest.mock import patch
from decimal import Decimal

import alert_parser
import hardware_enricher
import message_format as fmt
import run_report
import vinted_scanner as scanner


def plain_alert(item, headline, lines, query):
    header = scanner.telegram_header_lines(
        item["title"], item["price"], item["url"], item["image"], query, headline
    )
    return fmt.to_plain_text("\n".join([*header, *lines]))


def mini_pc_alert(title, description, price="150.0 EUR", costs=None):
    item = {"title": title, "description": description, "price": price,
            "url": "https://www.vinted.it/items/1", "image": "https://img/1.webp"}
    with patch.object(
        scanner.Config, "hardware_cpu_upgrade_costs_eur", costs or {}, create=True
    ):
        hardware_enricher.enrich_item(item, fetch_details=False)
    text = plain_alert(
        item, hardware_enricher.build_telegram_headline(item),
        hardware_enricher.build_telegram_lines(item, "ThinkCentre Tiny"), "ThinkCentre Tiny",
    )
    return item["hardware_analysis"], alert_parser.parse_alert(text)


OLD_LAYOUT_ALERT = """\
🟢 70/100 · Strong candidate
Lenovo ThinkCentre M920q Tiny i5-8500T 16GB RAM 256GB SSD
💶 €150  ·  🔗 Open listing  ·  📷 Photo
🔍 ThinkCentre Tiny

🖥 Hardware
CPU: Intel Core i5-8500T · 6 core / 6 thread · nominal TDP 35 W
RAM: 16 GB · max 32 GB
Disk: 256 GB SSD
GPU: Intel UHD Graphics 630
Base: Lenovo ThinkCentre M920q Tiny · 8th gen Intel · PCIe slot · CPU ≤65 W
vPro/AMT: likely: vPro chipset and vPro CPU (check AMT is enabled in BIOS)

💰 Cost (excl. shipping/fees)
With 16 GB: €150
With 32 GB: €280
16 GB + CPU upgrade: incomplete — add a CPU price to hardware_cpu_upgrade_costs_eur

🔧 CPU upgrade
Candidate: Intel Core i7-8700T · 6 core / 12 thread · 35 W
CPU budget: not configured — check current listings
Upgrade score: pending — CPU upgrade price not configured
   Lenovo PSREF includes an M920q configuration with i7-8700T; 6 cores / 12 threads.
   Verify exact MTM/BIOS/PSU and cooling before buying a CPU.

📊 Score breakdown
CPU 17/40 · Price 25/25 · RAM 8/10 · Disk 5/10 · Platform 15/15

🤖 AI: Basic integrated graphics; local AI mostly on CPU with small models.

🔎 To check
• check maximum RAM, free slots, power adapter and actual condition
• check storage expansion and compatibility of any upgrade
Specs are inferred from listing text and the local CPU catalog; verify with the seller.
"""


class MiniPcAlertTests(unittest.TestCase):
    def test_known_platform_alert_round_trips(self):
        analysis, data = mini_pc_alert(
            "Lenovo ThinkCentre M920q Tiny i5-8500T 16GB RAM 256GB SSD",
            "Lenovo ThinkCentre M920q Tiny, Intel Core i5-8500T, 16GB DDR4, 256GB NVMe SSD.",
        )
        self.assertEqual(data["mode"], "mini_pc")
        self.assertEqual((data["score"], data["verdict"]), (analysis["score"], analysis["verdict"]))
        self.assertEqual(data["title"], "Lenovo ThinkCentre M920q Tiny i5-8500T 16GB RAM 256GB SSD")
        self.assertEqual(data["price_eur"], 150.0)
        self.assertEqual(data["matched_query"], "ThinkCentre Tiny")
        self.assertEqual(data["cpu"], "Intel Core i5-8500T")
        self.assertEqual((data["cores"], data["threads"], data["ram_gb"]), (6, 6, 16))
        self.assertEqual(data["platform"], analysis["platform"]["name"])
        self.assertEqual(data["generation"], analysis["platform"]["generation"])
        self.assertEqual(data["total_16_eur"], analysis["total_to_16_eur"])
        self.assertEqual(data["total_32_eur"], analysis["total_to_32_eur"])
        titles = [section["title"] for section in data["sections"]]
        self.assertIn("🖥 Hardware", titles)
        self.assertIn("📊 Score breakdown", titles)
        self.assertIn("Lenovo ThinkCentre M920q", data["description"])

    def test_upgrade_score_and_cpu_totals_are_read(self):
        analysis, data = mini_pc_alert(
            "Lenovo ThinkCentre M700 Tiny Pentium G4400T 8GB 128GB SSD",
            "Lenovo ThinkCentre M700 Tiny, Pentium G4400T, 8GB RAM, 128GB SSD",
            price="80 EUR", costs={"i76700t": 68},
        )
        self.assertIsNotNone(analysis["upgrade_score"])
        self.assertEqual(data["upgrade_score"], analysis["upgrade_score"])
        self.assertEqual(data["total_16_cpu_eur"], analysis["total_to_16_with_cpu_eur"])

    def test_alert_shows_benchmark_row_and_still_parses_cpu(self):
        analysis, data = mini_pc_alert(
            "HP ProDesk 400 G5 Mini i5-9500T 16GB RAM 256GB NVMe", "", price="220 EUR",
        )
        hardware = next(section for section in data["sections"] if section["title"] == "🖥 Hardware")
        bench = next(row for row in hardware["rows"] if row.startswith("Bench: "))
        self.assertIn("MT 8,008", bench)
        self.assertIn("N100", bench)
        self.assertEqual(data["cpu"], "Intel Core i5-9500T")
        self.assertEqual((data["cores"], data["threads"]), (6, 6))

    def test_alert_from_before_benchmarks_still_parses(self):
        # A real alert layout sent before the Bench row and architecture existed;
        # Telegram history (and telegram_import.py) still contains these.
        data = alert_parser.parse_alert(OLD_LAYOUT_ALERT)
        self.assertEqual((data["mode"], data["score"], data["verdict"]), ("mini_pc", 70, "Strong candidate"))
        self.assertEqual(data["cpu"], "Intel Core i5-8500T")
        self.assertEqual((data["cores"], data["threads"], data["ram_gb"]), (6, 6, 16))
        self.assertEqual(data["storage"], "256 GB SSD")
        self.assertEqual((data["platform"], data["generation"]), ("Lenovo ThinkCentre M920q Tiny", 8))
        self.assertEqual((data["total_16_eur"], data["total_32_eur"]), (150.0, 280.0))

    def test_unrelated_text_is_not_an_alert(self):
        self.assertIsNone(alert_parser.parse_alert("HP EliteDesk 800 G3 Mini\n🏷️ 100.00 EUR"))
        self.assertIsNone(alert_parser.parse_alert("✅ Vinted scan finished · nothing new matched"))


class HuntAlertTests(unittest.TestCase):
    item = {"title": "Lenovo ThinkPad T480 32GB DDR4 guasto", "price": "75 EUR",
            "url": "https://www.vinted.it/items/6", "image": None}

    def test_ram_hunt_alert(self):
        candidate = scanner.classify_ram_hunt_candidate(
            {"title": self.item["title"], "price": {"amount": "75"}}, 100
        )
        headline, *lines = scanner.ram_hunt_telegram_lines(candidate, 100, "portatile guasto")
        data = alert_parser.parse_alert(plain_alert(self.item, headline, lines, "portatile guasto"))
        self.assertEqual(data["mode"], "ram")
        self.assertEqual(data["tier"], candidate["tier"])
        self.assertEqual((data["price_eur"], data["ram_gb"]), (75.0, 32))

    def test_cpu_hunt_alert(self):
        candidate = scanner.classify_cpu_hunt_candidate(
            {"title": "Processore Intel i5-8500T", "price": {"amount": "25"}},
            {"i58500t": Decimal("60")},
        )
        headline, *lines = scanner.cpu_hunt_telegram_lines(candidate, "i5-8500T")
        item = dict(self.item, title="Processore Intel i5-8500T", price="25 EUR")
        data = alert_parser.parse_alert(plain_alert(item, headline, lines, "i5-8500T"))
        self.assertEqual(data["mode"], "cpu")
        self.assertEqual(data["cpu"], "Intel Core i5-8500T")
        self.assertEqual((data["cores"], data["threads"], data["generation"]), (6, 6, 8))
        self.assertEqual(data["build_estimate_eur"], float(candidate["build_estimate"]))


class RunSummaryTests(unittest.TestCase):
    def report(self):
        report = run_report.RunReport(clock=lambda: 0.0)
        mini = report.mode("Mini PC (Proxmox)")
        mini.searches, mini.listings, mini.new, mini.matches, mini.notified = 12, 144, 3, 3, 2
        ram = report.mode("RAM Hunt")
        ram.searches, ram.failed_searches, ram.listings = 7, 1, 127
        report.add_issue("rate_limit", "Catalogue search “M720q”: HTTP 429")
        report.add_issue("rate_limit", "Catalogue search “M920q”: HTTP 429")
        report.add_issue("notification", "Mini PC (Proxmox): “HP Mini” (will retry next run)")
        return report

    def test_summary_round_trips(self):
        report = self.report()
        [data] = alert_parser.parse_run_summaries(fmt.to_plain_text(report.build_message(dry_run=True)))
        self.assertEqual((data["status"], data["dry_run"]), ("problems", True))
        self.assertEqual(data["modes"]["mini_pc"]["matches"], 3)
        self.assertEqual(data["modes"]["mini_pc"]["notified"], 2)
        self.assertEqual(data["modes"]["ram"]["failed_searches"], 1)
        self.assertEqual(data["started_at"], report.started_at.strftime("%Y-%m-%dT%H:%M:00Z"))
        self.assertEqual(data["duration_s"], 0)
        self.assertEqual([kind for kind, _ in data["issues"]], ["rate_limit", "rate_limit", "notification"])

    def test_availability_line_is_not_read_as_a_mode(self):
        report = self.report()
        report.availability.sold, report.availability.active = 1, 9
        text = fmt.to_plain_text(report.build_message())
        self.assertIn("• Availability: 10 checked · 1 sold", text)
        [data] = alert_parser.parse_run_summaries(text)
        self.assertEqual(set(data["modes"]), {"mini_pc", "ram"})

    def test_late_delivery_contains_every_report(self):
        messages = [self.report().build_message(), run_report.RunReport(clock=lambda: 0.0).build_message()]
        text = fmt.to_plain_text(run_report.build_late_delivery_message(messages))
        summaries = alert_parser.parse_run_summaries(text)
        self.assertEqual([s["status"] for s in summaries], ["problems", "ok"])
        self.assertTrue(all(s["delivered_late"] for s in summaries))


if __name__ == "__main__":
    unittest.main()
