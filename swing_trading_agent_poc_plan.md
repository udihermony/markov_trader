# Swing Trading Agent — POC Build Plan (v2)

## Overview
Build a local, Python-based swing trading agent POC operating on a multi-day holding horizon. The agent discovers stocks via an automated screener, fetches historical daily bars, generates signals from a pluggable strategy, executes virtual trades in a sandboxed paper-trading environment backed by SQLite, and tracks performance against a benchmark.

The system supports **two run modes sharing one code path**:
- **`backtest`**: an event-driven loop over a range of historical trading dates.
- **`paper`**: a single daily run (scheduled after market close) that operates on the latest data.

The only difference between modes is where the `as_of` date comes from. Every module receives `as_of: date` and is forbidden from reading any data timestamped after it. This is a hard invariant — enforce it in the data provider.

## Core Architectural Requirements
- **Decoupled modules**: `screener.py`, `data_provider.py`, `strategy/`, `sandbox.py`, `orchestrator.py`, `main.py`. No module imports the orchestrator; dependencies flow one direction.
- **Configuration split**:
  - `.env` (gitignored): secrets only — API keys if any are added later. yfinance and finvizfinance need no keys, but the pattern must exist.
  - `config.yaml` (versioned): everything else — screener criteria, strategy parameters, sizing rules, cost model, DB path, run mode defaults. Load with `pydantic-settings` or a small typed config dataclass. Strategy configs must be diffable and reviewable, so they do not belong in `.env`.
- **Persistence**: single local SQLite file. Schema designed so a later PostgreSQL migration is mechanical (no SQLite-only types, use ISO-8601 text timestamps or unix epochs consistently, integer primary keys).
- **`.gitignore`**: `.env`, `*.db`, `__pycache__`, cache directories.
- **Run identity**: every invocation gets a `run_id` (uuid) written to all rows it creates, plus a `mode` column (`backtest` | `paper`). Backtest runs must be re-runnable without contaminating paper-mode state — simplest approach: backtests write to a separate DB file specified in config.

## 1. Data & Discovery Pipeline

### Module A: Screener (`screener.py`)
- Use `finvizfinance` with criteria from `config.yaml`. Defaults: Index = S&P 500, Price > $20, Avg Volume > 500K. Return top N tickers (default N=10).
- **Persist every screen result** to a `watchlist_history` table: `(run_id, screen_date, ticker, rank)`. This creates an auditable record and, over time, real historical screens that future backtests can replay.
- **Backtest-mode limitation (must be documented in code and README)**: Finviz cannot be queried historically. In backtest mode, either (a) replay recorded rows from `watchlist_history` if they exist for the date range, or (b) fall back to a watchlist frozen at backtest start, with an explicit logged warning that this introduces selection/look-ahead bias and results are indicative only.
- Wrap the finviz call in retry-with-backoff and a hard timeout; it is a scraper and will break. On failure in paper mode, fall back to the most recent persisted watchlist and log a warning.

### Module B: Data Provider (`data_provider.py`)
- Use `yfinance` to fetch daily OHLCV. `auto_adjust=True` (splits and dividends folded into prices — document this choice).
- **Lookback**: fetch enough history to cover the longest indicator warmup plus the evaluation window. For the baseline strategy (20-day SMA + cross detection), require ≥ 25 trading days of data before a ticker is eligible for signals; default fetch window 90 calendar days.
- **Cache**: SQLite table `price_cache(ticker, date, open, high, low, close, volume)`, primary key `(ticker, date)`. On each request, compute missing dates and fetch only those. Never re-fetch data already cached, except a configurable refresh of the most recent bar (data for "today" can be partial intraday).
- **The `as_of` guard lives here**: the public read API is `get_bars(ticker, as_of, lookback_days)` and it must filter `date <= as_of` at the query level. No other module touches the cache directly.
- Handle yfinance failures per-ticker (retry ×2 with backoff, then skip ticker and log) — one bad ticker must not kill the run.

## 2. Sandbox & Strategy

### Module C: Virtual Sandbox (`sandbox.py`)
SQLite schema:

1. `account(id, run_id, mode, cash)` — single row per run context. Initial cash from config (default $100,000).
2. `positions(id, run_id, ticker, shares, avg_entry_price, entry_date, entry_signal)` — **`entry_date` is required** to implement the time stop. One row per open position; closed positions are deleted here and live on in `trade_log`.
3. `trade_log(id, run_id, mode, timestamp, ticker, action, shares, fill_price, cost_bps_applied, reason, signal_metadata_json)` — `reason` is mandatory and one of e.g. `crossover_buy`, `crossover_exit`, `time_stop_exit`. Also log **rejected/skipped signals** here with action=`SKIP` and a reason (`insufficient_cash`, `already_held`, `max_positions`, `min_notional`) — skipped decisions are as informative as executed ones when evaluating the agent.
4. `performance_history(id, run_id, date, cash, positions_value, total_equity, benchmark_equity)` — one row per trading day. `benchmark_equity` = value of the same starting capital fully invested in SPY from day one (uses the same price cache).

Accounting rules (all configurable in `config.yaml`):
- Whole shares only (floor). Reject orders below a minimum notional (default $500).
- **No pyramiding**: a BUY signal on an already-held ticker is skipped (logged).
- Position sizing: fixed fraction of *current cash* (default 10%), capped by `max_concurrent_positions` (default 8). With 10% of cash and no cap, later signals get starved — the cap makes behavior explicit.
- **Cost model**: apply flat slippage in bps to every fill (default 5 bps against the trader: buys fill at `price × (1 + bps/1e4)`, sells at `price × (1 − bps/1e4)`). Commission = $0. Even a crude cost model materially changes crossover-strategy results; zero-cost backtests are misleading.
- Selling is always full liquidation of the position in this POC.

Methods:
- `execute_buy(ticker, cash_amount, fill_price, as_of, reason, metadata)` → returns executed/rejected + details
- `execute_sell(ticker, fill_price, as_of, reason, metadata)`
- `get_open_positions()`
- `get_portfolio_value(prices: dict[str, float])`
- `snapshot_performance(as_of, prices, benchmark_price)`

Write unit tests for the sandbox accounting (cash conservation, rejection paths, slippage math) and for crossover detection. These are the two highest-probability bug sites in the project.

### Module D: Strategy Engine (`strategy/`)
- Define a pluggable interface (ABC or `Protocol`):

  ```python
  class Strategy(Protocol):
      def generate_signal(
          self,
          bars: pd.DataFrame,          # OHLCV up to and including as_of
          position: Position | None,   # entry_date, entry_price, shares — or None
          as_of: date,
      ) -> Signal                      # Signal(action: BUY|SELL|HOLD, reason: str, metadata: dict)
  ```

  Strategies are stateless; all position awareness comes in via the `position` argument. The active strategy and its parameters are chosen in `config.yaml`.

- **Baseline strategy — SMA crossover with time stop** (`strategy/sma_crossover.py`), params: `fast=10`, `slow=20`, `max_hold_days=5`:
  - Compute fast and slow SMAs on closes.
  - **BUY**: cross *event* only — fast ≤ slow at `t−1` AND fast > slow at `t`. Comparing levels instead of detecting the cross is a classic bug; the tests must cover this.
  - **SELL**: fast crosses below slow at `t`, OR `as_of − entry_date ≥ max_hold_days` trading days (count trading days from `bars`, not calendar days).
  - Return HOLD otherwise. Insufficient history (< slow + 1 bars) → HOLD with reason `insufficient_history`.

- **Execution timing (applies to both modes)**: signals are computed on the close of day `t`; fills occur at the **open of day `t+1`** (plus slippage). Filling at the same close used to compute the signal is look-ahead bias. In paper mode this means: run after close, persist pending orders to a small `pending_orders` table, execute them on the next run using that day's open.

## 3. Orchestration

### `orchestrator.py` — one function: `run_day(as_of: date)`
Ordered steps:
1. **Execute pending orders** from the previous session at today's open prices.
2. **Build universe** = screener output for `as_of` ∪ tickers with open positions. Open positions must always stay in the data/signal loop even after dropping off the screen — otherwise exits never fire.
3. **Fetch/refresh bars** for the universe via the data provider.
4. **Exits first**: evaluate strategy for every open position; queue SELL orders.
5. **Entries second**: evaluate strategy for screened tickers without positions; queue BUY orders subject to sizing rules and `max_concurrent_positions` (counting positions net of queued exits).
6. **Snapshot** `performance_history` (mark-to-market at `as_of` closes, plus SPY benchmark).
7. **Dashboard** to terminal (use `rich`): open positions with unrealized P&L and days held, cash, total equity, ROI %, ROI vs SPY, max drawdown to date, last 5 trades.

### `main.py`
- CLI (argparse or `typer`):
  - `python main.py paper` → `run_day(today)`
  - `python main.py backtest --start YYYY-MM-DD --end YYYY-MM-DD` → loop `run_day` over trading days (derive the trading calendar from SPY bars in the cache).
- At backtest end, print a summary: total return, benchmark return, max drawdown, number of trades, hit rate, average holding period.

## Repo layout
```
swing-agent/
  config.yaml
  .env.example
  main.py
  orchestrator.py
  screener.py
  data_provider.py
  sandbox.py
  strategy/
    __init__.py        # Strategy protocol, Signal dataclass
    sma_crossover.py
  db/schema.sql        # or programmatic schema init
  tests/
    test_sandbox.py
    test_sma_crossover.py
  README.md            # includes documented backtest limitations (screener bias, adjusted prices, cost model)
```

## Explicit non-goals for the POC
No intraday data, no live broker integration, no short selling, no partial exits, no multi-strategy ensembles, no ML models. The strategy interface exists so these can be added without touching the sandbox or orchestrator.

## Acceptance criteria
1. `python main.py backtest --start ... --end ...` runs end-to-end on a frozen watchlist, produces a populated `trade_log` and `performance_history`, and prints a summary with benchmark comparison.
2. `python main.py paper` is idempotent within a day (running twice does not double-execute orders).
3. No module can read price data past `as_of` (verified by a test).
4. Sandbox unit tests pass: cash conservation, slippage direction, rejection paths, no-pyramiding.
5. Crossover unit tests pass: cross-event detection (not level comparison), time-stop in trading days.
6. All secrets in `.env`; all strategy/sizing parameters in `config.yaml`; `.gitignore` covers `.env` and `*.db`.
