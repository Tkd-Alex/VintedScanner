# Telegram Hardware Enrichment

## What it does

VintedScanner still discovers listings and handles duplicate detection. Before sending a newly detected item to Telegram, `hardware_enricher.py` adds a local hardware summary to the **same Telegram message**:

- model and form factor when recognizable;
- CPU model, core/thread count and reference TDP when the exact model is in the local catalog;
- RAM and storage parsed from listing text;
- graphics identified in the listing or inferred from a recognized CPU's integrated GPU;
- a transparent 0–100 Proxmox suitability heuristic;
- a concise note about AI acceleration and details that should be verified with the seller.

Only Telegram is enabled by default through `notification_channels = ["telegram"]`. E-mail and Slack values are left empty in `Config.sample.py` and are not sent while they are not selected.

## Data and limitations

This module is deterministic and local. It does not call an external AI service. CPU specifications come from the small editable `CPU_CATALOG` in `hardware_enricher.py`; extracted RAM, storage, form factor and listing description come from the item data/page. These are not a hardware self-test, and seller claims are not independently confirmed.

If the catalogue result does not include a description and `hardware_fetch_details = True`, the module makes at most one normal page request for the newly detected listing, with a 15-second timeout. It does not rotate proxies or attempt to bypass access controls. If page retrieval fails, the notification still goes out with the data already available and marks missing fields for verification. Set `hardware_fetch_details = False` to disable these extra page requests.

The score estimates hardware fit for a small Proxmox node; it is not a market-price appraisal, a benchmark, or an AI performance guarantee. Storage is not treated as RAM, and unknown CPU models are not assigned invented core counts.

## Installation

1. Make a backup of the existing `vinted_scanner.py` and `Config.py`.
2. From the repository root, apply `patches/vinted_scanner_hardware.patch` to the exact VintedScanner source snapshot included in the supplied ZIP. The patch adds `hardware_enricher.py` and the tests, so do not copy that file separately when applying the full patch.
3. Alternatively, if applying changes manually, copy `hardware_enricher.py` into the same directory as `vinted_scanner.py` and install the updated scanner code from this package.
4. In the existing `Config.py`, add these settings if they are not already present:

   ```python
   notification_channels = ["telegram"]
   hardware_fetch_details = True
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
