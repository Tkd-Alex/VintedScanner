#!/usr/bin/env python3
"""Search Vinted and notify users about newly listed matching items."""
import argparse
import email.utils
import html
import json
import logging
import re
import smtplib
import sys
import time
from decimal import Decimal, InvalidOperation
from email.message import EmailMessage
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests

import Config
import hardware_enricher


PROJECT_DIR = Path(__file__).resolve().parent
ITEMS_DATABASE_PATH = PROJECT_DIR / "vinted_items.txt"
RAM_HUNT_DATABASE_PATH = PROJECT_DIR / "vinted_ram_hunt_items.txt"
FILTERED_DATABASE_PATH = PROJECT_DIR / "vinted_filtered_items.txt"
LOG_PATH = PROJECT_DIR / "vinted_scanner.log"
REQUEST_TIMEOUT_SECONDS = 30

WEB_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:128.0) "
        "Gecko/20100101 Firefox/128.0"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
        "image/webp,image/png,image/svg+xml,*/*;q=0.8"
    ),
    "Accept-Language": "it-IT,it;q=0.8,en-US;q=0.5,en;q=0.3",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "cross-site",
    "Sec-GPC": "1",
    "Priority": "u=0, i",
    "Pragma": "no-cache",
    "Cache-Control": "no-cache",
}

LEGACY_QUERY_KEYS = {
    "brand_ids",
    "catalog_ids",
    "color_ids",
    "material_ids",
    "size_ids",
    "status_ids",
}

CATALOG_QUERY_KEYS = {
    "currency",
    "filters",
    "order",
    "page",
    "per_page",
    "price_from",
    "price_to",
    "search_text",
}

CATALOG_ORDER_VALUES = {
    "newest_first",
    "price_high_to_low",
    "price_low_to_high",
    "relevance",
}

# RAM Hunt intentionally runs independently of the mini-PC search and its strict
# chassis filter. The search terms are configurable in Config.py.
# Vinted catalogue ID used by the sample queries for desktop computers.
DISCOVERY_CATALOG_ID = "3564"
DEFAULT_DISCOVERY_PRICE_FROM_EUR = 60
DEFAULT_DISCOVERY_PRICE_TO_EUR = 400
DEFAULT_HARDWARE_DISCOVERY_QUERIES = [
    {
        "page": "1", "per_page": "24", "search_text": term, "order": "newest_first",
        "filters": {"catalog": [DISCOVERY_CATALOG_ID]},
    }
    for term in (
        "ThinkCentre M700", "ThinkCentre M710", "ThinkCentre M720", "ThinkCentre M920",
        "ThinkCentre M70q", "ThinkCentre M90q", "ThinkCentre Tiny",
        "EliteDesk Mini", "EliteDesk 800 G4", "ProDesk Mini",
        "OptiPlex Micro", "OptiPlex 7060",
    )
]

RAM_HUNT_DEFAULT_MAX_PRICE_EUR = 100
RAM_HUNT_DEFAULT_QUERIES = [
    {"page": "1", "per_page": "24", "search_text": "portatile guasto", "order": "newest_first", "filters": {}, "price_to": "100"},
    {"page": "1", "per_page": "24", "search_text": "notebook guasto", "order": "newest_first", "filters": {}, "price_to": "100"},
    {"page": "1", "per_page": "24", "search_text": "laptop guasto", "order": "newest_first", "filters": {}, "price_to": "100"},
    {"page": "1", "per_page": "24", "search_text": "portatile non funzionante", "order": "newest_first", "filters": {}, "price_to": "100"},
    {"page": "1", "per_page": "24", "search_text": "portatile 32gb ddr4", "order": "newest_first", "filters": {}, "price_to": "100"},
    {"page": "1", "per_page": "24", "search_text": "notebook 32gb ddr4", "order": "newest_first", "filters": {}, "price_to": "100"},
    {"page": "1", "per_page": "24", "search_text": "portatile 16gb ddr4 guasto", "order": "newest_first", "filters": {}, "price_to": "100"},
]

LAPTOP_RE = re.compile(
    r"\b(portatile|notebook|laptop|computer portatile|thinkpad|latitude|elitebook|"
    r"probook|ideapad|inspiron|vivobook|zenbook|aspire|travelmate|matebook|"
    r"macbook|chromebook|pavilion|precision|zbook|toughbook|swift|satellite|"
    r"xps\s*\d|surface laptop)\b", re.IGNORECASE
)
DAMAGED_RE = re.compile(
    r"\b(guasto|guasta|rotto|rotta|non funzionante|da riparare|non si accende|"
    r"non accende|non parte|non avvia|schermo rotto|display rotto|senza video|"
    r"difetto|difettoso|per ricambi|ricambi|broken|faulty|for parts|no power)\b",
    re.IGNORECASE,
)
RAM_32_RE = re.compile(r"\b32\s*(?:gb|go|giga(?:byte)?)\b", re.IGNORECASE)
RAM_16_RE = re.compile(r"\b16\s*(?:gb|go|giga(?:byte)?)\b", re.IGNORECASE)
RAM_8_OR_LESS_RE = re.compile(r"\b(?:4|8)\s*(?:gb|go|giga(?:byte)?)\b", re.IGNORECASE)
RAM_2X16_RE = re.compile(r"\b2\s*[x×]\s*16(?:\s*(?:gb|go))?\b", re.IGNORECASE)
DDR4_RE = re.compile(r"\bddr\s*4\b", re.IGNORECASE)
DDR3_RE = re.compile(r"\bddr\s*3\b", re.IGNORECASE)


DEFAULT_DETAIL_FETCH_DELAY_SECONDS = 1.0


def build_detail_headers():
    """Return browser-like headers for listing pages, as used for the home page."""
    headers = dict(WEB_HEADERS)
    headers["Sec-Fetch-Site"] = "same-origin"
    headers["Referer"] = f"{Config.vinted_url.rstrip('/')}/"
    return headers


def wait_before_detail_fetch():
    """Pause between listing-page requests to keep the polling load modest."""
    try:
        delay = float(getattr(
            Config, "hardware_detail_fetch_delay_seconds",
            DEFAULT_DETAIL_FETCH_DELAY_SECONDS,
        ))
    except (TypeError, ValueError):
        delay = DEFAULT_DETAIL_FETCH_DELAY_SECONDS
    if delay > 0:
        time.sleep(delay)


def fetch_item_description(item, session=None):
    """Fetch one listing description politely; failures return an empty string."""
    wait_before_detail_fetch()
    return hardware_enricher.fetch_listing_description(
        item["url"], session=session, headers=build_detail_headers(),
        title=item["title"],
    )


class CatalogError(RuntimeError):
    """Raised when Vinted does not return a valid catalogue response."""


def configure_logging():
    """Configure a rotating log file relative to this script."""
    handler = RotatingFileHandler(
        LOG_PATH,
        maxBytes=5_000_000,
        backupCount=5,
    )
    logging.basicConfig(
        handlers=[handler],
        format=(
            "%(asctime)s - %(filename)s - %(funcName)10s():%(lineno)s - "
            "%(levelname)s - %(message)s"
        ),
        level=logging.INFO,
    )


def parse_arguments():
    """Parse command-line options."""
    parser = argparse.ArgumentParser(
        description=(
            "Search Vinted for new items and send the configured notifications."
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "show new items without sending notifications; "
            "found items are still saved in the local database"
        ),
    )
    return parser.parse_args()


def build_catalog_url(marketplace_url):
    """Build the country-specific catalogue API URL."""
    parsed_url = urlsplit(marketplace_url)
    hostname = parsed_url.hostname

    if parsed_url.scheme != "https" or not hostname:
        raise ValueError("vinted_url must be a valid HTTPS URL")

    if hostname.startswith("www."):
        hostname = hostname[4:]

    if not hostname.startswith("vinted."):
        raise ValueError("vinted_url must point to a Vinted marketplace")

    return urlunsplit(
        ("https", f"api.{hostname}", "/svc-catalogue/items", "", "")
    )


def build_api_headers(marketplace_url):
    """Return headers for the Vinted web catalogue API."""
    marketplace_url = marketplace_url.rstrip("/")
    api_headers = {
        "User-Agent": WEB_HEADERS["User-Agent"],
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": WEB_HEADERS["Accept-Language"],
        "Platform": "web",
        "x-next-app": "marketplace-web",
        "Origin": marketplace_url,
        "Referer": f"{marketplace_url}/",
    }

    locale = getattr(Config, "vinted_locale", None)
    if locale:
        api_headers["Locale"] = locale

    return api_headers


def build_catalog_params(query):
    """Validate a configured query and convert its filters to API parameters."""
    if not isinstance(query, dict):
        raise ValueError("each entry in queries must be a dictionary")

    legacy_keys = LEGACY_QUERY_KEYS.intersection(query)
    if legacy_keys:
        legacy_list = ", ".join(sorted(legacy_keys))
        raise ValueError(
            f"unsupported legacy query keys: {legacy_list}; "
            "replace them with the filters dictionary described in README.md"
        )

    unknown_keys = set(query).difference(CATALOG_QUERY_KEYS)
    if unknown_keys:
        unknown_list = ", ".join(sorted(unknown_keys))
        raise ValueError(f"unsupported query keys: {unknown_list}")

    for required_key in ("page", "per_page", "order", "filters"):
        if required_key not in query:
            raise ValueError(f"missing required query key: {required_key}")

    if str(query["order"]) not in CATALOG_ORDER_VALUES:
        raise ValueError(f"unsupported catalogue order: {query['order']}")

    filters = query["filters"]
    if not isinstance(filters, dict):
        raise ValueError("query filters must be a dictionary")

    params = {
        key: value
        for key, value in query.items()
        if key != "filters" and value not in (None, "")
    }
    for filter_code, filter_ids in filters.items():
        if not isinstance(filter_code, str) or not filter_code:
            raise ValueError("filter names must be non-empty strings")
        if not isinstance(filter_ids, (list, tuple)):
            raise ValueError(f"filter '{filter_code}' must contain a list of IDs")

        normalized_ids = [str(filter_id) for filter_id in filter_ids]
        if any(not filter_id for filter_id in normalized_ids):
            raise ValueError(f"filter '{filter_code}' contains an empty ID")
        if normalized_ids:
            params[f"attribute_ids[{filter_code}]"] = ",".join(normalized_ids)

    return params


def validate_notification_config(dry_run=False):
    """Validate only the configured channels, while retaining legacy fallback."""
    configured_channels = getattr(Config, "notification_channels", None)
    if configured_channels is None:
        # Backwards compatibility for existing Config.py files.
        selected = {"email", "slack", "telegram"}
    else:
        if not isinstance(configured_channels, (list, tuple, set)):
            raise ValueError("notification_channels must be a list of channel names")
        selected = {str(channel).strip().lower() for channel in configured_channels}
        unknown = selected.difference({"email", "slack", "telegram"})
        if unknown:
            raise ValueError("unsupported notification channel(s): " + ", ".join(sorted(unknown)))

    # A dry run must be usable before credentials are configured.
    if dry_run:
        return

    smtp_values = {
        "smtp_username": getattr(Config, "smtp_username", ""),
        "smtp_psw": getattr(Config, "smtp_psw", ""),
        "smtp_server": getattr(Config, "smtp_server", ""),
    }
    smtp_enabled = "email" in selected and any(smtp_values.values())
    if "email" in selected:
        missing = [name for name, value in smtp_values.items() if not value]
        recipients = getattr(Config, "smtp_toaddrs", None)
        if not recipients:
            missing.append("smtp_toaddrs")
        if missing:
            raise ValueError("incomplete email notification configuration: " + ", ".join(missing))

    slack_webhook = getattr(Config, "slack_webhook_url", "")
    slack_enabled = "slack" in selected and bool(slack_webhook)
    if "slack" in selected and not slack_webhook:
        raise ValueError("Slack is selected in notification_channels but slack_webhook_url is empty")

    telegram_values = {
        "telegram_bot_token": getattr(Config, "telegram_bot_token", ""),
        "telegram_chat_id": getattr(Config, "telegram_chat_id", ""),
    }
    telegram_enabled = "telegram" in selected and any(telegram_values.values())
    if "telegram" in selected:
        missing = [name for name, value in telegram_values.items() if not value]
        if missing:
            raise ValueError("incomplete Telegram notification configuration: " + ", ".join(missing))

    if not dry_run and not (smtp_enabled or slack_enabled or telegram_enabled):
        raise ValueError("configure at least one selected notification channel or use --dry-run")


def load_analyzed_items(database_path=ITEMS_DATABASE_PATH):
    """Load item IDs, creating the local database if it does not exist."""
    database_path.touch(exist_ok=True)
    with database_path.open("r", encoding="utf-8", errors="ignore") as database:
        return {line.strip() for line in database if line.strip()}


def save_analyzed_item(item_id, database_path=ITEMS_DATABASE_PATH):
    """Append an item ID to the local database."""
    with database_path.open("a", encoding="utf-8") as database:
        database.write(f"{item_id}\n")


def load_filtered_items(database_path=FILTERED_DATABASE_PATH):
    """Load IDs filtered out under the current rule version.

    Entries are stored as "<rules version>:<item id>". Entries written by an
    older rule set are dropped so those listings get evaluated again.
    """
    database_path.touch(exist_ok=True)
    prefix = f"{hardware_enricher.FILTER_RULES_VERSION}:"
    with database_path.open("r", encoding="utf-8", errors="ignore") as database:
        lines = [line.strip() for line in database if line.strip()]
    current = [line for line in lines if line.startswith(prefix)]
    if len(current) != len(lines):
        database_path.write_text(
            "".join(f"{line}\n" for line in current), encoding="utf-8"
        )
    return {line[len(prefix):] for line in current}


def save_filtered_item(item_id, database_path=FILTERED_DATABASE_PATH):
    """Record an item rejected by the current target rules."""
    save_analyzed_item(f"{hardware_enricher.FILTER_RULES_VERSION}:{item_id}", database_path)


def send_email(item_title, item_price, item_url, item_image, matched_query=None):
    """Send an email notification and report whether it succeeded."""
    try:
        message = EmailMessage()
        message["To"] = Config.smtp_toaddrs
        message["From"] = email.utils.formataddr(
            ("Vinted Scanner", Config.smtp_username)
        )
        message["Subject"] = "Vinted Scanner - New Item"
        message["Date"] = email.utils.formatdate(localtime=True)
        message["Message-ID"] = email.utils.make_msgid()

        body_lines = [item_title, str(item_price)]
        if matched_query:
            body_lines.append(f"Matched query: {matched_query}")
        body_lines.append(f"🔗 {item_url}")
        if item_image:
            body_lines.append(f"📷 {item_image}")
        message.set_content("\n".join(body_lines))

        with smtplib.SMTP(
            Config.smtp_server,
            587,
            timeout=REQUEST_TIMEOUT_SECONDS,
        ) as smtp_server:
            smtp_server.ehlo()
            smtp_server.starttls()
            smtp_server.ehlo()
            smtp_server.login(Config.smtp_username, Config.smtp_psw)
            smtp_server.send_message(message)
        logging.info("Email notification sent")
        return True
    except (OSError, TypeError, ValueError, smtplib.SMTPException) as error:
        logging.error("Error sending email notification: %s", error)
        return False


def send_slack_message(item_title, item_price, item_url, item_image, matched_query=None):
    """Send a Slack notification and report whether it succeeded."""
    message_lines = [f"*{item_title}*", f"🏷️ {item_price}"]
    if matched_query:
        message_lines.append(f"Matched query: {matched_query}")
    message_lines.append(f"🔗 {item_url}")
    if item_image:
        message_lines.append(f"📷 {item_image}")

    try:
        response = requests.post(
            Config.slack_webhook_url,
            json={"text": "\n".join(message_lines)},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        logging.info("Slack notification sent")
        return True
    except requests.exceptions.RequestException as error:
        logging.error("Error sending Slack notification: %s", error)
        return False


def send_telegram_message(
    item_title, item_price, item_url, item_image, matched_query=None, extra_lines=None
):
    """Send an HTML-safe Telegram notification and report its result."""
    message_lines = [
        f"<b>{html.escape(str(item_title))}</b>",
        f"🏷️ {html.escape(str(item_price))}",
    ]
    if matched_query:
        message_lines.append(
            f"Matched query: {html.escape(str(matched_query))}"
        )
    message_lines.append(f"🔗 {html.escape(str(item_url))}")
    if item_image:
        message_lines.append(f"📷 {html.escape(str(item_image))}")
    if extra_lines:
        message_lines.extend(html.escape(str(line)) for line in extra_lines)

    url = f"https://api.telegram.org/bot{Config.telegram_bot_token}/sendMessage"
    params = {
        "chat_id": Config.telegram_chat_id,
        "text": "\n".join(message_lines),
        "parse_mode": "HTML",
        "link_preview_options": json.dumps({"is_disabled": True}),
    }
    try:
        response = requests.post(
            url,
            params=params,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        logging.info("Telegram notification sent")
        return True
    except requests.exceptions.RequestException as error:
        logging.error("Error sending Telegram notification: %s", error)
        return False


def send_notifications(
    item_title, item_price, item_url, item_image, matched_query=None, extra_lines=None
):
    """Send via configured channels; hardware details are added to Telegram."""
    configured_channels = getattr(Config, "notification_channels", None)
    if configured_channels is None:
        # Backwards compatibility for existing Config.py files.
        selected = {"email", "slack", "telegram"}
    else:
        selected = {str(channel).strip().lower() for channel in configured_channels}

    results = []
    if "email" in selected and Config.smtp_username and Config.smtp_server:
        results.append(
            send_email(item_title, item_price, item_url, item_image, matched_query)
        )
    if "slack" in selected and Config.slack_webhook_url:
        results.append(
            send_slack_message(item_title, item_price, item_url, item_image, matched_query)
        )
    if "telegram" in selected and Config.telegram_bot_token and Config.telegram_chat_id:
        results.append(
            send_telegram_message(
                item_title, item_price, item_url, item_image, matched_query,
                extra_lines=extra_lines,
            )
        )

    if not results:
        logging.warning("No selected notification channel is configured; item was not saved")
        return False
    return all(results)


def get_catalog_items(session, catalog_url, params, api_headers):
    """Return catalogue items or raise CatalogError for an invalid response."""
    try:
        response = session.get(
            catalog_url,
            params=params,
            headers=api_headers,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except requests.exceptions.RequestException as error:
        raise CatalogError(f"Vinted catalogue request failed: {error}") from error

    try:
        data = response.json()
    except ValueError as error:
        raise CatalogError(
            f"Vinted returned a non-JSON response (HTTP {response.status_code})"
        ) from error

    if not isinstance(data, dict):
        raise CatalogError(
            "Unexpected Vinted response type: "
            f"expected an object, got {type(data).__name__}"
        )

    items = data.get("items")
    if not isinstance(items, list):
        error_message = data.get("message") or data.get("error") or "unknown error"
        raise CatalogError(
            "Vinted response does not contain a valid 'items' list "
            f"(HTTP {response.status_code}, message: {error_message})"
        )
    return items


def normalize_item(item):
    """Validate and normalize the fields used by notifications."""
    if not isinstance(item, dict):
        logging.warning("Skipping an invalid catalogue item: %r", item)
        return None

    item_id = item.get("id")
    item_title = item.get("title")
    item_url = item.get("url")
    if item_id is None or not item_title or not item_url:
        logging.warning("Skipping an incomplete catalogue item (id: %r)", item_id)
        return None
    item_url = urljoin(f"{Config.vinted_url.rstrip('/')}/", str(item_url))

    price_data = item.get("price") or {}
    if not isinstance(price_data, dict):
        price_data = {}
    amount = price_data.get("amount")
    currency = price_data.get("currency_code")
    item_price = f"{amount} {currency}" if amount is not None and currency else "N/A"

    photo_data = item.get("photo") or {}
    if not isinstance(photo_data, dict):
        photo_data = {}
    return {
        "id": str(item_id),
        "title": item_title,
        "price": item_price,
        "url": item_url,
        "image": photo_data.get("full_size_url"),
        "description": str(item.get("description") or ""),
    }


def print_dry_run_item(item, matched_query=None):
    """Print one normalized item without sending a notification."""
    print(f"Title: {item['title']}")
    print(f"Price: {item['price']}")
    if matched_query:
        print(f"Matched query: {matched_query}")
    print(f"URL: {item['url']}")
    if item["image"]:
        print(f"Image: {item['image']}")
    for line in hardware_enricher.build_telegram_lines(item, matched_query):
        print(line)
    print()


def process_item(
    item,
    analyzed_items,
    dry_run,
    database_path=ITEMS_DATABASE_PATH,
    matched_query=None,
    session=None,
    filtered_items=None,
    filtered_database_path=FILTERED_DATABASE_PATH,
):
    """Process one item and persist it only after the requested action succeeds."""
    normalized_item = normalize_item(item)
    if not normalized_item or normalized_item["id"] in analyzed_items:
        return True
    if filtered_items is not None and normalized_item["id"] in filtered_items:
        return True

    require_target = bool(getattr(Config, "hardware_require_target_models", True))
    fetch_details = bool(getattr(Config, "hardware_fetch_details", True))
    if fetch_details and not normalized_item["description"]:
        title_text = f"{normalized_item['title']}"
        worth_fetching = not require_target or (
            not hardware_enricher.is_non_system_title(title_text)
            and (
                hardware_enricher.is_target_system(title_text)[0]
                or hardware_enricher.names_target_family(title_text)
            )
        )
        if worth_fetching:
            normalized_item["description"] = fetch_item_description(normalized_item, session)
    normalized_item = hardware_enricher.enrich_item(
        normalized_item, session=session, fetch_details=False
    )
    analysis = normalized_item.get("hardware_analysis") or {}
    if require_target and not analysis.get("target_system", False):
        logging.info(
            "Ignoring item outside target mini-PC families (id=%s, title=%r)",
            normalized_item["id"], normalized_item["title"],
        )
        if dry_run:
            reason = (
                "spare part, accessory or multi-unit lot"
                if analysis.get("non_system")
                else "only HP EliteDesk/ProDesk Mini, Lenovo ThinkCentre Tiny "
                "or Dell OptiPlex Micro are accepted"
            )
            print(
                f"SKIPPED — outside target: {reason}.\n"
                f"Title: {normalized_item['title']}\n"
                f"URL: {normalized_item['url']}\n"
            )
        # Keep filtered results out of the notification ledger. They go to a
        # separate ledger tagged with the rule version, so they are not fetched
        # again on every run but are re-evaluated after a rule change.
        if filtered_items is not None:
            save_filtered_item(normalized_item["id"], filtered_database_path)
            filtered_items.add(normalized_item["id"])
        return True

    extra_lines = hardware_enricher.build_telegram_lines(normalized_item, matched_query)

    if dry_run:
        print_dry_run_item(normalized_item, matched_query)
        succeeded = True
    else:
        succeeded = send_notifications(
            normalized_item["title"],
            normalized_item["price"],
            normalized_item["url"],
            normalized_item["image"],
            matched_query,
            extra_lines=extra_lines,
        )

    if succeeded:
        save_analyzed_item(normalized_item["id"], database_path)
        analyzed_items.add(normalized_item["id"])
    return succeeded



def load_ram_hunt_configuration():
    """Return RAM Hunt query parameters and maximum listing price.

    RAM Hunt defaults to enabled for this patched package, but can be disabled
    explicitly with ``ram_hunt_enabled = False`` in Config.py.
    """
    enabled = bool(getattr(Config, "ram_hunt_enabled", True))
    max_price = Decimal(str(getattr(
        Config, "ram_hunt_max_price_eur", RAM_HUNT_DEFAULT_MAX_PRICE_EUR
    )))
    if max_price <= 0:
        raise ValueError("ram_hunt_max_price_eur must be greater than zero")
    if not enabled:
        return [], max_price

    configured = getattr(Config, "ram_hunt_queries", RAM_HUNT_DEFAULT_QUERIES)
    if not isinstance(configured, list):
        raise ValueError("ram_hunt_queries must be a list of search dictionaries")
    if not configured:
        return [], max_price

    result = []
    for configured_query in configured:
        if not isinstance(configured_query, dict):
            raise ValueError("each ram_hunt_queries entry must be a dictionary")
        query = dict(configured_query)
        existing_cap = query.get("price_to")
        try:
            effective_cap = min(max_price, Decimal(str(existing_cap))) if existing_cap not in (None, "") else max_price
        except InvalidOperation as error:
            raise ValueError("RAM Hunt price_to values must be numeric") from error
        query["price_to"] = format(effective_cap, "f")
        if "." in query["price_to"]:
            query["price_to"] = query["price_to"].rstrip("0").rstrip(".")
        if "page" not in query:
            query["page"] = "1"
        if "per_page" not in query:
            query["per_page"] = "24"
        if "order" not in query:
            query["order"] = "newest_first"
        if "filters" not in query:
            query["filters"] = {}
        result.append(build_catalog_params(query))
    return result, max_price


def parse_item_price_amount(item):
    """Parse the numeric price from the raw catalogue item, or return None."""
    price_data = item.get("price") if isinstance(item, dict) else None
    if isinstance(price_data, dict):
        raw_amount = price_data.get("amount")
    else:
        raw_amount = price_data
    if raw_amount is None:
        return None
    match = re.search(r"\d+(?:[.,]\d{1,2})?", str(raw_amount).replace(" ", ""))
    if not match:
        return None
    try:
        return Decimal(match.group(0).replace(",", "."))
    except InvalidOperation:
        return None


def classify_ram_hunt_candidate(item, max_price_eur=RAM_HUNT_DEFAULT_MAX_PRICE_EUR):
    """Classify an under-budget laptop listing as a RAM recovery candidate.

    Returns a small, human-readable classification or None for unrelated items.
    Listing claims are not proof that modules are removable or functional.
    """
    if not isinstance(item, dict):
        return None
    price = parse_item_price_amount(item)
    if price is None or price > Decimal(str(max_price_eur)):
        return None

    title = str(item.get("title") or "")
    description = str(item.get("description") or "")
    text = f"{title}\n{description}"
    if not LAPTOP_RE.search(text):
        return None

    damaged = bool(DAMAGED_RE.search(text))
    has_32 = bool(RAM_32_RE.search(text) or RAM_2X16_RE.search(text))
    has_16 = bool(RAM_16_RE.search(text))
    has_ddr4 = bool(DDR4_RE.search(text))
    has_ddr3 = bool(DDR3_RE.search(text))
    has_small_only = bool(RAM_8_OR_LESS_RE.search(text)) and not has_16 and not has_32

    # Avoid alerts for listings explicitly identified as DDR3: it does not meet
    # the desired DDR4 recovery target. Unknown generation stays a verification item.
    if has_ddr3 and not has_ddr4:
        return None
    if has_small_only:
        return None

    if has_32:
        return {
            "tier": "ALTA — 32 GB dichiarati",
            "reason": "L'annuncio menziona 32 GB o 2×16 GB; la configurazione reale va verificata.",
            "ram_claim": "32 GB dichiarati" + (" DDR4" if has_ddr4 else "; generazione da verificare"),
            "confidence": "alta sul dato dichiarato, non sul funzionamento",
        }

    if damaged and has_16 and has_ddr4:
        return {
            "tier": "MEDIA — 16 GB DDR4 dichiarati",
            "reason": "Possibile recupero di moduli DDR4; quantità di moduli e capacità dei singoli banchi da verificare.",
            "ram_claim": "16 GB DDR4 dichiarati",
            "confidence": "media; moduli e funzionamento non verificati",
        }

    if damaged and has_16:
        return {
            "tier": "DA VERIFICARE — 16 GB dichiarati",
            "reason": "La capacità è menzionata, ma il tipo di RAM non è confermato come DDR4.",
            "ram_claim": "16 GB dichiarati; generazione da verificare",
            "confidence": "bassa finché non viene verificato il tipo di RAM",
        }

    if damaged and not (has_16 or has_32):
        return {
            "tier": "DA VERIFICARE — RAM non specificata",
            "reason": "Portatile guasto entro budget; potrebbe essere un donatore, ma capacità e tipo di RAM non sono dichiarati.",
            "ram_claim": "Capacità/tipo RAM non specificati",
            "confidence": "bassa; chiedere al venditore prima di acquistare",
        }

    return None


def ram_hunt_telegram_lines(candidate, max_price_eur, matched_query=None):
    """Build concise, actionable Telegram notes for a RAM Hunt candidate."""
    lines = [
        "🧩 RAM HUNT — possibile portatile donatore",
        f"Priorità: {candidate['tier']}",
        f"RAM rilevata nell'annuncio: {candidate['ram_claim']}",
        f"Motivo: {candidate['reason']}",
        f"Limite prezzo annuncio: €{max_price_eur} (spedizione/commissioni escluse)",
        f"Affidabilità: {candidate['confidence']}",
        "Prima di comprare: verifica DDR4 SO-DIMM rimovibile, numero/capacità dei moduli, funzionamento, modello esatto e costo totale con spedizione/commissioni.",
        "Il testo dell'annuncio non prova che la RAM sia presente o funzionante.",
    ]
    if matched_query:
        lines.insert(1, f"Ricerca: {matched_query}")
    return lines


def process_ram_hunt_item(
    raw_item,
    analyzed_items,
    dry_run,
    max_price_eur,
    database_path=RAM_HUNT_DATABASE_PATH,
    matched_query=None,
    session=None,
):
    """Independently process a RAM Hunt listing using its own dedupe database."""
    normalized = normalize_item(raw_item)
    if not normalized or normalized["id"] in analyzed_items:
        return True

    # The catalogue often provides only a title. Fetching a detail page is opt-in
    # for RAM Hunt to keep the added polling load modest and predictable.
    if (not normalized["description"]
            and bool(getattr(Config, "ram_hunt_fetch_details", False))):
        normalized["description"] = fetch_item_description(normalized, session)

    candidate = classify_ram_hunt_candidate(raw_item, max_price_eur)
    if candidate is None and normalized["description"]:
        item_for_classification = dict(raw_item)
        item_for_classification["description"] = normalized["description"]
        candidate = classify_ram_hunt_candidate(item_for_classification, max_price_eur)

    if candidate is None:
        # Store irrelevant fuzzy-search results separately so they are not
        # reprocessed on every cycle; this does not affect normal mini-PC alerts.
        save_analyzed_item(normalized["id"], database_path)
        analyzed_items.add(normalized["id"])
        logging.info("RAM Hunt ignored non-candidate listing id=%s title=%r", normalized["id"], normalized["title"])
        if dry_run:
            print(f"RAM HUNT SKIPPED: {normalized['title']} — outside candidate rules\n")
        return True

    extra_lines = ram_hunt_telegram_lines(candidate, max_price_eur, matched_query)
    if dry_run:
        print(f"RAM HUNT CANDIDATE: {normalized['title']}")
        print(f"Price: {normalized['price']}")
        print(f"URL: {normalized['url']}")
        for line in extra_lines:
            print(line)
        print()
        succeeded = True
    else:
        succeeded = send_notifications(
            normalized["title"], normalized["price"], normalized["url"],
            normalized["image"], matched_query, extra_lines=extra_lines,
        )

    if succeeded:
        save_analyzed_item(normalized["id"], database_path)
        analyzed_items.add(normalized["id"])
    return succeeded


def process_ram_hunt_queries(
    session, catalog_url, queries, api_headers, analyzed_items, dry_run, max_price_eur,
):
    """Run RAM Hunt queries without applying the main mini-PC chassis filter."""
    processing_failed = False
    for params in queries:
        matched_query = params.get("search_text") or "RAM Hunt"
        try:
            items = get_catalog_items(session, catalog_url, params, api_headers)
        except CatalogError as error:
            logging.error("RAM Hunt query failed (%s): %s", matched_query, error)
            processing_failed = True
            continue
        for item in items:
            # Enforce a price cap locally too, even when Vinted's API filter is fuzzy.
            amount = parse_item_price_amount(item) if isinstance(item, dict) else None
            if amount is None or amount > Decimal(str(max_price_eur)):
                continue
            if not process_ram_hunt_item(
                item, analyzed_items, dry_run, max_price_eur,
                matched_query=matched_query, session=session,
            ):
                processing_failed = True
    return processing_failed


def initialize_vinted_session(marketplace_url):
    """Initialize an anonymous Vinted web session."""
    session = requests.Session()
    response = session.get(
        marketplace_url,
        headers=WEB_HEADERS,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return session


def process_queries(
    session, catalog_url, queries, api_headers, analyzed_items, dry_run,
    filtered_items=None,
):
    """Process every configured query and report whether any query failed."""
    processing_failed = False
    for params in queries:
        matched_query = params.get("search_text") or "filters only"
        try:
            items = get_catalog_items(session, catalog_url, params, api_headers)
        except CatalogError as error:
            logging.error("%s", error)
            processing_failed = True
            continue

        for item in items:
            if not process_item(
                item,
                analyzed_items,
                dry_run,
                matched_query=matched_query,
                session=session,
                filtered_items=filtered_items,
            ):
                processing_failed = True
    return processing_failed


def load_configuration(dry_run=False):
    """Validate configuration and return the derived API values."""
    catalog_url = build_catalog_url(Config.vinted_url)
    configured_queries = getattr(Config, "queries", [])
    if not isinstance(configured_queries, list):
        raise ValueError("queries must be a list")

    # Add broad chassis/model discovery searches by default. This is additive:
    # user-configured category/attribute searches are preserved.
    discovery_enabled = bool(getattr(Config, "hardware_add_discovery_queries", True))
    raw_queries = list(configured_queries)
    if discovery_enabled:
        known = {
            (str(query.get("search_text", "")).casefold(), str(query.get("order", "newest_first")))
            for query in raw_queries if isinstance(query, dict)
        }
        price_from = getattr(
            Config, "hardware_discovery_price_from", DEFAULT_DISCOVERY_PRICE_FROM_EUR
        )
        price_to = getattr(
            Config, "hardware_discovery_price_to", DEFAULT_DISCOVERY_PRICE_TO_EUR
        )
        for template in DEFAULT_HARDWARE_DISCOVERY_QUERIES:
            key = (template["search_text"].casefold(), template["order"])
            if key in known:
                continue
            query = dict(template, filters=dict(template["filters"]))
            if price_from not in (None, ""):
                query["price_from"] = str(price_from)
            if price_to not in (None, ""):
                query["price_to"] = str(price_to)
            raw_queries.append(query)
            known.add(key)
    if not raw_queries:
        raise ValueError("queries must contain at least one search")
    queries = [build_catalog_params(query) for query in raw_queries]
    validate_notification_config(dry_run)
    return catalog_url, queries, build_api_headers(Config.vinted_url)


def main(dry_run=False):
    """Run mini-PC searches and, independently, the optional RAM Hunt search."""
    configure_logging()
    try:
        catalog_url, queries, api_headers = load_configuration(dry_run)
        ram_hunt_queries, ram_hunt_max_price = load_ram_hunt_configuration()
        analyzed_items = load_analyzed_items()
        filtered_items = load_filtered_items()
        ram_hunt_items = (
            load_analyzed_items(RAM_HUNT_DATABASE_PATH) if ram_hunt_queries else set()
        )
    except (AttributeError, OSError, ValueError, InvalidOperation) as error:
        message = f"Configuration or local database error: {error}"
        logging.error(message)
        print(message, file=sys.stderr)
        return 2

    try:
        session = initialize_vinted_session(Config.vinted_url)
    except requests.exceptions.RequestException as error:
        logging.error("Unable to initialize the Vinted session: %s", error)
        return 1

    anon_id = session.cookies.get("anon_id")
    if anon_id:
        api_headers["X-Anon-Id"] = anon_id

    try:
        # Keep the original mini-PC search behavior and dedupe database intact.
        query_failed = process_queries(
            session, catalog_url, queries, api_headers, analyzed_items, dry_run,
            filtered_items=filtered_items,
        )
        if ram_hunt_queries:
            ram_hunt_failed = process_ram_hunt_queries(
                session, catalog_url, ram_hunt_queries, api_headers,
                ram_hunt_items, dry_run, ram_hunt_max_price,
            )
            query_failed = query_failed or ram_hunt_failed
    except OSError as error:
        logging.error("Unable to update the local database: %s", error)
        return 1
    return 1 if query_failed else 0


if __name__ == "__main__":
    arguments = parse_arguments()
    sys.exit(main(dry_run=arguments.dry_run))
