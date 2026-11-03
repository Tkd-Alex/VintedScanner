# This package is configured to send Telegram notifications only.
notification_channels = ["telegram"]

# Keep SMTP and Slack disabled unless you intentionally add those channels.
# SMTP Settings for e-mail notification
smtp_username = ""
smtp_psw = ""
smtp_server = ""
smtp_toaddrs = []

# Slack WebHook for notification
slack_webhook_url = ""

# Telegram Token and ChatID for notification
telegram_bot_token = ""
telegram_chat_id = ""

# Vinted URL: change the TLD according to your country (.fr, .es, etc.)
vinted_url = "https://www.vinted.it"

# Locale sent to the Vinted API. This can differ for multilingual markets.
vinted_locale = "it-IT"

# Hardware enrichment for Telegram notifications.
# The scanner tries one ordinary listing-page request for each newly detected item
# if the catalogue result does not already include a description. Set False to skip it.
hardware_fetch_details = True

# Vinted search queries
# "search_text" may be empty when searching only by filters.
# The query builder defaults to relevance and 24 results to limit weak matches
# returned by Vinted's fuzzy search.
# Each filter value must be a list of Vinted IDs. Leave the list empty to
# disable that filter. Multiple IDs are supported in the same filter.

# Use vinted_query_builder.py to generate entries for this list.
queries = []
