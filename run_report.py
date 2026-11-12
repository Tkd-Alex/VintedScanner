"""Collect what happened during one scanner run and summarize it for Telegram.

Every run ends with a short status message, so a silent chat always means the
scanner did not run at all. The message says how many searches ran per mode,
how many new listings were evaluated and matched, and lists every problem met
on the way: Vinted rate limits (HTTP 429), network errors, unexpected Vinted
responses, listing-processing errors, failed notifications and crashes.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import message_format as fmt


ISSUE_TITLES = {
    "crash": "💥 Scanner crashed",
    "config": "⚙️ Configuration or local file error",
    "rate_limit": "🚦 Vinted rate limit (HTTP 429)",
    "http": "⛔ Vinted HTTP error",
    "network": "🌐 Network error",
    "vinted_response": "🧾 Unexpected Vinted response",
    "listing_page": "📄 Listing page not read",
    "processing": "🐛 Listing processing error",
    "notification": "📨 Notification not delivered",
    "local_database": "💾 Local database error",
}
MAX_EXAMPLES_PER_KIND = 3
MAX_EXAMPLE_CHARS = 220
# Telegram rejects messages longer than 4096 characters.
MAX_MESSAGE_CHARS = 4000
MAX_PENDING_MESSAGES = 10
SUMMARY_MODES = {"always", "problems", "off"}


@dataclass
class ModeStats:
    """Counters for one search mode (mini PC, RAM Hunt, CPU Hunt)."""

    searches: int = 0
    failed_searches: int = 0
    skipped_searches: int = 0
    listings: int = 0
    new: int = 0
    matches: int = 0
    notified: int = 0


@dataclass
class AvailabilityStats:
    """Outcome of the availability checks of alerted listings in one run."""

    active: int = 0
    reserved: int = 0
    sold: int = 0
    closed: int = 0
    removed: int = 0
    unreadable: int = 0
    errors: int = 0
    skipped: bool = False  # not started because Vinted already rate-limited this run
    stopped: bool = False  # interrupted by a rate limit

    def count(self, state: str | None) -> None:
        if state is None:
            self.unreadable += 1
        else:
            setattr(self, state, getattr(self, state) + 1)

    @property
    def checked(self) -> int:
        return self.active + self.reserved + self.sold + self.closed + self.removed + self.unreadable


class RunReport:
    """Mutable record of a single scanner run."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self.started = clock()
        self.started_at = datetime.now()
        self.modes: dict[str, ModeStats] = {}
        self.issues: dict[str, list[str]] = {}
        self.availability = AvailabilityStats()
        self.fatal = False

    def mode(self, label: str) -> ModeStats:
        return self.modes.setdefault(label, ModeStats())

    def add_issue(self, kind: str, message: str, fatal: bool = False) -> None:
        self.issues.setdefault(kind, []).append(str(message))
        self.fatal = self.fatal or fatal

    @property
    def issue_count(self) -> int:
        return sum(len(messages) for messages in self.issues.values())

    def total(self, field: str) -> int:
        return sum(getattr(stats, field) for stats in self.modes.values())

    def elapsed_seconds(self) -> float:
        return max(0.0, self._clock() - self.started)

    def headline(self, dry_run: bool = False) -> str:
        suffix = " (dry run)" if dry_run else ""
        matches = self.total("matches")
        if self.fatal:
            return f"🔴 <b>Vinted scan failed</b>{suffix}"
        if self.issues:
            count = self.issue_count
            plural = "" if count == 1 else "s"
            found = f" · {matches} match{'es' if matches != 1 else ''}" if matches else ""
            return f"⚠️ <b>Vinted scan finished with {count} problem{plural}</b>{found}{suffix}"
        if matches:
            return (
                f"✅ <b>Vinted scan finished</b> · {matches} "
                f"match{'es' if matches != 1 else ''} notified{suffix}"
            )
        return f"✅ <b>Vinted scan finished</b> · nothing new matched{suffix}"

    def build_lines(self, dry_run: bool = False) -> list[str]:
        """Return the summary as Telegram HTML lines, headline first."""
        lines = [self.headline(dry_run)]
        for label, stats in self.modes.items():
            lines.append(_mode_line(label, stats))
        availability_line = _availability_line(self.availability)
        if availability_line:
            lines.append(availability_line)
        for kind, messages in self.issues.items():
            title = ISSUE_TITLES.get(kind, f"❗ {kind}")
            count = f" ×{len(messages)}" if len(messages) > 1 else ""
            lines.append("")
            lines.append(f"<b>{fmt.esc(title)}</b>{count}")
            unique = list(dict.fromkeys(messages))
            for message in unique[:MAX_EXAMPLES_PER_KIND]:
                lines.append(f"• {fmt.esc(_shorten(message))}")
            if len(unique) > MAX_EXAMPLES_PER_KIND:
                lines.append(f"• … and {len(unique) - MAX_EXAMPLES_PER_KIND} more (see vinted_scanner.log)")
        lines.append("")
        lines.append(
            f"🕒 {self.started_at:%Y-%m-%d %H:%M} · took {_format_duration(self.elapsed_seconds())}"
        )
        return lines

    def build_message(self, dry_run: bool = False) -> str:
        return truncate_message("\n".join(self.build_lines(dry_run)))


def _mode_line(label: str, stats: ModeStats) -> str:
    if stats.matches:
        outcome = f"<b>{stats.matches} match{'es' if stats.matches != 1 else ''}</b>"
        if stats.notified != stats.matches:
            outcome += f" ({stats.notified} notified)"
    elif stats.searches == 0:
        outcome = "<b>not searched</b>"
    elif stats.failed_searches == stats.searches:
        outcome = "<b>no search succeeded</b>"
    else:
        outcome = "nothing matched"
    details = [
        f"{stats.searches} search{'es' if stats.searches != 1 else ''}",
        f"{stats.listings} listings",
        f"{stats.new} new",
    ]
    if stats.failed_searches:
        details.append(f"{stats.failed_searches} failed")
    if stats.skipped_searches:
        details.append(f"{stats.skipped_searches} skipped")
    return f"• {fmt.esc(label)}: {outcome} · " + ", ".join(details)


def _availability_line(stats: AvailabilityStats) -> str | None:
    """"• Availability: 10 checked · 2 sold, 1 closed", or None when nothing happened."""
    if stats.skipped:
        return "• Availability: <b>not checked</b> · Vinted was rate-limiting this run"
    if not stats.checked and not stats.errors and not stats.stopped:
        return None
    changes = [
        f"{getattr(stats, state)} {state}" for state in ("sold", "closed", "removed", "reserved")
        if getattr(stats, state)
    ]
    if stats.unreadable:
        changes.append(f"{stats.unreadable} unreadable")
    if stats.errors:
        changes.append(f"{stats.errors} failed")
    if stats.stopped:
        changes.append("stopped by rate limit")
    outcome = ", ".join(changes) if changes else "all still for sale"
    return f"• Availability: {stats.checked} checked · {outcome}"


def _shorten(text: str) -> str:
    text = " ".join(str(text).split())
    if len(text) <= MAX_EXAMPLE_CHARS:
        return text
    return text[: MAX_EXAMPLE_CHARS - 1] + "…"


def _format_duration(seconds: float) -> str:
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes}m {seconds:02d}s"


def truncate_message(text: str) -> str:
    """Keep a message under Telegram's length limit, cutting on a line break."""
    if len(text) <= MAX_MESSAGE_CHARS:
        return text
    cut = text.rfind("\n", 0, MAX_MESSAGE_CHARS - 40)
    if cut <= 0:
        cut = MAX_MESSAGE_CHARS - 40
    return text[:cut] + "\n… (truncated, see vinted_scanner.log)"


def load_pending_messages(path: Path) -> list[str]:
    """Return status messages that could not be delivered by earlier runs."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as error:
        logging.warning("Ignoring unreadable pending alerts file %s: %s", path, error)
        return []
    if not isinstance(data, list):
        return []
    return [str(message) for message in data if message]


def save_pending_messages(path: Path, messages: list[str]) -> None:
    """Persist undelivered status messages, keeping only the most recent ones."""
    try:
        if messages:
            path.write_text(
                json.dumps(messages[-MAX_PENDING_MESSAGES:], ensure_ascii=False),
                encoding="utf-8",
            )
        else:
            path.unlink(missing_ok=True)
    except OSError as error:
        logging.error("Unable to update pending alerts file %s: %s", path, error)


def build_late_delivery_message(messages: list[str]) -> str:
    """Combine undelivered reports into one message for the next successful run."""
    count = len(messages)
    header = (
        f"📬 <b>Delivered late</b> · {count} earlier report{'s' if count != 1 else ''} "
        "could not be sent (Telegram or the network was unreachable)"
    )
    return truncate_message("\n\n———\n".join([header, *messages]))
