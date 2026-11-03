import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

import vinted_scanner as scanner


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

    def test_discovery_queries_can_be_disabled(self):
        with patch.object(scanner.Config, "queries", []), patch.object(
            scanner.Config, "hardware_add_discovery_queries", False
        ):
            with self.assertRaisesRegex(ValueError, "at least one search"):
                scanner.load_configuration(dry_run=True)


class DatabaseTests(unittest.TestCase):
    def test_load_creates_missing_database_and_returns_a_set(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "items.txt"

            items = scanner.load_analyzed_items(database_path)

            self.assertEqual(items, set())
            self.assertTrue(database_path.exists())

    def test_load_deduplicates_item_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "items.txt"
            database_path.write_text("123\n123\n456\n", encoding="utf-8")

            self.assertEqual(
                scanner.load_analyzed_items(database_path),
                {"123", "456"},
            )


class NotificationTests(unittest.TestCase):
    @patch("vinted_scanner.send_telegram_message", return_value=True)
    @patch("vinted_scanner.send_email", return_value=True)
    @patch("vinted_scanner.send_slack_message", return_value=True)
    def test_configured_channels_can_force_telegram_only(self, send_slack, send_email, send_telegram):
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
        self.assertIn("Matched query: gl.&lt;inet&gt;", call.kwargs["params"]["text"])

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


class ItemProcessingTests(unittest.TestCase):
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
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "items.txt"
            database_path.touch()
            analyzed_items = set()

            succeeded = scanner.process_item(
                self.item,
                analyzed_items,
                dry_run=False,
                database_path=database_path,
                matched_query="gl.inet",
            )

            self.assertFalse(succeeded)
            self.assertEqual(database_path.read_text(encoding="utf-8"), "")
            self.assertEqual(analyzed_items, set())
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
    def test_successful_notification_saves_item(self, send_notifications):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "items.txt"
            database_path.touch()
            analyzed_items = set()

            succeeded = scanner.process_item(
                self.item,
                analyzed_items,
                dry_run=False,
                database_path=database_path,
            )

            self.assertTrue(succeeded)
            self.assertEqual(database_path.read_text(encoding="utf-8"), "123\n")
            self.assertEqual(analyzed_items, {"123"})
            send_notifications.assert_called_once()

    @patch("vinted_scanner.print_dry_run_item")
    def test_dry_run_prints_and_saves_item(self, print_item):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "items.txt"
            database_path.touch()
            analyzed_items = set()

            succeeded = scanner.process_item(
                self.item,
                analyzed_items,
                dry_run=True,
                database_path=database_path,
                matched_query="gl.inet",
            )

            self.assertTrue(succeeded)
            self.assertEqual(database_path.read_text(encoding="utf-8"), "123\n")
            self.assertEqual(analyzed_items, {"123"})
            print_item.assert_called_once()
            self.assertEqual(print_item.call_args.args[-1], "gl.inet")

    @patch("vinted_scanner.send_notifications")
    @patch.object(scanner.Config, "hardware_fetch_details", False)
    def test_non_target_listing_is_skipped_without_poisoning_notified_database(self, send_notifications):
        item = dict(self.item)
        item["id"] = 987
        item["title"] = "GEEKOM A6 Ryzen 7 6800H 32GB RAM 1TB SSD"
        item["description"] = item["title"]
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "items.txt"
            database_path.touch()
            analyzed_items = set()
            succeeded = scanner.process_item(
                item, analyzed_items, dry_run=False, database_path=database_path
            )
            self.assertTrue(succeeded)
            self.assertEqual(analyzed_items, set())
            self.assertEqual(database_path.read_text(encoding="utf-8"), "")
            send_notifications.assert_not_called()

    @patch("vinted_scanner.send_notifications", return_value=True)
    @patch.object(scanner.Config, "hardware_fetch_details", False)
    def test_m700_usff_candidate_is_not_filtered_and_is_notified(self, send_notifications):
        item = dict(self.item)
        item["id"] = 988
        item["title"] = "Lenovo ThinkCentre M700"
        item["description"] = "Ultra Small Form Factor (USFF), Celeron G3900T, 4 GB DDR4, HDD 500 GB"
        item["price"] = {"amount": "49", "currency_code": "EUR"}
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "items.txt"
            database_path.touch()
            analyzed_items = set()
            succeeded = scanner.process_item(
                item, analyzed_items, dry_run=False, database_path=database_path
            )
            self.assertTrue(succeeded)
            self.assertEqual(analyzed_items, {"988"})
            self.assertEqual(database_path.read_text(encoding="utf-8"), "988\n")
            send_notifications.assert_called_once()
            extra_lines = send_notifications.call_args.kwargs["extra_lines"]
            self.assertTrue(any("i7-6700T" in line for line in extra_lines))


class MainTests(unittest.TestCase):
    @patch("vinted_scanner.process_queries", return_value=True)
    @patch("vinted_scanner.initialize_vinted_session")
    @patch("vinted_scanner.load_analyzed_items", return_value=set())
    @patch("vinted_scanner.load_configuration")
    @patch("vinted_scanner.configure_logging")
    def test_notification_failure_produces_exit_code_one(
        self,
        configure_logging,
        load_configuration,
        load_analyzed_items,
        initialize_session,
        process_queries,
    ):
        load_configuration.return_value = ("catalog-url", [{}], {})
        initialize_session.return_value.cookies.get.return_value = None

        with patch.object(scanner.Config, "ram_hunt_enabled", False):
            exit_code = scanner.main(dry_run=False)

        self.assertEqual(exit_code, 1)
        configure_logging.assert_called_once()
        load_analyzed_items.assert_called_once_with()
        process_queries.assert_called_once()


if __name__ == "__main__":
    unittest.main()


class RamHuntTests(unittest.TestCase):
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
        self.assertIn("RAM non specificata", candidate["tier"])

    def test_ram_hunt_flags_32gb_ddr4_listing_under_100_without_fault_claim(self):
        item = {
            "id": 2, "title": "Dell Latitude laptop 32GB DDR4 RAM",
            "url": "https://www.vinted.it/items/2",
            "price": {"amount": "89", "currency_code": "EUR"},
        }
        candidate = scanner.classify_ram_hunt_candidate(item, 100)
        self.assertIsNotNone(candidate)
        self.assertIn("32 GB dichiarati", candidate["tier"])

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
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "ram_hunt_items.txt"
            analyzed = set()
            result = scanner.process_ram_hunt_item(
                item, analyzed, dry_run=False, max_price_eur=100,
                database_path=database_path, matched_query="portatile guasto",
            )
            self.assertTrue(result)
            self.assertEqual(analyzed, {"6"})
            self.assertEqual(database_path.read_text(encoding="utf-8"), "6\n")
        send_notifications.assert_called_once()
        extra_lines = send_notifications.call_args.kwargs["extra_lines"]
        self.assertTrue(any("RAM HUNT" in line for line in extra_lines))
