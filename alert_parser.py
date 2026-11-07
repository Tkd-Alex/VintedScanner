"""Turn the plain text of a Telegram alert or run summary back into data.

The scanner and the Telegram importer both use these parsers, so listings saved
live and listings restored from a chat export have exactly the same shape.
Input is plain text (``message_format.to_plain_text`` of the sent HTML, or the
text of an exported message): blocks separated by blank lines, the first one
being the header and every other one a titled section.
"""
from __future__ import annotations

import re
from typing import Any

from run_report import ISSUE_TITLES
from storage import MODE_BY_LABEL

MINI_PC_HEADLINE_RE = re.compile(r"^\S+\s+(\d+)/100 · (.+?)(?: · ⬆️ (\d+)/100 after upgrade)?$")
RAM_HEADLINE_RE = re.compile(r"^🧩 RAM HUNT · (.+)$")
CPU_HEADLINE_RE = re.compile(r"^🔧 CPU HUNT · (.+?) · €[\d.,]+$")
EURO_RE = re.compile(r"€\s?(\d[\d,]*(?:\.\d+)?)")
CORES_RE = re.compile(r"(\d+) core / (\d+) thread")
GB_RE = re.compile(r"(\d+) GB")
GENERATION_RE = re.compile(r"(\d+)th gen")
NOTE_PREFIXES = ("⚠️", "🤖")
COST_FIELDS = {
    "With 16 GB": "total_16_eur",
    "With 32 GB": "total_32_eur",
    "16 GB + CPU upgrade": "total_16_cpu_eur",
    "32 GB + CPU upgrade": "total_32_cpu_eur",
}

SUMMARY_STATUS = {"✅": "ok", "⚠️": "problems", "🔴": "failed"}
MODE_LINE_RE = re.compile(
    r"^• (?P<label>[^:]+): (?P<outcome>.+?) · (?P<searches>\d+) search(?:es)?, "
    r"(?P<listings>\d+) listings, (?P<new>\d+) new"
    r"(?:, (?P<failed_searches>\d+) failed)?(?:, (?P<skipped_searches>\d+) skipped)?$"
)
FOOTER_RE = re.compile(r"^🕒 (\d{4}-\d\d-\d\d) (\d\d:\d\d) · took (?:(\d+)m )?(\d+)s$")
LATE_HEADER = "📬 Delivered late"
LATE_SEPARATOR = re.compile(r"\n\s*———\s*\n")
KIND_BY_TITLE = {title: kind for kind, title in ISSUE_TITLES.items()}


def _blocks(text: str) -> list[list[str]]:
    return [block.split("\n") for block in re.split(r"\n[ \t]*\n", text.strip()) if block.strip()]


def _euro(text: str) -> float | None:
    match = EURO_RE.search(text)
    return float(match.group(1).replace(",", "")) if match else None


def _int(pattern: re.Pattern, text: str, group: int = 1) -> int | None:
    match = pattern.search(text)
    return int(match.group(group)) if match else None


def parse_alert(text: str) -> dict[str, Any] | None:
    """Parse a mini-PC, RAM Hunt or CPU Hunt alert; None for anything else."""
    blocks = _blocks(text)
    if not blocks or len(blocks[0]) < 2:
        return None
    header = blocks[0]
    headline = header[0].strip()
    data: dict[str, Any] = {}
    if match := RAM_HEADLINE_RE.match(headline):
        data.update(mode="ram", tier=match.group(1))
    elif match := CPU_HEADLINE_RE.match(headline):
        data.update(mode="cpu", cpu=match.group(1))
    elif match := MINI_PC_HEADLINE_RE.match(headline):
        data.update(mode="mini_pc", score=int(match.group(1)), verdict=match.group(2))
        if match.group(3):
            data["upgrade_score"] = int(match.group(3))
    else:
        return None

    data["title"] = header[1].strip()
    for line in header[2:]:
        if line.startswith("💶"):
            data["price_eur"] = _euro(line.split("·")[0])
        elif line.startswith("🔍 "):
            data["matched_query"] = line[2:].strip()

    sections = []
    for block in blocks[1:]:
        if block[0].startswith(NOTE_PREFIXES):
            sections.append({"title": "Notes", "rows": block})
        else:
            sections.append({"title": block[0].strip(), "rows": block[1:]})
    for section in sections:
        if section["title"].endswith("Seller description"):
            data["description"] = " ".join(row.strip() for row in section["rows"])
            continue
        for row in section["rows"]:
            _read_row(row.strip(), data)
    data["sections"] = sections
    return data


def _read_row(row: str, data: dict[str, Any]) -> None:
    """Map one "Label: value" row to the structured listing columns."""
    label, separator, value = row.partition(": ")
    if not separator:
        return
    parts = [part.strip() for part in value.split(" · ")]
    if label == "CPU" and not value.startswith("❓"):
        data["cpu"] = parts[0]
        if match := CORES_RE.search(value):
            data["cores"], data["threads"] = int(match.group(1)), int(match.group(2))
    elif label in ("RAM", "RAM in listing"):
        data["ram_gb"] = _int(GB_RE, parts[0])
    elif label == "Disk" and not value.startswith("❓"):
        data["storage"] = value
    elif label == "GPU":
        data["gpu"] = value
    elif label == "Base":
        if value.endswith("not in the platform table"):
            if parts[0] != "model not identified":
                data["model"] = parts[0]
        else:
            data["platform"] = data["model"] = parts[0]
            data["generation"] = _int(GENERATION_RE, value)
    elif label in COST_FIELDS:
        data[COST_FIELDS[label]] = _euro(value)
    elif label.startswith("Fits ("):
        data["generation"] = _int(GENERATION_RE, label)
    elif label == "Total":
        data["build_estimate_eur"] = _euro(value.split("=")[0])


def parse_run_summaries(text: str) -> list[dict[str, Any]]:
    """Parse an end-of-run summary, or every report inside a "Delivered late" one."""
    text = text.strip()
    if text.startswith(LATE_HEADER):
        parts = LATE_SEPARATOR.split(text)[1:]
        summaries = [parse_run_summary(part) for part in parts]
        return [dict(summary, delivered_late=True) for summary in summaries if summary]
    summary = parse_run_summary(text)
    return [summary] if summary else []


def parse_run_summary(text: str) -> dict[str, Any] | None:
    blocks = _blocks(text)
    if not blocks:
        return None
    headline = blocks[0][0].strip()
    status = next((value for icon, value in SUMMARY_STATUS.items() if headline.startswith(icon)), None)
    if status is None or "Vinted scan" not in headline:
        return None

    modes = {}
    for line in blocks[0][1:]:
        if match := MODE_LINE_RE.match(line.strip()):
            fields = match.groupdict()
            outcome = fields.pop("outcome")
            label = fields.pop("label")
            stats = {key: int(value or 0) for key, value in fields.items()}
            stats["matches"] = int(re.match(r"(\d+) match", outcome).group(1)) if re.match(r"\d+ match", outcome) else 0
            notified = re.search(r"\((\d+) notified\)", outcome)
            stats["notified"] = int(notified.group(1)) if notified else stats["matches"]
            modes[MODE_BY_LABEL.get(label, label)] = stats

    issues, started_at, duration = [], None, None
    for block in blocks[1:]:
        if match := FOOTER_RE.match(block[0].strip()):
            started_at = f"{match.group(1)}T{match.group(2)}:00Z"
            duration = int(match.group(3) or 0) * 60 + int(match.group(4))
            continue
        title = re.sub(r" ×\d+$", "", block[0].strip())
        kind = KIND_BY_TITLE.get(title) or title.removeprefix("❗ ")
        for row in block[1:]:
            row = row.strip()
            if row.startswith("• ") and not row.startswith("• … and "):
                issues.append((kind, row[2:]))

    return {
        "status": status,
        "dry_run": "(dry run)" in headline,
        "modes": modes,
        "issues": issues,
        "started_at": started_at,
        "duration_s": duration,
        "summary": text,
    }
