# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A fork of a Vinted search-and-notify script, adapted into a personal finder for second-hand business mini-PCs (a second Proxmox node), plus two side modes: **RAM Hunt** (cheap/damaged laptops as DDR4 SO-DIMM donors) and **CPU Hunt** (loose low-power T-series CPUs). Notifications go to Telegram by default. `docs/DEVELOPMENT_HISTORY.md` is the detailed handoff document (product decisions, phase history, known limitations); read its §2, §6, §7 and §10 before changing filtering, scoring or deduplication.

## Commands

Python 3.11+, only runtime dependency is `requests`. Every module does `import Config`, so a `Config.py` must exist (it is gitignored):

```bash
pip install -r requirements.txt
cp Config.sample.py Config.py                      # needed before tests or running
# (if there is no Config.py, copy the sample to a scratch dir and run tests with PYTHONPATH=<that dir>)
python3 -m unittest discover -s tests -v           # full suite (what CI runs on 3.11/3.13/3.14)
python3 -m unittest tests.test_hardware_enricher -v                 # one module
python3 -m unittest tests.test_vinted_scanner.ConfigurationTests.test_build_catalog_url_uses_marketplace_domain  # one test
python3 vinted_scanner.py --dry-run                # no notifications, but DOES record IDs (see below)
python3 telegram_import.py telegram-export.json [--dry-run]   # restore history into vinted_scanner.db
python3 dashboard.py [--port 8000] [--db vinted_scanner.db]       # http://127.0.0.1:8000
# Deployment: scanner via host crontab (Proxmox LXC, /root/VintedScanner, venv); dashboard via deploy/vinted-dashboard.service on 0.0.0.0:8000
python3 vinted_query_builder.py [--refresh-cache | categories <q> | brands <q> --category <id> | filters --category <id> --options]
```

No linter or formatter is configured. Tests use `unittest` + `unittest.mock`; config values are overridden with `patch.object(scanner.Config, name, value, create=True)` and HTTP is mocked — nothing hits live Vinted.

## Architecture

- **`vinted_scanner.py`** — entry point and orchestration. `main()` → `run_scan()` runs three independent modes in order: mini-PC queries (`process_queries`: user `queries` + built-in discovery queries in catalog `3564`), RAM Hunt (`process_ram_hunt_queries`), CPU Hunt (`process_cpu_hunt_queries`). It opens an anonymous session on `vinted_url`, then searches `https://api.<marketplace-domain>/svc-catalogue/items`. Config is read via `getattr(Config, ..., default)`, so new settings must have code defaults (users merge `Config.sample.py` into existing `Config.py` by hand). Legacy query keys (`catalog_ids`, `brand_ids`, …) raise a config error; queries use a `filters` dict of ID lists.
- **`hardware_enricher.py`** — deterministic, local (no AI/external calls) listing analysis: CPU/RAM/storage parsing, `CPU_CATALOG`, `PLATFORM_PROFILES`, chassis whitelist filter (`is_target_system`, `names_target_family`), scoring (default CPU 40 / price 25 / RAM 10 / storage 10 / platform 15, configurable via `hardware_score_weights`; CPU points from PassMark data in `CPU_BENCHMARKS`, tier table only as fallback), reference-CPU comparison, RAM module layout, upgrade estimates, listing-page detail fetch, and Telegram message body building. RAM Hunt and CPU Hunt reuse its parsers.
- **`run_report.py`** — `RunReport` accumulates per-mode stats and categorized issues (429s, HTTP/network errors, processing errors, undelivered alerts, crashes) during a run; `main()` always sends an end-of-run Telegram summary. Undeliverable summaries are queued in `vinted_pending_alerts.json` and sent late next run.
- **`message_format.py`** — Telegram HTML escaping/formatting helpers (`esc`, `bold`, `section`, `expandable`, `to_plain_text`). All alert text is Telegram HTML; escape user/listing text through these.
- **`storage.py`** — SQLite schema and `Store` (dedup sets, listing upsert, runs, one-time `.txt` migration). Timestamps are UTC ISO strings.
- **`alert_parser.py`** — parses the *plain text* of an alert/run summary back into listing columns and sections. The scanner (`save_listing` builds the plain text of the exact lines it sends) and `telegram_import.py` both use it, so a change to alert wording in `hardware_enricher.build_telegram_lines`, the hunt `*_telegram_lines` or `run_report` must keep `tests/test_alert_parser.py` passing (those tests feed it output of the real builders).
- **`telegram_import.py`** — rebuilds listings/runs from a Telegram Desktop JSON export (current alert layout only); idempotent.
- **`dashboard.py`** + `dashboard/` — stdlib `ThreadingHTTPServer`, read-only DB connection, JSON API (`/api/overview|facets|listings|runs`) and a vanilla HTML/CSS/JS UI styled on Vinted's tokens (no build step). Query functions are plain functions tested in `tests/test_dashboard.py`. Benchmarks are not stored: `register_functions` adds SQL functions `cpu_bench_mt`/`cpu_bench_st` that look them up in `hardware_enricher` by CPU name.
- **`vinted_query_builder.py`** — standalone interactive wizard/CLI for discovering Vinted category/filter IDs; never writes `Config.py`.

### State files (all next to the script, gitignored)

All state lives in `vinted_scanner.db` (SQLite, `storage.Store`). `listings` has one row per (mode, item_id) with a status (`notified`, `dry_run`, `failed`, `filtered`, `ignored`, `legacy`) plus the alert parsed into columns and `sections` JSON; `runs`/`run_modes`/`run_issues` mirror `RunReport`. `Store.seen_ids()` defines dedup: `failed` is retried, `filtered` is skipped only while its `filter_version` equals `hardware_enricher.FILTER_RULES_VERSION` — **bump that version whenever filter rules change** so previously filtered IDs are reconsidered. A notified row is never downgraded. The old `.txt` ledgers are imported once (`migrate_legacy_ledgers`) and left on disk; `telegram-export.json` and the DB are gitignored and must never be committed. Logs: `vinted_scanner.log` (rotating).

### Dedup/notify semantics

- An item counts as notified only after **all** selected channels succeed; otherwise it is stored as `failed` and retried next run.
- `--dry-run` means "don't send", not "don't mutate": it prints and records eligible IDs (intended for initializing a fresh install).
- Titles that don't name a supported family are rejected without fetching the listing page. Detail-page fetches and catalogue searches back off on HTTP 429 (Retry-After, retry once, then pause for the rest of the run).
- One listing raising an exception is reported and skipped (`run_item_safely`); it must not abort the run.

## Product rules (from the user; do not change without explicit approval)

- Every new listing passing the HP EliteDesk/ProDesk Mini / Lenovo ThinkCentre Tiny / Dell OptiPlex Micro whitelist is notified **regardless of score**. Scores (current-spec and upgrade-opportunity, kept separate) are informational only — never add a score threshold.
- Vinted only — no eBay/Subito or other marketplace integrations. Component prices in config are manual planning assumptions.
- Don't broaden the chassis filter casually; add model-specific rules with tests and a manufacturer reference (e.g. the M700 + USFF/Tiny exception must not accept generic USFF desktops). Distinguish T vs non-T CPU SKUs; don't overstate upgrade compatibility.
- Don't silently change RAM Hunt (enabled by default) or CPU Hunt (disabled by default) behavior.
- Never overwrite `Config.py` or the ledger files. Prefer small, targeted patches over rewrites.
- When too few alerts are reported, add instrumentation/counters and inspect real results before changing filters or dedup logic.
- When reporting, separate what was verified by unit tests vs. dry-run vs. live Vinted; don't claim live validation from mocks.
- User-facing behavior changes are documented in `README.md` / `HARDWARE_ENRICHMENT.md`, and notable phases are appended to `docs/DEVELOPMENT_HISTORY.md`.
