# Code review — commit `09958c8` (FastAPI backend, web UI, backtest history)

Reviewed: `api.py`, `static/index.html`, `dashboard.py`, `seed_watchlist.py`, plus the effect of the new layer on the core invariants. All 22 tests still pass.

Correction to something I said earlier: `*.db` **is** correctly ignored — `swing_paper.db`, `swing_backtest.db` and `backtests/*.db` are all untracked. I was wrong about that.

---

## Blockers

### 1. Running a backtest silently rewrites `config.yaml`
`static/index.html:738-746` — picking a saved strategy in the Run tab issues `PATCH /api/config` *before* launching, so the global config file is overwritten with that strategy's params. Side effects:

- Your working config is clobbered by whatever you last backtested. (`config.yaml` currently reads `fast: 19, slow: 20` — 19/20 SMAs cross on noise and will churn; likely a leftover from an experiment, not an intent.)
- `PATCH /api/config` (`api.py:180`) rewrites via `yaml.dump`, which **strips every comment**. The documentation that made the config self-explanatory is already gone.
- CLI runs (`python main.py paper`) silently inherit whatever the last UI backtest set.

**Fix:** pass strategy/sizing/costs in the `POST /api/backtest` body and thread them into `_build_orch_at` as an override, leaving `config.yaml` untouched. If you want config editing to persist, use `ruamel.yaml` (round-trips comments) and restore the comment block.

### 2. Saved-run KPIs are recomputed against *today's* `initial_cash`
`api.py:290, 299` — `_compute_kpis`/`_compute_summary` call `_initial_cash()`, which re-reads `config.yaml`. Change `initial_cash` and every historical run's ROI, P&L and vs-SPY numbers change retroactively. The registry already stores `sizing` per run; use `registry[run].sizing.initial_cash` when `run_id` is set.

Same class of bug in `dashboard.py:67`.

### 3. `run_id` in the registry ≠ `run_id` in the run's own DB
`api.py:402` mints a fresh uuid inside `_build_orch_at`, while the registry and filename use `job_id`. Verified in the saved run: registry says `cc0d63a4…`, every `trade_log` row says `49e7590e…`. Any future join on `run_id`, or debugging by grepping a DB for its registry id, breaks. Pass `job_id` into `_build_orch_at` as the run_id.

---

## Real bugs, lower severity

### 4. Positions view can show post-`as_of` prices (look-ahead leak in the display layer)
`api.py:312-313` and `dashboard.py:128-129` both do `pc.date = (SELECT MAX(date) FROM price_cache WHERE ticker = p.ticker)` — an unbounded read straight against `price_cache`, bypassing the `DataProvider.get_bars` guard the plan says is the single chokepoint ("no other module touches the cache directly").

It's latent right now only because the one saved run happens to have `cache max == backtest end`. But `_setup_run_db` (`api.py:418`) copies the *whole* main backtest cache into each run DB. Run a backtest through June, then one through March: the March run's open positions will be marked at June prices. Bound the price lookup by `MAX(date) FROM performance_history` (the run's `as_of`), or trim the copied cache to the run's end date.

### 5. `seed_watchlist.py` seeds Mondays only, but the screener looks up exact dates
`screener.py:_backtest_watchlist` queries `screen_date = as_of`. Seeding only Mondays (`seed_watchlist.py:30`) means Tue–Fri find nothing and fall through to `self._frozen` → a live Finviz call, or `_load_latest_recorded()`. That last one returns `MAX(screen_date)` across the whole table — i.e. a watchlist dated *after* `as_of`. Structurally it's look-ahead, and it's the reason the run "works" at all on non-Mondays.

**Fix:** either seed every trading day, or make `_backtest_watchlist` query `screen_date <= as_of ORDER BY screen_date DESC LIMIT 1` — the as-of-correct semantics, which is what a weekly screen cadence actually means.

### 6. Trade ordering within a day is arbitrary
`api.py:342` and `dashboard.py:182` sort by `timestamp DESC`, but timestamps are dates. That run has 16 same-date trade pairs, so the log displays them in undefined order. Use `ORDER BY id DESC` (insertion order = execution order).

### 7. Manually setting a watchlist has no effect in paper mode
`POST /api/watchlist` writes rows for today, but `screener._paper_watchlist` always calls Finviz first and only falls back to persisted rows on failure — then overwrites today's rows with the Finviz result. The Watchlist tab therefore appears to work and silently does nothing. Either add a `screener.source: finviz | manual` config switch, or have paper mode prefer a manually-written watchlist for `as_of`.

### 8. Nothing ever writes a `SKIP` row
Zero `SKIP` rows across 122 days and 60 fills. This one is mine, not yours: `orchestrator.py:149` logs the `max_concurrent_positions` rejection to `log.info` instead of `trade_log`, and the orchestrator pre-filters already-held tickers before `execute_buy`, so the sandbox's SKIP paths almost never fire. Acceptance criterion "log rejected/skipped signals" is effectively dead. Log the skip in `_evaluate_entries` directly.

---

## Smaller things

- `api.py:429-441` — if `Thread.start()` throws, `_run_lock` is never released and every later run returns 409 until restart. Wrap in `try/except: _run_lock.release(); raise`.
- `api.py:479-486` — the registry snapshots `config.yaml` *after* the run finishes; a concurrent `PATCH /api/config` records the wrong params. Snapshot at job start.
- `api.py:42` — `_jobs` grows unbounded. Fine locally; cap it if this ever runs longer than a session.
- `api.py:70, 233, 351` — `mode` is unvalidated; any string that isn't `"paper"` silently resolves to the backtest DB. Use a `Literal["paper","backtest"]`.
- `api.py:386-394` — `delete_run` will happily unlink the DB of a run that's still executing.
- `dashboard.py:164, 207` — `.applymap` is deprecated in pandas 2.1+ (`.map` on a Styler). Will warn now, break later.
- `static/index.html` — table rows are built with `innerHTML` from server data. Ticker strings are the only user-controlled input and they're uppercased, so it's not exploitable today; worth knowing if watchlist input ever accepts free text.

---

## What's good

The per-run DB + registry design is the right call — it makes runs genuinely reproducible and comparable, which the original plan didn't cover. Seeding each run DB from the main price cache is a nice touch (no refetching). Job polling with progress, the 409 single-run lock, and storing full params per run are all sound. The core invariants I care about most — next-open fills and within-day idempotency — are untouched by this layer and still hold.

## Suggested order

1. Decouple backtest params from `config.yaml` (#1) — it's corrupting your source of truth right now.
2. Per-run `initial_cash` (#2) and `run_id` consistency (#3).
3. Bound the positions price lookup by `as_of` (#4) — it's a look-ahead leak, and those are the ones that make results quietly wrong.
4. Watchlist as-of semantics (#5, #7).
5. The rest as cleanup.
