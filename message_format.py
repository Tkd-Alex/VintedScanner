"""HTML helpers for Telegram notifications (sent with parse_mode=HTML).

Line builders return lines that are already valid Telegram HTML: every dynamic
value goes through ``esc`` (or a helper that calls it). ``to_plain_text`` turns
the same lines back into readable console output for dry runs.
"""

from __future__ import annotations

import html
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

_TAG_RE = re.compile(r"<[^>]+>")
_PRICE_RE = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*EUR\s*$", re.I)


def esc(value: Any) -> str:
    """Escape a value for Telegram HTML text."""
    return html.escape(str(value), quote=False)


def bold(value: Any) -> str:
    return f"<b>{esc(value)}</b>"


def italic(value: Any) -> str:
    return f"<i>{esc(value)}</i>"


def code(value: Any) -> str:
    return f"<code>{esc(value)}</code>"


def link(label: Any, url: Any) -> str:
    return f'<a href="{html.escape(str(url), quote=True)}">{esc(label)}</a>'


def euro(amount: Any) -> str:
    """Format a euro amount compactly: 120 -> '€120', 120.5 -> '€120.50'."""
    try:
        value = Decimal(str(amount))
    except InvalidOperation:
        return f"€{amount}"
    if value == value.to_integral_value():
        return f"€{int(value)}"
    return f"€{value:.2f}"


def format_price(price: Any) -> str:
    """Turn the scanner's '120.00 EUR' into '€120'; leave other currencies as-is."""
    match = _PRICE_RE.match(str(price))
    if not match:
        return str(price)
    return euro(match.group(1).replace(",", "."))


def section(title: str, rows: Iterable[str]) -> list[str]:
    """A bold section title followed by its rows; empty when there are no rows."""
    rows = [row for row in rows if row]
    if not rows:
        return []
    return ["", f"<b>{esc(title)}</b>", *rows]


def expandable(title: str, rows: Iterable[str]) -> list[str]:
    """A collapsed-by-default blockquote with a bold title; rows are HTML."""
    rows = [row for row in rows if row]
    if not rows:
        return []
    body = "\n".join([f"<b>{esc(title)}</b>", *rows])
    return ["", f"<blockquote expandable>{body}</blockquote>"]


def to_plain_text(line: str) -> str:
    """Strip Telegram HTML for console output."""
    return html.unescape(_TAG_RE.sub("", line))
