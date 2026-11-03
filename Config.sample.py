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
# Pause (seconds) before each listing-page request.
hardware_detail_fetch_delay_seconds = 1.0

# Notify listings from the supported HP Mini / Lenovo Tiny / Dell Micro chassis families.
hardware_require_target_models = True
# Add broad model searches (M700/M710/M720/M920, EliteDesk/ProDesk Mini, OptiPlex Micro)
# to any custom searches below. Set False to use only your own queries.
hardware_add_discovery_queries = True

# RAM upgrade planning assumptions (EUR; update when you find better prices).
# €50: planning allowance for an 8 -> 16 GB upgrade; for a 4 GB base this is
# only a rough allowance because module topology/kit requirements are unknown.
# €130: planning allowance for a 2x16 GB DDR4 kit.
hardware_ram_8gb_upgrade_eur = 50
hardware_ram_32gb_kit_eur = 130

# Optional CPU price map. Keys are normalized CPU model identifiers.
# €68 for i7-6700T is based on the Vinted listing observed in Nov 2026
# (€52.70 displayed incl. protection + €15 shipping); not a market average.
# Add other CPUs only after checking a real listing and compatibility.
hardware_cpu_upgrade_costs_eur = {
    "i76700t": 68,
}

# Vinted search queries
# "search_text" may be empty when searching only by filters.
# The query builder defaults to relevance and 24 results to limit weak matches
# returned by Vinted's fuzzy search.
# Each filter value must be a list of Vinted IDs. Leave the list empty to
# disable that filter. Multiple IDs are supported in the same filter.

# Use vinted_query_builder.py to generate entries for this list.
queries = []

# Independent RAM Hunt mode: looks for cheap damaged laptops or low-priced
# laptops explicitly advertised with 32 GB DDR4, for possible SO-DIMM recovery.
# This has its own ID database and does not use the mini-PC chassis filter.
ram_hunt_enabled = True
ram_hunt_max_price_eur = 100
# Optional: fetch the listing page when catalogue results lack a description.
# Leave False to reduce extra HTTP requests during each scheduled scan.
ram_hunt_fetch_details = False
ram_hunt_queries = [
    {
        "page": "1", "per_page": "24", "search_text": "portatile guasto",
        "order": "newest_first", "filters": {}, "price_to": "100",
    },
    {
        "page": "1", "per_page": "24", "search_text": "notebook guasto",
        "order": "newest_first", "filters": {}, "price_to": "100",
    },
    {
        "page": "1", "per_page": "24", "search_text": "laptop guasto",
        "order": "newest_first", "filters": {}, "price_to": "100",
    },
    {
        "page": "1", "per_page": "24", "search_text": "portatile non funzionante",
        "order": "newest_first", "filters": {}, "price_to": "100",
    },
    {
        "page": "1", "per_page": "24", "search_text": "portatile 32gb ddr4",
        "order": "newest_first", "filters": {}, "price_to": "100",
    },
    {
        "page": "1", "per_page": "24", "search_text": "notebook 32gb ddr4",
        "order": "newest_first", "filters": {}, "price_to": "100",
    },
    {
        "page": "1", "per_page": "24", "search_text": "portatile 16gb ddr4 guasto",
        "order": "newest_first", "filters": {}, "price_to": "100",
    },
]
