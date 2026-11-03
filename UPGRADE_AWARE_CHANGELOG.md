# Upgrade-Aware Hardware Search

## Why this revision exists

The scanner now treats an inexpensive, upgradeable business mini-PC as a legitimate opportunity even when its shipped CPU or RAM is below the preferred target. A weak starting configuration is no longer enough to make a listing unattractive by itself.

## What changed

- Added local CPU profiles for common entry-level Intel Celeron/Pentium processors, so low-cost M700/M710/M720 listings can be parsed instead of being reported as unknown CPUs.
- Added reference CPU upgrade candidates for Lenovo ThinkCentre M700, M710, M720 and M920 families, plus HP EliteDesk 800 G2 Desktop Mini.
- Added an estimated build cost for the listing plus the configured RAM/CPU upgrade budgets.
- Added a separate upgrade-opportunity score when both a documented platform candidate and a configured CPU budget are available.
- Kept the current-spec score separate from the upgrade-opportunity score. Neither score suppresses a notification.
- Added default discovery searches for ThinkCentre M700/M710/M720/M920, ThinkCentre Tiny, EliteDesk/ProDesk Mini and OptiPlex Micro. These searches are additive to custom queries.
- Kept the strict chassis-family rule. Generic mini-PCs, regular desktop/SFF systems and unconfirmed form factors remain outside the configured target.
- Filtered-out items are no longer written to the notified-items database. Only successfully delivered (or deliberately dry-run processed) target items are recorded.
- Kept RAM Hunt as an optional, Vinted-only mode with a separate deduplication file.

## Upgrade references and caveats

The upgrade candidate shown in a notification is a manufacturer-reference lead, not a guarantee for every individual unit. Exact machine type/model (MTM), BIOS revision, board, power adapter and cooling must be checked before buying a replacement CPU.

The M700 and M710 references top out at 4 cores / 8 threads. The M720 and M920 reference matrix includes six-core i7-8700T configurations. The candidate is deliberately conservative and is not necessarily the highest CPU SKU listed for every machine family.

CPU upgrade costs are not guessed. The sample configuration includes only `i76700t: 68`, based on one Vinted listing observed in November 2026 with protection and shipping included in that observed budget. Add other CPU keys only after checking a real listing and the exact platform compatibility.

RAM assumptions are editable: €50 for the planned 8-to-16 GB step and €130 for a 2×16 GB kit. For a 4 GB starting configuration, the €50 estimate is only a rough allowance; module layout, number of slots and compatibility need checking.

## Upgrade an existing installation

1. Back up `Config.py`, `vinted_items.txt`, `vinted_ram_hunt_items.txt` and the current scripts.
2. Replace `vinted_scanner.py` and `hardware_enricher.py` with the versions in this package. Keep `vinted_query_builder.py` and `requirements.txt` unless intentionally upgrading those separately.
3. Merge the new `hardware_*` settings from `Config.sample.py` into your existing `Config.py`. Preserve your Telegram token/chat ID, custom `queries`, other notification settings and RAM Hunt preferences.
4. Keep the existing item databases to avoid duplicate notifications. Older releases may have stored filtered false-positive IDs in `vinted_items.txt`; those historical entries cannot be distinguished reliably from successfully notified items. Resetting that file can re-trigger alerts, so do it only after taking a backup and accepting possible duplicates.
5. Run the test suite and a `--dry-run` before enabling the scheduled run. A dry-run records eligible item IDs, so do not use it on a live database when you intend to receive those same listings later.

## Verification performed for this package

- Python compilation check passed for the scanner, hardware enricher and query builder.
- Local unit tests passed. These tests mock external requests; they do not guarantee that Vinted's live catalogue endpoint will remain unchanged or return every matching listing.
