import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

import run_report
import vinted_scanner as scanner


def http_error(status, retry_after=None):
    response = Mock(status_code=status, headers={"Retry-After": retry_after} if retry_after else {})
    return requests.exceptions.HTTPError(f"{status} error", response=response)


class FreshReportMixin:
    def setUp(self):
        self.report = run_report.RunReport(clock=lambda: 0.0)
        patcher = patch.object(scanner, "run_report", self.report)
        patcher.start()
        self.addCleanup(patcher.stop)
        scanner._catalog_state["paused"] = False
        scanner._detail_fetch_state["paused"] = False
        self.addCleanup(scanner._catalog_state.update, paused=False)
        self.addCleanup(scanner._detail_fetch_state.update, paused=False)


class RunReportMessageTests(unittest.TestCase):
    def test_quiet_run_says_nothing_matched_per_mode(self):
        report = run_report.RunReport(clock=lambda: 0.0)
        stats = report.mode("Mini PC (Proxmox)")
        stats.searches, stats.listings, stats.new = 12, 288, 4
        report.mode("RAM Hunt").searches = 7
        text = report.build_message()
        self.assertTrue(text.startswith("✅ <b>Vinted scan finished</b> · nothing new matched"))
        self.assertIn("• Mini PC (Proxmox): nothing matched · 12 searches, 288 listings, 4 new", text)
        self.assertIn("• RAM Hunt: nothing matched · 7 searches", text)

    def test_matches_are_counted_in_headline(self):
        report = run_report.RunReport(clock=lambda: 0.0)
        stats = report.mode("CPU Hunt")
        stats.searches, stats.matches, stats.notified = 1, 2, 2
        self.assertIn("2 matches notified", report.headline())

    def test_issues_are_grouped_escaped_and_capped(self):
        report = run_report.RunReport(clock=lambda: 0.0)
        for index in range(5):
            report.add_issue("network", f"search <{index}> timed out")
        report.add_issue("rate_limit", "429")
        text = report.build_message()
        self.assertTrue(text.startswith("⚠️ <b>Vinted scan finished with 6 problems</b>"))
        self.assertIn("<b>🌐 Network error</b> ×5", text)
        self.assertIn("• search &lt;0&gt; timed out", text)
        self.assertNotIn("search &lt;3&gt;", text)
        self.assertIn("… and 2 more", text)
        self.assertIn("<b>🚦 Vinted rate limit (HTTP 429)</b>", text)

    def test_fatal_issue_marks_the_run_failed(self):
        report = run_report.RunReport(clock=lambda: 0.0)
        report.add_issue("crash", "boom", fatal=True)
        self.assertTrue(report.headline().startswith("🔴 <b>Vinted scan failed</b>"))

    def test_long_message_is_truncated_for_telegram(self):
        text = "\n".join("x" * 100 for _ in range(100))
        truncated = run_report.truncate_message(text)
        self.assertLessEqual(len(truncated), run_report.MAX_MESSAGE_CHARS)
        self.assertTrue(truncated.endswith("(truncated, see vinted_scanner.log)"))

    def test_pending_messages_round_trip_and_clear(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pending.json"
            self.assertEqual(run_report.load_pending_messages(path), [])
            run_report.save_pending_messages(path, [f"m{i}" for i in range(15)])
            loaded = run_report.load_pending_messages(path)
            self.assertEqual(len(loaded), run_report.MAX_PENDING_MESSAGES)
            self.assertEqual(loaded[-1], "m14")
            run_report.save_pending_messages(path, [])
            self.assertFalse(path.exists())


class CatalogueErrorTests(FreshReportMixin, unittest.TestCase):
    def test_catalogue_errors_are_classified(self):
        session = Mock()
        session.get.side_effect = requests.exceptions.ConnectionError("dns failure")
        with self.assertRaises(scanner.CatalogError) as raised:
            scanner.get_catalog_items(session, "url", {}, {})
        self.assertEqual(raised.exception.kind, "network")

        session.get.side_effect = None
        session.get.return_value.raise_for_status.side_effect = http_error(429, "9")
        with self.assertRaises(scanner.CatalogError) as raised:
            scanner.get_catalog_items(session, "url", {}, {})
        self.assertEqual(raised.exception.kind, "rate_limit")
        self.assertEqual(raised.exception.retry_after, 9.0)

        session.get.return_value.raise_for_status.side_effect = http_error(403)
        with self.assertRaises(scanner.CatalogError) as raised:
            scanner.get_catalog_items(session, "url", {}, {})
        self.assertEqual(raised.exception.kind, "http")

    @patch("vinted_scanner.time.sleep")
    @patch("vinted_scanner.get_catalog_items")
    def test_catalogue_429_retries_once_then_skips_remaining_searches(self, get_items, sleep):
        get_items.side_effect = scanner.CatalogError("429", kind="rate_limit", retry_after=5)
        with self.assertLogs(level="WARNING"):
            failed = scanner.process_queries(
                Mock(), "url", [{"search_text": "a"}, {"search_text": "b"}], {}, set(), False
            )
        self.assertTrue(failed)
        self.assertEqual(get_items.call_count, 2)
        sleep.assert_called_once_with(5)
        stats = self.report.mode(scanner.MINI_PC_LABEL)
        self.assertEqual((stats.searches, stats.failed_searches, stats.skipped_searches), (1, 1, 1))
        self.assertEqual(len(self.report.issues["rate_limit"]), 2)

    @patch("vinted_scanner.get_catalog_items")
    def test_network_failure_of_a_search_is_reported(self, get_items):
        get_items.side_effect = scanner.CatalogError("down", kind="network")
        with self.assertLogs(level="ERROR"):
            scanner.process_queries(Mock(), "url", [{"search_text": "M720q"}], {}, set(), False)
        self.assertIn("M720q", self.report.issues["network"][0])

    @patch("vinted_scanner.process_item", side_effect=IndexError("regex group missing"))
    @patch("vinted_scanner.get_catalog_items", return_value=[{"id": 1, "title": "A"}, {"id": 2}])
    def test_processing_error_is_reported_and_other_listings_continue(self, get_items, process_item):
        with self.assertLogs(level="ERROR"):
            failed = scanner.process_queries(Mock(), "url", [{"search_text": "x"}], {}, set(), False)
        self.assertTrue(failed)
        self.assertEqual(process_item.call_count, 2)
        self.assertEqual(len(self.report.issues["processing"]), 2)
        self.assertIn("IndexError: regex group missing", self.report.issues["processing"][0])

    @patch("vinted_scanner.time.sleep")
    @patch("vinted_scanner.hardware_enricher.fetch_listing_description")
    def test_listing_page_429_is_reported(self, fetch, sleep):
        fetch.side_effect = scanner.hardware_enricher.ListingRateLimited(None)
        item = {"url": "https://example.test/items/1", "title": "ThinkCentre"}
        with self.assertLogs(level="WARNING"):
            scanner.fetch_item_description(item)
        self.assertEqual(len(self.report.issues["rate_limit"]), 2)

    def test_listing_page_network_error_is_reported(self):
        session = Mock()
        session.get.side_effect = requests.exceptions.Timeout("slow")
        item = {"url": "https://example.test/items/1", "title": "ThinkCentre M720q"}
        with patch("vinted_scanner.time.sleep"), self.assertLogs(level="WARNING"):
            self.assertEqual(scanner.fetch_item_description(item, session), "")
        self.assertEqual(self.report.issues["listing_page"], ["ThinkCentre M720q: timed out"])


class RunSummaryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.pending_path = Path(directory.name) / "pending.json"
        for name, value in {
            "PENDING_ALERTS_PATH": self.pending_path,
            "DATABASE_PATH": Path(directory.name) / "test.db",
            "LEGACY_LEDGER_PATHS": {},
            "LEGACY_FILTERED_PATH": Path(directory.name) / "filtered.txt",
            "configure_logging": Mock(),
            "wait_after_telegram_message": Mock(),
        }.items():
            patcher = patch.object(scanner, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name, value in {
            "telegram_bot_token": "token", "telegram_chat_id": "123",
            "telegram_run_summary": "always",
        }.items():
            patcher = patch.object(scanner.Config, name, value, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)

    @patch("vinted_scanner.post_telegram_message", return_value=True)
    @patch("vinted_scanner.run_scan", return_value=0)
    def test_quiet_run_still_sends_a_summary(self, run_scan, post):
        def quiet_scan(dry_run):
            scanner.run_report.mode(scanner.MINI_PC_LABEL).searches = 3
            return 0
        run_scan.side_effect = quiet_scan
        self.assertEqual(scanner.main(), 0)
        post.assert_called_once()
        self.assertIn("nothing new matched", post.call_args.args[0])
        self.assertIn("Mini PC (Proxmox): nothing matched", post.call_args.args[0])

    @patch("vinted_scanner.post_telegram_message", return_value=True)
    @patch("vinted_scanner.run_scan", side_effect=ZeroDivisionError("bug"))
    def test_crash_is_reported(self, run_scan, post):
        with self.assertLogs(level="ERROR"):
            self.assertEqual(scanner.main(), 1)
        self.assertIn("Vinted scan failed", post.call_args.args[0])
        self.assertIn("ZeroDivisionError: bug", post.call_args.args[0])

    @patch("vinted_scanner.post_telegram_message", return_value=True)
    @patch("vinted_scanner.initialize_vinted_session", side_effect=http_error(403))
    @patch("vinted_scanner.load_configuration", return_value=("url", [{}], {}))
    def test_session_failure_is_reported(self, load_configuration, init_session, post):
        with patch.object(scanner.Config, "ram_hunt_enabled", False), self.assertLogs(level="ERROR"):
            self.assertEqual(scanner.main(), 1)
        text = post.call_args.args[0]
        self.assertIn("Vinted scan failed", text)
        self.assertIn("HTTP 403; no search was run", text)

    @patch("vinted_scanner.run_scan", return_value=0)
    def test_undelivered_summary_is_sent_late_by_the_next_run(self, run_scan):
        with patch("vinted_scanner.post_telegram_message", return_value=False):
            scanner.main()
        self.assertEqual(len(run_report.load_pending_messages(self.pending_path)), 1)

        with patch("vinted_scanner.post_telegram_message", return_value=True) as post:
            scanner.main()
        self.assertEqual(post.call_count, 2)
        self.assertIn("Delivered late", post.call_args_list[0].args[0])
        self.assertFalse(self.pending_path.exists())

    @patch("vinted_scanner.post_telegram_message", return_value=True)
    @patch("vinted_scanner.run_scan", return_value=0)
    def test_problems_mode_skips_quiet_runs(self, run_scan, post):
        with patch.object(scanner.Config, "telegram_run_summary", "problems"):
            scanner.main()
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
