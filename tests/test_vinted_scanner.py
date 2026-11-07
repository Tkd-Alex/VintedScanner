import json
import tempfile
import unittest
import unittest.mock
from pathlib import Path
from unittest.mock import Mock, patch

import requests

import storage
import vinted_scanner as scanner


class TempStoreMixin:
    """Point the scanner at a temporary SQLite store."""

    def setUp(self):
        super().setUp()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = storage.Store(Path(directory.name) / "test.db")
        self.addCleanup(self.store.close)
        patcher = patch.object(scanner, "store", self.store)
        patcher.start()
        self.addCleanup(patcher.stop)

    def listing(self, item_id, mode="mini_pc"):
        return self.store.db.execute(
            "SELECT * FROM listings WHERE mode = ? AND item_id = ?", (mode, str(item_id))
        ).fetchone()

    def status(self, item_id, mode="mini_pc"):
        row = self.listing(item_id, mode)
        return row["status"] if row else None


class ConfigurationTests(unittest.TestCase):
    def test_build_catalog_url_uses_marketplace_domain(self):
        self.assertEqual(
            scanner.build_catalog_url("https://www.vinted.it"),
            "https://api.vinted.it/svc-catalogue/items",
        )

    def test_build_catalog_params_encodes_dynamic_filters(self):
        params = scanner.build_catalog_params(
            {
                "page": "1",
                "per_page": "96",
                "search_text": "jeans",
                "order": "newest_first",
                "filters": {"brand": [417, "53"], "size": []},
            }
        )

        self.assertEqual(params["attribute_ids[brand]"], "417,53")
        self.assertNotIn("attribute_ids[size]", params)

    def test_build_catalog_params_rejects_legacy_keys(self):
        with self.assertRaisesRegex(ValueError, "legacy query keys"):
            scanner.build_catalog_params(
                {
                    "page": "1",
                    "per_page": "96",
                    "order": "newest_first",
                    "filters": {},
                    "catalog_ids": "2996",
                }
            )

    def test_empty_custom_query_list_uses_default_hardware_discovery_queries(self):
        with patch.object(scanner.Config, "queries", []), patch.object(
            scanner.Config, "hardware_add_discovery_queries", True
        ):
            _catalog_url, queries, _headers = scanner.load_configuration(dry_run=True)
        search_terms = {query.get("search_text") for query in queries}
        self.assertIn("ThinkCentre M700", search_terms)
        self.assertIn("ThinkCentre M920", search_terms)
        self.assertIn("EliteDesk Mini", search_terms)
        self.assertIn("OptiPlex Micro", search_terms)

    def test_discovery_queries_use_computer_catalog_and_price_range(self):
        with patch.object(scanner.Config, "queries", []), patch.object(
            scanner.Config, "hardware_add_discovery_queries", True
        ), patch.object(scanner.Config, "hardware_discovery_price_from", 70), patch.object(
            scanner.Config, "hardware_discovery_price_to", 300
        ):
            _catalog_url, queries, _headers = scanner.load_configuration(dry_run=True)
        for query in queries:
            self.assertEqual(query["attribute_ids[catalog]"], scanner.DISCOVERY_CATALOG_ID)
            self.assertEqual(query["price_from"], "70")
            self.assertEqual(query["price_to"], "300")
        self.assertEqual(
            scanner.DEFAULT_HARDWARE_DISCOVERY_QUERIES[0]["filters"]["catalog"],
            [scanner.DISCOVERY_CATALOG_ID],
        )

    def test_discovery_queries_can_be_disabled(self):
        with patch.object(scanner.Config, "queries", []), patch.object(
            scanner.Config, "hardware_add_discovery_queries", False
        ):
            with self.assertRaisesRegex(ValueError, "at least one search"):
                scanner.load_configuration(dry_run=True)


class DatabaseTests(unittest.TestCase):
    def test_open_store_imports_legacy_ledgers_and_loads_seen_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            (directory / "items.txt").write_text("123\n123\n456\n", encoding="utf-8")
            version = scanner.hardware_enricher.FILTER_RULES_VERSION
            (directory / "filtered.txt").write_text(f"{version}:7\nold:8\n", encoding="utf-8")
            with patch.object(scanner, "DATABASE_PATH", directory / "test.db"), patch.object(
                scanner, "LEGACY_LEDGER_PATHS", {"mini_pc": directory / "items.txt"}
            ), patch.object(scanner, "LEGACY_FILTERED_PATH", directory / "filtered.txt"):
                database = scanner.open_store()
                with patch.object(scanner, "store", database):
                    seen = scanner.load_seen_ids()
                database.close()
        self.assertEqual(seen["mini_pc"], {"123", "456", "7"})
        self.assertEqual((seen["ram"], seen["cpu"]), (set(), set()))


class NotificationTests(unittest.TestCase):
    @patch("vinted_scanner.time.sleep")
    @patch("vinted_scanner.requests.post")
    def test_telegram_rate_limit_is_retried_once_after_retry_after(self, post, sleep):
        limited = Mock(status_code=429)
        limited.json.return_value = {"ok": False, "parameters": {"retry_after": 7}}
        ok = Mock(status_code=200)
        ok.raise_for_status.return_value = None
        post.side_effect = [limited, ok]
        with self.assertLogs(level="WARNING"):
            self.assertTrue(scanner.send_telegram_message("Title", "10 EUR", "url", None))
        self.assertEqual(post.call_count, 2)
        sleep.assert_called_once_with(7.0)

    @patch("vinted_scanner.time.sleep")
    @patch("vinted_scanner.send_telegram_message", return_value=True)
    def test_telegram_messages_are_spaced(self, send_telegram, sleep):
        with patch.object(scanner.Config, "notification_channels", ["telegram"]), patch.object(
            scanner.Config, "telegram_bot_token", "token"
        ), patch.object(scanner.Config, "telegram_chat_id", "123"), patch.object(
            scanner.Config, "telegram_send_delay_seconds", 1.5
        ):
            scanner.send_notifications("Title", "10 EUR", "url", None)
        sleep.assert_called_once_with(1.5)

    @patch("vinted_scanner.time.sleep")
    @patch("vinted_scanner.send_telegram_message", return_value=True)
    @patch("vinted_scanner.send_email", return_value=True)
    @patch("vinted_scanner.send_slack_message", return_value=True)
    def test_configured_channels_can_force_telegram_only(self, send_slack, send_email, send_telegram, sleep):
        with patch.object(scanner.Config, "notification_channels", ["telegram"]), patch.object(
            scanner.Config, "smtp_username", "user@example.test"
        ), patch.object(scanner.Config, "smtp_server", "smtp.example.test"), patch.object(
            scanner.Config, "slack_webhook_url", "https://slack.example.test/hook"
        ), patch.object(scanner.Config, "telegram_bot_token", "token"), patch.object(
            scanner.Config, "telegram_chat_id", "123"
        ):
            result = scanner.send_notifications("Title", "10 EUR", "url", None, extra_lines=["CPU details"])
        self.assertTrue(result)
        send_telegram.assert_called_once()
        send_email.assert_not_called()
        send_slack.assert_not_called()

    @patch("vinted_scanner.requests.post")
    def test_telegram_escapes_html_and_sets_timeout(self, post):
        post.return_value.raise_for_status.return_value = None

        result = scanner.send_telegram_message(
            "A < B & C",
            "10 & 20 EUR",
            "https://example.test/item?a=1&b=2",
            None,
            "gl.<inet>",
        )

        self.assertTrue(result)
        call = post.call_args
        self.assertEqual(call.kwargs["timeout"], scanner.REQUEST_TIMEOUT_SECONDS)
        self.assertIn("A &lt; B &amp; C", call.kwargs["params"]["text"])
        self.assertIn("a=1&amp;b=2", call.kwargs["params"]["text"])
        self.assertIn("🔍 <i>gl.&lt;inet&gt;</i>", call.kwargs["params"]["text"])
        self.assertIn('href="https://example.test/item?a=1&amp;b=2"', call.kwargs["params"]["text"])
        self.assertEqual(
            json.loads(call.kwargs["params"]["link_preview_options"]), {"is_disabled": True}
        )

    @patch("vinted_scanner.requests.post")
    def test_telegram_puts_headline_first_and_previews_the_photo(self, post):
        post.return_value.raise_for_status.return_value = None
        scanner.send_telegram_message(
            "Title", "120.00 EUR", "https://example.test/item", "https://img.test/1.webp",
            extra_lines=["", "<b>🖥 Hardware</b>"], headline="🔴 <b>51/100</b> · Weak",
        )
        params = post.call_args.kwargs["params"]
        lines = params["text"].split("\n")
        self.assertEqual(lines[0], "🔴 <b>51/100</b> · Weak")
        self.assertEqual(lines[1], "<b>Title</b>")
        self.assertIn("💶 <b>€120</b>", lines[2])
        self.assertIn('<a href="https://img.test/1.webp">Photo</a>', lines[2])
        # Body lines are already HTML and must not be escaped a second time.
        self.assertIn("<b>🖥 Hardware</b>", params["text"])
        preview = json.loads(params["link_preview_options"])
        self.assertEqual(preview["url"], "https://img.test/1.webp")
        self.assertTrue(preview["prefer_small_media"])

    @patch("vinted_scanner.send_email", return_value=False)
    def test_all_configured_notifications_must_succeed(self, send_email):
        with patch.object(scanner.Config, "notification_channels", ["email"]), patch.object(
            scanner.Config, "smtp_username", "user@example.test"
        ), patch.object(
            scanner.Config, "smtp_server", "smtp.example.test"
        ), patch.object(
            scanner.Config, "smtp_psw", "password"
        ), patch.object(
            scanner.Config, "smtp_toaddrs", ["recipient@example.test"]
        ), patch.object(
            scanner.Config, "slack_webhook_url", ""
        ), patch.object(
            scanner.Config, "telegram_bot_token", ""
        ), patch.object(
            scanner.Config, "telegram_chat_id", ""
        ):
            result = scanner.send_notifications("Title", "10 EUR", "url", None)

        self.assertFalse(result)
        send_email.assert_called_once()


class NotificationConfigurationTests(unittest.TestCase):
    notification_defaults = {
        "smtp_username": "",
        "smtp_psw": "",
        "smtp_server": "",
        "smtp_toaddrs": ["User <user@example.test>"],
        "notification_channels": [],
        "slack_webhook_url": "",
        "telegram_bot_token": "",
        "telegram_chat_id": "",
    }

    def test_normal_run_requires_a_notification_channel(self):
        with patch.multiple(scanner.Config, **self.notification_defaults):
            with self.assertRaisesRegex(ValueError, "at least one"):
                scanner.validate_notification_config(dry_run=False)

    def test_dry_run_does_not_require_a_notification_channel(self):
        with patch.multiple(scanner.Config, **self.notification_defaults):
            scanner.validate_notification_config(dry_run=True)

    def test_partial_telegram_configuration_is_rejected(self):
        values = dict(self.notification_defaults)
        values["notification_channels"] = ["telegram"]
        values["telegram_bot_token"] = "token"

        with patch.multiple(scanner.Config, **values):
            with self.assertRaisesRegex(ValueError, "telegram_chat_id"):
                scanner.validate_notification_config(dry_run=False)

    def test_partial_email_configuration_is_rejected(self):
        values = dict(self.notification_defaults)
        values["notification_channels"] = ["email"]
        values["smtp_username"] = "user@example.test"

        with patch.multiple(scanner.Config, **values):
            with self.assertRaisesRegex(ValueError, "smtp_psw, smtp_server"):
                scanner.validate_notification_config(dry_run=False)


class CatalogueTests(unittest.TestCase):
    def test_missing_items_is_reported_as_catalog_error(self):
        response = Mock(status_code=200)
        response.json.return_value = {"message": "invalid request"}
        response.raise_for_status.return_value = None
        session = Mock()
        session.get.return_value = response

        with self.assertRaisesRegex(scanner.CatalogError, "valid 'items' list"):
            scanner.get_catalog_items(session, "url", {}, {})

    def test_request_failure_is_reported_as_catalog_error(self):
        session = Mock()
        session.get.side_effect = requests.RequestException("network error")

        with self.assertRaisesRegex(scanner.CatalogError, "request failed"):
            scanner.get_catalog_items(session, "url", {}, {})

    @patch(
        "vinted_scanner.get_catalog_items",
        side_effect=scanner.CatalogError("invalid response"),
    )
    def test_query_failure_is_returned_to_the_caller(self, get_catalog_items):
        with self.assertLogs(level="ERROR"):
            failed = scanner.process_queries(
                Mock(),
                "url",
                [{}],
                {},
                set(),
                dry_run=False,
            )

        self.assertTrue(failed)
        get_catalog_items.assert_called_once()

    @patch("vinted_scanner.process_item", return_value=False)
    @patch("vinted_scanner.get_catalog_items", return_value=[{"id": 123}])
    def test_notification_failure_is_returned_to_the_caller(
        self,
        get_catalog_items,
        process_item,
    ):
        failed = scanner.process_queries(
            Mock(),
            "url",
            [{}],
            {},
            set(),
            dry_run=False,
        )

        self.assertTrue(failed)
        get_catalog_items.assert_called_once()
        process_item.assert_called_once()
        self.assertEqual(
            process_item.call_args.kwargs["matched_query"],
            "filters only",
        )


class ItemProcessingTests(TempStoreMixin, unittest.TestCase):
    item = {
        "id": 123,
        "title": "Lenovo ThinkCentre M920q Tiny i5-8600T 16GB RAM 256GB SSD",
        "url": "https://example.test/items/123",
        "price": {"amount": "10", "currency_code": "EUR"},
        "photo": {"full_size_url": "https://example.test/image.jpg"},
        "description": "Lenovo ThinkCentre M920q Tiny i5-8600T 16GB RAM 256GB SSD",
    }

    @patch("vinted_scanner.send_notifications", return_value=False)
    def test_failed_notification_does_not_save_item(self, send_notifications):
        seen_ids = set()
        succeeded = scanner.process_item(self.item, seen_ids, dry_run=False, matched_query="gl.inet")

        self.assertFalse(succeeded)
        self.assertEqual(self.status(123), "failed")
        self.assertEqual(self.store.seen_ids("mini_pc"), set())
        self.assertEqual(seen_ids, set())
        send_notifications.assert_called_once()
        self.assertEqual(send_notifications.call_args.args[-1], "gl.inet")

    def test_relative_item_url_is_resolved_against_marketplace(self):
        with patch.object(scanner.Config, "vinted_url", "https://www.vinted.it"):
            normalized = scanner.normalize_item(
                {
                    "id": 123,
                    "title": "Test item",
                    "url": "/items/123-test-item",
                }
            )

        self.assertEqual(
            normalized["url"],
            "https://www.vinted.it/items/123-test-item",
        )

    def test_absolute_item_url_is_preserved(self):
        normalized = scanner.normalize_item(self.item)

        self.assertEqual(normalized["url"], self.item["url"])

    @patch("vinted_scanner.send_notifications", return_value=True)
    def test_successful_notification_saves_item_with_alert_data(self, send_notifications):
        seen_ids = set()
        succeeded = scanner.process_item(self.item, seen_ids, dry_run=False, matched_query="M920q")

        self.assertTrue(succeeded)
        self.assertEqual(seen_ids, {"123"})
        row = self.listing(123)
        self.assertEqual((row["status"], row["price_eur"], row["matched_query"]), ("notified", 10.0, "M920q"))
        self.assertEqual((row["cpu"], row["ram_gb"]), ("Intel Core i5-8600T", 16))
        self.assertIsNotNone(row["score"])
        self.assertEqual(row["image_url"], "https://example.test/image.jpg")
        self.assertIn("Hardware", row["sections"])
        send_notifications.assert_called_once()

    @patch("vinted_scanner.print_dry_run_item")
    def test_dry_run_prints_and_saves_item(self, print_item):
        seen_ids = set()
        succeeded = scanner.process_item(self.item, seen_ids, dry_run=True, matched_query="gl.inet")

        self.assertTrue(succeeded)
        self.assertEqual(self.status(123), "dry_run")
        self.assertEqual(seen_ids, {"123"})
        print_item.assert_called_once()
        self.assertEqual(print_item.call_args.args[-1], "gl.inet")

    @patch("vinted_scanner.send_notifications")
    @patch.object(scanner.Config, "hardware_fetch_details", False)
    def test_non_target_listing_is_skipped_without_poisoning_notified_database(self, send_notifications):
        item = dict(self.item)
        item["id"] = 987
        item["title"] = "GEEKOM A6 Ryzen 7 6800H 32GB RAM 1TB SSD"
        item["description"] = item["title"]
        succeeded = scanner.process_item(item, set(), dry_run=False)
        self.assertTrue(succeeded)
        self.assertEqual(self.status(987), "filtered")
        self.assertIsNone(self.listing(987)["notified_at"])
        send_notifications.assert_not_called()

    @patch("vinted_scanner.send_notifications", return_value=True)
    @patch.object(scanner.Config, "hardware_fetch_details", False)
    def test_m700_usff_candidate_is_not_filtered_and_is_notified(self, send_notifications):
        item = dict(self.item)
        item["id"] = 988
        item["title"] = "Lenovo ThinkCentre M700"
        item["description"] = "Ultra Small Form Factor (USFF), Celeron G3900T, 4 GB DDR4, HDD 500 GB"
        item["price"] = {"amount": "49", "currency_code": "EUR"}
        seen_ids = set()
        succeeded = scanner.process_item(item, seen_ids, dry_run=False)
        self.assertTrue(succeeded)
        self.assertEqual(seen_ids, {"988"})
        self.assertEqual(self.status(988), "notified")
        send_notifications.assert_called_once()
        extra_lines = send_notifications.call_args.kwargs["extra_lines"]
        self.assertTrue(any("i7-6700T" in line for line in extra_lines))

    @patch("vinted_scanner.time.sleep")
    @patch("vinted_scanner.send_notifications", return_value=True)
    @patch.object(scanner.Config, "hardware_fetch_details", True)
    def test_detail_fetch_uses_browser_headers_and_delay(self, send_notifications, sleep):
        item = dict(self.item)
        item["id"] = 989
        item["description"] = ""
        session = Mock()
        session.get.return_value = Mock(
            text='<meta charset="utf-8"><meta name="description" content="Seller text with i5-8600T and 16GB RAM">',
            raise_for_status=Mock(),
        )
        with patch.object(scanner.Config, "hardware_detail_fetch_delay_seconds", 0.5):
            scanner.process_item(item, set(), dry_run=False, session=session)
        headers = session.get.call_args.kwargs["headers"]
        self.assertIn("User-Agent", headers)
        self.assertTrue(headers["Referer"].startswith("https://"))
        sleep.assert_called_once_with(0.5)

    @patch("vinted_scanner.fetch_item_description", return_value="")
    @patch("vinted_scanner.send_notifications")
    @patch.object(scanner.Config, "hardware_fetch_details", True)
    def test_filtered_listing_is_versioned_and_is_not_fetched(
        self, send_notifications, fetch_description
    ):
        item = dict(self.item)
        item["id"] = 990
        item["title"] = "GEEKOM A6 Ryzen 7 6800H 32GB RAM 1TB SSD"
        item["description"] = ""
        version = scanner.hardware_enricher.FILTER_RULES_VERSION
        seen_ids = set()
        for _ in range(2):
            scanner.process_item(item, seen_ids, dry_run=False)
        self.assertEqual(self.status(990), "filtered")
        self.assertEqual(self.listing(990)["filter_version"], version)
        self.assertEqual(self.store.seen_ids("mini_pc", version), {"990"})
        self.assertEqual(self.store.seen_ids("mini_pc", version + "-next"), set())
        fetch_description.assert_not_called()
        send_notifications.assert_not_called()

    @patch("vinted_scanner.fetch_item_description", return_value="Tiny chassis, i5-6500T, 8GB RAM")
    @patch("vinted_scanner.send_notifications", return_value=True)
    @patch.object(scanner.Config, "hardware_fetch_details", True)
    def test_family_title_without_form_factor_still_fetches_description(
        self, send_notifications, fetch_description
    ):
        item = dict(self.item)
        item["id"] = 991
        item["title"] = "Lenovo ThinkCentre M700"
        item["description"] = ""
        scanner.process_item(item, set(), dry_run=False)
        fetch_description.assert_called_once()
        send_notifications.assert_called_once()

    @patch("vinted_scanner.time.sleep")
    @patch("vinted_scanner.hardware_enricher.fetch_listing_description")
    def test_rate_limited_detail_fetch_retries_once_then_pauses(self, fetch, sleep):
        fetch.side_effect = scanner.hardware_enricher.ListingRateLimited(5)
        item = {"url": "https://example.test/items/1", "title": "Lenovo ThinkCentre M720q"}
        try:
            with self.assertLogs(level="WARNING"):
                self.assertEqual(scanner.fetch_item_description(item), "")
            self.assertEqual(fetch.call_count, 2)
            self.assertIn(unittest.mock.call(5), sleep.call_args_list)
            self.assertEqual(scanner.fetch_item_description(item), "")
            self.assertEqual(fetch.call_count, 2)
        finally:
            scanner._detail_fetch_state["paused"] = False

    def test_listing_page_429_raises_only_when_requested(self):
        session = Mock()
        session.get.return_value = Mock(status_code=429, headers={"Retry-After": "12"})
        session.get.return_value.raise_for_status.side_effect = requests.exceptions.HTTPError("429")
        enricher = scanner.hardware_enricher
        self.assertEqual(enricher.fetch_listing_description("https://example.test/i", session), "")
        with self.assertRaises(enricher.ListingRateLimited) as raised:
            enricher.fetch_listing_description("https://example.test/i", session, raise_on_rate_limit=True)
        self.assertEqual(raised.exception.retry_after, 12.0)


class MainTests(unittest.TestCase):
    @patch("vinted_scanner.send_run_summary")
    @patch("vinted_scanner.process_queries", return_value=True)
    @patch("vinted_scanner.initialize_vinted_session")
    @patch("vinted_scanner.load_configuration")
    @patch("vinted_scanner.configure_logging")
    def test_notification_failure_produces_exit_code_one_and_run_is_saved(
        self, configure_logging, load_configuration, initialize_session, process_queries, summary,
    ):
        load_configuration.return_value = ("catalog-url", [{}], {})
        initialize_session.return_value.cookies.get.return_value = None

        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "test.db"
            with patch.object(scanner.Config, "ram_hunt_enabled", False), patch.object(
                scanner, "DATABASE_PATH", database_path
            ), patch.object(scanner, "LEGACY_LEDGER_PATHS", {}), patch.object(
                scanner, "LEGACY_FILTERED_PATH", Path(directory) / "filtered.txt"
            ):
                exit_code = scanner.main(dry_run=False)
            database = storage.Store(database_path)
            run = database.db.execute("SELECT * FROM runs").fetchone()
            database.close()

        self.assertEqual(exit_code, 1)
        configure_logging.assert_called_once()
        process_queries.assert_called_once()
        self.assertEqual(run["status"], "ok")
        self.assertIn("Vinted scan finished", run["summary"])
        self.assertIsNone(scanner.store)


if __name__ == "__main__":
    unittest.main()


class RamHuntTests(TempStoreMixin, unittest.TestCase):
    def test_ram_hunt_loads_default_queries_with_price_cap(self):
        with patch.object(scanner.Config, "ram_hunt_enabled", True), patch.object(
            scanner.Config, "ram_hunt_max_price_eur", 100
        ), patch.object(scanner.Config, "ram_hunt_queries", scanner.RAM_HUNT_DEFAULT_QUERIES):
            queries, cap = scanner.load_ram_hunt_configuration()
        self.assertEqual(len(queries), 7)
        self.assertEqual(cap, 100)
        self.assertTrue(all(query["price_to"] == "100" for query in queries))

    def test_ram_hunt_can_be_disabled(self):
        with patch.object(scanner.Config, "ram_hunt_enabled", False):
            queries, cap = scanner.load_ram_hunt_configuration()
        self.assertEqual(queries, [])
        self.assertEqual(cap, 100)

    def test_ram_hunt_flags_broken_laptop_with_ram_unspecified(self):
        item = {
            "id": 1, "title": "HP EliteBook 840 G5 portatile guasto",
            "url": "https://www.vinted.it/items/1",
            "price": {"amount": "45", "currency_code": "EUR"},
        }
        candidate = scanner.classify_ram_hunt_candidate(item, 100)
        self.assertIsNotNone(candidate)
        self.assertIn("RAM not specified", candidate["tier"])

    def test_ram_hunt_flags_32gb_ddr4_listing_under_100_without_fault_claim(self):
        item = {
            "id": 2, "title": "Dell Latitude laptop 32GB DDR4 RAM",
            "url": "https://www.vinted.it/items/2",
            "price": {"amount": "89", "currency_code": "EUR"},
        }
        candidate = scanner.classify_ram_hunt_candidate(item, 100)
        self.assertIsNotNone(candidate)
        self.assertIn("32 GB stated", candidate["tier"])

    def test_ram_hunt_rejects_over_budget_mini_pc_and_explicit_8gb_donor(self):
        over_budget = {
            "id": 3, "title": "HP EliteBook portatile guasto 32GB DDR4",
            "url": "https://www.vinted.it/items/3",
            "price": {"amount": "101", "currency_code": "EUR"},
        }
        mini_pc = {
            "id": 4, "title": "HP EliteDesk Mini 32GB DDR4",
            "url": "https://www.vinted.it/items/4",
            "price": {"amount": "50", "currency_code": "EUR"},
        }
        small_ram = {
            "id": 5, "title": "Dell Latitude 8GB RAM guasto",
            "url": "https://www.vinted.it/items/5",
            "price": {"amount": "60", "currency_code": "EUR"},
        }
        self.assertIsNone(scanner.classify_ram_hunt_candidate(over_budget, 100))
        self.assertIsNone(scanner.classify_ram_hunt_candidate(mini_pc, 100))
        self.assertIsNone(scanner.classify_ram_hunt_candidate(small_ram, 100))

    @patch("vinted_scanner.send_notifications", return_value=True)
    def test_ram_hunt_uses_separate_database_and_sends_candidate(self, send_notifications):
        item = {
            "id": 6, "title": "Lenovo ThinkPad T480 16GB DDR4 guasto",
            "url": "https://www.vinted.it/items/6",
            "price": {"amount": "75", "currency_code": "EUR"},
        }
        seen_ids = set()
        result = scanner.process_ram_hunt_item(
            item, seen_ids, dry_run=False, max_price_eur=100, matched_query="portatile guasto",
        )
        self.assertTrue(result)
        self.assertEqual(seen_ids, {"6"})
        row = self.listing(6, "ram")
        self.assertEqual((row["status"], row["ram_gb"]), ("notified", 16))
        self.assertTrue(row["tier"].startswith("MEDIUM"))
        self.assertIsNone(self.listing(6, "mini_pc"))
        send_notifications.assert_called_once()
        self.assertIn("RAM HUNT", send_notifications.call_args.kwargs["headline"])
        extra_lines = send_notifications.call_args.kwargs["extra_lines"]
        self.assertTrue(any("16 GB" in line for line in extra_lines))

    def test_ram_hunt_ignores_portable_non_computers_and_storage_sizes(self):
        lamp = {
            "id": 9, "title": "Faro Solare LED Portatile Campeggio/Lavoro – non funzionante",
            "price": {"amount": "4", "currency_code": "EUR"},
        }
        chromebook = {
            "id": 10, "title": "HP Chromebook 11A G6 EE-4GB RAM / 32GB- met oplader.",
            "price": {"amount": "55", "currency_code": "EUR"},
        }
        self.assertIsNone(scanner.classify_ram_hunt_candidate(lamp, 100))
        self.assertIsNone(scanner.classify_ram_hunt_candidate(chromebook, 100))
        broken = {
            "id": 11, "title": "Portatile HP guasto 16GB RAM DDR4 512GB SSD",
            "price": {"amount": "60", "currency_code": "EUR"},
        }
        self.assertIn("16 GB DDR4", scanner.classify_ram_hunt_candidate(broken, 100)["tier"])


class CpuHuntTests(TempStoreMixin, unittest.TestCase):
    targets = {key: scanner.Decimal(str(cap)) for key, cap in scanner.CPU_HUNT_DEFAULT_TARGETS.items()}

    def item(self, title, price="80", description=""):
        return {
            "id": 77, "title": title, "url": "https://example.test/items/77",
            "price": {"amount": price, "currency_code": "EUR"}, "description": description,
        }

    def test_loose_target_cpu_under_cap_is_a_candidate(self):
        candidate = scanner.classify_cpu_hunt_candidate(
            self.item("Processore Intel Core i7-8700T LGA1151", "85"), self.targets
        )
        self.assertEqual(candidate["cpu_key"], "i78700t")
        self.assertEqual(candidate["generation"], 8)
        self.assertIn("Lenovo ThinkCentre M920q Tiny", candidate["platforms"])
        self.assertNotIn("Lenovo ThinkCentre M710q Tiny", candidate["platforms"])
        lines = scanner.cpu_hunt_telegram_lines(candidate, "i7-8700T")
        self.assertTrue(lines[0].startswith("🔧 <b>CPU HUNT</b>"))
        self.assertTrue(any(line.startswith("Total: <b>€") for line in lines))

    def test_short_bare_cpu_title_is_a_candidate(self):
        candidate = scanner.classify_cpu_hunt_candidate(self.item("intel i5-9500t", "55"), self.targets)
        self.assertEqual(candidate["cpu_key"], "i59500t")
        self.assertIsNone(scanner.classify_cpu_hunt_candidate(
            self.item("Lenovo ThinkCentre M920q i5-9500T", "55"), self.targets
        ))

    def test_non_candidates_are_rejected(self):
        rejected = [
            self.item("Mini PC Lenovo ThinkCentre M920q CPU i7-8700T 16GB", "85"),
            self.item("CPU Intel i7-8750H", "40"),
            self.item("Processore Intel i7-8700 LGA1151", "60"),
            self.item("CPU Intel i7-8700T ES QS", "40"),
            self.item("Processore Intel i7-8700T", "150"),
            self.item("Portatile i7-8700T processore", "60"),
            self.item("Processore Intel Core i5-8400T", "30"),
        ]
        for item in rejected:
            with self.subTest(title=item["title"]):
                self.assertIsNone(scanner.classify_cpu_hunt_candidate(item, self.targets))

    def test_configuration_generates_one_query_per_target_with_its_cap(self):
        with patch.object(scanner.Config, "cpu_hunt_enabled", True), patch.object(
            scanner.Config, "cpu_hunt_targets", {"i78700t": 90, "i76700t": 50}
        ):
            queries, targets = scanner.load_cpu_hunt_configuration()
        self.assertEqual([q["search_text"] for q in queries], ["i7-8700T", "i7-6700T"])
        self.assertEqual([q["price_to"] for q in queries], ["90", "50"])
        self.assertEqual(targets["i76700t"], 50)

    def test_unknown_target_key_is_rejected(self):
        with patch.object(scanner.Config, "cpu_hunt_enabled", True), patch.object(
            scanner.Config, "cpu_hunt_targets", {"i99999t": 90}
        ):
            with self.assertRaisesRegex(ValueError, "unknown CPU key"):
                scanner.load_cpu_hunt_configuration()

    def test_disabled_by_default_in_sample(self):
        with patch.object(scanner.Config, "cpu_hunt_enabled", False):
            self.assertEqual(scanner.load_cpu_hunt_configuration(), ([], {}))

    @patch("vinted_scanner.send_notifications", return_value=True)
    @patch("vinted_scanner.get_catalog_items")
    def test_cpu_hunt_uses_its_own_ledger(self, get_catalog_items, send_notifications):
        get_catalog_items.return_value = [
            self.item("Processore Intel i5-9500T", "60"),
            dict(self.item("Mini PC HP EliteDesk 800 G5", "200"), id=78),
        ]
        with patch.object(scanner.Config, "cpu_hunt_fetch_details", False):
            failed = scanner.process_cpu_hunt_queries(
                Mock(), "url", [{"search_text": "i5-9500T"}], {}, set(), False, self.targets,
            )
        self.assertFalse(failed)
        self.assertEqual((self.status(77, "cpu"), self.status(78, "cpu")), ("notified", "ignored"))
        self.assertEqual(self.listing(77, "cpu")["cpu"], "Intel Core i5-9500T")
        send_notifications.assert_called_once()
        self.assertIn("CPU HUNT", send_notifications.call_args.kwargs["headline"])
