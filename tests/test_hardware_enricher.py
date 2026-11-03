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


if __name__ == "__main__":
    unittest.main()
