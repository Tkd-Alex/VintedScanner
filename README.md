# Vinted Scanner & Notifier

## Overview

Vinted Scanner is a Python script designed to periodically search for new items listed on [Vinted](https://www.vinted.com). Since Vinted does not provide built-in automatic notifications for saved searches, this project sends notifications when a scheduled scan finds a new matching item.

With this script, you no longer need to manually open the Vinted app and search for your favorite items every time. Instead, the script runs periodically and sends notifications via **e-mail**, **Slack**, or **Telegram** whenever a new item is detected. It also keeps track of already analyzed items to prevent duplicate notifications.

## Features

- **Automated Search**: The script performs searches on Vinted according to your custom queries.
- **Optional CPU Hunt mode**: Disabled by default; finds loose low-power CPUs (e.g. i7-8700T, i5-9500T) under a per-model price cap and lists the mini PCs they fit, with its own deduplication database.
- **Separate RAM Hunt mode**: Enabled by default; finds under-€100 damaged laptops and listings mentioning 16/32 GB DDR4, for possible memory recovery. It has its own deduplication database and does not replace or filter the normal mini-PC notifications.
- **Automatic Notifications**: Receive notifications through:
  - **Email**
  - **Slack**
  - **Telegram**
- **Duplicate Prevention**: The script logs items that have already been analyzed, so you won't receive multiple notifications for the same item.
- **Interactive Query Builder**: Discover localized categories and contextual
  filters without manually looking up Vinted IDs.
- **Periodic Execution**: The script is designed to be executed periodically, such as through a cron job.

## Getting Started

### Prerequisites

- Python 3.11 or higher

### Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/drego85/VintedScanner.git
   cd VintedScanner
   ```

2. Install the required dependencies:
   ```bash
   pip3 install -r requirements.txt
   ```

3. Copy the configuration file `Config.sample.py` to `Config.py`:
   ```bash
   cp Config.sample.py Config.py
   ```

### Configuration

To customize the script for your needs, you must configure the `Config.py` file.

> [!WARNING]
> The search configuration format changed in September 2026 when Vinted
> replaced the legacy
> `/api/v2/catalog/items` endpoint. The old `catalog_ids`, `brand_ids`,
> `size_ids`, `status_ids`, `color_ids`, and `material_ids` keys are no longer
> supported. Existing `Config.py` files must be updated to use the `filters`
> dictionary shown below. The scanner exits with a configuration error when it
> detects a legacy key.

> [!IMPORTANT]
> This independent project is not affiliated with or endorsed by Vinted. It
> uses undocumented internal API endpoints that may change without notice. Use
> the scanner responsibly and comply with the rules applicable to your Vinted
> marketplace.

1. **SMTP Settings (for Email notifications)**:
   - Set your SMTP username, password, server, and recipient address in the configuration file:
     ```python
     smtp_username = "your_email@example.com"
     smtp_psw = "your_password"
     smtp_server = "smtp.example.com"
     smtp_toaddrs = ["Recipient <recipient@example.com>"]
     ```

2. **Slack Webhook (for Slack notifications)**:
   - Set the `slack_webhook_url` with your Slack Incoming Webhook URL:
     ```python
     slack_webhook_url = "https://hooks.slack.com/services/..."
     ```

3. **Telegram Bot (for Telegram notifications)**:
   - Set the Telegram bot token and chat ID:
     ```python
     telegram_bot_token = "your_bot_token"
     telegram_chat_id = "your_chat_id"
     ```

4. **Vinted Marketplace**:
   - Set the marketplace URL and locale for the country to search. The scanner
     derives the matching country-specific API host automatically:
     ```python
     vinted_url = "https://www.vinted.it"
     vinted_locale = "it-IT"
     ```
   - Change the domain and locale for other marketplaces, for example
     `https://www.vinted.fr` with `fr-FR` or `https://www.vinted.de` with
     `de-DE`.

5. **Vinted Search Queries**:
   - Define one or more searches in the same configuration file. Filter values
     must be lists of Vinted IDs; use an empty list to disable a filter:
     ```python
     queries = [
         {
             "page": "1",
            "per_page": "24",
             "search_text": "jeans",
            "order": "relevance",
             "filters": {
                 "catalog": [],
                 "brand": ["417"],  # Example brand ID
                 "size": [],
                 "status": [],
                 "color": [],
                 "material": [],
             },
         },
         {
             "page": "1",
            "per_page": "24",
             "search_text": "t-shirt",
            "order": "relevance",
             "filters": {
                 "catalog": ["2996"],  # Example category ID
                 "brand": [],
                 "size": [],
                 "status": [],
                 "color": [],
                 "material": [],
             },
         },
     ]
     ```

   **Notes on search parameters**:
   - `search_text`: Keyword to search (leave blank for all items).
   - `filters`: Dynamic Vinted filters. Supported examples include `catalog`,
     `brand`, `size`, `status`, `color`, and `material`.
   - Multiple IDs can be selected in a filter, for example
     `"brand": ["417", "53"]`.
   - `price_from`, `price_to`, and `currency` can be added at query level when
     price filtering is required.
   - `order`: Use `relevance` to prioritize strong matches and reduce unrelated
     results produced by Vinted's fuzzy search. `newest_first` remains available
     for precise searches where chronological order is more important.
   - `per_page`: The recommended value is `24`. This limits weak matches while
     remaining suitable for a scanner that runs frequently.

The scanner initializes an anonymous session on the configured marketplace and
then searches through `https://api.<marketplace-domain>/svc-catalogue/items`.
Browser cookies, access tokens, CSRF tokens, and Cloudflare cookies must not be
copied into the configuration.

### Interactive query builder

Run the query builder to discover categories, brands, sizes, colors,
conditions, materials, and other filters available for the selected category:

```bash
python3 vinted_query_builder.py
```

The wizard uses the marketplace and locale from `Config.py`, optionally shows
a matching-item preview, and prints a query dictionary ready to copy into the
`queries` list. Category names must be searched in the language identified by
`vinted_locale` (for example, Italian with `it-IT`). The builder displays the
active locale in the category prompt. Generated queries use `relevance` and
request 24 results to prioritize stronger matches. The builder never modifies
`Config.py`.

Copy the generated dictionary into `queries` in `Config.py`. The sample
configuration intentionally contains no active searches, preventing accidental
notifications before a query has been reviewed.

Categories are cached locally for 24 hours. Force an update with:

```bash
python3 vinted_query_builder.py --refresh-cache
```

The following commands provide direct lookups without starting the wizard:

```bash
python3 vinted_query_builder.py categories jeans
python3 vinted_query_builder.py brands nike --category 183
python3 vinted_query_builder.py filters --category 183 --options
```

Brand and filter results are requested dynamically because their availability
depends on the selected category and current Vinted catalogue.

### Running the Script

To run the script manually, use:

```bash
python3 vinted_scanner.py
```

The script will check for new items based on your queries and send notifications accordingly.
Each notification includes the matching search text so results can be traced
back to the query that produced them.
An item is saved as analyzed only after every configured notification succeeds.
If a delivery fails, the item is retried during the next execution. When more
than one channel is enabled, this may repeat a notification on a channel that
had already succeeded.

For a new installation, initialize the local item database before enabling
notifications:

```bash
python3 vinted_scanner.py --dry-run
```

Review the displayed items, then run the scanner normally. This prevents the
current search backlog from generating notifications on the first scheduled
execution. Skip this initialization only if notifications for all currently
matching items are intentional.

### Dry-run mode

To check the configured searches without sending e-mail, Slack, or Telegram
notifications, use:

```bash
python3 vinted_scanner.py --dry-run
```

New items and their matching search text are printed in the terminal and are
still saved in the local `vinted_items.txt` database. Consequently, they will
be considered already analyzed during subsequent executions. This runtime
file is created automatically on first use and is excluded from Git.

To display all available command-line options:

```bash
python3 vinted_scanner.py -h
```

### Telegram Hardware Enrichment and Ranking

This tailored version adds a local hardware analysis to each relevant Telegram alert. It shows a current-spec score weighted **CPU 40 points, estimated total price 25, RAM 10, storage 10, platform 15**, plus a separate upgrade-opportunity score when the model has a documented CPU candidate and its cost is configured. The message displays estimates for 16 GB and 32 GB RAM, with and without the CPU upgrade. Scores are informational and never suppress a matching listing.

The platform part comes from a local table of the supported models (`PLATFORM_PROFILES` in `hardware_enricher.py`): CPU generation, official maximum RAM, PCIe riser slot (ThinkCentre M720q/M920q), the highest CPU class the chassis ships with, and whether the chipset supports Intel vPro/AMT. Remote power control and console through AMT matter for a node without anyone on site, so a vPro chipset paired with a vPro CPU earns the most platform points; a vPro chipset with a non-vPro CPU (e.g. i5-8400T, i3) is reported as such. 6th/7th-gen platforms are still notified, with fewer platform points and a "legacy platform" warning, and are never labelled "Strong candidate". When the CPU model is not stated, the score uses a typical CPU for the platform and the tier named in the title (i3/i5/i7/Celeron), discounted and marked as an estimate. On 35 W-only chassis (Dell OptiPlex Micro, Lenovo 6th/7th-gen Tiny, HP ProDesk 400 Mini) a listed non-T CPU such as `i7 8700` is read as its T variant, with a note to verify.

By default, strict filtering accepts **HP EliteDesk/ProDesk Mini, Lenovo ThinkCentre Tiny, and Dell OptiPlex Micro** listings. Generic mini PCs, Geekom models, ordinary SFF desktops, and barebones are skipped. Spare parts, accessories and multi-unit lots are skipped from the title (e.g. `ricambi`, `pièces détachées`, `Lot de 5`); component words such as `antenna`, `ventola` or `staffa` exclude a title only when it names no CPU and no RAM size. One model-specific exception is Lenovo ThinkCentre M700: Lenovo's PSREF identifies it as a 1L Tiny chassis, so listings that call it `M700 USFF` are accepted when the M700 model and USFF/Tiny wording are both present. Filtered results are not written to the notified-items database. They are recorded in `vinted_filtered_items.txt` together with the filter-rule version, so they are not re-fetched on every run but are reconsidered automatically after a rule change. When a listing title does not even name a supported family (ThinkCentre, EliteDesk, ProDesk, OptiPlex), it is rejected from the title alone, without requesting its page.

The scanner also adds broad model discovery searches by default (ThinkCentre M700/M710/M720/M920/M70q/M90q and Tiny, EliteDesk Mini and 800 G4, ProDesk Mini, OptiPlex Micro and 7060). They are limited to the desktop-computer catalogue and to the `hardware_discovery_price_from`/`hardware_discovery_price_to` range (default €60–400). They are additive to the user's custom searches and can be disabled with `hardware_add_discovery_queries = False`. **Every new listing that passes the chassis filter is notified regardless of its score**; the score is informational only.

Default sample configuration:

```python
notification_channels = ["telegram"]
hardware_fetch_details = True
hardware_detail_fetch_delay_seconds = 1.0
hardware_require_target_models = True
hardware_ram_8gb_upgrade_eur = 50
hardware_ram_32gb_kit_eur = 130
hardware_add_discovery_queries = True
hardware_discovery_price_from = 60
hardware_discovery_price_to = 400
hardware_cpu_upgrade_costs_eur = {"i76700t": 68}
```

The RAM figures are editable assumptions, not live market prices. The €50 allowance assumes adding a compatible 8 GB module to an existing 8 GB setup; on a 4 GB base it is only a rough planning budget because the module layout may require replacement or a kit. The €130 figure assumes a 2×16 GB DDR4 kit. CPU upgrade candidates are shown only for model families present in the local reference matrix; the exact MTM, BIOS, power adapter and cooling must still be checked. CPU costs are editable in `hardware_cpu_upgrade_costs_eur`; the sample includes €68 for an i7-6700T based on one observed Vinted offer and is not a market average. Combined build estimates include listing price plus configured component budgets, **not Vinted buyer-protection fees or shipping unless already included in the chosen budget**. Missing or unclear RAM/CPU configuration stays marked for verification. The extra detail request is attempted only for newly detected items, uses the same browser-like headers as the initial marketplace request, waits `hardware_detail_fetch_delay_seconds` (default 1 s) before each page, and is non-fatal if Vinted does not return the page content. To skip it, set `hardware_fetch_details = False`.

The M700 Tiny platform reference lists Celeron/Pentium and 6th-gen Core CPUs through i7-6700T (4 cores / 8 threads, 32 GB maximum DDR4, two SO-DIMM slots). The M710 Tiny reference lists 6th/7th-gen Core CPUs through i7-7700T (4/8). The M720 Tiny reference lists 8th/9th-gen options including i7-8700T (6/12); the M920q PSREF has an i7-8700T configuration. These references support upgrade candidates, not blanket compatibility for every unit; BIOS and exact machine type still matter.

Telegram limits how fast a bot can post to one chat. The scanner waits `telegram_send_delay_seconds` (default 1 s) after each Telegram message and, if Telegram still answers with HTTP 429, waits the advertised `retry_after` (capped at 30 s) and retries once.

### Automation with Cron

Before scheduling the scanner, run `python3 vinted_scanner.py --dry-run` once. It records the listings currently online in `vinted_items.txt`, so the first scheduled run only notifies new listings instead of sending dozens of messages at once.

To run the script periodically, you can set up a cron job. For example, to run the script every hour:

1. Open the crontab editor:
   ```bash
   crontab -e
   ```

2. Add the following line to schedule the script to run every hour:
   ```bash
   0 * * * * /usr/bin/python3 /path/to/vinted_scanner.py >> /path/to/logfile.log 2>&1
   ```

This will run the script every hour and log the output to `logfile.log`.

### Logging

Logs are stored next to the script in `vinted_scanner.log`. The local item
database is also resolved relative to the script, so both paths remain stable
when the scanner is started by cron.

### Tests

Run the automated tests with:

```bash
python3 -m unittest discover -s tests -v
```

The GitHub Actions workflow runs the same test suite on every push and pull
request.

### Contributing and Supporting the Project

There are two ways you can contribute to the development of **Vinted Scanner**:

1. **Development Contributions**:

   Please ensure that your code follows best practices and includes relevant tests.

2. **Donation Support**:
   If you find this project useful and would like to support its development, you can also make a donation via [Buy Me a Coffee](https://buymeacoffee.com/andreadraghetti). Your support is greatly appreciated and helps to keep this project going!

   [![Buy Me a Coffee](https://img.shields.io/badge/-Buy%20Me%20a%20Coffee-orange?logo=buy-me-a-coffee&logoColor=white&style=flat-square)](https://buymeacoffee.com/andreadraghetti)

### License

This project is licensed under the [GNU General Public License v3.0](LICENSE).

## RAM Hunt mode

RAM Hunt runs alongside the normal mini-PC search and uses a separate local database,
`vinted_ram_hunt_items.txt`. It does **not** use the strict EliteDesk Mini /
ThinkCentre Tiny / OptiPlex Micro filter and it does not change normal mini-PC
notifications. RAM Hunt notifications are sent through the same configured channel
(the provided sample configuration selects Telegram only).

The sample configuration enables RAM Hunt with a €100 maximum **listing** price
(excluding shipping and buyer-protection fees). Its default searches include damaged/non-working
laptops and listings explicitly mentioning 16 GB DDR4 or 32 GB DDR4. You can disable the feature with `ram_hunt_enabled = False`,
change the ceiling with `ram_hunt_max_price_eur`, or customize the search dictionaries
in `ram_hunt_queries`. These searches are additional catalogue requests on each run.
Set `ram_hunt_fetch_details = True` only if you want an extra attempt to retrieve
the listing description when the catalogue response omits it; this adds page requests.

Alerts distinguish stronger claims (for example, 32 GB declared) from low-confidence
donors where RAM capacity is not listed. A listing is not proof that DDR4 SO-DIMM
modules are removable or working. Before buying, verify the exact laptop model, RAM
form factor/type, number and size of modules, whether they can be removed, and the
final cost including shipping and marketplace fees. A known 4/8 GB-only listing and
listings identified as DDR3 are filtered out. The separate database prevents duplicate
RAM Hunt alerts; ordinary mini-PC notifications keep using `vinted_items.txt`. As with the
existing scanner, `--dry-run` records analyzed IDs, so listings shown during a dry run will
not notify again on the next run.

When updating an existing installation, merge the RAM Hunt settings from
`Config.sample.py` into the existing `Config.py` rather than replacing credentials.
If those settings are absent, the code uses the built-in defaults; set
`ram_hunt_enabled = False` explicitly to turn the feature off.

## CPU Hunt mode

CPU Hunt is an optional, independent search for loose low-power CPUs that can turn a
cheap i3/Pentium mini PC into a useful node. It is disabled in the sample
configuration; enable it with `cpu_hunt_enabled = True`.

```python
cpu_hunt_enabled = True
cpu_hunt_targets = {
    "i78700t": 90, "i79700t": 110, "i59500t": 70,
    "i58500t": 60, "i77700t": 55, "i76700t": 50,
}
cpu_hunt_base_price_eur = 90
cpu_hunt_fetch_details = False
```

Each key is a CPU identifier from `CPU_CATALOG` in `hardware_enricher.py` and each
value is the maximum listing price in EUR. One search per target is generated (for
example `i7-8700T` with `price_to` set to its cap) unless `cpu_hunt_queries` is
provided. A listing is notified only when its title describes a loose CPU
(processore, CPU, LGA1151, socket, tray...), the exact target model is parsed (so
non-T, mobile H/U and other models are ignored), it is not a complete system or a
laptop, it is not an engineering/qualification sample, and the price is within the
cap.

The alert lists the supported chassis offered with that CPU generation and power
class, and a build estimate: `cpu_hunt_base_price_eur` (a typical i3/Pentium unit
of the same generation) + the CPU + the RAM-to-16 GB budget. 6th/7th-gen parts carry
a legacy warning: they only make sense for a unit you already own or can buy very
cheaply. Before buying, check that the exact model/MTM lists the CPU (9th-gen parts
need a recent BIOS on 8th/9th-gen models), the 35 W cooler and the socket. CPU Hunt
uses its own `vinted_cpu_hunt_items.txt` database.

### Upgrade references

- [Lenovo ThinkCentre M700 Tiny PSREF](https://psref.lenovo.com/syspool/Sys/PDF/ThinkCentre/ThinkCentre_M700_Tiny/ThinkCentre_M700_Tiny_Spec.PDF)
- [Lenovo ThinkCentre M710 Tiny PSREF](https://psref.lenovo.com/syspool/Sys/PDF/ThinkCentre/ThinkCentre_M710_Tiny/ThinkCentre_M710_Tiny_Spec.pdf)
- [Lenovo ThinkCentre M720 Tiny PSREF](https://psref.lenovo.com/syspool/Sys/PDF/ThinkCentre/ThinkCentre_M720_Tiny/ThinkCentre_M720_Tiny_Spec.PDF)
- [Lenovo ThinkCentre M920 Tiny PSREF configuration](https://psref.lenovo.com/Detail/ThinkCentre/ThinkCentre_M920_Tiny?M=10RS001PUS)
- [HP EliteDesk 800 G2 QuickSpecs](https://h30434.www3.hp.com/psg/attachments/psg/Business-PC-Workstation-POS/56278/1/HP%20EliteDesk%20800%20G2%20QuickSpecs%20v31.pdf)
