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
# Pause (seconds) after each Telegram message; a 429 rate-limit answer is
# retried once after Telegram's retry_after hint (capped at 30 s).
telegram_send_delay_seconds = 1.0
# End-of-run status message on Telegram, so a silent chat always means the
# scanner did not run. It lists searches, new listings and matches per mode,
# plus every problem met: Vinted HTTP 429, network errors, unexpected Vinted
# responses, listing-processing errors, undelivered alerts and crashes.
# "always": after every run, also when nothing matched (default)
# "problems": only when something went wrong
# "off": never (the summary is still written to vinted_scanner.log)
telegram_run_summary = "always"

# Vinted URL: change the TLD according to your country (.fr, .es, etc.)
vinted_url = "https://www.vinted.it"

# Locale sent to the Vinted API. This can differ for multilingual markets.
vinted_locale = "it-IT"

# Hardware enrichment for Telegram notifications.
# The scanner tries one ordinary listing-page request for each newly detected item
# if the catalogue result does not already include a description. Set False to skip it.
hardware_fetch_details = True
# Pause (seconds) before each listing-page request.
hardware_detail_fetch_delay_seconds = 2.0

# Availability tracker: after the searches, re-open a few alerted mini-PC
# listings to record whether they are still for sale, reserved, sold, closed or
# removed, so the dashboard can show how fast each model sells. It uses the
# pause above between pages, stops at the first HTTP 429 and is skipped when
# the run was already rate-limited, and on --dry-run.
availability_check_enabled = True
# Listings re-checked per run (max 50); hourly runs × 10 cover ~240 a day.
availability_checks_per_run = 10
# Re-check each listing at most this often; the sale date is known to this precision.
availability_recheck_hours = 20
# Stop following a listing this many days after it was first seen.
availability_max_age_days = 45

# Notify listings from the supported HP Mini / Lenovo Tiny / Dell Micro chassis families.
hardware_require_target_models = True
# Add broad model searches (M700/M710/M720/M920, EliteDesk/ProDesk Mini, OptiPlex Micro)
# to any custom searches below. Set False to use only your own queries.
hardware_add_discovery_queries = True
# Price range (EUR) applied to those discovery searches; they are also limited
# to the desktop-computer catalogue to keep accessories and spare parts out.
hardware_discovery_price_from = 60
hardware_discovery_price_to = 400

# RAM upgrade planning assumptions (EUR; update when you find better prices).
# €50: planning allowance for an 8 -> 16 GB upgrade; for a 4 GB base this is
# only a rough allowance because module topology/kit requirements are unknown.
# €130: planning allowance for a 2x16 GB DDR4 kit.
hardware_ram_8gb_upgrade_eur = 50
hardware_ram_32gb_kit_eur = 130
# €65: one extra 16 GB SO-DIMM, used only when a listing states 1×16 GB installed.
hardware_ram_16gb_module_eur = 65

# Optional CPU price map. Keys are normalized CPU model identifiers.
# €68 for i7-6700T is based on the Vinted listing observed in Nov 2026
# (€52.70 displayed incl. protection + €15 shipping); not a market average.
# Add other CPUs only after checking a real listing and compatibility.
hardware_cpu_upgrade_costs_eur = {
    "i76700t": 68,
}

# Reference CPUs for the "Bench:" comparison line (catalog keys or names such as
# "i5-12500T"). Defaults: N100 = current homelab node, i5-12500T = target CPU.
# They are comparison anchors only, never a notification filter.
hardware_reference_cpus = ["n100", "i512500t"]

# Relative weights of the informational mini-PC score, rescaled to 100 points.
# Scores never decide whether a listing is notified.
hardware_score_weights = {"cpu": 40, "price": 25, "ram": 10, "storage": 10, "platform": 15}

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
# It keeps its own analyzed-listing IDs and does not use the mini-PC chassis filter.
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
# RAM Hunt also flags laptops within the price cap that state a 1 TB+ SSD (or a
# 500 GB NVMe when the laptop costs no more than parts_hunt_caps["nvme_500"]).
ram_hunt_ssd_donors = True
# Also search "portatile ssd 1tb", "notebook ssd 1tb" and "portatile guasto ssd",
# in addition to ram_hunt_queries.
ram_hunt_add_ssd_queries = True

# Independent Parts Hunt mode: loose DDR4 SO-DIMM modules and SSDs for a
# Dell OptiPlex 7090 Micro, judged from the listing title and price. Caps are on
# the item price (buyer fee and shipping excluded); a listing up to
# parts_hunt_discount_margin above its cap is also alerted, as "ask for a discount".
# Listings above that are skipped without being stored, so a later price drop
# is still noticed. Built-in searches use the RAM (3603) and internal storage
# (3607) categories unless parts_hunt_queries is set.
parts_hunt_enabled = True
parts_hunt_caps = {
    "ram_8": 30,       # one 8 GB DDR4 SO-DIMM
    "ram_16": 50,      # one 16 GB DDR4 SO-DIMM
    "ram_2x16": 80,    # 2x16 GB kit (or 32 GB in total)
    "nvme_500": 40,    # 500/512 GB M.2 NVMe, only when really cheap
    "nvme_1tb": 110,   # 1 TB M.2 NVMe (2280, or 2230)
    "nvme_2tb": 190,
    "sata_1tb": 85,    # 1 TB 2.5" SATA
    "sata_2tb": 150,
}
parts_hunt_discount_margin = 0.10
parts_hunt_fetch_details = False
# parts_hunt_queries = [...]  # optional; same format as ram_hunt_queries

# Independent CPU Hunt mode: looks for loose low-power CPUs that can upgrade a
# cheap i3/Pentium mini PC. Keys are CPU identifiers from hardware_enricher.py;
# values are the maximum listing price in EUR. Search terms are generated from
# the keys (e.g. "i7-8700T") unless cpu_hunt_queries is set. Uses its own
# list of analyzed listings in vinted_scanner.db.
cpu_hunt_enabled = False
cpu_hunt_targets = {
    "i78700t": 90,
    "i79700t": 110,
    "i59500t": 70,
    "i58500t": 60,
    "i77700t": 55,
    "i76700t": 50,
}
# Typical price of an i3/Pentium base unit, used for the build estimate.
cpu_hunt_base_price_eur = 90
cpu_hunt_fetch_details = False
