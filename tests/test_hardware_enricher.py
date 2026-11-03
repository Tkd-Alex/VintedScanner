import unittest
from unittest.mock import Mock, patch

import hardware_enricher as enricher


class HardwareAnalysisTests(unittest.TestCase):
    def test_hp_or_lenovo_listing_title_parses_i5_7500_ram_and_storage(self):
        result = enricher.analyze_hardware(
            "Lenovo M710q / i5-7500 / 16 GB di RAM / NVMe 512 GB + SSHD 500 GB / Wi-Fi"
            , "USFF; Intel HD Graphics 630"
        )
        self.assertEqual(result["cpu"], "Intel Core i5-7500")
        self.assertEqual(result["cores"], 4)
        self.assertEqual(result["threads"], 4)
        self.assertEqual(result["ram_gb"], 16)
        self.assertEqual(result["model"], "Lenovo M710q")
        self.assertEqual(result["form_factor"], "USFF")
        self.assertTrue(any("512 GB NVME" in item for item in result["storage"]))
        self.assertTrue(any("500 GB SSHD" in item for item in result["storage"]))

    def test_m920q_title_parses_six_core_i5_8600t_and_16_gb(self):
        result = enricher.analyze_hardware(
            "Lenovo ThinkCentre M920q Tiny i5-8600T | 16GB | 256GB SSD"
        )
        self.assertEqual(result["cpu"], "Intel Core i5-8600T")
        self.assertEqual(result["cores"], 6)
        self.assertEqual(result["threads"], 6)
        self.assertEqual(result["ram_gb"], 16)
        self.assertEqual(result["storage"][0], "256 GB SSD")
        self.assertEqual(result["form_factor"], "Tiny")

    def test_i7_9700t_is_not_confused_with_i7_9700(self):
        result = enricher.analyze_hardware("HP EliteDesk Mini i7-9700T 16GB RAM 512GB NVMe")
        self.assertEqual(result["cpu"], "Intel Core i7-9700T")
        self.assertEqual(result["cores"], 8)
        self.assertEqual(result["threads"], 8)
        self.assertEqual(result["cpu_detail"], "8 core / 8 thread · TDP nominale 35 W")

    def test_amd_ryzen_7_6800h_parses_integrated_gpu(self):
        result = enricher.analyze_hardware("GEEKOM A6 Ryzen 7 6800H 32GB 1TB SSD")
        self.assertEqual(result["cpu"], "AMD Ryzen 7 6800H")
        self.assertEqual(result["cores"], 8)
        self.assertEqual(result["threads"], 16)
        self.assertEqual(result["gpu"], "AMD Radeon 680M")

    def test_ram_is_not_misread_from_capacity_after_the_word_ram(self):
        result = enricher.analyze_hardware("HP EliteDesk 800 G5 Mini i7-9700T 16GB RAM 512GB NVMe")
        self.assertEqual(result["ram_gb"], 16)
        self.assertEqual(result["storage"], ["512 GB NVME"])

    def test_unknown_cpu_is_not_invented(self):
        result = enricher.analyze_hardware("Mini PC 16GB RAM 512GB SSD")
        self.assertIsNone(result["cpu"])
        self.assertIn("CPU esatta/core da verificare", result["missing"])

    def test_cpu_has_half_of_total_score_and_outweighs_ram_difference(self):
        stronger = enricher.analyze_hardware(
            "Lenovo ThinkCentre M920q Tiny i5-8600T 8GB RAM 256GB SSD", price="150 EUR"
        )
        older = enricher.analyze_hardware(
            "Lenovo ThinkCentre M910q Tiny i7-7700T 32GB RAM 256GB SSD", price="150 EUR"
        )
        self.assertTrue(stronger["target_system"])
        self.assertEqual(stronger["score_parts"]["cpu"], 34)
        self.assertGreater(stronger["score_parts"]["cpu"], older["score_parts"]["cpu"])
        self.assertEqual(sum(stronger["score_parts"][part] for part in ("cpu", "price", "ram", "storage_expandability")), stronger["score"])

    def test_ram_upgrade_costs_use_user_supplied_estimates(self):
        result = enricher.analyze_hardware(
            "Lenovo ThinkCentre M920q Tiny i5-8600T 8GB RAM 256GB SSD", price="150 EUR",
            ram_8gb_upgrade_eur=50, ram_32gb_kit_eur=130,
        )
        self.assertEqual(result["total_to_16_eur"], 200)
        self.assertEqual(result["total_to_32_eur"], 280)
        self.assertIn("1×8 GB", result["ram_upgrade_note"])

    def test_geekom_is_not_in_the_target_families(self):
        result = enricher.analyze_hardware("GEEKOM A6 Ryzen 7 6800H 32GB 1TB SSD", price="350 EUR")
        self.assertFalse(result["target_system"])
        self.assertIsNone(result["target_family"])

    def test_m700_usff_is_recognized_as_lenovo_tiny_and_has_upgrade_path(self):
        result = enricher.analyze_hardware(
            "Lenovo ThinkCentre M700",
            "Ultra Small Form Factor (USFF), Celeron G3900T, 4 GB DDR4, HDD 500 GB",
            price="49 EUR",
            ram_8gb_upgrade_eur=50,
            cpu_upgrade_costs_eur={"i76700t": 68},
        )
        self.assertTrue(result["target_system"])
        self.assertEqual(result["target_family"], "Lenovo ThinkCentre Tiny (M700 / USFF wording)")
        self.assertEqual(result["cpu"], "Intel Celeron G3900T")
        self.assertEqual(result["cores"], 2)
        self.assertEqual(result["cpu_upgrade"]["candidate_cpu"], "Intel Core i7-6700T")
        self.assertEqual(result["cpu_upgrade"]["candidate_cores"], 4)
        self.assertEqual(result["cpu_upgrade"]["candidate_threads"], 8)
        self.assertEqual(result["total_to_16_with_cpu_eur"], 167)
        self.assertEqual(result["total_to_32_with_cpu_eur"], 247)
        self.assertIsNotNone(result["upgrade_score"])
        self.assertGreater(result["upgrade_score"], result["score"])

    def test_m710q_upgrade_candidate_is_i7_7700t(self):
        result = enricher.analyze_hardware(
            "Lenovo ThinkCentre M710q Tiny Celeron G3900T 4GB RAM",
            price="80 EUR",
        )
        self.assertTrue(result["target_system"])
        self.assertEqual(result["cpu_upgrade"]["candidate_cpu"], "Intel Core i7-7700T")
        self.assertEqual(result["cpu_upgrade"]["candidate_cores"], 4)

    def test_m720q_upgrade_candidate_has_six_cores(self):
        result = enricher.analyze_hardware(
            "Lenovo ThinkCentre M720q Tiny Pentium Gold G5400T 8GB RAM 256GB SSD",
            price="90 EUR",
        )
        self.assertEqual(result["cpu"], "Intel Pentium Gold G5400T")
        self.assertEqual(result["cpu_upgrade"]["candidate_cpu"], "Intel Core i7-8700T")
        self.assertEqual(result["cpu_upgrade"]["candidate_cores"], 6)
        self.assertEqual(result["cpu_upgrade"]["candidate_threads"], 12)

    def test_m700_without_tiny_or_usff_evidence_is_not_accepted(self):
        result = enricher.analyze_hardware("Lenovo ThinkCentre M700 desktop Celeron G3900T")
        self.assertFalse(result["target_system"])

    def test_cpu_upgrade_cost_is_not_invented_when_not_configured(self):
        result = enricher.analyze_hardware(
            "Lenovo ThinkCentre M700 Tiny Celeron G3900T 4GB RAM",
            price="49 EUR",
        )
        self.assertTrue(result["cpu_upgrade"]["needed"])
        self.assertIsNone(result["cpu_upgrade"]["cost_eur"])
        self.assertIsNone(result["total_to_16_with_cpu_eur"])

    def test_non_t_variant_i5_9500_is_known(self):
        result = enricher.analyze_hardware("HP EliteDesk 800 G5 Mini i5-9500 16GB RAM 512GB SSD")
        self.assertEqual(result["cpu"], "Intel Core i5-9500")
        self.assertEqual(result["cores"], 6)
        self.assertEqual(result["cpu_detail"], "6 core / 6 thread · TDP nominale 65 W")

    def test_html_is_escaped_by_caller_and_lines_include_analysis(self):
        item = {
            "title": "Test <mini>", "price": "100 EUR", "url": "https://vinted.test/1",
            "description": "", "hardware_analysis": enricher.analyze_hardware("i5-7500T 16GB RAM 512GB SSD")
        }
        lines = enricher.build_telegram_lines(item, "Tiny")
        self.assertTrue(any("Proxmox suitability" in line for line in lines))
        self.assertTrue(any("AI:" in line for line in lines))

    def test_detail_fetch_failure_is_non_fatal(self):
        session = Mock()
        session.get.side_effect = RuntimeError("blocked")
        self.assertEqual(enricher.fetch_listing_description("https://example.test/item", session), "")

    def test_listing_page_with_meta_charset_yields_seller_description(self):
        page = (
            '<html><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width">'
            '<meta name="description" content="Lenovo M920q - Processore Intel Core '
            'i5-9500T, 16 GB RAM, SSD NVMe 256 GB, perfettamente funzionante.">'
            "</head><body></body></html>"
        )
        session = Mock()
        session.get.return_value = Mock(text=page, raise_for_status=Mock())
        headers = {"User-Agent": "test-agent"}
        description = enricher.fetch_listing_description(
            "https://example.test/item", session, headers=headers, title="Lenovo M920q"
        )
        self.assertTrue(description.startswith("Processore Intel Core i5-9500T"))
        self.assertEqual(session.get.call_args.kwargs["headers"], headers)

    def test_spare_parts_accessories_and_lots_are_not_target_systems(self):
        titles = [
            "Staffa + Antenna Wi-Fi Frontale 00XJ093 Lenovo ThinkCentre M75Q 2.4GHz",
            "Lot de pièces détachées HP EliteDesk 800 G2 Mini – Ventilateur, dissipateur",
            "Lot de 5 - Mini PC Lenovo ThinkCentre M710q Tiny - Celeron - 4 Go - HDD",
            "Alimentatore Lenovo 65W per ThinkCentre Tiny",
        ]
        for title in titles:
            with self.subTest(title=title):
                analysis = enricher.analyze_hardware(title, "", "20 EUR")
                self.assertFalse(analysis["target_system"])
                self.assertTrue(analysis["non_system"])
                self.assertFalse(enricher.is_target_system(title)[0])

    def test_system_mentioning_accessories_is_still_a_target(self):
        analysis = enricher.analyze_hardware(
            "Lenovo ThinkCentre M920q Tiny i5-8500T 16GB con antenna WiFi",
            "Alimentatore incluso, ventola silenziosa.",
            "180 EUR",
        )
        self.assertTrue(analysis["target_system"])
        self.assertFalse(analysis["non_system"])


if __name__ == "__main__":
    unittest.main()
