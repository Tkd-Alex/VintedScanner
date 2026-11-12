import unittest
from unittest.mock import Mock

import requests

import availability


def page(item_id, closed=False, reserved=False, sold_title=None, other_id="999"):
    """Listing-page fragment shaped like the real vinted.it pages (escaped Next.js payload)."""
    parts = [
        # A recommended item with the opposite state must not be read as this one.
        '{\\"item_id\\":\\"%s\\",\\"is_closed\\":%s,\\"is_reserved\\":true}' % (other_id, str(not closed).lower()),
        "x" * 400,
        '{\\"data\\":{\\"accessibility_label\\":\\"M720q\\",\\"is_closed\\":%s,\\"item_id\\":\\"%s\\"}}'
        % (str(closed).lower(), item_id),
        "y" * 400,
        '{\\"id\\":%s,\\"is_reserved\\":%s,\\"can_buy\\":%s}' % (item_id, str(reserved).lower(), str(not closed).lower()),
    ]
    if sold_title:
        parts.append(
            '{\\"plugins\\":[{\\"data\\":{\\"item_id\\":\\"%s\\",\\"seller_id\\":\\"1\\",'
            '\\"theme\\":\\"SUCCESS\\",\\"title\\":\\"%s\\"},\\"exposures\\":[],\\"name\\":\\"buyer_item_status\\"}]}'
            % (item_id, sold_title)
        )
    return "<html><script>" + "".join(parts) + "</script></html>"


class ParseTests(unittest.TestCase):
    def test_states(self):
        cases = [
            (page("42"), "active", None),
            (page("42", reserved=True), "reserved", None),
            (page("42", closed=True, sold_title="Venduto"), "sold", "Venduto"),
            (page("42", closed=True), "closed", None),
        ]
        for html, state, label in cases:
            with self.subTest(state=state):
                status = availability.parse_listing_status(html, "42")
                self.assertEqual((status.state, status.label), (state, label))

    def test_state_of_another_item_is_not_used(self):
        self.assertIsNone(availability.parse_listing_status(page("42"), "7").state)
        self.assertIsNone(availability.parse_listing_status("<html>no data</html>", "42").state)

    def test_sold_block_of_another_item_does_not_make_it_sold(self):
        html = page("42", closed=True) + page("7", closed=True, sold_title="Venduto", other_id="8")
        self.assertEqual(availability.parse_listing_status(html, "42").state, "closed")


class CheckTests(unittest.TestCase):
    def session(self, status, text="", url="https://www.vinted.it/items/42-m720q"):
        response = Mock(status_code=status, text=text, url=url)
        if status >= 400:
            response.raise_for_status.side_effect = requests.exceptions.HTTPError(response=response)
        return Mock(get=Mock(return_value=response))

    def test_page_is_parsed(self):
        status = availability.check_listing("https://www.vinted.it/items/42", "42", self.session(200, page("42")))
        self.assertEqual((status.state, status.http_status), ("active", 200))

    def test_missing_listing_is_removed(self):
        for code in (404, 410):
            status = availability.check_listing("u", "42", self.session(code))
            self.assertEqual(status.state, "removed")

    def test_rate_limit_raises(self):
        with self.assertRaises(availability.AvailabilityRateLimited):
            availability.check_listing("u", "42", self.session(429))

    def test_other_http_errors_propagate(self):
        with self.assertRaises(requests.exceptions.HTTPError):
            availability.check_listing("u", "42", self.session(503))

    def test_redirect_to_another_page_is_unreadable(self):
        session = self.session(200, page("42"), url="https://www.vinted.it/catalog")
        self.assertIsNone(availability.check_listing("u", "42", session).state)


if __name__ == "__main__":
    unittest.main()
