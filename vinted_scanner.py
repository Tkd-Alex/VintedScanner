#!/usr/bin/env python3
"""Search Vinted and notify users about newly listed matching items."""
import argparse
import email.utils
import json
import logging
import re
import smtplib
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from email.message import EmailMessage
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests

import Config
import alert_parser
import availability
import hardware_enricher
import message_format as fmt
import run_report as report_module
import storage


PROJECT_DIR = Path(__file__).resolve().parent
PENDING_ALERTS_PATH = PROJECT_DIR / "vinted_pending_alerts.json"
MINI_PC_LABEL = storage.MODE_LABELS["mini_pc"]
RAM_HUNT_LABEL = storage.MODE_LABELS["ram"]
CPU_HUNT_LABEL = storage.MODE_LABELS["cpu"]
# Records searches, matches and problems of the current run; reset by main().
run_report = report_module.RunReport()
# SQLite store and the id of the current run; opened by main().
store = None
current_run_id = None
DATABASE_PATH = PROJECT_DIR / "vinted_scanner.db"
# Text ledgers used before the SQLite database; imported once, then left alone.
LEGACY_LEDGER_PATHS = {
    "mini_pc": PROJECT_DIR / "vinted_items.txt",
    "ram": PROJECT_DIR / "vinted_ram_hunt_items.txt",
    "cpu": PROJECT_DIR / "vinted_cpu_hunt_items.txt",
}
LEGACY_FILTERED_PATH = PROJECT_DIR / "vinted_filtered_items.txt"
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
LAPTOP_WORD_RE = re.compile(
    r"\b(notebook|laptop|computer portatile|pc portatile|thinkpad|latitude|elitebook|"
    r"probook|ideapad|inspiron|vivobook|zenbook|aspire|travelmate|matebook|"
    r"macbook|chromebook|pavilion|precision|zbook|toughbook|satellite|"
    r"xps\s*\d|surface laptop|netbook)\b", re.IGNORECASE
)
COMPUTER_HINT_RE = re.compile(
    r"\b(pc|computer|intel|amd|ryzen|core\s*i[3579]|i[3579]-?\d{3,5}|ram|ssd|hdd|"
    r"windows|hp|lenovo|dell|asus|acer|msi|toshiba|samsung|apple|huawei|fujitsu|"
    r"sony|vaio|medion|ddr[34])\b",
    re.IGNORECASE,
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


DEFAULT_DETAIL_FETCH_DELAY_SECONDS = 2.0
DEFAULT_DETAIL_RATE_LIMIT_WAIT_SECONDS = 60.0
MAX_DETAIL_RATE_LIMIT_WAIT_SECONDS = 120.0
# Listing-page fetching pauses for the rest of a run after repeated HTTP 429.
_detail_fetch_state = {"paused": False}
# Catalogue searches stop for the rest of a run after repeated HTTP 429.
_catalog_state = {"paused": False}
# Availability tracker: how many alerted listings to re-check per run, how often
# each one is re-checked, and for how long after the alert it is followed.
DEFAULT_AVAILABILITY_CHECKS_PER_RUN = 10
MAX_AVAILABILITY_CHECKS_PER_RUN = 50
DEFAULT_AVAILABILITY_RECHECK_HOURS = 20.0
DEFAULT_AVAILABILITY_MAX_AGE_DAYS = 45.0
DEFAULT_TELEGRAM_SEND_DELAY_SECONDS = 1.0
TELEGRAM_DEFAULT_RETRY_SECONDS = 5.0
TELEGRAM_MAX_RETRY_SECONDS = 30.0


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
    """Fetch one listing description politely; failures return an empty string.

    On HTTP 429 the request is retried once after Vinted's Retry-After hint (or
    a default wait); a second 429 stops listing-page requests for the rest of
    the run, and the remaining listings are analyzed from their titles.
    """
    if _detail_fetch_state["paused"]:
        return ""

    def report_page_error(error):
        run_report.add_issue(
            "listing_page", f"{item['title']}: {describe_request_error(error)}"
        )

    for attempt in (1, 2):
        wait_before_detail_fetch()
        try:
            return hardware_enricher.fetch_listing_description(
                item["url"], session=session, headers=build_detail_headers(),
                title=item["title"], raise_on_rate_limit=True,
                on_error=report_page_error,
            )
        except hardware_enricher.ListingRateLimited as limited:
            if attempt == 1:
                wait = min(
                    limited.retry_after or DEFAULT_DETAIL_RATE_LIMIT_WAIT_SECONDS,
                    MAX_DETAIL_RATE_LIMIT_WAIT_SECONDS,
                )
                logging.warning("Vinted rate-limited listing pages; retrying in %s s", wait)
                run_report.add_issue(
                    "rate_limit", f"Listing page rate-limited; waited {wait:g} s and retried"
                )
                time.sleep(wait)
                continue
            _detail_fetch_state["paused"] = True
            logging.warning(
                "Vinted still rate-limits listing pages; skipping descriptions for the rest of this run"
            )
            run_report.add_issue(
                "rate_limit",
                "Listing pages still rate-limited after the retry; the rest of this run "
                "judged listings from their titles only",
            )
    return ""


def request_error_kind(error):
    """Classify a requests exception as rate_limit, http or network."""
    response = getattr(error, "response", None)
    status = getattr(response, "status_code", None)
    if status == 429:
        return "rate_limit"
    if isinstance(status, int):
        return "http"
    return "network"


def describe_request_error(error):
    """Return a short, readable description of a request failure."""
    response = getattr(error, "response", None)
    status = getattr(response, "status_code", None)
    if isinstance(status, int):
        return f"HTTP {status}"
    if isinstance(error, requests.exceptions.Timeout):
        return "timed out"
    if isinstance(error, requests.exceptions.ConnectionError):
        return "connection failed"
    return f"{type(error).__name__}: {error}"


# CPU Hunt: loose low-power CPUs that can upgrade a cheap i3/Pentium mini PC.
# Keys are hardware_enricher.CPU_CATALOG keys; values are price caps in EUR.
CPU_HUNT_DEFAULT_TARGETS = {
    "i78700t": 90, "i79700t": 110, "i59500t": 70, "i58500t": 60,
    "i77700t": 55, "i76700t": 50,
}
CPU_HUNT_DEFAULT_BASE_PRICE_EUR = 90
BARE_CPU_TITLE_MAX_CHARS = 40
LOOSE_CPU_TITLE_RE = re.compile(
    r"\b(?:processore|processori|processor|prozessor|processeur|procesador|cpu|"
    r"lga\s*-?\s*1151|socket|tray)\b",
    re.IGNORECASE,
)
WHOLE_SYSTEM_TITLE_RE = re.compile(
    r"\b(?:mini\s*pc|pc\s+completo|computer|desktop|notebook|laptop|portatile|"
    r"thinkcentre|elitedesk|prodesk|optiplex|tiny|nuc)\b",
    re.IGNORECASE,
)
STORAGE_WORD_RE = re.compile(r"\b(?:ssd|hdd|nvme|emmc)\b", re.IGNORECASE)
SAMPLE_CPU_RE = re.compile(
    r"\b(?:engineering\s+sample|qualification\s+sample|campione\s+di\s+ingegneria)\b"
    r"|(?<![\w-])(?:ES|QS)(?![\w-])"
)


class CatalogError(RuntimeError):
    """Raised when Vinted does not return a valid catalogue response.

    ``kind`` is a run_report issue kind (rate_limit, http, network or
    vinted_response); ``retry_after`` is Vinted's hint for HTTP 429, if any.
    """

    def __init__(self, message, kind="vinted_response", retry_after=None):
        super().__init__(message)
        self.kind = kind
        self.retry_after = retry_after


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


def open_store():
    """Open the SQLite database, importing the old .txt ledgers on first use."""
    database = storage.Store(DATABASE_PATH)
    migrated = database.migrate_legacy_ledgers(LEGACY_LEDGER_PATHS, LEGACY_FILTERED_PATH)
    if migrated:
        logging.info("Imported %s listing IDs from the old .txt ledgers", migrated)
    return database


def load_seen_ids():
    """Return {mode: IDs to skip}; mini-PC filtered IDs count only under the current rules."""
    return {
        mode: store.seen_ids(
            mode, hardware_enricher.FILTER_RULES_VERSION if mode == "mini_pc" else None
        )
        for mode in storage.MODE_LABELS
    }


def item_price_eur(item):
    """Return the numeric amount of a normalized "75.0 EUR" price, or None."""
    match = re.match(r"\s*(\d+(?:\.\d+)?)", str(item.get("price") or ""))
    return float(match.group(1)) if match else None


def alert_plain_text(item, headline, extra_lines, matched_query=None):
    """Return the plain text of the Telegram alert built for ``item``."""
    header = telegram_header_lines(
        item["title"], item["price"], item["url"], item["image"], matched_query, headline
    )
    return fmt.to_plain_text("\n".join([*header, *map(str, extra_lines)]))


def save_listing(mode, item, status, seen_ids, matched_query=None, alert=None, filter_version=None):
    """Store a processed listing with the structured content of its alert.

    ``alert`` is the (headline, extra_lines) pair sent to Telegram, if any.
    """
    fields = {}
    if alert:
        fields = alert_parser.parse_alert(alert_plain_text(item, *alert, matched_query)) or {}
        fields.pop("mode", None)
        if mode == "mini_pc":
            fields["score_version"] = hardware_enricher.SCORING_VERSION
    fields.update(
        title=item["title"], url=item["url"], image_url=item["image"],
        price_eur=item_price_eur(item), matched_query=matched_query,
    )
    if item["description"]:
        fields["description"] = item["description"]
    store.record_listing(
        mode, item["id"], status, run_id=current_run_id, filter_version=filter_version, **fields
    )
    if status in storage.SEEN_STATUSES or status == "filtered":
        seen_ids.add(item["id"])


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


def telegram_header_lines(item_title, item_price, item_url, item_image, matched_query=None, headline=None):
    """Return the Telegram HTML header: decision line, title, price and links."""
    lines = [headline] if headline else []
    lines.append(fmt.bold(item_title))
    links = [f"💶 <b>{fmt.esc(fmt.format_price(item_price))}</b>", f"🔗 {fmt.link('Open listing', item_url)}"]
    if item_image:
        links.append(f"📷 {fmt.link('Photo', item_image)}")
    lines.append("  ·  ".join(links))
    if matched_query:
        lines.append(f"🔍 {fmt.italic(matched_query)}")
    return lines


def send_telegram_message(
    item_title, item_price, item_url, item_image, matched_query=None, extra_lines=None,
    headline=None,
):
    """Send a Telegram notification and report its result.

    ``headline`` and ``extra_lines`` must already be Telegram HTML (see
    message_format); the title, price, URLs and query are escaped here.
    """
    message_lines = telegram_header_lines(
        item_title, item_price, item_url, item_image, matched_query, headline
    )
    if extra_lines:
        message_lines.extend(str(line) for line in extra_lines)

    # Use the listing photo as a small preview instead of a raw image URL.
    preview = (
        {"url": item_image, "prefer_small_media": True, "show_above_text": False}
        if item_image else {"is_disabled": True}
    )
    return post_telegram_message("\n".join(message_lines), preview)


def post_telegram_message(text, preview=None):
    """Post Telegram HTML text, retrying once on a 429; return whether it was sent."""
    url = f"https://api.telegram.org/bot{Config.telegram_bot_token}/sendMessage"
    params = {
        "chat_id": Config.telegram_chat_id,
        "text": text,
        "parse_mode": "HTML",
        "link_preview_options": json.dumps(preview or {"is_disabled": True}),
    }
    try:
        response = requests.post(
            url,
            params=params,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if response.status_code == 429:
            # Telegram rate limit: wait the advertised time once, then retry.
            retry_after = telegram_retry_after(response)
            logging.warning("Telegram rate limit hit; retrying in %s s", retry_after)
            time.sleep(retry_after)
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


def telegram_retry_after(response):
    """Return Telegram's retry_after hint in seconds, capped to keep runs bounded."""
    try:
        retry_after = float(response.json()["parameters"]["retry_after"])
    except (ValueError, KeyError, TypeError):
        retry_after = TELEGRAM_DEFAULT_RETRY_SECONDS
    return max(1.0, min(retry_after, TELEGRAM_MAX_RETRY_SECONDS))


def wait_after_telegram_message():
    """Space consecutive Telegram messages to stay under the chat rate limit."""
    try:
        delay = float(getattr(
            Config, "telegram_send_delay_seconds", DEFAULT_TELEGRAM_SEND_DELAY_SECONDS
        ))
    except (TypeError, ValueError):
        delay = DEFAULT_TELEGRAM_SEND_DELAY_SECONDS
    if delay > 0:
        time.sleep(delay)


def send_notifications(
    item_title, item_price, item_url, item_image, matched_query=None, extra_lines=None,
    headline=None,
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
                extra_lines=extra_lines, headline=headline,
            )
        )
        wait_after_telegram_message()

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
        kind = request_error_kind(error)
        retry_after = (
            hardware_enricher._retry_after_seconds(error.response)
            if kind == "rate_limit" else None
        )
        raise CatalogError(
            f"Vinted catalogue request failed ({describe_request_error(error)}): {error}",
            kind=kind, retry_after=retry_after,
        ) from error

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


def fetch_query_items(label, matched_query, session, catalog_url, params, api_headers):
    """Run one catalogue search for ``label`` and record the outcome.

    Returns the items, or None when the search failed or was skipped. A first
    HTTP 429 is retried once after Vinted's Retry-After hint (or a default wait);
    a second one skips the remaining searches of this run.
    """
    stats = run_report.mode(label)
    if _catalog_state["paused"]:
        stats.skipped_searches += 1
        return None
    stats.searches += 1
    for attempt in (1, 2):
        try:
            items = get_catalog_items(session, catalog_url, params, api_headers)
            break
        except CatalogError as error:
            if error.kind == "rate_limit" and attempt == 1:
                wait = min(
                    error.retry_after or DEFAULT_DETAIL_RATE_LIMIT_WAIT_SECONDS,
                    MAX_DETAIL_RATE_LIMIT_WAIT_SECONDS,
                )
                logging.warning(
                    "%s search %r rate-limited; retrying in %s s", label, matched_query, wait
                )
                run_report.add_issue(
                    "rate_limit",
                    f"{label} search “{matched_query}” rate-limited; waited {wait:g} s and retried",
                )
                time.sleep(wait)
                continue
            stats.failed_searches += 1
            logging.error("%s search failed (%s): %s", label, matched_query, error)
            if error.kind == "rate_limit":
                _catalog_state["paused"] = True
                run_report.add_issue(
                    "rate_limit",
                    f"{label} search “{matched_query}” still rate-limited after the retry; "
                    "remaining searches of this run skipped",
                )
            else:
                run_report.add_issue(error.kind, f"{label} search “{matched_query}”: {error}")
            return None
    stats.listings += len(items)
    return items


def run_item_safely(label, item, process):
    """Call ``process()`` for one listing; report unexpected errors instead of crashing.

    Local database errors (OSError, sqlite3.Error) still propagate so the run stops cleanly.
    """
    try:
        return process()
    except requests.exceptions.RequestException as error:
        description = describe_request_error(error)
        kind = request_error_kind(error)
    except (OSError, sqlite3.Error):
        raise
    except Exception as error:  # parsing/regex bugs must not hide the rest of the run
        logging.exception("%s failed to process a listing: %r", label, item)
        description = f"{type(error).__name__}: {error}"
        kind = "processing"
    item_id = item.get("id") if isinstance(item, dict) else None
    title = item.get("title") if isinstance(item, dict) else None
    logging.error("%s listing id=%s (%r) failed: %s", label, item_id, title, description)
    run_report.add_issue(kind, f"{label} listing {item_id} “{title}”: {description}")
    return False


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
    print(fmt.to_plain_text(hardware_enricher.build_telegram_headline(item)))
    print(f"Title: {item['title']}")
    print(f"Price: {item['price']}")
    if matched_query:
        print(f"Matched query: {matched_query}")
    print(f"URL: {item['url']}")
    if item["image"]:
        print(f"Image: {item['image']}")
    for line in hardware_enricher.build_telegram_lines(item, matched_query):
        print(fmt.to_plain_text(line))
    print()


def process_item(item, seen_ids, dry_run, matched_query=None, session=None):
    """Process one mini-PC listing; it counts as seen only after the action succeeds."""
    normalized_item = normalize_item(item)
    if not normalized_item or normalized_item["id"] in seen_ids:
        return True
    stats = run_report.mode(MINI_PC_LABEL)
    stats.new += 1

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
        # Filtered listings are tagged with the rule version, so they are not
        # fetched again on every run but are re-evaluated after a rule change.
        save_listing(
            "mini_pc", normalized_item, "filtered", seen_ids, matched_query,
            filter_version=hardware_enricher.FILTER_RULES_VERSION,
        )
        return True

    stats.matches += 1
    extra_lines = hardware_enricher.build_telegram_lines(normalized_item, matched_query)
    headline = hardware_enricher.build_telegram_headline(normalized_item)

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
            headline=headline,
        )

    status = ("dry_run" if dry_run else "notified") if succeeded else "failed"
    save_listing(
        "mini_pc", normalized_item, status, seen_ids, matched_query, alert=(headline, extra_lines)
    )
    if succeeded:
        stats.notified += 1
    else:
        run_report.add_issue(
            "notification",
            f"{MINI_PC_LABEL}: “{normalized_item['title']}” (will retry next run)",
        )
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

    # "Portatile" alone also means "portable" (lamps, speakers...): require a
    # computer hint when it is the only laptop word.
    if not LAPTOP_WORD_RE.search(text) and not COMPUTER_HINT_RE.search(text):
        return None

    damaged = bool(DAMAGED_RE.search(text))
    has_ddr4 = bool(DDR4_RE.search(text))
    has_ddr3 = bool(DDR3_RE.search(text))
    two_by_16 = bool(RAM_2X16_RE.search(text))
    # Reuse the hardware parser so "4GB RAM / 32GB eMMC" reads as 4 GB of RAM.
    ram_gb = hardware_enricher._find_ram_gb(text)
    if ram_gb is not None:
        has_32 = ram_gb >= 32 or two_by_16
        has_16 = 16 <= ram_gb < 32
        has_small_only = ram_gb < 16 and not two_by_16
    else:
        has_32 = bool(RAM_32_RE.search(text) or two_by_16)
        has_16 = bool(RAM_16_RE.search(text))
        has_small_only = bool(RAM_8_OR_LESS_RE.search(text)) and not has_16 and not has_32

    # Avoid alerts for listings explicitly identified as DDR3: it does not meet
    # the desired DDR4 recovery target. Unknown generation stays a verification item.
    if has_ddr3 and not has_ddr4:
        return None
    if has_small_only:
        return None

    if has_32:
        return {
            "tier": "HIGH — 32 GB stated",
            "reason": "The listing mentions 32 GB or 2×16 GB; the actual configuration must be verified.",
            "ram_claim": "32 GB stated" + (" DDR4" if has_ddr4 else "; generation to be verified"),
            "confidence": "high for the stated figure, not for working condition",
        }

    if damaged and has_16 and has_ddr4:
        return {
            "tier": "MEDIUM — 16 GB DDR4 stated",
            "reason": "Possible DDR4 module recovery; number and size of modules to be verified.",
            "ram_claim": "16 GB DDR4 stated",
            "confidence": "medium; modules and working condition not verified",
        }

    if damaged and has_16:
        return {
            "tier": "TO VERIFY — 16 GB stated",
            "reason": "The capacity is mentioned, but the RAM type is not confirmed as DDR4.",
            "ram_claim": "16 GB stated; generation to be verified",
            "confidence": "low until the RAM type is verified",
        }

    if damaged and not (has_16 or has_32):
        return {
            "tier": "TO VERIFY — RAM not specified",
            "reason": "Broken laptop within budget; possible donor, but RAM size and type are not stated.",
            "ram_claim": "RAM size/type not specified",
            "confidence": "low; ask the seller before buying",
        }

    return None


def ram_hunt_telegram_lines(candidate, max_price_eur, matched_query=None):
    """Build Telegram HTML lines for a RAM Hunt candidate; the first is the headline."""
    return [
        f"🧩 <b>RAM HUNT</b> · {fmt.esc(candidate['tier'])}",
        *fmt.section("🧩 Possible donor laptop", [
            f"RAM in listing: <b>{fmt.esc(candidate['ram_claim'])}</b>",
            f"Why: {fmt.esc(candidate['reason'])}",
            f"Confidence: {fmt.esc(candidate['confidence'])}",
            f"Price cap: {fmt.euro(max_price_eur)} (excl. shipping/fees)",
        ]),
        *fmt.expandable("🔎 Before buying", [
            "• removable DDR4 SO-DIMM",
            "• number and size of the modules",
            "• working condition and exact model",
            "• total cost with shipping/fees",
            fmt.italic("The listing text does not prove the RAM is present or working."),
        ]),
    ]


def process_hunt_item(
    raw_item,
    seen_ids,
    dry_run,
    *,
    label,
    classify,
    build_lines,
    fetch_details,
    matched_query=None,
    session=None,
):
    """Process one listing of an independent hunt mode with its own seen IDs.

    ``classify`` receives a raw catalogue item and returns a candidate or None;
    ``build_lines`` turns a candidate into Telegram HTML lines, headline first.
    """
    mode = storage.MODE_BY_LABEL[label]
    normalized = normalize_item(raw_item)
    if not normalized or normalized["id"] in seen_ids:
        return True
    stats = run_report.mode(label)
    stats.new += 1

    # The catalogue often provides only a title. Fetching a detail page is
    # opt-in for hunt modes to keep the added polling load modest.
    if not normalized["description"] and fetch_details:
        normalized["description"] = fetch_item_description(normalized, session)

    candidate = classify(raw_item)
    if candidate is None and normalized["description"]:
        item_for_classification = dict(raw_item)
        item_for_classification["description"] = normalized["description"]
        candidate = classify(item_for_classification)

    if candidate is None:
        # Store irrelevant fuzzy-search results so they are not reprocessed on
        # every cycle; this does not affect mini-PC alerts.
        save_listing(mode, normalized, "ignored", seen_ids, matched_query)
        logging.info("%s ignored non-candidate listing id=%s title=%r", label, normalized["id"], normalized["title"])
        if dry_run:
            print(f"{label.upper()} SKIPPED: {normalized['title']} — outside candidate rules\n")
        return True

    stats.matches += 1
    headline, *extra_lines = build_lines(candidate)
    if dry_run:
        print(fmt.to_plain_text(headline))
        print(f"{label.upper()} CANDIDATE: {normalized['title']}")
        print(f"Price: {normalized['price']}")
        print(f"URL: {normalized['url']}")
        for line in extra_lines:
            print(fmt.to_plain_text(line))
        print()
        succeeded = True
    else:
        succeeded = send_notifications(
            normalized["title"], normalized["price"], normalized["url"],
            normalized["image"], matched_query, extra_lines=extra_lines,
            headline=headline,
        )

    status = ("dry_run" if dry_run else "notified") if succeeded else "failed"
    save_listing(mode, normalized, status, seen_ids, matched_query, alert=(headline, extra_lines))
    if succeeded:
        stats.notified += 1
    else:
        run_report.add_issue(
            "notification", f"{label}: “{normalized['title']}” (will retry next run)"
        )
    return succeeded


def process_ram_hunt_item(
    raw_item, seen_ids, dry_run, max_price_eur, matched_query=None, session=None,
):
    """Independently process a RAM Hunt listing with its own seen IDs."""
    return process_hunt_item(
        raw_item, seen_ids, dry_run,
        label=RAM_HUNT_LABEL,
        classify=lambda item: classify_ram_hunt_candidate(item, max_price_eur),
        build_lines=lambda candidate: ram_hunt_telegram_lines(
            candidate, max_price_eur, matched_query
        ),
        fetch_details=bool(getattr(Config, "ram_hunt_fetch_details", False)),
        matched_query=matched_query,
        session=session,
    )


def process_ram_hunt_queries(
    session, catalog_url, queries, api_headers, seen_ids, dry_run, max_price_eur,
):
    """Run RAM Hunt queries without applying the main mini-PC chassis filter."""
    processing_failed = False
    for params in queries:
        matched_query = params.get("search_text") or RAM_HUNT_LABEL
        items = fetch_query_items(
            RAM_HUNT_LABEL, matched_query, session, catalog_url, params, api_headers
        )
        if items is None:
            processing_failed = True
            continue
        for item in items:
            # Enforce a price cap locally too, even when Vinted's API filter is fuzzy.
            amount = parse_item_price_amount(item) if isinstance(item, dict) else None
            if amount is None or amount > Decimal(str(max_price_eur)):
                continue
            if not run_item_safely(RAM_HUNT_LABEL, item, lambda item=item: process_ram_hunt_item(
                item, seen_ids, dry_run, max_price_eur,
                matched_query=matched_query, session=session,
            )):
                processing_failed = True
    return processing_failed


def load_cpu_hunt_configuration():
    """Return CPU Hunt query parameters and the {cpu_key: Decimal cap} targets."""
    if not bool(getattr(Config, "cpu_hunt_enabled", False)):
        return [], {}
    configured_targets = getattr(Config, "cpu_hunt_targets", CPU_HUNT_DEFAULT_TARGETS)
    if not isinstance(configured_targets, dict) or not configured_targets:
        raise ValueError("cpu_hunt_targets must be a non-empty {cpu_key: max_price_eur} dictionary")
    targets = {}
    for key, cap in configured_targets.items():
        key = str(key).strip().lower()
        if key not in hardware_enricher.CPU_CATALOG:
            raise ValueError(f"cpu_hunt_targets contains an unknown CPU key: {key}")
        try:
            targets[key] = Decimal(str(cap))
        except InvalidOperation as error:
            raise ValueError(f"cpu_hunt_targets price for {key} must be numeric") from error
        if targets[key] <= 0:
            raise ValueError(f"cpu_hunt_targets price for {key} must be greater than zero")

    configured_queries = getattr(Config, "cpu_hunt_queries", None)
    if configured_queries is None:
        configured_queries = [
            {
                "page": "1", "per_page": "24", "order": "newest_first", "filters": {},
                "search_text": cpu_search_text(key),
                "price_to": format(cap, "f"),
            }
            for key, cap in targets.items()
        ]
    if not isinstance(configured_queries, list):
        raise ValueError("cpu_hunt_queries must be a list of search dictionaries")
    queries = []
    for configured_query in configured_queries:
        if not isinstance(configured_query, dict):
            raise ValueError("each cpu_hunt_queries entry must be a dictionary")
        query = {"page": "1", "per_page": "24", "order": "newest_first", "filters": {}}
        query.update(configured_query)
        queries.append(build_catalog_params(query))
    return queries, targets


def cpu_search_text(cpu_key):
    """Return a search term such as "i7-8700T" for a catalog key."""
    name = hardware_enricher.CPU_CATALOG[cpu_key]["name"]
    return re.sub(r"^Intel Core\s+", "", name)


def classify_cpu_hunt_candidate(item, targets):
    """Classify a listing as a loose target CPU under its price cap.

    The title must describe a loose part (processore, CPU, LGA1151...) or be a
    short title naming only the CPU. Returns None for complete systems,
    laptops, other CPU models (including
    non-T, mobile H/U parts), engineering/qualification samples and prices
    above the target's cap.
    """
    if not isinstance(item, dict):
        return None
    price = parse_item_price_amount(item)
    if price is None:
        return None
    title = str(item.get("title") or "")
    description = str(item.get("description") or "")
    # Short titles such as "intel i5-9500t" usually describe the bare part.
    if not LOOSE_CPU_TITLE_RE.search(title) and len(title.strip()) > BARE_CPU_TITLE_MAX_CHARS:
        return None
    if LAPTOP_RE.search(title) or WHOLE_SYSTEM_TITLE_RE.search(title):
        return None
    if RAM_16_RE.search(title) or RAM_32_RE.search(title) or RAM_8_OR_LESS_RE.search(title):
        if STORAGE_WORD_RE.search(title):
            return None
    if SAMPLE_CPU_RE.search(f"{title}\n{description}"):
        return None

    cpu_name, cpu = hardware_enricher._canonical_cpu(title)
    if not cpu:
        cpu_name, cpu = hardware_enricher._canonical_cpu(description)
    cpu_key = hardware_enricher._cpu_key_for_name(cpu_name)
    if cpu_key not in targets or price > targets[cpu_key]:
        return None

    try:
        base_price = Decimal(str(getattr(
            Config, "cpu_hunt_base_price_eur", CPU_HUNT_DEFAULT_BASE_PRICE_EUR
        )))
        ram_budget = Decimal(str(getattr(
            Config, "hardware_ram_8gb_upgrade_eur", hardware_enricher.DEFAULT_RAM_8GB_UPGRADE_EUR
        )))
    except InvalidOperation:
        base_price = Decimal(CPU_HUNT_DEFAULT_BASE_PRICE_EUR)
        ram_budget = Decimal(hardware_enricher.DEFAULT_RAM_8GB_UPGRADE_EUR)
    return {
        "cpu_key": cpu_key,
        "cpu_name": cpu_name,
        "cores": cpu["cores"],
        "threads": cpu["threads"],
        "tdp_w": cpu["tdp_w"],
        "generation": hardware_enricher.cpu_generation(cpu_key),
        "price": price,
        "cap": targets[cpu_key],
        "platforms": hardware_enricher.compatible_platforms(cpu_key),
        "base_price": base_price,
        "ram_budget": ram_budget,
        "build_estimate": base_price + price + ram_budget,
    }


def cpu_hunt_telegram_lines(candidate, matched_query=None):
    """Build Telegram HTML lines for a CPU Hunt candidate; the first is the headline."""
    cpu = fmt.esc(candidate["cpu_name"])
    rows = [
        f"CPU: <b>{cpu}</b> · {candidate['cores']} core / {candidate['threads']} thread"
        f" · {candidate['tdp_w']} W",
        f"Price: <b>{fmt.euro(candidate['price'])}</b> (cap {fmt.euro(candidate['cap'])}, "
        "excl. shipping/fees)",
    ]
    if candidate["platforms"]:
        rows.append(
            f"Fits ({candidate['generation']}th gen): " + fmt.esc(", ".join(candidate["platforms"]))
        )
    if candidate["generation"] is not None and candidate["generation"] <= 7:
        rows.append("⚠️ 6th/7th-gen part: only useful for an existing legacy unit (max 32 GB, 4 cores)")
    return [
        f"🔧 <b>CPU HUNT</b> · {cpu} · {fmt.euro(candidate['price'])}",
        *fmt.section("🔧 Loose CPU for a mini-PC upgrade", rows),
        *fmt.section("💰 Build estimate", [
            f"Total: <b>{fmt.euro(candidate['build_estimate'])}</b> = base unit "
            f"{fmt.euro(candidate['base_price'])} + this CPU {fmt.euro(candidate['price'])}"
            f" + RAM to 16 GB {fmt.euro(candidate['ram_budget'])}",
            "   " + fmt.italic(
                "Base unit = cpu_hunt_base_price_eur, a typical i3/Pentium unit of the same generation"
            ),
        ]),
        *fmt.expandable("🔎 Before buying", [
            "• the exact model/MTM lists this CPU (9th gen needs a recent BIOS on 8th/9th-gen models)",
            "• the 35 W cooler and fresh thermal paste",
            "• undamaged socket pins",
            "• not an ES/QS sample",
        ]),
    ]


def process_cpu_hunt_queries(
    session, catalog_url, queries, api_headers, seen_ids, dry_run, targets,
):
    """Run CPU Hunt queries with their own seen IDs and classification."""
    processing_failed = False
    fetch_details = bool(getattr(Config, "cpu_hunt_fetch_details", False))
    for params in queries:
        matched_query = params.get("search_text") or CPU_HUNT_LABEL
        items = fetch_query_items(
            CPU_HUNT_LABEL, matched_query, session, catalog_url, params, api_headers
        )
        if items is None:
            processing_failed = True
            continue
        for item in items:
            if not run_item_safely(CPU_HUNT_LABEL, item, lambda item=item: process_hunt_item(
                item, seen_ids, dry_run,
                label=CPU_HUNT_LABEL,
                classify=lambda raw: classify_cpu_hunt_candidate(raw, targets),
                build_lines=lambda candidate, query=matched_query: cpu_hunt_telegram_lines(
                    candidate, query
                ),
                fetch_details=fetch_details,
                matched_query=matched_query,
                session=session,
            )):
                processing_failed = True
    return processing_failed


def load_availability_configuration():
    """Return (checks per run, recheck hours, max age days); 0 checks means disabled."""
    if not getattr(Config, "availability_check_enabled", True):
        return 0, DEFAULT_AVAILABILITY_RECHECK_HOURS, DEFAULT_AVAILABILITY_MAX_AGE_DAYS
    try:
        per_run = int(getattr(Config, "availability_checks_per_run", DEFAULT_AVAILABILITY_CHECKS_PER_RUN))
        recheck_hours = float(getattr(Config, "availability_recheck_hours", DEFAULT_AVAILABILITY_RECHECK_HOURS))
        max_age_days = float(getattr(Config, "availability_max_age_days", DEFAULT_AVAILABILITY_MAX_AGE_DAYS))
    except (TypeError, ValueError):
        logging.warning("Invalid availability settings; using the defaults")
        per_run = DEFAULT_AVAILABILITY_CHECKS_PER_RUN
        recheck_hours = DEFAULT_AVAILABILITY_RECHECK_HOURS
        max_age_days = DEFAULT_AVAILABILITY_MAX_AGE_DAYS
    per_run = max(0, min(per_run, MAX_AVAILABILITY_CHECKS_PER_RUN))
    return per_run, max(recheck_hours, 1.0), max(max_age_days, 1.0)


def _utc_iso(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def process_availability_checks(session, dry_run=False):
    """Re-check a few alerted mini-PC listings and record whether they are still for sale.

    Runs after the searches, which always take priority. A small batch per run,
    with the same delay as listing-page requests; the first HTTP 429 stops the
    checks (and listing-page requests) for the rest of the run. Dry runs skip it.
    """
    per_run, recheck_hours, max_age_days = load_availability_configuration()
    if dry_run or per_run == 0:
        return
    stats = run_report.availability
    if _detail_fetch_state["paused"] or _catalog_state["paused"]:
        stats.skipped = True
        return
    now = datetime.now(timezone.utc)
    rows = store.listings_to_check(
        "mini_pc", per_run,
        checked_before=_utc_iso(now - timedelta(hours=recheck_hours)),
        seen_after=_utc_iso(now - timedelta(days=max_age_days)),
    )
    for row in rows:
        wait_before_detail_fetch()
        try:
            status = availability.check_listing(
                row["url"], row["item_id"], session, headers=build_detail_headers()
            )
        except availability.AvailabilityRateLimited:
            _detail_fetch_state["paused"] = True
            stats.stopped = True
            logging.warning("Vinted rate-limited availability checks; stopping them for this run")
            run_report.add_issue(
                "rate_limit", "Availability checks rate-limited; the rest wait for the next run"
            )
            break
        except Exception as error:  # one listing must not stop the others
            stats.errors += 1
            store.record_availability("mini_pc", row["item_id"], None)
            run_report.add_issue(
                "listing_page", f"Availability of “{row['title']}”: {describe_request_error(error)}"
            )
            continue
        store.record_availability("mini_pc", row["item_id"], status.state, status.label)
        stats.count(status.state)
        if status.state in availability.FINAL_STATES:
            logging.info("Listing %s is now %s (%s)", row["item_id"], status.state, row["title"])


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


def process_queries(session, catalog_url, queries, api_headers, seen_ids, dry_run):
    """Process every configured query and report whether any query failed."""
    processing_failed = False
    for params in queries:
        matched_query = params.get("search_text") or "filters only"
        items = fetch_query_items(
            MINI_PC_LABEL, matched_query, session, catalog_url, params, api_headers
        )
        if items is None:
            processing_failed = True
            continue

        for item in items:
            if not run_item_safely(MINI_PC_LABEL, item, lambda item=item: process_item(
                item, seen_ids, dry_run, matched_query=matched_query, session=session,
            )):
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


def telegram_configured():
    """Return whether Telegram credentials are available for status messages."""
    return bool(getattr(Config, "telegram_bot_token", "")) and bool(
        getattr(Config, "telegram_chat_id", "")
    )


def run_summary_mode():
    """Return the telegram_run_summary setting: always, problems or off."""
    value = str(getattr(Config, "telegram_run_summary", "always")).strip().lower()
    if value not in report_module.SUMMARY_MODES:
        logging.warning("Unknown telegram_run_summary %r; using 'always'", value)
        return "always"
    return value


def send_run_summary(dry_run=False):
    """Send the end-of-run status message, delivering earlier undelivered ones first.

    Messages that cannot be delivered (for example while the network is down)
    are kept in PENDING_ALERTS_PATH and sent by the next run that reaches Telegram.
    """
    message = run_report.build_message(dry_run)
    logging.info("Run summary:\n%s", fmt.to_plain_text(message))
    if dry_run:
        print(fmt.to_plain_text(message))
        return
    mode = run_summary_mode()
    if mode == "off" or (mode == "problems" and not run_report.issues):
        return
    if not telegram_configured():
        logging.warning("Telegram is not configured; the run summary was only logged")
        return

    pending = report_module.load_pending_messages(PENDING_ALERTS_PATH)
    if pending and post_telegram_message(report_module.build_late_delivery_message(pending)):
        pending = []
        wait_after_telegram_message()
    if not post_telegram_message(message):
        pending.append(message)
    report_module.save_pending_messages(PENDING_ALERTS_PATH, pending)


def main(dry_run=False):
    """Run every search mode, then report the outcome of the run on Telegram."""
    global run_report, store, current_run_id
    configure_logging()
    run_report = report_module.RunReport()
    _detail_fetch_state["paused"] = False
    _catalog_state["paused"] = False
    try:
        store = open_store()
        current_run_id = store.start_run(dry_run)
        exit_code = run_scan(dry_run)
    except (OSError, sqlite3.Error) as error:
        logging.exception("Local database error")
        run_report.add_issue("local_database", str(error), fatal=True)
        exit_code = 1
    except Exception as error:  # report crashes instead of failing silently
        logging.exception("Vinted scanner crashed")
        run_report.add_issue("crash", f"{type(error).__name__}: {error}", fatal=True)
        exit_code = 1
    try:
        send_run_summary(dry_run)
    except Exception:  # the summary must never change the run's exit code
        logging.exception("Unable to send the run summary")
    save_run_outcome(dry_run)
    return exit_code


def save_run_outcome(dry_run=False):
    """Store the run report in the database and close it; failures are only logged."""
    global store, current_run_id
    if store is None:
        return
    try:
        if current_run_id is not None:
            summary = fmt.to_plain_text(run_report.build_message(dry_run))
            store.finish_run(current_run_id, run_report, summary)
    except sqlite3.Error:
        logging.exception("Unable to save the run in the database")
    finally:
        store.close()
        store, current_run_id = None, None


def run_scan(dry_run=False):
    """Run mini-PC searches and, independently, the optional RAM/CPU Hunt searches."""
    try:
        catalog_url, queries, api_headers = load_configuration(dry_run)
        ram_hunt_queries, ram_hunt_max_price = load_ram_hunt_configuration()
        cpu_hunt_queries, cpu_hunt_targets = load_cpu_hunt_configuration()
        seen_ids = load_seen_ids()
    except (AttributeError, OSError, ValueError, InvalidOperation, sqlite3.Error) as error:
        message = f"Configuration or local database error: {error}"
        logging.error(message)
        print(message, file=sys.stderr)
        run_report.add_issue("config", str(error), fatal=True)
        return 2

    try:
        session = initialize_vinted_session(Config.vinted_url)
    except requests.exceptions.RequestException as error:
        logging.error("Unable to initialize the Vinted session: %s", error)
        run_report.add_issue(
            request_error_kind(error),
            f"Could not open {Config.vinted_url}: {describe_request_error(error)}; "
            "no search was run",
            fatal=True,
        )
        return 1

    anon_id = session.cookies.get("anon_id")
    if anon_id:
        api_headers["X-Anon-Id"] = anon_id

    # Register enabled modes up front so the summary lists them in a stable order.
    run_report.mode(MINI_PC_LABEL)
    if ram_hunt_queries:
        run_report.mode(RAM_HUNT_LABEL)
    if cpu_hunt_queries:
        run_report.mode(CPU_HUNT_LABEL)

    try:
        # Keep the original mini-PC search behavior and dedupe database intact.
        query_failed = process_queries(
            session, catalog_url, queries, api_headers, seen_ids["mini_pc"], dry_run,
        )
        if ram_hunt_queries:
            ram_hunt_failed = process_ram_hunt_queries(
                session, catalog_url, ram_hunt_queries, api_headers,
                seen_ids["ram"], dry_run, ram_hunt_max_price,
            )
            query_failed = query_failed or ram_hunt_failed
        if cpu_hunt_queries:
            cpu_hunt_failed = process_cpu_hunt_queries(
                session, catalog_url, cpu_hunt_queries, api_headers,
                seen_ids["cpu"], dry_run, cpu_hunt_targets,
            )
            query_failed = query_failed or cpu_hunt_failed
        # Availability problems are reported but never fail the run.
        process_availability_checks(session, dry_run)
    except (OSError, sqlite3.Error) as error:
        logging.error("Unable to update the local database: %s", error)
        run_report.add_issue("local_database", str(error), fatal=True)
        return 1
    return 1 if query_failed else 0


if __name__ == "__main__":
    arguments = parse_arguments()
    sys.exit(main(dry_run=arguments.dry_run))
