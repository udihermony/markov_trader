# Swing Trading Agent — POC

A local, Python-based swing trading agent operating on a multi-day holding horizon. It discovers stocks via a Finviz screener, fetches daily bars with yfinance, generates signals from a pluggable strategy (baseline: SMA crossover with time stop), executes virtual trades in a SQLite-backed paper-trading sandbox, and tracks performance against SPY.

Two run modes share one code path — the only difference is where `as_of` comes from. Every module receives `as_of: date` and can never read data past it (enforced in the data provider, verified by tests).

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # secrets pattern; no keys needed today
```

## Usage

```bash
# Single daily run — schedule after market close
python main.py paper

# Historical backtest
python main.py backtest --start 2026-01-05 --end 2026-06-30
```

Paper mode is idempotent within a day: running twice does not double-execute orders.

## How it works

Each `run_day(as_of)`: execute pending orders at today's open → build universe (screener output ∪ open positions) → refresh bars → evaluate exits first, then entries → queue orders to `pending_orders` → snapshot performance → render terminal dashboard.

Execution timing: signals are computed on the close of day *t*; fills occur at the **open of day t+1** plus slippage. Filling at the signal close would be look-ahead bias.

Configuration: all strategy/sizing/cost parameters live in `config.yaml` (versioned, diffable); secrets live in `.env` (gitignored). Backtests write to a separate DB file (`swing_backtest.db`) so they never contaminate paper state (`swing_paper.db`). Every invocation stamps its rows with a `run_id` and `mode`.

## Documented limitations

**Screener bias (backtest mode).** Finviz cannot be queried historically. Backtests replay recorded rows from `watchlist_history` when they exist for a date; otherwise they fall back to a watchlist frozen at backtest start, which introduces selection/look-ahead bias — results are indicative only. A warning is logged. Over time, daily paper runs accumulate real historical screens that future backtests can replay.

**Adjusted prices.** yfinance is used with `auto_adjust=True`: splits and dividends are folded into OHLC. Backtest fills therefore use adjusted, not raw, historical prices.

**Cost model.** Flat slippage (default 5 bps against the trader on every fill), zero commission. Crude, but zero-cost crossover backtests are misleading; treat absolute results with skepticism regardless.

**Fragile screener.** Finviz is scraped; calls are wrapped in retry-with-backoff and a hard timeout. On failure in paper mode the most recent persisted watchlist is used.

## Accounting rules

Whole shares only (floored); orders under $500 notional rejected; no pyramiding (BUY on a held ticker is skipped and logged); position size = 10% of current cash, capped at 8 concurrent positions; sells always liquidate the full position. Skipped signals are logged to `trade_log` with `action='SKIP'` and a reason — skipped decisions are as informative as executed ones.

## Tests

```bash
python -m pytest tests/
```

Covers sandbox accounting (cash conservation, slippage direction, rejection paths, no-pyramiding), crossover detection (cross *event* vs. level comparison, time stop in trading days), the `as_of` look-ahead guard, and an offline end-to-end backtest with idempotency checks.

## Layout

```
config.yaml          # all non-secret parameters
.env.example         # secrets pattern
main.py              # CLI: paper | backtest
orchestrator.py      # run_day pipeline + dashboard
screener.py          # finviz discovery + watchlist_history
data_provider.py     # yfinance + SQLite cache + as_of guard
sandbox.py           # virtual account, positions, trade log
strategy/            # Strategy protocol, Signal; sma_crossover.py
db/schema.sql        # SQLite schema (PostgreSQL-migratable)
tests/
```

## Non-goals (POC)

No intraday data, live broker integration, short selling, partial exits, multi-strategy ensembles, or ML models. The strategy interface exists so these can be added without touching the sandbox or orchestrator.
