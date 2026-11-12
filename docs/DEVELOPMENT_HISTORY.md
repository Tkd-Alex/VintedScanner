# VintedScanner — Development History, Hardware Enricher & Agent Handoff

**Document purpose:** give a coding agent enough context to understand why this project was selected, how the hardware-analysis layer evolved, which product-search decisions were made, what the current code is intended to do, and which limitations must not be mistaken for completed features.

**Current reference state:** the Upgrade-Aware revision (commit `bfbddc7`, 2 November 2026).

**Important:** this document combines (1) facts verified in the archived source snapshots, (2) decisions explicitly made in the project conversation, and (3) caveats inferred from the current code. Where a behavior is only a design intention or has not been tested against live Vinted, it is labelled accordingly.

---

## 1. Executive summary

VintedScanner started as a small Python Vinted search-and-notification script. It was selected as the base because it was substantially simpler to operate than Vintrack, had a newer catalogue endpoint than the older Vinted-Notifications project, had explicit request timeouts, a query builder, and a working local test suite.

It was then adapted into a personal second-hand business-mini-PC finder for a second Proxmox node. The key addition was `hardware_enricher.py`, a deterministic local module that parses listing text and adds hardware details to Telegram alerts. It does not call an external AI model.

The hardware ranking evolved from a generic hardware score to a CPU-first score, then to an upgrade-aware model that evaluates both the advertised configuration and a possible upgraded configuration. The central product decision is now:

> **Do not reject an otherwise eligible listing just because its starting CPU or RAM is weak. Estimate what it could become after realistic upgrades, and show the cost. The score is information, not a notification gate.**

There is still a hard chassis-family filter by default. The scanner is not intended to alert on every random mini-PC: it targets HP EliteDesk/ProDesk Mini, Lenovo ThinkCentre Tiny, and Dell OptiPlex Micro. Every newly detected item that passes that target filter is notified regardless of its score.

The latest version also includes an optional, separate `RAM Hunt` mode for finding cheap laptops on Vinted that may be useful as DDR4 SO-DIMM donors. It uses its own search queries and deduplication file. It does not integrate eBay or Subito.

---

## 2. The actual use case and buying philosophy

### 2.1 What the hardware search is for

The target purchase is a compact business mini-PC to act as a second Proxmox node. The machine is expected to host a work VM (the important workload) and additional containers/services. The existing Rome node is a BMAX B4 Plus with an Intel N100, 16 GB RAM and a 512 GB SSD; the second machine should provide a meaningful CPU/RAM step for the VM workload.

### 2.2 Preferred physical format

The preferred chassis families are deliberately narrow:

- HP EliteDesk / ProDesk **Mini** / Desktop Mini.
- Lenovo ThinkCentre **Tiny**.
- Dell OptiPlex **Micro**.

Generic mini-PC brands, normal SFF/tower desktops, barebones and unrelated form factors are not part of the normal search target. Geekom was considered during the hardware search but was not retained in the target whitelist.

There is one model-specific exception: Lenovo ThinkCentre M700 listings sometimes say `USFF` instead of `Tiny`. The current code accepts an M700 listing when the model name is present and nearby text also explicitly says `USFF`, `Ultra Small Form Factor`, or `Tiny`. This exception is not meant to accept arbitrary USFF desktops.

### 2.3 Performance target versus upgradeable bargain

The initial preference was CPU-first, with six physical cores or better being the desirable target for the second Proxmox node. The user later clarified that this must not be treated as a rigid minimum for the *advertised* configuration:

- An €80–€100 machine with a Celeron/Pentium and little RAM may be worth buying if the platform can take a significantly better CPU and more RAM.
- A more expensive machine that is already configured well is not automatically the better deal.
- The correct comparison is the **total build cost after plausible upgrades**, not just the listing price or current specs.
- However, CPU upgradeability alone does not make a machine a great buy. For example, M700/M710 upgrades remain 4-core/8-thread platforms; M720/M920 can reach six-core/12-thread i7-8700T options in the reference matrix.

This is why the project has two separate views: current-spec suitability and upgrade-opportunity suitability.

### 2.4 Marketplace scope

The scanner must search **Vinted only**. No eBay or Subito search integration is wanted. A component price observed on another listing/site may be entered manually as a planning budget, but that is not an integration and must not create additional marketplace searches.

---

## 3. Chronological development history

Dates below are based on the archived upload/build timestamps and the conversation chronology. The iterations were originally produced as full source snapshots and were later committed one per phase, in creation order.

### Phase 0 — Original source selection and code review (27 October 2026)

The candidates were the upstream VintedScanner project, the Vinted-Notifications project, the Vintrack Vinted monitor, and two small personal prototype scripts (a Telegram notifier and an article fetcher). A static comparison was performed before modifying anything.

**Why VintedScanner was selected**

- It was a focused Python script rather than a multi-service application.
- It used the newer Vinted catalogue route `/svc-catalogue/items` rather than the legacy `/api/v2/catalog/items` route used by Vinted-Notifications and the personal prototype.
- It initialized an anonymous session, used explicit request timeouts, checked the response shape, and had notification failure handling.
- It already had a query builder and a local unit-test suite.
- The first review ran its tests successfully: **34 tests passed**. These were local/mock tests, not live Vinted API tests.

**Why the alternatives were not selected**

- **Vintrack:** much richer and more elaborate (Go worker, Next.js dashboard, PostgreSQL, Redis, Caddy, scheduler and other services), but too operationally heavy for a personal Vinted notifier. It also includes broader account/action functionality that is not needed for this use case.
- **Vinted-Notifications:** used the old `/api/v2/catalog/items` endpoint and old query schema, lacked explicit request timeouts, and had notification/deduplication ordering risks.
- **Personal Telegram notifier prototype:** useful, but used the legacy endpoint, had only 10 results per query, volatile in-memory deduplication, no request timeouts, and did not safely escape Telegram HTML.
- **Personal article-fetcher prototype:** not integrated; its `/vinted/{item_id}` handler contained an unconditional `exit(1)` before the intended response logic.

**Baseline concerns identified and not all solved yet**

- One page is requested per configured query; the scanner does not automatically paginate/backfill every result page.
- The original state store is a plain text ID file, not SQLite.
- Scheduled execution is external (for example cron); the scanner is not a continuously running poller.
- No proven live-Vinted compatibility test was performed.
- The initial review recommended later improvements such as retry/backoff for 429/403, overlap protection, heartbeat/health alerting, richer counters and possibly SQLite. These should still be treated as open work unless separately implemented.

### Phase 1 — First hardware-enrichment integration (28 October 2026, morning)

A standalone `hardware_enricher.py` was introduced and integrated into VintedScanner. The standalone module was committed first (`72dd8c3`), then integrated into the scanner (`8fb151a`).

**What was added**

- `vinted_scanner.py` imports `hardware_enricher`.
- Normalized catalogue items now retain the listing description when available.
- For a newly found listing, the scanner calls `hardware_enricher.enrich_item(...)` before building the Telegram message.
- `hardware_enricher.build_telegram_lines(...)` adds a hardware section to the same Telegram alert.
  (Since phase 7 it returns Telegram HTML lines, and `build_telegram_headline(...)` returns the decision line placed above the title; see section 13.)
- The same enrichment is printed in `--dry-run` output.
- A configurable `hardware_fetch_details` switch allows one normal listing-page fetch when the catalogue response does not include a useful description.
- The page-fetch helper uses Python's standard HTML parser / JSON traversal rather than adding BeautifulSoup as a dependency. It has a 15-second timeout and the fetch failure is non-fatal: missing data stays missing and the alert can still be sent.

**What the first hardware parser extracted**

- CPU model, cores, threads, reference TDP and integrated GPU when the exact CPU exists in the local `CPU_CATALOG`.
- RAM capacity from listing title/description.
- Storage capacity/type, including multiple drives when described.
- Model and form-factor clues.
- Explicit graphics-card claims and a simple note about AI acceleration.
- A short list of fields that need seller verification.

**Important architecture decision**

The hardware-enrichment text was added to **Telegram**, while email and Slack retained their standard notification body. `notification_channels` was introduced so the active delivery channels could be selected explicitly. The supplied configuration was changed to Telegram-only by default:

```python
notification_channels = ["telegram"]
```

Email and Slack remain in the code, but are not selected in the sample configuration.

**Initial score model**

The first enricher used a general-purpose 0–100 score based on approximate CPU core count, RAM, storage, form factor and GPU. It was later replaced because the user wanted CPU capability to dominate, and did not want RAM/GPU/form-factor points to make a weak CPU look artificially attractive.

### Phase 2 — CPU-first ranking, target chassis filter and upgrade-cost estimates (28 October 2026, afternoon)

This phase was never committed on its own; it is included, together with Phase 3, in commit `42667da`.

**Score rebalanced**

The current-spec score was changed to four explicit parts:

| Dimension | Weight |
|---|---:|
| CPU | 50 points |
| Price / effective build cost | 25 points |
| Installed RAM | 10 points |
| Storage and expandability | 15 points |
| **Total** | **100 points** |

CPU weights are represented by a curated `CPU_PERFORMANCE_POINTS` table, not live benchmark data. This makes CPU generation/tier and the exact SKU suffix more important than merely counting cores.

The later implementation also distinguishes T and non-T processors. For example, an i7-6700 and an i7-6700T are not interchangeable catalogue entries: the suffix is meaningful for power/thermal compatibility.

**Target chassis filter introduced**

`hardware_require_target_models = True` became the default. The intent was to avoid noise from generic mini-PCs, Geekom, normal SFF/USFF desktops and barebones. The whitelist is the HP Mini / Lenovo Tiny / Dell Micro set described above, including the narrow M700 USFF exception.

At this stage, filtered-out item IDs were written to `vinted_items.txt` to prevent the same fuzzy-search false positives from being re-evaluated on every scan. This behavior changed in the latest Upgrade-Aware phase; see Phase 5.

**RAM upgrade estimates introduced**

Two editable reference amounts were added:

```python
hardware_ram_8gb_upgrade_eur = 50
hardware_ram_32gb_kit_eur = 130
```

These are user planning assumptions, not guaranteed market prices:

- €50 is intended as a rough allowance for an 8 GB → 16 GB step, usually adding a compatible 8 GB module to an existing 8 GB configuration.
- €130 is the allowance for a 2×16 GB DDR4 kit to reach 32 GB.
- For a 4 GB base, €50 is only a rough placeholder. Depending on slot count and module layout, the buyer may need to replace modules or buy a different kit.
- The code does not inspect the physical machine and cannot prove that a listing's claimed RAM is installed, removable or working.

**Notification delivery configuration**

The code now supports selecting channels through `notification_channels`, with Telegram as the default in the tailored configuration. Existing `Config.py` files should be merged carefully; never replace the live file with `Config.sample.py` because it contains private Telegram credentials and custom searches.

### Phase 3 — RAM Hunt as a separate Vinted-only mode (28 October 2026, evening)

This phase (commit `42667da`, together with Phase 2) added a second search purpose: finding cheap laptop listings that could be DDR4 SO-DIMM donors for RAM upgrades.

**Why it was added**

The user was considering the possibility of recovering memory from inexpensive or damaged laptops instead of always buying new RAM modules. This is separate from the normal Mini/Tiny/Micro computer search.

**Behavior**

- RAM Hunt uses the same Vinted catalogue endpoint; it does not integrate eBay or Subito.
- It runs independently from the normal mini-PC chassis filter.
- Its default maximum listing price is €100, excluding shipping and buyer-protection fees.
- Default searches include terms such as `portatile guasto`, `notebook guasto`, `laptop guasto`, `portatile non funzionante`, and listings advertising 16 GB / 32 GB DDR4.
- It parses claims for 16 GB, 32 GB, 2×16 GB, DDR4 and DDR3.
- Explicit DDR3-only claims are rejected; listings explicitly known to have only 4/8 GB are rejected.
- Higher priority is given to listings claiming 32 GB / 2×16 GB; lower-confidence damaged-laptop candidates may still be surfaced when the RAM is unspecified.
- Alerts explicitly warn that the listing description is not proof that the RAM exists, is removable, is DDR4 SO-DIMM, or works.
- It has a separate deduplication database: `vinted_ram_hunt_items.txt`. Normal mini-PC notifications continue to use `vinted_items.txt`.
- `ram_hunt_fetch_details` is False in the sample configuration to avoid unnecessary extra page requests; it can be enabled.

**Current default caveat**

The latest `Config.sample.py` sets `ram_hunt_enabled = True`. If the live user configuration has no explicit value, the code's default is also True. An agent must not assume RAM Hunt is disabled. To switch it off, set:

```python
ram_hunt_enabled = False
```

The user explicitly decided to keep marketplace searching Vinted-only. RAM Hunt is consistent with that constraint; it is not permission to add other marketplaces.

### Phase 4 — Clarification: scores must not suppress matching listings (late October 2026)

The user clarified that they want a notification for every new listing matching the configured search and accepted target format, not just listings with a high score.

This changed the meaning of the ranking:

- A low current-spec score is not a reason to suppress the alert.
- A Celeron/Pentium or low-RAM starting configuration can still be a bargain if the platform can be upgraded cheaply.
- The score is descriptive guidance for the user, not a hard threshold.
- The target chassis filter remains a separate rule. “Every listing” means every newly detected listing that passes the configured chassis-family filter, not literally every item returned by Vinted.

This principle is preserved in the Upgrade-Aware snapshot and is a core requirement for future changes.

### Phase 5 — Upgrade-Aware revision (2 November 2026)

The latest revision is commit `bfbddc7`. It builds on the RAM Hunt branch and makes platform upgradeability a first-class part of hardware analysis.

**A. Entry-level CPU recognition expanded**

The local CPU catalogue gained common Celeron and Pentium models so low-cost M700/M710/M720 listings can be recognized instead of being left as unknown CPUs. The catalog contains reference core/thread/TDP/GPU data for selected Intel Core, Intel Celeron/Pentium, Intel N100 and AMD Ryzen PRO/mobile CPUs.

Examples of entry-level keys include G3900T, G3930T, G4400T/G4500T, G4560T/G4600T and later Pentium Gold/Celeron T models. The catalogue also includes a broad range of i5/i7 T and non-T SKUs and selected AMD Ryzen PRO / 6600H / 6800H processors.

**Known parser gap:** the latest catalog does not yet contain a set of i3 SKUs. The regex can notice an i3-like token, but without a catalog entry it will not produce a trusted CPU specification. This matters for listings such as an M710q described only as “Core i3”; the exact model should be added deliberately after checking which SKUs are relevant.

**B. Platform upgrade reference matrix added**

The latest local matrix maps selected chassis families to a conservative reference CPU candidate:

| Platform family | Reference CPU candidate | Reference core/thread count | Important nuance |
|---|---|---:|---|
| Lenovo ThinkCentre M700 Tiny | i7-6700T | 4 / 8 | Does not reach the six-physical-core preference. |
| Lenovo ThinkCentre M710 Tiny | i7-7700T | 4 / 8 | Does not reach the six-physical-core preference. |
| Lenovo ThinkCentre M720(q) Tiny | i7-8700T | 6 / 12 | Relevant six-core upgrade path. |
| Lenovo ThinkCentre M920(q) Tiny | i7-8700T | 6 / 12 | Relevant six-core upgrade path. |
| HP EliteDesk 800 G2 Desktop Mini | i7-6700T | 4 / 8 | HP QuickSpecs reference the 35 W Desktop Mini CPU family. |

The source references are embedded in `hardware_enricher.py` and `README.md`:

- Lenovo ThinkCentre M700 Tiny PSREF: https://psref.lenovo.com/syspool/Sys/PDF/ThinkCentre/ThinkCentre_M700_Tiny/ThinkCentre_M700_Tiny_Spec.PDF
- Lenovo ThinkCentre M710 Tiny PSREF: https://psref.lenovo.com/syspool/Sys/PDF/ThinkCentre/ThinkCentre_M710_Tiny/ThinkCentre_M710_Tiny_Spec.pdf
- Lenovo ThinkCentre M720 Tiny PSREF: https://psref.lenovo.com/syspool/Sys/PDF/ThinkCentre/ThinkCentre_M720_Tiny/ThinkCentre_M720_Tiny_Spec.PDF
- Lenovo ThinkCentre M920 Tiny configuration: https://psref.lenovo.com/Detail/ThinkCentre/ThinkCentre_M920_Tiny?M=10RS001PUS
- HP EliteDesk 800 G2 QuickSpecs: https://h30434.www3.hp.com/psg/attachments/psg/Business-PC-Workstation-POS/56278/1/HP%20EliteDesk%20800%20G2%20QuickSpecs%20v31.pdf

**These are model-family leads, not a universal compatibility promise.** Before buying a replacement CPU, verify the exact machine type/model (MTM/Product Number), motherboard/BIOS, PSU wattage, cooling and CPU TDP. Similar sockets do not prove platform support.

**C. CPU and RAM upgrade cost model**

The scanner can now show:

- the reference upgrade CPU for a recognized platform;
- candidate core/thread count and TDP;
- whether the currently parsed CPU is below the listed candidate;
- the CPU budget if the corresponding canonical CPU key has a value in `hardware_cpu_upgrade_costs_eur`;
- estimated totals to 16 GB and 32 GB RAM;
- combined build estimates including both RAM and CPU budgets;
- a separate upgrade-opportunity score when the necessary platform/cost facts exist.

The sample configuration currently contains only:

```python
hardware_cpu_upgrade_costs_eur = {
    "i76700t": 68,
}
```

The €68 is a single observed planning amount for an i7-6700T offer, not a market average. It must not be copied to other CPU models by assumption. For an M710/M720/M920 listing, if the matching CPU price is not configured, the program should display that the price is unknown / score pending rather than inventing a number.

The displayed build totals generally add the listing price and configured component budgets. Shipping and buyer-protection fees are excluded unless they have explicitly been incorporated into a configured budget. The user should therefore compare the notification estimate with the true final checkout price.

**D. Current-spec score versus upgrade-opportunity score**

- `score` / current-spec score describes the listed configuration.
- `upgrade_score` describes a hypothetical configuration using the platform's reference CPU and the modeled RAM-to-16-GB step, when sufficient configuration data and costs exist.
- The two scores must not be conflated.
- Neither score blocks or delays a notification.

**E. New discovery searches are additive**

When `hardware_add_discovery_queries = True`, default broad searches are appended for:

- ThinkCentre M700
- ThinkCentre M710
- ThinkCentre M720
- ThinkCentre M920
- ThinkCentre Tiny
- EliteDesk Mini
- ProDesk Mini
- OptiPlex Micro

They use `newest_first` and 24 results per query. These searches are additive to the user's configured `queries`; they do not replace custom queries. Set `hardware_add_discovery_queries = False` to disable only the automatic discovery terms.

The query deduplication used when appending defaults compares normalized `(search_text, order)` pairs. It does not perform semantic search-equivalence analysis.

**F. Filtered listings are no longer written to the main deduplication ledger**

An important correction was made in `process_item()`:

- A listing outside the target chassis whitelist is skipped.
- Its ID is no longer written to `vinted_items.txt`.
- This allows a future scanner version with broader model recognition/filter rules to reconsider an older listing instead of permanently treating it as processed.

This differs from the CPU-Priority and RAM-Hunt snapshots, where filtered-out main-search results were stored in the ledger to avoid repeated evaluation. The latest behavior deliberately favors re-evaluation after rule changes.

Eligible listings are still written to the main ledger only after notification delivery succeeds (or after a deliberate dry-run, which is treated as successful for deduplication purposes).

---

## 4. Current architecture and responsibilities

### `vinted_scanner.py`

Responsible for:

- building the country-specific Vinted catalogue URL (`https://api.<marketplace>/svc-catalogue/items`);
- initializing an anonymous Vinted web session;
- validating the new-style query schema and rejecting legacy query keys;
- issuing catalogue requests with a 30-second timeout and validating that JSON has an `items` collection;
- normalizing catalogue items (ID, title, price, URL, image, description);
- deduplication and persistence;
- optionally fetching/enriching newly detected items;
- applying the strict target chassis rule;
- dispatching standard notifications and hardware-enriched Telegram details;
- running the optional RAM Hunt loop.

### `hardware_enricher.py`

Responsible for deterministic, local hardware interpretation:

- CPU-name normalization and lookup in `CPU_CATALOG`;
- CPU performance points in `CPU_PERFORMANCE_POINTS`;
- RAM/storage/model/form-factor extraction from text;
- inferred or explicitly listed GPU information;
- target chassis recognition;
- platform upgrade profile lookup;
- RAM and CPU upgrade price estimates;
- current-spec and upgrade-opportunity scores;
- AI acceleration note;
- human-readable Telegram hardware lines.

It does not call a hosted LLM, a benchmark API, a parts marketplace, eBay or Subito.

### `vinted_query_builder.py`

This was retained from the original VintedScanner and is unchanged in the archived revisions compared here. It discovers localized categories and contextual filters from Vinted, caches catalogue data locally for 24 hours, and helps create query dictionaries. It does not automatically edit `Config.py`.

### Configuration and runtime state

- `Config.sample.py`: safe template; current sample selects Telegram and defines hardware/upgrade/RAM Hunt settings.
- `Config.py`: user-local configuration containing Telegram credentials and personal query choices. It is intentionally not committed. Never overwrite it blindly.
- `vinted_items.txt`: main mini-PC notified/processed-ID ledger.
- `vinted_ram_hunt_items.txt`: separate RAM Hunt ledger.
- `vinted_scanner.log`: rotating log file next to the script.

The only runtime dependency listed in `requirements.txt` is `requests`; the hardware parser uses the Python standard library.

---

## 5. Current configuration reference

The latest sample configuration includes the following important values:

```python
notification_channels = ["telegram"]

hardware_fetch_details = True
hardware_require_target_models = True
hardware_add_discovery_queries = True

hardware_ram_8gb_upgrade_eur = 50
hardware_ram_32gb_kit_eur = 130
hardware_cpu_upgrade_costs_eur = {"i76700t": 68}

queries = []

ram_hunt_enabled = True
ram_hunt_max_price_eur = 100
ram_hunt_fetch_details = False
```

The sample also contains default `ram_hunt_queries` (Vinted searches only). The actual live `Config.py` may differ and must be inspected before any edit or deployment.

### Migration rule

When installing a new version over an existing installation:

1. Back up `Config.py`, `vinted_items.txt`, `vinted_ram_hunt_items.txt`, scripts and logs.
2. Replace only the code files intentionally being upgraded (`vinted_scanner.py`, `hardware_enricher.py`, and any other file explicitly changed).
3. Keep the existing `vinted_query_builder.py` and `requirements.txt` if they have not changed.
4. Merge new `hardware_*` and RAM Hunt settings into the existing `Config.py` manually.
5. Preserve `telegram_bot_token`, `telegram_chat_id`, custom queries, channel settings and personal preferences.
6. Keep the item databases unless the user consciously accepts duplicate notifications. A dry-run records eligible IDs, so do not run it against the live ledger if the intention is to receive those same listings on the next normal run.

---

## 6. Notification and deduplication semantics

### Main mini-PC searches

1. Fetch one result page for each query.
2. Normalize each result.
3. Skip IDs already in `vinted_items.txt`.
4. Add the local hardware analysis.
5. If `hardware_require_target_models` is enabled and the item is not recognized as a target chassis, skip it without writing its ID to the main ledger (latest behavior).
6. For an eligible item, send notifications through selected channels; the hardware detail lines are attached to Telegram.
7. Record the item ID only if all selected delivery calls succeed. In `--dry-run`, no notification is sent but the item is printed and recorded.

If multiple channels are selected and Telegram succeeds while another channel fails, `all(results)` is false and the item is not recorded. On the next scan Telegram can receive the same item again. The sample selects Telegram only, reducing this scenario.

### RAM Hunt

RAM Hunt has its own query list and ledger. It enforces a local price ceiling as well as adding the `price_to` query parameter. Irrelevant fuzzy-search results are saved in the RAM Hunt ledger to avoid reprocessing; genuine candidates are saved after successful notification or dry-run.

### The `--dry-run` trap

`--dry-run` means “do not send a message”, not “do not mutate state”. The current scanner saves eligible main-search and RAM Hunt item IDs after printing them. This is intentional initialization behavior, but it can suppress those exact items during the next live run.

---

## 7. Known limitations and unresolved issues

These are important for the next agent. Do not silently claim that they have been solved.

1. **Live validation is a snapshot, not a guarantee.** Phase 6 ran two consecutive live `--dry-run` scans against vinted.it (see section 12); the API is still undocumented and can change, and the unit tests still mock requests.
2. **One page per query.** The current sample uses 24 results per query. There is no automatic pagination/backfill. A high listing volume or long interval between scans can cause missed listings.
3. **Retry/backoff is limited.** Phase 6 added a 2 s pause and a single Retry-After retry for listing pages, a pause for the rest of the run after a second 429, and a single retry for Telegram 429. Catalogue API requests still have no retry or backoff.
4. **No concurrency lock / overlap prevention.** The append-only text ledgers assume one scanner process at a time. Do not schedule overlapping runs.
5. **No heartbeat or external health alert.** A cron job can stop running without an independent notification.
6. **Plain-text deduplication files, not SQLite.** There is no built-in history table with query-level state, timestamps, delivery attempts or error status.
7. **Hardware extraction is based on listing text.** Seller descriptions may be inaccurate. A parsed CPU/RAM/storage value is not a hardware diagnostic.
8. **CPU benchmarks are PassMark averages, not measurements of the listed machine.** Since phase 10 the CPU score comes from PassMark CPU Mark ratings copied on 2026-11-09; they drift as PassMark collects submissions and must be refreshed by hand. The curated tier table remains only as a fallback for a CPU without benchmark data.
9. **Platform profiles are not universal compatibility guarantees.** Exact MTM/Product Number, BIOS, motherboard, power supply, thermals and CPU suffix must be checked before purchasing a CPU.
10. **The model whitelist can produce false negatives.** A genuine Tiny/Mini/Micro listing may be rejected if the seller omits the model/form-factor keywords. It can also be fooled by inaccurate listing text; seller evidence/photos still matter.
11. **RAM cost model is coarse.** The €50 8→16 GB allowance is not a reliable estimate for every 4 GB machine or unusual module layout. No inspection of slots or RAM topology is possible from text alone.
12. **Build totals may omit real checkout costs.** Vinted buyer-protection fees and shipping are generally not included unless manually included in a cost budget.
13. **Only one CPU cost is configured by default.** Upgrade scores for other platforms may be pending until the user enters actual observed costs.
14. **CPU coverage is curated, not exhaustive.** Phase 10 added 11th-14th gen T parts (i5-11400T/11600T, i3-12100T/13100T, i5-12400T/12600T/13400T/13500T/13600T/14500T, i7-13700T, i9-9900T/10900T/11900T) and Pentium G6400T/G7400T; AMD Athlon parts are still missing.
15. **Filtered listings are re-evaluated only after a rule change.** Since phase 6 they are stored in `vinted_filtered_items.txt` tagged with `FILTER_RULES_VERSION`; bump that constant whenever target or exclusion rules change, otherwise old rejections stay in force.
16. **RAM Hunt is enabled by default in the sample.** Check the actual live setting before assuming it is wanted on every scan; seven extra Vinted queries can add request volume.
17. **The score must never become a notification threshold.** This is an explicit product requirement. Only the separate chassis rule and normal deduplication determine eligibility.

### Historical debugging note: “only one Telegram notification”

The user previously reported receiving only one Telegram notification and asked why. No confirmed root cause was established from the archived evidence. Plausible causes discussed were query coverage (one page / 24 results), search overlap, already-seen IDs, strict chassis filtering, and RAM Hunt candidate filtering. Do not assert one of these as the proven cause. If this issue is revisited, instrument per-query counts: API results, normalized items, already-seen IDs, target-filter skips, RAM Hunt price skips, RAM Hunt classification skips, send attempts, delivery failures and persisted IDs.

---

## 8. Hardware examples that shaped the upgrade logic

These examples are part of the decision history, not verified inventory or universal price references.

### Lenovo ThinkCentre M700 Tiny — cheap base, weak starting CPU

An observed M700 listing was advertised with a Celeron G3900T, 4 GB DDR4, 500 GB SATA HDD and an M.2 SATA slot, at roughly €49 before Vinted fees/shipping. Its seller claimed several other CPU options, but seller compatibility claims should not be trusted without the exact machine documentation.

The local upgrade matrix points to an i7-6700T (4 cores / 8 threads). This is a meaningful upgrade over a 2-core Celeron, but it still does not meet the preferred six-physical-core target. The example helped motivate upgrade-aware evaluation, but it must not be scored as equivalent to an M720/M920 with a six-core option.

### Lenovo ThinkCentre M710q Tiny — 4-core upgrade ceiling

A separate M710q listing was described only as “Core i3”, with 16 GB RAM and a 240 GB SSD. It was not compelling at its asking price without knowing the exact i3 SKU. The platform candidate in the local matrix is i7-7700T (4 cores / 8 threads), still below the six-core preference. This listing also highlighted that common i3 T processors are not yet covered in the local catalog.

### HP EliteDesk 800 G2 Desktop Mini — T versus non-T matters

A French seller's HP label photo identified the unit as **HP EliteDesk 800 G2 Desktop Mini 65W**, Product ID **L1D85AV**. The seller could not identify the installed CPU without disassembly; the listing claims about installed RAM/storage therefore still needed verification.

The platform reference profile points to i7-6700T (35 W), not the desktop i7-6700 (65 W). The two are not interchangeable just because both are Skylake LGA1151 CPUs. The user also examined an i7-6700 non-T listing and an i7-6700T listing. The non-T 65 W CPU should not be treated as the correct G2 Mini upgrade candidate; the T suffix and the machine's official support list matter.

The configured €68 CPU budget is a single observed i7-6700T offer with protection/shipping incorporated into that planning amount. It is not a market average. Even with a new CPU and RAM, the G2 remains a 4-core/8-thread platform, so the total spend must be compared against an M920q/G5-class six-core system before committing.

---

## 9. Test and verification status of the current snapshot

The Upgrade-Aware revision was tested locally on 2 November 2026 and again after being committed (with `Config.sample.py` copied to `Config.py`, as the CI workflow does).

Commands run:

```bash
python -m unittest discover -s tests -v
python -m py_compile vinted_scanner.py hardware_enricher.py vinted_query_builder.py
```

Results:

- **61 unit tests passed** (phase 5; see section 12 for the phase-6 status).
- Python compilation passed for the scanner, hardware enricher and query builder.
- The tests cover query building, request and response failures, Telegram channel selection and escaping, deduplication, dry-run behavior, target filtering, M700 USFF recognition, CPU upgrade candidates, RAM cost assumptions and RAM Hunt classification.
- Tests mock network behavior. **No live Vinted scan was made**, so endpoint compatibility and real search coverage remain unverified.

The current tree contains (among other files):

- `vinted_scanner.py`
- `hardware_enricher.py`
- `vinted_query_builder.py`
- `Config.sample.py`
- `README.md`
- `HARDWARE_ENRICHMENT.md`
- `UPGRADE_AWARE_CHANGELOG.md`
- `tests/test_vinted_scanner.py`
- `tests/test_hardware_enricher.py`
- `tests/test_query_builder.py`
- `.github/workflows/tests.yml`
- `requirements.txt`

There is no personal `Config.py` in the repository.

---

## 10. Instructions for the next AI coding agent

Treat the current `main` branch as the source of truth unless the user supplies a newer working tree.

1. **Preserve the product rules.** Every new listing that passes the HP Mini / Lenovo Tiny / Dell Micro whitelist must be notified regardless of score. Never add a minimum-score notification filter without explicit user approval.
2. **Keep Vinted-only searching.** Do not add eBay/Subito scrapers, integrations or marketplace polling. A manual CPU/RAM price entered into config is just a planning assumption.
3. **Use total-cost reasoning.** Evaluate listing price + plausible RAM cost + plausible platform-compatible CPU cost. Mark unknown prices as unknown; do not invent them.
4. **Do not overstate upgrade compatibility.** A socket match is insufficient. Use manufacturer references and verify exact MTM, BIOS, board, PSU and cooling. Distinguish T and non-T SKUs.
5. **Keep current-spec and upgrade-opportunity scores separate.** They answer different questions and must both remain informational.
6. **Do not broaden the chassis filter casually.** Add model-specific rules with tests and a real chassis reference. Do not let an M700 USFF exception accept unrelated USFF desktops.
7. **Preserve local secrets and state.** Never overwrite `Config.py`, `vinted_items.txt` or `vinted_ram_hunt_items.txt` without an explicit migration plan and backup. Never commit credentials.
8. **Make small, reviewable patches.** The user prefers targeted patches/functions over full file rewrites. When working against their live installation, request or inspect the current file before patching; do not assume it is byte-identical to the repository.
9. **Add tests for behavioral changes.** Especially test: notification independence from score, target filter behavior, filtered-ID persistence policy, duplicate suppression, costs missing versus configured, CPU suffix correctness, and RAM Hunt's independent state.
10. **Report what was actually verified.** Separate static inspection, local unit tests, compilation, dry-run output and live Vinted results. Do not claim live validation when only mocks were used.
11. **Do not change RAM Hunt silently.** It is Vinted-only, separate from the Mini-PC search, and enabled by default in the current sample. Confirm intent or retain the existing setting.
12. **Instrument before diagnosing missing alerts.** If the user reports too few notifications, add counters/logging and inspect actual query results before changing filters or deduplication logic.

---

## 11. Phase-to-commit map

| Phase | Commit | Summary |
|---|---|---|
| 0 | `d2924a3` and earlier | Upstream VintedScanner baseline selected for this project. |
| 1 | `72dd8c3` | Standalone `hardware_enricher.py` module. |
| 1 | `8fb151a` | Hardware enrichment integrated into Telegram alerts. |
| 2 + 3 | `42667da` | CPU-first scoring, target chassis filter, RAM upgrade estimates and RAM Hunt mode. |
| 5 | `bfbddc7` | Upgrade-aware platform analysis, discovery queries and non-persistent main-filter skips. |
| 6 | `5d02b29` | Listing descriptions are actually extracted (parser crash and 403 fixed). |
| 6 | `eb36447` | Title-only rejection before detail fetches; versioned filtered-items ledger. |
| 6 | `411c858` | Spare parts, accessories and multi-unit lots excluded. |
| 6 | `4aea11c` | Discovery queries limited to the computer catalogue and a price range. |
| 6 | `adda9fb` | CPU catalogue extended; I-5/bare-T/AMD A-series parsing; French Go/To units. |
| 6 | `b860215` | All analysis and RAM Hunt messages in English. |
| 6 | `7b62c05` | Platform profiles (generation, max RAM, PCIe, vPro) and the CPU 40 / price 25 / RAM 10 / storage 10 / platform 15 score. |
| 6 | `b1cb0dc` | Upgrade-opportunity message names the actual missing input. |
| 6 | `b520f6a` | Telegram pacing and 429 retry. |
| 6 | `792140a` | Optional CPU Hunt mode. |
| 6 | `60218c7` | Listing-page backoff on Vinted 429. |
| 6 | `9c476aa` | CPU Hunt accepts short bare CPU titles. |
| 6 | `764b6f0` | RAM Hunt reads RAM with the hardware parser and ignores non-computer "portatile" listings. |

**Current source of truth for implementation:** the code on `main`, not the prose in any older README or historical assistant messages. If a historical statement and the current code disagree, inspect the code and update this handoff before making changes.

---

## 12. Phase 6 — Proxmox evaluation fixes (3 November 2026)

A live dry run of the phase-5 code with three brand-filtered queries exposed these problems: no listing description was ever read (the HTML parser crashed on `<meta charset>` and pages were requested without browser headers, so Vinted answered 403); every run re-fetched about a hundred filtered listings; 37 of 53 alerts had an unidentified CPU; unknown CPUs scored 0/50, below a Celeron; discovery queries returned brackets and spare-part lots; and the score ignored generation, vPro/AMT and PCIe, which matter for a remote second node.

Decisions taken with the user:

- vPro/AMT is a score bonus, not a filter.
- 6th/7th-gen platforms are still notified, with fewer platform points, a legacy warning, and never the "Strong candidate" label.
- RAM Hunt stays enabled; there is still no minimum-score threshold.
- A CPU Hunt mode was added for loose 8th/9th-gen T CPUs (and 6th/7th-gen ones with lower caps), because an i3/Pentium base plus a cheap i7-8700T or i5-9500T can beat a configured unit.

Verification on 3 November 2026: 89 unit tests passed, and two consecutive live `--dry-run` scans ran with twelve brand-filtered queries, RAM Hunt and CPU Hunt enabled. The first scan took about 4.5 minutes: 86 alerts, all with a seller description, 45 filtered listings, no HTTP 429 at a 2 s page pause, 28 alerts whose sellers stated only "i5"/"i7" (scored with a tier estimate), 4 RAM Hunt and 6 CPU Hunt candidates. The second scan took 4 seconds and made no listing-page requests. These are snapshots of live data, not guarantees.

---

## 13. Phase 7 — Readable Telegram alerts (3 November 2026)

The user found the alerts unreadable: about twenty lines of equal weight under a `──── HARDWARE ANALYSIS ────` banner, with the verdict buried in the middle, two raw URLs, recurring "Not identified" lines and a description cut mid-word. Of the three layouts proposed, the user picked the full restructure:

- A decision line comes first: a marker for the verdict (🟢/🟡/🟠/🔴, ⚪ outside the target), then the score, the verdict and the upgrade score when there is one.
- The title follows, then the price as `€120`, the links as *Open listing* / *Photo*, and the matched query. The listing photo is the link preview (`prefer_small_media`) instead of a raw URL.
- The banner is gone. The body is split into short bold sections: Hardware, Cost, CPU upgrade and Score breakdown. The checklist and the seller description are `<blockquote expandable>` blocks, and the description is cut on a word boundary.
- Lines without information are left out: an unidentified GPU, the generic AI note, an unknown vPro status, the "CPU upgrade not identified" line. The CPU note and the RAM-estimate note appear in their own section and are no longer repeated in the checklist.
- RAM Hunt and CPU Hunt follow the same layout. Their line builders return the headline as the first line.

Contract change: line builders now return **already-escaped Telegram HTML**, and `send_telegram_message` no longer escapes `extra_lines` (it still escapes the title, the price, the URLs and the query). Any new line must go through the `message_format` helpers. Dry runs print the same lines with the tags stripped.

Verification: 93 unit tests passed. The example M900 Tiny listing renders as about 1,850 characters, well below Telegram's 4,096 limit. The example alert was then sent to the live Telegram group and accepted (HTTP 200), which confirms the HTML is valid; the photo preview for Vinted `.webp` images was left for the user to check on the phone.

---

## 14. Phase 8 — Run summary and error reporting (3 November 2026)

The user went three to six hours without any Telegram message and could not tell a quiet market from a broken scanner. They asked for every failure to reach Telegram (Vinted HTTP 429, network problems, parsing errors) and for a message even when a run finds nothing.

- A new `run_report` module records, for each run, the searches, listings, new listings, matches and notifications per mode (Mini PC, RAM Hunt, CPU Hunt) and every problem met, grouped by type.
- Every run ends with one Telegram status message: ✅ when nothing went wrong (including "nothing new matched"), ⚠️ when problems were met, 🔴 when the run could not search at all or crashed. `telegram_run_summary` selects `always` (default), `problems` or `off`.
- Catalogue errors are classified as rate limit, HTTP error, network error or unexpected response. A catalogue 429 is now retried once after Retry-After and a second one skips the remaining searches, like listing pages already did.
- Listing-page failures, which were only logged at INFO level, are logged as warnings and reported.
- An unexpected exception while processing one listing no longer aborts the run: it is reported with the listing ID and title, and the scan continues. Any other crash is caught by `main` and reported.
- A summary that cannot be delivered is kept in `vinted_pending_alerts.json` and sent as "Delivered late" by the next run that reaches Telegram.

Verification: 110 unit tests passed.

## 15. Phase 9 — SQLite history, Telegram import and dashboard (7 November 2026)

The user wanted a global view of the second-hand market for the second Proxmox node: every run, every alert split by Mini PC / RAM / CPU, in a shop-like page styled after Vinted, with the alerts stored as structured data instead of only as Telegram messages.

- `storage.py` replaces the four `.txt` ledgers with `vinted_scanner.db`. Every listing the scanner processes is stored with its outcome (`notified`, `dry_run`, `failed`, `filtered`, `ignored`) and, for alerts, the parsed content of the message. Runs are stored with their per-mode counters and issues. Dedup semantics are unchanged; the old ledgers are imported once and left on disk.
- `alert_parser.py` turns alert and run-summary text back into data; it is shared by the scanner and the importer so live and restored rows have the same shape.
- `telegram_import.py` restores history from a Telegram Desktop JSON export. Only the alert layout introduced on 3 November (score headline) is parsed, as the user asked; the real export of 7 November gave 47 mini-PC, 6 RAM Hunt and 7 CPU Hunt alerts and 88 runs, with 10 older-format messages skipped.
- `dashboard.py` (standard-library HTTP server, read-only) and `dashboard/` (plain HTML/CSS/JS, Vinted design tokens) show the market overview, catalogues per mode, run history and a per-listing detail panel.

Verification: unit tests pass (storage, parser fed by the real message builders, importer, dashboard queries and HTTP); the real export was imported twice into a scratch database (idempotent); an offline end-to-end `main(dry_run=True)` with stubbed Vinted responses wrote listings and a run; the dashboard was checked in headless Chromium at desktop and phone width, light and dark. Not verified: a live run against Vinted with the new storage.

## 16. Phase 10 — Benchmark-backed CPU scoring and best-deal dashboard (9 November 2026)

A friend's "CPU Intelligence & Best-Deal" proposal was reviewed against the code. About half already existed (catalog, platform profiles, T/non-T handling, total-cost estimates, separate current/upgrade scores, score never a filter). The useful remainder was implemented without restructuring the modules:

- `CPU_CATALOG` gained PassMark single/multithread ratings (`CPU_BENCHMARKS`, with `BENCHMARK_SOURCE`/`BENCHMARK_VERSION`), microarchitecture and launch year, plus 18 newer CPUs. Intel values come from cpubenchmark.net's CPU lists, AMD values from each CPU's page (its "Multithread Rating", not the older V9 figure).
- The CPU score blends 70% multithread and 30% single-thread relative to the i7-12700T; the tier table is a fallback. The upgrade check compares benchmarks too.
- Alerts gain a `Bench:` row with ratios against `hardware_reference_cpus` (default N100 and i5-12500T), and the CPU line shows architecture and year.
- Platform profiles for ThinkCentre M70q Gen 2/3/4, M80q Gen 3, M90q Gen 2/3 (Lenovo PSREF), HP EliteDesk 800 G8 / Elite Mini 800 G9 and Dell OptiPlex 7090 Micro. HP QuickSpecs could not be read (TLS verification failed), so the HP TDP class and PCIe fields stay unknown. ProDesk G8/G9 and OptiPlex 3000/5000/7000 Micro profiles were not added for lack of a readable reference.
- RAM module layout (`2x8GB`, `1x16GB`, `8GB+8GB`, "single module") is shown and refines the 32 GB estimate (`hardware_ram_16gb_module_eur`); a layout that does not add up stays unknown.
- `hardware_score_weights` rescales the five components to 100 points; invalid values fall back to the defaults.
- A listing naming several catalog CPUs keeps the most specific match and lists all candidates under "To check".
- The dashboard registers SQL functions that look benchmarks up by CPU name (no schema change, works for Telegram imports): value map with a cost/performance frontier, "Most CPU per euro" shelf and sort, fastest-CPU sort, threads/RAM/performance filters, benchmark columns per generation and a CPU price guide.
- The alert layout gained rows but nothing was renamed; the parser and `telegram_import.py` handle old and new alerts (tests use a fixed pre-phase alert and a mixed export).

Not changed: chassis filter (`FILTER_RULES_VERSION` stays "2"), notification eligibility, deduplication, storage schema. Note that the HP **Elite Mini 800 G9** is branded without "EliteDesk", so the chassis filter does not accept it yet; adding it is a filter change needing approval.

Verification: unit tests pass; alerts were rendered offline from synthetic titles; the dashboard was checked in headless Chrome at desktop and phone width on a synthetic database built through the real enricher/parser/store path. Not verified: a live Vinted run or the user's real database.

## 17. Phase 11 — Parser and filter fixes from the dashboard review (12 November 2026)

The 105 mini-PC alerts and the recently filtered listings on the deployed dashboard were reviewed. The fixes below each come with a regression test built from the real listing title (`ListingRegressionTests`):

- **RAM misread as storage size.** The title "... SSD 500GO" followed by the description "RAM : 8 Go" was read as 500 GB of RAM (full RAM points, no RAM upgrade cost). Explicit RAM patterns now stay on one line and accept only plausible RAM sizes.
- **RAM read as a drive.** "i3 8Go SSD 256" produced an "8 GB SSD" and lost the RAM value. Drive readings under 16 GB are ignored in both the storage and the RAM parser; "SSD 256" without a unit is read for common drive sizes only, and "ssd m.2 256gb" is now read as storage.
- **Motherboard alerted as a system.** "Dell OptiPlex 7010 Micro carte mere" passed; "carte mère" and "placa base/madre" are now component words.
- **Newer Dell Micros had no profile.** OptiPlex 3000/5000/7000 Micro, 7010 Micro and Micro 7020 were scored with 2/15 platform points, so a €325 i5-13500T scored below 6th-gen quad-cores. They now have name/generation profiles; all other fields stay unknown because no Dell reference was read (as noted in Phase 10).
- **Chassis filter word order (approved by the user).** Three filtered listings were real targets: "Dell Optiplex Micro 7020", "Hp 400 G4 mini pc" and "Mini PC HP Elitedesk 705 G5". Accepted variants are listed in `HARDWARE_ENRICHMENT.md`; titles without a generation ("MiniPC HP Prodesk 600"), "HP 260 G4 mini", and SFF desktops stay rejected. `FILTER_RULES_VERSION` is now "3", so previously filtered listings are reconsidered on the next run.

Found but not changed: scores stored before Phase 10 are not recomputed, and the database does not record which scoring version produced a score, so the dashboard mixes old and new scores; `build_estimate_eur` is empty for every alert and `upgrade_score` is set on only 3, because the only configured CPU upgrade cost is the i7-6700T; an estimated CPU gets an "upgrade to be verified" verdict even when the title says "6 core"; relisted items get a new ID and are alerted again.

Verification: unit tests pass; the fixed titles were re-run through `analyze_hardware`. Not verified: a live Vinted run or a rescan of the user's database.
