#!/usr/bin/env python3
"""Rebuild the scanner database from a Telegram Desktop JSON chat export.

Telegram Desktop → chat menu → Export chat history → format "Machine-readable
JSON" produces ``result.json``. Alerts in the current layout (headline such as
"🔴 51/100 · Weak for a Proxmox node", RAM Hunt and CPU Hunt alerts) become
notified listings; end-of-run summaries (also those inside "Delivered late")
become runs. Older alert formats are skipped. Running it twice changes nothing.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import alert_parser
import storage

DEFAULT_DATABASE_PATH = Path(__file__).resolve().parent / "vinted_scanner.db"
ITEM_ID_RE = re.compile(r"/items/(\d+)")
# Alerts are sent while their run is still going: allow this slack after the run's end.
RUN_MATCH_SLACK_SECONDS = 120


def message_text(message: dict) -> str:
    text = message.get("text", "")
    if isinstance(text, str):
        return text
    return "".join(part if isinstance(part, str) else part.get("text", "") for part in text)


def message_links(message: dict) -> dict[str, str]:
    """Return {link label: URL}, e.g. {"Open listing": "...", "Photo": "..."}."""
    return {
        entity.get("text", ""): entity["href"]
        for entity in message.get("text_entities", [])
        if entity.get("type") == "text_link" and entity.get("href")
    }


def message_time(message: dict) -> datetime:
    if message.get("date_unixtime"):
        return datetime.fromtimestamp(int(message["date_unixtime"]), timezone.utc)
    # Older exports only have the exporter's local time.
    return datetime.fromisoformat(message["date"]).astimezone(timezone.utc)


def iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def import_export(export: dict, store: storage.Store) -> Counter:
    """Import every recognised message of ``export``; return what was found."""
    counts: Counter = Counter()
    for message in export.get("messages", []):
        if message.get("type") != "message":
            continue
        text = message_text(message)
        sent_at = message_time(message)
        mode = import_alert(text, message_links(message), sent_at, store)
        if mode:
            counts[f"listing:{mode}"] += 1
            continue
        summaries = alert_parser.parse_run_summaries(text)
        for summary in summaries:
            counts["runs_new" if import_run(summary, sent_at, store) else "runs_known"] += 1
        if not summaries:
            counts["skipped"] += 1
    link_listings_to_runs(store)
    return counts


def import_alert(text: str, links: dict[str, str], sent_at: datetime, store: storage.Store) -> str | None:
    """Save an alert as a notified listing; return its mode, or None if it is not one."""
    data = alert_parser.parse_alert(text)
    url = links.get("Open listing", "")
    match = ITEM_ID_RE.search(url)
    if not data or not match:
        return None
    mode = data.pop("mode")
    store.record_listing(
        mode, match.group(1), "notified", source="telegram", seen_at=iso(sent_at),
        url=url, image_url=links.get("Photo"), **data,
    )
    return mode


def import_run(summary: dict, sent_at: datetime, store: storage.Store) -> bool:
    """Save one run; its start is the message time minus the run duration.

    Reports delivered late were sent much later, so their footer time is used.
    """
    if summary.get("delivered_late") or summary["duration_s"] is None:
        started_at = summary["started_at"] or iso(sent_at)
    else:
        started_at = iso(sent_at - timedelta(seconds=summary["duration_s"]))
    _, created = store.save_run(
        started_at=started_at, source="telegram", status=summary["status"],
        duration_s=summary["duration_s"], dry_run=summary["dry_run"],
        summary=summary["summary"], modes=summary["modes"], issues=summary["issues"],
    )
    return created


def link_listings_to_runs(store: storage.Store) -> None:
    """Attach imported listings to the run that was going on when they were sent."""
    with store.db:
        store.db.execute(
            """
            UPDATE listings SET run_id = (
                SELECT runs.id FROM runs
                WHERE runs.started_at <= listings.notified_at
                  AND julianday(listings.notified_at) <= julianday(runs.started_at)
                      + (COALESCE(runs.duration_s, 0) + ?) / 86400.0
                ORDER BY runs.started_at DESC LIMIT 1
            )
            WHERE source = 'telegram' AND run_id IS NULL AND notified_at IS NOT NULL
            """,
            (RUN_MATCH_SLACK_SECONDS,),
        )


def parse_arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("export", type=Path, help="result.json from Telegram Desktop")
    parser.add_argument("--db", type=Path, default=DEFAULT_DATABASE_PATH, help="database to update")
    parser.add_argument("--dry-run", action="store_true", help="parse and report without saving")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    arguments = parse_arguments(argv)
    try:
        export = json.loads(arguments.export.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        print(f"Cannot read {arguments.export}: {error}", file=sys.stderr)
        return 2
    store = storage.Store(":memory:" if arguments.dry_run else arguments.db)
    try:
        counts = import_export(export, store)
    finally:
        store.close()
    for mode, label in storage.MODE_LABELS.items():
        print(f"{label}: {counts[f'listing:{mode}']} alerts")
    print(f"Runs: {counts['runs_new']} new, {counts['runs_known']} already imported")
    print(f"Skipped messages (older format or not an alert): {counts['skipped']}")
    if arguments.dry_run:
        print("Dry run: nothing was saved.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
