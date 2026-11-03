# Telegram Hardware Enrichment

## What it does

VintedScanner still discovers listings and handles duplicate detection. Before sending a newly detected item to Telegram, `hardware_enricher.py` adds a local hardware summary to the **same Telegram message**:

- model and form factor when recognizable;
- CPU model, core/thread count and reference TDP when the exact model is in the local catalog;
- RAM and storage parsed from listing text;
- graphics identified in the listing or inferred from a recognized CPU's integrated GPU;
- an explainable 0–100 score weighted **CPU 50 / price 25 / RAM 10 / storage and expandability 15**;
- estimated total prices for 16 GB and 32 GB RAM upgrades, with optional CPU replacement budgets;
- documented CPU upgrade candidates for selected Lenovo M700/M710/M720/M920 and HP EliteDesk 800 G2 platforms;
- strict filtering for supported HP EliteDesk/ProDesk Mini, Lenovo ThinkCentre Tiny, and Dell OptiPlex Micro listings;
- a concise note about AI acceleration and details that should be verified with the seller.

Only Telegram is enabled by default through `notification_channels = ["telegram"]`. E-mail and Slack values are left empty in `Config.sample.py` and are not sent while they are not selected.

## Data and limitations

This module is deterministic and local. It does not call an external AI service. CPU specifications come from the small editable `CPU_CATALOG` in `hardware_enricher.py`; extracted RAM, storage, form factor and listing description come from the item data/page. These are not a hardware self-test, and seller claims are not independently confirmed.

If the catalogue result does not include a description and `hardware_fetch_details = True`, the scanner makes at most one normal page request for the newly detected listing, with the same browser-like headers used to open the marketplace, a 15-second timeout and a configurable pause before each request (`hardware_detail_fetch_delay_seconds`, default 1 s). The seller text is read from the page's description metadata; the listing-title prefix Vinted adds there is removed. It does not rotate proxies or attempt to bypass access controls. If page retrieval fails, the notification still goes out with the data already available and marks missing fields for verification. Set `hardware_fetch_details = False` to disable these extra page requests.

The current-spec score estimates suitability for a small Proxmox node; CPU model tiers are curated relative estimates, not fresh benchmark results. An upgrade-opportunity score is calculated only when the platform has a local reference profile and the CPU cost is configured. The M700/M710 CPU candidates remain 4-core parts; M720/M920 references include 6-core options. The exact machine type, BIOS, PSU and cooling still need checking. Price estimates include listing price plus the configured component budgets, but exclude buyer-protection fees and shipping unless those were included in a specific configured budget. The default RAM assumptions are €50 for the 8 → 16 GB plan and €130 for a 2×16 GB kit; on a 4 GB base, €50 is only a rough allowance because the module arrangement may require replacement or a kit.

By default, the target filter accepts HP EliteDesk/ProDesk Mini, Lenovo ThinkCentre Tiny and Dell OptiPlex Micro. The actual Lenovo M700 Tiny is accepted when its listing says M700 plus Tiny/USFF wording; ordinary SFF/USFF desktops and generic mini-PC brands are not accepted. Titles that describe spare parts, accessories or multi-unit lots are rejected; component words (antenna, fan, bracket, power adapter) reject a title only when it names no CPU and no RAM size, because descriptions of complete systems often mention them. Filtered results are not saved to `vinted_items.txt`; only successfully notified items are persisted there. Filtered IDs go to `vinted_filtered_items.txt`, tagged with `FILTER_RULES_VERSION`, so they are skipped on later runs until the rules change. Listings whose title does not name a supported family are rejected without a detail-page request. Broad model discovery searches, restricted to the desktop-computer catalogue and to a configurable price range (`hardware_discovery_price_from`/`hardware_discovery_price_to`, default €60–400), are appended by default and can be disabled with `hardware_add_discovery_queries = False`. A listing that passes the chassis filter is notified regardless of its score.

## Installation

1. Back up the existing `vinted_scanner.py`, `hardware_enricher.py` (if present), `Config.py`, and the local item databases.
2. Copy the updated `vinted_scanner.py` and `hardware_enricher.py` from this package into your existing scanner directory. Keep `vinted_query_builder.py` and the rest of your installation unless you are intentionally upgrading the full project snapshot.
3. Merge these settings into your existing `Config.py` if they are not already present:

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

4. Keep your existing `queries`, `telegram_bot_token`, and `telegram_chat_id`. Do not replace your personal `Config.py` with the sample file unless you intend to recreate all settings manually.
5. The only runtime dependency remains `requests`, already listed in `requirements.txt`.

## Test

Run from the VintedScanner directory after creating `Config.py` from `Config.sample.py` or using your own valid local configuration:

```bash
python -m unittest discover -s tests -v
python -m py_compile vinted_scanner.py hardware_enricher.py vinted_query_builder.py
```

To preview output without sending a Telegram message:

```bash
python vinted_scanner.py --dry-run
```

**Note:** dry-run still saves recognized target listing IDs to the notified-items database. Use it to inspect output, then clear only the IDs you deliberately want to be notified about again. Older versions also stored filtered false-positive IDs in `vinted_items.txt`; those historical IDs cannot be distinguished from successfully notified IDs. Back up the file before resetting it for a one-time recheck, because that can cause duplicate alerts for recent listings.
