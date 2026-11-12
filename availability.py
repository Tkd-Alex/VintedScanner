"""Read whether an alerted Vinted listing is still for sale.

The public listing page embeds the item's state next to its own id:
``"is_closed"``, ``"is_reserved"`` and, once a sale went through, a
``buyer_item_status`` block with theme ``SUCCESS`` and the localized title
("Venduto", "Vendu", "Sold"). Observed on vinted.it pages in November 2026:

- active:   is_closed false, is_reserved false
- reserved: is_reserved true (a buyer is completing the purchase)
- sold:     is_closed true and the SUCCESS status block
- closed:   is_closed true without that block (reason not shown, e.g. the
            seller closed it); kept apart from "sold" so it is never overstated
- removed:  HTTP 404/410, the listing no longer exists

A page where the item's own state cannot be found is "unreadable": the caller
records the check time but keeps the previous state.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

ACTIVE_STATES = ("active", "reserved")
FINAL_STATES = ("sold", "closed", "removed")
STATES = ACTIVE_STATES + FINAL_STATES
# A state field belongs to the listing only if its id is within this distance;
# the page can also contain other items (recommendations).
OWN_ID_WINDOW_CHARS = 300
MAX_PAGE_CHARS = 3_000_000
REQUEST_TIMEOUT_SECONDS = 15


@dataclass
class ListingStatus:
    state: str | None            # one of STATES, None when unreadable
    label: str | None = None     # Vinted's own wording, e.g. "Venduto"
    http_status: int | None = None


class AvailabilityRateLimited(Exception):
    """Vinted answered HTTP 429; the caller stops checking for this run."""


def _flags_near_id(text: str, field: str, item_id: str) -> list[bool]:
    """Values of a boolean field whose surroundings name this item id."""
    own = re.compile(r'"(?:item_id|id)":"?%s\b' % re.escape(item_id))
    values = []
    for match in re.finditer(r'"%s":(true|false)' % re.escape(field), text):
        start = max(0, match.start() - OWN_ID_WINDOW_CHARS)
        if own.search(text, start, match.end() + OWN_ID_WINDOW_CHARS):
            values.append(match.group(1) == "true")
    return values


def _sold_label(text: str, item_id: str) -> str | None:
    """Title of the item's SUCCESS buyer_item_status block ("Venduto"), if any."""
    for match in re.finditer(r'"name":"buyer_item_status"', text):
        block = text[max(0, match.start() - OWN_ID_WINDOW_CHARS):match.start()]
        if f'"item_id":"{item_id}"' not in block and f'"item_id":{item_id}' not in block:
            continue
        status = re.search(r'"theme":"(\w+)","title":"([^"]{1,40})"', block)
        if status and status.group(1) == "SUCCESS":
            return status.group(2)
    return None


def parse_listing_status(html: str, item_id: str) -> ListingStatus:
    """Return the state of ``item_id`` from its listing page HTML."""
    text = (html or "")[:MAX_PAGE_CHARS].replace('\\"', '"')
    item_id = str(item_id)
    closed = _flags_near_id(text, "is_closed", item_id)
    if not closed:
        return ListingStatus(None)
    if any(closed):
        label = _sold_label(text, item_id)
        return ListingStatus("sold" if label else "closed", label)
    if any(_flags_near_id(text, "is_reserved", item_id)):
        return ListingStatus("reserved")
    return ListingStatus("active")


def check_listing(url: str, item_id: str, session: Any, headers: dict[str, str] | None = None) -> ListingStatus:
    """Fetch one listing page and read its state.

    Raises AvailabilityRateLimited on HTTP 429 and lets other request errors
    propagate; a 404/410 means the listing was removed.
    """
    response = session.get(url, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
    if response.status_code == 429:
        raise AvailabilityRateLimited(url)
    if response.status_code in (404, 410):
        return ListingStatus("removed", http_status=response.status_code)
    response.raise_for_status()
    # A redirect to another page (not this item) is not evidence either way.
    if f"/items/{item_id}" not in str(getattr(response, "url", "") or url):
        return ListingStatus(None, http_status=response.status_code)
    status = parse_listing_status(response.text, item_id)
    status.http_status = response.status_code
    return status
