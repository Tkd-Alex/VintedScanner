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
        self.assertEqual(result["cpu_detail"], "8 core / 8 thread · nominal TDP 35 W · Coffee Lake Refresh, 2019")

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
        self.assertIn("exact CPU/core count to be verified", result["missing"])

    def test_cpu_weight_outweighs_ram_difference(self):
        stronger = enricher.analyze_hardware(
            "Lenovo ThinkCentre M920q Tiny i5-8600T 8GB RAM 256GB SSD", price="150 EUR"
        )
        older = enricher.analyze_hardware(
            "Lenovo ThinkCentre M910q Tiny i7-7700T 32GB RAM 256GB SSD", price="150 EUR"
        )
        self.assertTrue(stronger["target_system"])
        # i5-8600T PassMark 9149 MT / 2261 ST against the i7-12700T ceiling.
        self.assertEqual(stronger["score_parts"]["cpu"], 20)
        self.assertEqual(stronger["score_parts"]["cpu_source"], "benchmark")
        self.assertGreater(stronger["score_parts"]["cpu"], older["score_parts"]["cpu"])
        self.assertGreater(stronger["score"], older["score"])
        self.assertEqual(
            sum(stronger["score_parts"][part] for part in ("cpu", "price", "ram", "storage", "platform")),
            stronger["score"],
        )

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
        self.assertEqual(result["cpu_detail"], "6 core / 6 thread · nominal TDP 65 W · Coffee Lake Refresh, 2019")

    def test_lines_are_grouped_in_sections(self):
        item = {
            "title": "Test <mini>", "price": "100 EUR", "url": "https://vinted.test/1",
            "description": "", "hardware_analysis": enricher.analyze_hardware("i5-7500T 16GB RAM 512GB SSD")
        }
        lines = enricher.build_telegram_lines(item, "Tiny")
        self.assertTrue(any("<b>🖥 Hardware</b>" in line for line in lines))
        self.assertTrue(any(line.startswith("<blockquote expandable><b>🔎 To check</b>") for line in lines))
        self.assertFalse(any("HARDWARE ANALYSIS" in line for line in lines))

    def test_telegram_lines_escape_listing_text_and_headline_shows_verdict(self):
        analysis = enricher.analyze_hardware(
            "Lenovo ThinkCentre M900 Tiny 8GB RAM 256GB SSD",
            "Seller <script> & co " + "parola " * 120,
            price="120.00 EUR",
        )
        lines = enricher.build_telegram_lines({"title": "x", "hardware_analysis": analysis})
        text = "\n".join(lines)
        self.assertIn("Seller &lt;script&gt; &amp; co", text)
        self.assertNotIn("<script>", text)
        self.assertIn("parola …</blockquote>", text)
        headline = enricher.build_telegram_headline({"hardware_analysis": analysis})
        self.assertIn(f"<b>{analysis['score']}/100</b>", headline)
        self.assertIn(analysis["verdict"], headline)
        self.assertIn("With 16 GB: <b>€", text)

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

    def test_dry_run_titles_with_missing_cpu_models_are_parsed(self):
        cases = {
            "Lenovo ThinkCentre M70Q Tiny i5-10400t | 16 GB | 256GB Nvme + 500HDD": "Intel Core i5-10400T",
            "HP ProDesk 400 G4 mini 8500T/256go/8go": "Intel Core i5-8500T",
            "HP EliteDesk 800 G2 Mini, I-5 6500": "Intel Core i5-6500",
            "Mini PC HP EliteDesk 800 G3 Mini | Intel i7-7700 | 16GB RAM": "Intel Core i7-7700",
            "Lenovo ThinkCentre M70q Tiny, i3-10100T": "Intel Core i3-10100T",
            "HP ProDesk 405 G6 Mini PC Ryzen 3 PRO 3200GE 8GB RAM 256GB SSD": "AMD Ryzen 3 PRO 3200GE",
            "Lenovo ThinkCentre M715q Tiny AMD PRO A12-9800E 8GB": "AMD PRO A12-9800E",
        }
        for title, expected in cases.items():
            with self.subTest(title=title):
                self.assertEqual(enricher.analyze_hardware(title)["cpu"], expected)

    def test_bare_number_is_not_guessed_when_ambiguous(self):
        self.assertIsNone(enricher.analyze_hardware("Mini PC 1234T 8GB")["cpu"])

    def test_french_go_and_to_units_are_read(self):
        result = enricher.analyze_hardware(
            "Dell OptiPlex 3080 Micro – i5 10e génération – 32 Go RAM – SSD 512 Go"
        )
        self.assertEqual(result["ram_gb"], 32)
        self.assertIn("512 GB SSD", result["storage"])
        result = enricher.analyze_hardware("HP ProDesk 600 G4 Mini – 16 Go RAM – 1,25 To SSD Samsung")
        self.assertEqual(result["ram_gb"], 16)
        self.assertIn("1.25 TB SSD", result["storage"])
        result = enricher.analyze_hardware("HP ProDesk 400 G4 mini 8500T/256go/8go")
        self.assertEqual(result["ram_gb"], 8)

    def test_vpro_platform_with_vpro_cpu_scores_higher_than_non_vpro_chassis(self):
        m920q = enricher.analyze_hardware(
            "Lenovo ThinkCentre M920q Tiny i5-8500T 16GB RAM 256GB NVMe", price="180 EUR"
        )
        m720q = enricher.analyze_hardware(
            "Lenovo ThinkCentre M720q Tiny i5-8500T 16GB RAM 256GB NVMe", price="180 EUR"
        )
        self.assertTrue(m920q["remote_management"].startswith("likely"))
        self.assertTrue(m720q["remote_management"].startswith("no"))
        self.assertGreater(m920q["score_parts"]["platform"], m720q["score_parts"]["platform"])
        self.assertTrue(m920q["platform"]["pcie_slot"])

    def test_non_vpro_cpu_on_vpro_chipset_is_reported(self):
        result = enricher.analyze_hardware("HP EliteDesk 800 G4 Mini i5-8400T 8GB RAM 256GB SSD")
        self.assertIn("lacks vPro", result["remote_management"])

    def test_legacy_platform_is_warned_and_penalized(self):
        legacy = enricher.analyze_hardware(
            "Lenovo ThinkCentre M710q Tiny i5-7500T 16GB RAM 256GB SSD", price="150 EUR"
        )
        modern = enricher.analyze_hardware(
            "Lenovo ThinkCentre M720q Tiny i5-8500T 16GB RAM 256GB SSD", price="150 EUR"
        )
        self.assertIn("Legacy platform", legacy["legacy_note"])
        self.assertIsNone(modern["legacy_note"])
        self.assertLess(legacy["score_parts"]["platform"], modern["score_parts"]["platform"])
        lines = enricher.build_telegram_lines({"title": "x", "hardware_analysis": legacy})
        self.assertTrue(any(line.startswith("⚠️ Legacy platform") for line in lines))

    def test_unknown_cpu_on_known_platform_is_estimated_not_zero(self):
        result = enricher.analyze_hardware(
            "Dell OptiPlex 3080 Micro presque neuf – i5 10e génération – 32 Go RAM", price="380 EUR"
        )
        self.assertIsNone(result["cpu"])
        self.assertTrue(result["cpu_estimated"])
        self.assertEqual(result["score_parts"]["cpu_source"], "estimated")
        self.assertGreater(result["score_parts"]["cpu"], enricher.UNKNOWN_CPU_POINTS)
        celeron = enricher.analyze_hardware(
            "Lenovo ThinkCentre M700 Tiny Celeron G3900T 4GB RAM", price="60 EUR"
        )
        self.assertGreater(result["score"], celeron["score"])

    def test_non_t_cpu_in_35w_chassis_is_read_as_t_variant(self):
        result = enricher.analyze_hardware(
            "Dell OptiPlex 7060 Micro Mini PC – i7 8700 / 8GB RAM / 256GB NVMe SSD"
        )
        self.assertEqual(result["cpu"], "Intel Core i7-8700T")
        self.assertIn("35 W chassis", result["cpu_note"])
        hp = enricher.analyze_hardware("HP EliteDesk 800 G5 Mini i5-9500 16GB RAM")
        self.assertEqual(hp["cpu"], "Intel Core i5-9500")

    def test_upgrade_message_distinguishes_missing_cpu_price_and_unknown_ram(self):
        unknown_ram = enricher.analyze_hardware(
            "Lenovo ThinkCentre M700 Tiny", price="100 EUR", cpu_upgrade_costs_eur={"i76700t": 68}
        )
        lines = enricher.build_telegram_lines({"title": "x", "hardware_analysis": unknown_ram})
        self.assertTrue(any("installed RAM unknown" in line for line in lines))
        self.assertFalse(any("price not configured" in line for line in lines))

        no_price = enricher.analyze_hardware("Lenovo ThinkCentre M700 Tiny Celeron G3900T 4GB RAM", price="60 EUR")
        lines = enricher.build_telegram_lines({"title": "x", "hardware_analysis": no_price})
        self.assertTrue(any("CPU upgrade price not configured" in line for line in lines))

    def test_amd_desktop_apu_in_35w_mini_is_read_as_ge_variant(self):
        result = enricher.analyze_hardware(
            "HP EliteDesk 705 G5 Desktop Mini Windows 11 pro - reconditionné",
            "Caractéristiques : * AMD Ryzen 3 PRO 3200G – 4 cœurs * 12 Go de RAM DDR4",
        )
        self.assertEqual(result["cpu"], "AMD Ryzen 3 PRO 3200GE")
        self.assertIn("35 W chassis", result["cpu_note"])

    def test_cpu_stated_late_in_a_long_description_is_found(self):
        page = (
            '<meta name="description" content="Lenovo M920q - ' + "Ottime condizioni. " * 40
            + 'Processore Intel Core i5-8500T.">'
        )
        session = Mock()
        session.get.return_value = Mock(status_code=200, text=page, raise_for_status=Mock())
        description = enricher.fetch_listing_description("https://example.test/i", session, title="Lenovo M920q")
        self.assertGreater(len(description), enricher.MAX_DESCRIPTION_CHARS)
        result = enricher.analyze_hardware("Lenovo ThinkCentre M920q Tiny", description)
        self.assertEqual(result["cpu"], "Intel Core i5-8500T")
        self.assertLessEqual(len(result["description"]), enricher.MAX_DESCRIPTION_CHARS)



class CpuCatalogBenchmarkTests(unittest.TestCase):
    def test_every_catalog_cpu_has_benchmark_and_architecture(self):
        for key, cpu in enricher.CPU_CATALOG.items():
            with self.subTest(key=key):
                self.assertGreater(cpu["bench_mt"], cpu["bench_st"] / 2)
                self.assertIn("architecture", cpu)
                self.assertGreaterEqual(cpu["launch_year"], 2015)

    def test_benchmark_table_only_references_catalog_keys(self):
        self.assertLessEqual(set(enricher.CPU_BENCHMARKS), set(enricher.CPU_CATALOG))
        self.assertTrue(enricher.BENCHMARK_SOURCE)
        self.assertTrue(enricher.BENCHMARK_VERSION)

    def test_t_and_non_t_skus_keep_their_own_benchmarks(self):
        t_part = enricher.CPU_CATALOG["i512500t"]
        full = enricher.CPU_CATALOG["i512500"]
        self.assertLess(t_part["bench_mt"], full["bench_mt"])
        self.assertEqual(enricher._canonical_cpu("i5-12500T 16GB")[0], "Intel Core i5-12500T")
        self.assertEqual(enricher._canonical_cpu("i5 12500 16GB")[0], "Intel Core i5-12500")

    def test_generation_and_architecture_lookup(self):
        self.assertEqual(enricher.CPU_CATALOG["i59500t"]["architecture"], "Coffee Lake Refresh")
        self.assertEqual(enricher.CPU_CATALOG["i512500t"]["architecture"], "Alder Lake")
        self.assertEqual(enricher.CPU_CATALOG["ryzen5pro4650ge"]["architecture"], "Renoir (Zen 2)")

    def test_newer_t_series_parts_are_recognized(self):
        for title, expected in (
            ("Lenovo M70q Gen 3 i5-12400T", "Intel Core i5-12400T"),
            ("HP EliteDesk 800 G9 Mini i5-13500T", "Intel Core i5-13500T"),
            ("Dell OptiPlex 7090 Micro i5-11400T", "Intel Core i5-11400T"),
        ):
            with self.subTest(title=title):
                self.assertEqual(enricher._canonical_cpu(title)[0], expected)


class BenchmarkScoringTests(unittest.TestCase):
    def test_benchmark_is_used_and_tier_is_only_a_fallback(self):
        fraction, source = enricher.cpu_performance("i59500t")
        self.assertEqual(source, "benchmark")
        self.assertAlmostEqual(fraction, 0.7 * 8008 / 21129 + 0.3 * 2105 / 3558, places=6)
        fake = {"name": "Test CPU", "cores": 4, "threads": 4, "tdp_w": 35}
        with patch.dict(enricher.CPU_CATALOG, {"testcpu": fake}), \
                patch.dict(enricher.CPU_PERFORMANCE_POINTS, {"testcpu": 25}):
            self.assertEqual(enricher.cpu_performance("testcpu"), (0.5, "tier"))
        self.assertEqual(enricher.cpu_performance("not-a-cpu"), (None, None))

    def test_ceiling_cpu_and_faster_parts_are_capped(self):
        self.assertAlmostEqual(enricher.cpu_performance("i712700t")[0], 1.0)
        self.assertAlmostEqual(enricher.cpu_performance("i713700t")[0], 1.0)

    def test_i7_label_does_not_beat_a_faster_i5(self):
        def cpu_points(cpu):
            return enricher.analyze_hardware(f"Lenovo ThinkCentre M920q Tiny {cpu} 16GB RAM")["score_parts"]["cpu"]
        self.assertGreater(cpu_points("i5-8500T"), cpu_points("i7-6700T"))
        self.assertGreater(cpu_points("i5-9500T"), cpu_points("i7-7700T"))

    def test_cheaper_slower_system_can_outrank_overpriced_faster_one(self):
        cheap = enricher.analyze_hardware(
            "HP EliteDesk 800 G4 Mini i5-8500T 16GB RAM 256GB NVMe", price="150 EUR")
        pricey = enricher.analyze_hardware(
            "HP EliteDesk 800 G4 Mini i5-12500T 16GB RAM 256GB NVMe", price="650 EUR")
        self.assertGreater(pricey["score_parts"]["cpu"], cheap["score_parts"]["cpu"])
        self.assertGreater(cheap["score"], pricey["score"])

    def test_unknown_cpu_never_gets_full_cpu_points(self):
        result = enricher.analyze_hardware("Lenovo ThinkCentre M920q Tiny 16GB RAM 256GB SSD")
        self.assertIsNone(result["cpu"])
        self.assertLess(result["score_parts"]["cpu"], 40)
        unknown = enricher.analyze_hardware("Lenovo ThinkCentre Tiny 16GB RAM")
        self.assertEqual(unknown["score_parts"]["cpu"], enricher.UNKNOWN_CPU_POINTS)
        self.assertEqual(unknown["score_parts"]["cpu_source"], "unknown")

    def test_upgrade_need_follows_benchmarks(self):
        # i5-8600T (9149 MT) is slower than the M920q candidate i7-8700T (10212 MT).
        slower = enricher.analyze_hardware("Lenovo ThinkCentre M920q Tiny i5-8600T 16GB RAM")
        self.assertTrue(slower["cpu_upgrade"]["needed"])
        faster = enricher.analyze_hardware("Lenovo ThinkCentre M920q Tiny i7-9700T 16GB RAM")
        self.assertFalse(faster["cpu_upgrade"]["needed"])


class ReferenceCpuTests(unittest.TestCase):
    def test_default_references_are_n100_and_i5_12500t(self):
        refs = enricher.reference_cpus()
        self.assertEqual([ref["name"] for ref in refs], ["N100", "i5-12500T"])
        self.assertEqual(refs[0]["mt"], 5304)

    def test_references_accept_names_and_skip_unknown(self):
        refs = enricher.reference_cpus(["i7-8700T", "i78700t", "nonsense"])
        self.assertEqual([ref["key"] for ref in refs], ["i78700t"])
        self.assertEqual(enricher.reference_cpus("not-a-list")[0]["key"], "n100")

    def test_ratios_and_incomplete_comparisons(self):
        result = enricher.analyze_hardware("HP ProDesk 400 G5 Mini i5-9500T 16GB RAM")
        n100, target = result["cpu_vs_reference"]
        self.assertEqual(n100["mt_ratio"], round(8008 / 5304, 2))
        self.assertTrue(n100["complete"])
        self.assertLess(target["mt_ratio"], 1)
        self.assertEqual(result["cpu_benchmark"]["mt"], 8008)
        self.assertIn("PassMark", result["cpu_benchmark"]["source"])
        incomplete = enricher.compare_to_references({"st": 2000, "mt": None}, enricher.reference_cpus())
        self.assertFalse(incomplete[0]["complete"])
        self.assertIsNone(incomplete[0]["mt_ratio"])

    def test_bench_row_shows_ratios_or_na(self):
        item = {"title": "HP ProDesk 400 G5 Mini i5-9500T 16GB RAM", "description": "", "price": "220 EUR"}
        lines = enricher.build_telegram_lines(item)
        self.assertIn("Bench: ST 2,105 · MT <b>8,008</b> · ×1.5 N100 · ×0.5 i5-12500T", lines)
        fake = {"name": "Intel Core i5-0000T", "cores": 4, "threads": 4, "tdp_w": 35}
        with patch.dict(enricher.CPU_CATALOG, {"i50000t": fake}):
            analysis = {"cpu": fake["name"], "cpu_benchmark": None}
            self.assertTrue(enricher._benchmark_row(analysis).startswith("Bench: N/A"))
        unknown = enricher.build_telegram_lines({"title": "Lenovo ThinkCentre M920q Tiny 8GB", "description": ""})
        self.assertFalse(any(line.startswith("Bench:") for line in unknown))

    def test_configured_references_are_used_by_enrich_item(self):
        item = {"title": "HP ProDesk 400 G5 Mini i5-9500T 16GB RAM", "description": "", "price": "220 EUR"}
        with patch.object(enricher, "DEFAULT_REFERENCE_CPUS", ("n100",)):
            import Config
            with patch.object(Config, "hardware_reference_cpus", ["i7-8700T"], create=True):
                enricher.enrich_item(item, fetch_details=False)
        names = [ref["name"] for ref in item["hardware_analysis"]["cpu_vs_reference"]]
        self.assertEqual(names, ["i7-8700T"])


class NewerPlatformTests(unittest.TestCase):
    def test_generation_suffix_picks_the_newer_profile(self):
        cases = {
            "Lenovo ThinkCentre M70q Gen 3 Tiny i5-12400T": ("Lenovo ThinkCentre M70q Gen 3 Tiny", 12),
            "Lenovo ThinkCentre M70q Gen2 Tiny": ("Lenovo ThinkCentre M70q Gen 2 Tiny", 11),
            "Lenovo ThinkCentre M70q Tiny i5-10400T": ("Lenovo ThinkCentre M70q Tiny", 10),
            "Lenovo ThinkCentre M90q Gen 3 Tiny": ("Lenovo ThinkCentre M90q Gen 3 Tiny", 12),
            "Lenovo ThinkCentre M90q Tiny": ("Lenovo ThinkCentre M90q Tiny", 10),
            "HP EliteDesk 800 G8 Mini i5-11500T": ("HP EliteDesk 800 G8 Mini", 11),
            "Dell OptiPlex 7090 Micro i5-11500T": ("Dell OptiPlex 7090 Micro", 11),
            "Dell OptiPlex 7040 Micro": ("Dell OptiPlex 7040 Micro", 6),
        }
        for title, (name, generation) in cases.items():
            with self.subTest(title=title):
                profile = enricher._find_platform_profile(title)
                self.assertEqual((profile["name"], profile["generation"]), (name, generation))

    def test_unconfirmed_platform_facts_stay_unknown(self):
        result = enricher.analyze_hardware("HP EliteDesk 800 G8 Mini i5-11500T 16GB RAM 256GB NVMe")
        self.assertIsNone(result["platform"]["max_tdp_w"])
        self.assertIsNone(result["platform"]["pcie_slot"])
        lines = enricher.build_telegram_lines({"title": "HP EliteDesk 800 G8 Mini i5-11500T 16GB RAM"})
        self.assertTrue(any("PCIe to verify" in line and "CPU TDP to verify" in line for line in lines))

    def test_unknown_cpu_on_12th_gen_platform_uses_typical_part(self):
        result = enricher.analyze_hardware("Lenovo ThinkCentre M70q Gen 3 Tiny i3 16GB RAM")
        self.assertTrue(result["cpu_estimated"])
        expected = round(enricher.cpu_performance("i312100t")[0] * 50 * enricher.CPU_WEIGHT
                         * enricher.ESTIMATED_CPU_FACTOR)
        self.assertEqual(result["score_parts"]["cpu"], expected)

    def test_12th_gen_cpu_fits_newer_platforms(self):
        self.assertIn("Lenovo ThinkCentre M70q Gen 3 Tiny", enricher.compatible_platforms("i512500t"))
        self.assertNotIn("Lenovo ThinkCentre M70q Gen 3 Tiny", enricher.compatible_platforms("i512500"))


if __name__ == "__main__":
    unittest.main()
