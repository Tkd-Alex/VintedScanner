# Telegram Hardware Enrichment

## What it does

VintedScanner still discovers listings and handles duplicate detection. Before sending a newly detected item to Telegram, `hardware_enricher.py` adds a local hardware summary to the **same Telegram message**:

- model and form factor when recognizable;
- CPU model, core/thread count and reference TDP when the exact model is in the local catalog;
- RAM and storage parsed from listing text;
- graphics identified in the listing or inferred from a recognized CPU's integrated GPU;
- an explainable 0–100 score weighted **CPU 50 / price 25 / RAM 10 / storage and expandability 15**;
- estimated total prices for 16 GB and 32 GB RAM upgrades;
- strict filtering for HP EliteDesk Mini, Lenovo ThinkCentre Tiny, and Dell OptiPlex Micro listings;
- a concise note about AI acceleration and details that should be verified with the seller.

Only Telegram is enabled by default through `notification_channels = ["telegram"]`. E-mail and Slack values are left empty in `Config.sample.py` and are not sent while they are not selected.

## Data and limitations

This module is deterministic and local. It does not call an external AI service. CPU specifications come from the small editable `CPU_CATALOG` in `hardware_enricher.py`; extracted RAM, storage, form factor and listing description come from the item data/page. These are not a hardware self-test, and seller claims are not independently confirmed.

If the catalogue result does not include a description and `hardware_fetch_details = True`, the module makes at most one normal page request for the newly detected listing, with a 15-second timeout. It does not rotate proxies or attempt to bypass access controls. If page retrieval fails, the notification still goes out with the data already available and marks missing fields for verification. Set `hardware_fetch_details = False` to disable these extra page requests.

The score estimates suitability for a small Proxmox node; CPU model tiers are curated relative estimates, not fresh benchmark results. Price points are based on the listing price plus the estimated cost to reach 16 GB RAM. The displayed totals include listing price and RAM estimate, but exclude Vinted buyer-protection fees and shipping. The default RAM assumptions are €50 for adding a compatible 8 GB module to an existing 8 GB setup, and €130 for a 2×16 GB DDR4 kit; edit them in `Config.py` when prices change. Unknown RAM layout is not treated as guaranteed compatible.

By default, only HP EliteDesk Mini, Lenovo ThinkCentre Tiny and Dell OptiPlex Micro listings pass the hard target filter. Other models—including generic mini PCs, Geekom models, SFF/USFF systems and barebones—are ignored. Ignored IDs are persisted in `vinted_items.txt`, so those results are not repeatedly revisited on each scan. Set `hardware_require_target_models = False` only to disable this behavior.

## Installation

1. Make a backup of the existing `vinted_scanner.py` and `Config.py`.
2. From the repository root, apply `patches/vinted_scanner_hardware.patch` to the exact VintedScanner source snapshot included in the supplied ZIP. The patch adds `hardware_enricher.py` and the tests, so do not copy that file separately when applying the full patch.
3. Alternatively, if applying changes manually, copy `hardware_enricher.py` into the same directory as `vinted_scanner.py` and install the updated scanner code from this package.
4. In the existing `Config.py`, add these settings if they are not already present:

   ```python
   notification_channels = ["telegram"]
   hardware_fetch_details = True
   hardware_require_target_models = True
   hardware_ram_8gb_upgrade_eur = 50
   hardware_ram_32gb_kit_eur = 130
   ```

5. Keep your existing `queries`, `telegram_bot_token`, and `telegram_chat_id`. Do not replace your personal `Config.py` with the sample file unless you intend to recreate all settings manually.
6. The only runtime dependency remains `requests`, already listed in `requirements.txt`.

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

**Note:** the scanner's existing dry-run behavior also saves observed item IDs. Review its output before running it against a live configuration if you do not want those currently found listings suppressed on the next normal scan.
