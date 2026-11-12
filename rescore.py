"""Recalculate stored mini-PC analyses with the current hardware rules.

Each alerted listing (notified, dry_run or failed) is analysed again from its
stored title, price and description, through the same path as a new alert:
``hardware_enricher`` builds the alert text and ``alert_parser`` turns it into
columns. Only the analysis columns change; status, dates, title, price, links
and description stay as they are, and nothing is sent to Telegram.

Listings imported from Telegram only have the description excerpt shown in the
alert. The old alert's identified CPU, RAM and disk lines (summaries of the full
description at the time) are added to the analysed text, so the rescore does not
lose them; values the old alert marked as unknown (❓) are not carried over.

    python3 rescore.py --dry-run          # print the changes, write nothing
    python3 rescore.py                    # back up the database, then update it
    python3 rescore.py --db other.db --all
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import alert_parser
import hardware_enricher
import storage
import vinted_scanner

RESCORED_STATUSES = ("notified", "dry_run", "failed")
# Columns compared in the report, besides score and verdict.
REPORTED_FIELDS = ("upgrade_score", "platform", "cpu", "ram_gb", "storage", "total_16_eur")
# Hardware rows of an old alert that are carried over for Telegram-imported listings.
CARRIED_ROW_PREFIXES = ("CPU:", "RAM:", "Disk:")


def earlier_hardware_rows(sections_json: str | None) -> list[str]:
    """Identified CPU/RAM/disk rows of the stored alert ("❓" rows are unknowns)."""
    try:
        sections = json.loads(sections_json or "[]")
    except ValueError:
        return []
    rows = []
    for section in sections if isinstance(sections, list) else []:
        if not str(section.get("title", "")).endswith("Hardware"):
            continue
        for row in section.get("rows", []):
            row = str(row).strip()
            if row.startswith(CARRIED_ROW_PREFIXES) and "❓" not in row:
                rows.append(row)
    return rows


def analysis_fields(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    """Return the analysis columns the current rules give a stored listing."""
    price = row["price_eur"]
    item = {
        "id": str(row["item_id"]),
        "title": row["title"] or "",
        "price": f"{price} EUR" if price is not None else "N/A",
        "url": row["url"] or "",
        "image": row["image_url"],
        "description": row["description"] or "",
    }
    if row["source"] == "telegram":
        carried = earlier_hardware_rows(row["sections"])
        if carried:
            item["description"] = "\n".join([item["description"], *carried])
    hardware_enricher.enrich_item(item, fetch_details=False)
    headline = hardware_enricher.build_telegram_headline(item)
    lines = hardware_enricher.build_telegram_lines(item, row["matched_query"])
    text = vinted_scanner.alert_plain_text(item, headline, lines, row["matched_query"])
    parsed = alert_parser.parse_alert(text)
    if not parsed or parsed.get("mode") != "mini_pc":
        raise ValueError("the rebuilt alert could not be parsed")
    fields = {field: parsed.get(field) for field in storage.ANALYSIS_FIELDS}
    fields["score_version"] = hardware_enricher.SCORING_VERSION
    return fields


def select_rows(db: sqlite3.Connection, include_current: bool) -> list[sqlite3.Row]:
    marks = ", ".join("?" for _ in RESCORED_STATUSES)
    columns = {row["name"] for row in db.execute("PRAGMA table_info(listings)")}
    query = (
        f"SELECT * FROM listings WHERE mode = 'mini_pc' AND status IN ({marks})"
        " AND title IS NOT NULL"
    )
    args: list[Any] = list(RESCORED_STATUSES)
    if not include_current and "score_version" in columns:
        query += " AND score_version IS NOT ?"
        args.append(hardware_enricher.SCORING_VERSION)
    return list(db.execute(query + " ORDER BY first_seen", args))


def backup_database(path: Path) -> Path:
    """Copy the database with SQLite's backup API (safe while it is open)."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = path.with_name(f"{path.name}.bak-{stamp}")
    source = sqlite3.connect(path)
    destination = sqlite3.connect(target)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    return target


def describe_change(row: sqlite3.Row, fields: dict[str, Any]) -> str | None:
    """One report line for a listing whose analysis changed, else None."""
    changes = []
    for field in REPORTED_FIELDS:
        if row[field] != fields.get(field):
            changes.append(f"{field} {row[field]!r} → {fields.get(field)!r}")
    if row["verdict"] != fields.get("verdict"):
        changes.append(f"verdict “{row['verdict']}” → “{fields.get('verdict')}”")
    if row["score"] == fields.get("score") and not changes:
        return None
    source = " [Telegram excerpt]" if row["source"] == "telegram" else ""
    head = f"{row['score']!s:>4} → {fields.get('score')!s:<4}{source} {row['title'][:70]}"
    return "\n".join([head, *(f"         {change}" for change in changes)])


def rescore(path: Path, dry_run: bool = False, include_current: bool = False) -> int:
    if not path.exists():
        print(f"Database not found: {path}", file=sys.stderr)
        return 1
    reader = storage.connect(path, readonly=True)
    rows = select_rows(reader, include_current)
    reader.close()
    if not rows:
        print("Nothing to rescore: every alerted listing already has the current score version.")
        return 0

    results: list[tuple[sqlite3.Row, dict[str, Any]]] = []
    errors: list[str] = []
    for row in rows:
        try:
            results.append((row, analysis_fields(row)))
        except Exception as error:  # one bad row must not stop the others
            errors.append(f"{row['item_id']} ({row['title'][:50]}): {error}")

    report = [(row, fields, describe_change(row, fields)) for row, fields in results]
    changed = [entry for entry in report if entry[2]]
    changed.sort(key=lambda entry: -abs((entry[1].get("score") or 0) - (entry[0]["score"] or 0)))
    for _row, _fields, line in changed:
        print(line)
    telegram = sum(row["source"] == "telegram" for row, _ in results)
    print(
        f"\n{len(results)} listings analysed ({telegram} from Telegram excerpts), "
        f"{len(changed)} changed, {len(results) - len(changed)} unchanged, {len(errors)} errors."
    )
    for error in errors:
        print(f"ERROR {error}")

    if dry_run:
        print("Dry run: the database was not changed.")
        return 1 if errors else 0
    backup = backup_database(path)
    print(f"Backup written to {backup}")
    store = storage.Store(path)
    try:
        for row, fields in results:
            store.replace_analysis("mini_pc", row["item_id"], fields)
    finally:
        store.close()
    print(f"Updated {len(results)} listings to score version {hardware_enricher.SCORING_VERSION}.")
    return 1 if errors else 0


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--db", type=Path, default=vinted_scanner.DATABASE_PATH,
                        help="database to update (default: vinted_scanner.db next to the scanner)")
    parser.add_argument("--dry-run", action="store_true", help="print the changes without writing")
    parser.add_argument("--all", action="store_true",
                        help="also recalculate listings already at the current score version")
    return parser.parse_args(argv)


if __name__ == "__main__":
    arguments = parse_arguments()
    sys.exit(rescore(arguments.db, dry_run=arguments.dry_run, include_current=arguments.all))
