# Design — v2

A swing-trading practice environment for people who are not professional traders. Users assemble strategies from plain-language blocks, test them under discipline that makes results honest, run them forward in parallel wallets, and work alongside an AI copilot that uses the exact same building blocks they do.

This document supersedes `swing_trading_agent_poc_plan.md`. The POC proved the engine; this is the product built on top of it. See [§8 What survives from the POC](#8-what-survives-from-the-poc) for the migration boundary.

---

## 1. Principles

These are load-bearing. When a feature conflicts with one of these, the principle wins.

**The user is not a trader.** No jargon reaches the surface without a plain-language equivalent. `slippage_bps: 5` becomes "trading friction, about $312 so far." If a concept can't be explained in one sentence, it doesn't get a control.

**Honest evidence over impressive numbers.** Every result is labelled with how much to trust it. A backtest found after 47 searches is worth less than a two-week forward run, and the UI must say so. The app's competitive advantage is being the tool that tells you your strategy is probably luck.

**Nothing is a black box.** The AI uses the same sources and nodes as the user. Every AI action produces an artifact the user can read, edit, re-run, or delete. If the AI can do something the UI cannot express, that is a bug.

**Never trade past `as_of`.** The single hard technical invariant, inherited from the POC. Signals compute on the close of day *t*; fills happen at the open of day *t+1*. Enforced in the data provider, verified by tests. Any component that reads price data goes through `DataProvider.get_bars`.

**Deliberate friction.** No leverage, no shorting, no trending-tickers feed. The app is designed to make users trade less and think more. Inactivity is a valid, well-presented answer.

**Practice money stays visibly practice money.** The play-money nature is part of the visual identity, not a disclaimer. Any future step toward real money is a deliberate, uncomfortable gate.

---

## 2. The evidence pipeline

The organising idea of the whole product. An idea moves through four stages, each producing stronger evidence than the last, each gated by an explicit user action.

```
  BUILD           LAB              HOLDOUT           WALLET
  assemble   →    search      →    one honest   →    forward, real time
  a graph         freely           shot              no cheating possible

  evidence:  none  contaminated    weak but clean    strongest
                   by search
```

**Build** — assembling a strategy produces no evidence at all. Nothing is claimed.

**Lab** — free search over history. Results here are *contaminated by search*: test enough variants and something looks good by chance. Every Lab result carries its search count.

**Holdout** — a period sealed at account creation that neither user nor AI may test against. A finite budget of unseals (default 3). The only honest historical number in the app.

**Wallet** — forward paper trading in real time. Look-ahead is impossible because the future hasn't happened. Slowest, and the only evidence that fully counts.

Promotion between stages is always a human click. The AI may propose; it may not promote.

---

## 3. Surfaces

Four surfaces plus a persistent copilot panel. Each maps to a pipeline stage.

### Today (home)

What happens next, across every wallet. The daily-return surface.

- Pending orders for tomorrow's open, one card each: action, size, and a one-line plain reason ("the 10-day trend crossed above the 20-day yesterday, first time in six weeks").
- Approve / Skip per order. Skips are logged and scored — the override ledger (§3.5) is built from them.
- What changed since yesterday, per wallet.
- "Nothing to do today" is a first-class, calm empty state. Most days it is the correct answer.

### Strategies

The strategy builder and library.

- A strategy renders as a **funnel**: stacked nodes, each showing a live count of how many candidates survive it (`503 → 10 → 3 → 2 → 1`), plus a missing-data count where a node's source has partial coverage. The counts are the teaching mechanism — add a node that's too strict and you see `2 → 0` immediately.
- Nodes are added, removed, and reordered within their kind. Kinds are fixed (§4.5).
- Each strategy shows its **trust label**, derived from the weakest source it reads (§4.2) — this is what tells the user whether a backtest of it means anything.
- A complexity meter: more nodes and more parameters means more overfitting risk. The builder gets progressively more insistent about holdout testing as the count grows.
- An advanced **canvas view** of the same graph, for branching and weighted scoring (§4.10). Later milestone; users who never open it lose nothing.
- Library of saved strategies with their provenance — who built it (user or AI), what evidence stage it has reached.
- Presets for first-time users: three starting strategies with honest descriptions of how they behave and when they fail.

### Lab

The experiments notebook. Where ideas are tested and mostly die.

- **Every experiment requires a hypothesis before it runs** — a question with an expected answer, not a title. This is the primary brake on blind search, for humans and AI alike.
- Experiment list: hypothesis, strategy diff, period, result, who ran it, whether the prediction was right.
- **Search counter with a luck baseline**: "47 experiments; your best (+14.2%) is what pure chance produces at this count."
- **Sealed holdout panel**: the sealed date range, unseals remaining, and a deliberate confirmation to spend one.
- Parameter neighbourhood view: a heatmap of results for nearby settings. A lone bright cell surrounded by red is overfitting, visible without statistics vocabulary.
- Luck test: re-run the strategy N times with shuffled entry dates, show where the real result falls in the distribution.
- Unattended AI runs appear here as they complete, with a digest rather than a dump.

### Wallets

Parallel forward paper-trading accounts. Each wallet is a commitment: one strategy, a starting balance, a start date.

- A wallet cannot be backdated. A wallet that starts today is honest; a backdated one is a backtest wearing a costume.
- **A SPY buy-and-hold wallet is created by default and cannot be deleted.** The benchmark competes as a peer under identical rules — same slippage, same whole-share rounding — rather than existing as a computed line.
- Wallets can be **retired but never deleted**. Deleting losers would reintroduce survivorship bias through the back door.
- Comparison view across wallets: the honest leaderboard, forward-only.
- Per-wallet: equity curve vs SPY, open positions as physical slots (filled and empty, making position limits self-explanatory), trade history with a plain-language reason on every row, friction meter (cumulative slippage paid), and idle-cash percentage.

### Copilot panel

A side panel available on every surface, context-aware of what the user is looking at. Not a tab. Details in §5.

### 3.5 Cross-cutting behavioural features

These appear across surfaces and are core to the product thesis:

- **Override ledger.** Approve/Skip decisions on Today are scored over time: "You skipped 6 signals this month; they would have made $1,400." Works in both directions.
- **Flinch test.** During backtest replay, pause at the worst drawdown and ask the user what they would have done, then reveal what happened.
- **Friction meter.** Cumulative slippage as a share of P&L. Makes the invisible cost visible.
- **Replay scrubber.** Drag through a backtest day by day; the equity curve draws itself and trades appear on the price chart. Framed as a time machine, not a "backtest."
- **Report card.** Strategy results graded in plain language against four questions: did it beat doing nothing, is it real or luck, how often was it right, could you have stomached it.

---
## 4. Data sources and strategies

The central abstraction, and the thing that unblocks everything else. The POC's monolithic `generate_signal(bars, position, as_of) -> Signal` is replaced by two decoupled layers: **sources** that publish point-in-time features, and a **strategy graph** of nodes that read those features and decide.

The decoupling is what makes X, Polymarket, and anything else additive. A new data source is a registry entry, not a change to the strategy model.

### 4.1 Sources and features

A source publishes named features into a namespace. Nodes reference features by expression; they never touch a provider or a table directly.

```
px.open  px.high  px.low  px.close  px.volume     -- price_bars
x.sentiment  x.mention_count                       -- x_firehose
pm.prob  pm.volume                                 -- polymarket
news.headline_count  news.tone                     -- news_archive
fund.next_earnings_date                            -- corporate_events
```

Derived features are expressions over raw ones, using a small fixed vocabulary — `sma`, `ema`, `rsi`, `atr`, `pct_change`, `rank`, `zscore`, `rolling_mean`:

```
sma(px.close, 20)
zscore(x.sentiment, 30)
pct_change(pm.prob, 5)
```

Two things fall out of this for free. **Warmup is computed, not declared** — the expression parser knows `sma(px.close, 20)` needs 20 bars, so a strategy's history requirement is derived from its expressions rather than the POC's hardcoded `min_history_days`. And **nodes become generic**: a `cross` node over two arbitrary expressions replaces a bespoke `sma_cross` block, and the same node works on sentiment or prediction-market odds without new code.

A source registry entry declares:

```python
@dataclass(frozen=True)
class SourceSpec:
    id: str                       # "polymarket"
    features: dict[str, FeatureSpec]
    trust_class: TrustClass       # §4.2
    native_frequency: str         # "daily" | "intraday" | "event"
    alignment: AlignmentPolicy    # §4.3
    coverage_note: str            # shown in the UI when coverage is partial
```

### 4.2 Trust classes

Sources differ enormously in whether history can be replayed honestly. This is a property of the source, and it propagates: **a strategy is only as trustworthy as its weakest source**, and the UI labels it accordingly.

| Class | Meaning | Examples |
|---|---|---|
| `point_in_time` | The historical value at time *T* is a recorded fact. Fully backtestable. | price bars, Polymarket odds, corporate events with announcement timestamps |
| `reconstructable` | History exists but must be carefully rebuilt with publication timestamps. Backtestable with effort and caveats. | news archives, X historical (paid API), index membership history |
| `live_only` | History cannot be replayed honestly. Forward-only. | LLM judgments (§5.2) |

Polymarket is worth calling out as unusually good material: the odds at time *T* are a fact about what the market believed then, not a memory of what happened after. Unlike an LLM, it can be backtested cleanly.

**Prediction markets are read-only signal sources.** Polymarket odds are an input to equity decisions — nothing in this system places, holds, or settles a position on a prediction market. The only tradeable instruments are equities, and only ever in a paper wallet.

A strategy containing a `live_only` source can never satisfy a holdout unseal. One containing only `point_in_time` sources gets the strongest available label.

### 4.3 Alignment and missing data

Two policies every source must declare. Both are places where look-ahead and silent bias hide, so both are explicit rather than defaulted.

**Alignment.** Price is daily bars; sentiment and prediction-market odds are continuous streams. The rule is uniform and enforced centrally, in the same layer that enforces the price guard today: *the value of a feature on day t is its last observation strictly before the day-t close.* Sources with event frequency (earnings announcements) additionally declare a validity window. No node performs its own join.

**Missing data.** Coverage is never universal — X has signal for NVDA and nothing for a mid-cap industrial; Polymarket has markets for a handful of events. A node conditioned on absent data silently shrinks the universe, so every node declares:

- `on_missing: "fail_open"` — condition ignored, candidate passes through
- `on_missing: "fail_closed"` — treated as a failed condition

The two produce genuinely different strategies and users will never guess which they got, so the funnel shows the count of candidates that hit missing data at each node, and the report card flags any strategy where a large fraction of decisions were made on absent data.

### 4.4 The strategy graph

A strategy is a **directed acyclic graph** of nodes. The default shape — and everything the v1 editor produces — is linear, but the stored form is a graph from day one so that branching, weighted scoring, and sub-strategies need no migration later.

```
  price ─────┐
  x ─────────┼──▶ features ──▶ trigger ──▶ confirm ──▶ veto ──▶ size ──▶ order
  polymarket ┘                                 ▲
                                        (any node may read any namespace)
```

Sources fan in; the decision chain is linear by default. Evaluation is a topological sort. Cycles are rejected by the validator.

### 4.5 Node kinds

| Kind | Question | Default combination | Required |
|---|---|---|---|
| `universe` | What do I watch? | chained (each narrows) | yes |
| `trigger` | When do I buy? | OR (any fires → candidate) | yes |
| `confirm` | What must also be true? | AND (all must pass) | no |
| `veto` | When do I never buy? | OR (any veto blocks) | no |
| `exit` | When do I sell? | OR (any fires → sell) | yes |
| `size` | How much? | single node | yes |
| `score` | Weighted combination *(canvas only, later)* | weighted sum | no |

Kinds constrain what a node may connect to, which is what keeps the graph comprehensible and lets the funnel editor exist at all. Exits are evaluated independently for held positions, not as part of the entry chain.

### 4.6 Strategy spec

Versioned JSON — the shared contract between the UI, the AI tool surface, and the engine. All three read and write the same structure.

```json
{
  "spec_version": 2,
  "name": "Momentum swing",
  "sources": [
    {"id": "px", "type": "price_bars"},
    {"id": "sent", "type": "x_firehose", "params": {"window_days": 7}},
    {"id": "pm", "type": "polymarket"}
  ],
  "nodes": [
    {"id": "u1", "kind": "universe", "type": "index_membership",
     "params": {"index": "SP500"}},
    {"id": "u2", "kind": "universe", "type": "liquidity_filter",
     "params": {"min_price": 20, "min_avg_volume": 500000}},

    {"id": "t1", "kind": "trigger", "type": "cross",
     "params": {"a": "sma(px.close, 10)", "b": "sma(px.close, 20)", "direction": "up"}},

    {"id": "c1", "kind": "confirm", "type": "threshold",
     "params": {"feature": "zscore(sent.sentiment, 30)", "op": ">", "value": 0.5},
     "on_missing": "fail_open"},
    {"id": "c2", "kind": "confirm", "type": "market_regime",
     "params": {"benchmark": "SPY", "condition": "above", "feature": "sma(px.close, 200)"}},

    {"id": "v1", "kind": "veto", "type": "earnings_blackout",
     "params": {"days_before": 3, "days_after": 1}, "on_missing": "fail_closed"},
    {"id": "v2", "kind": "veto", "type": "threshold",
     "params": {"feature": "pm.prob", "op": "<", "value": 0.4},
     "on_missing": "fail_open"},

    {"id": "x1", "kind": "exit", "type": "cross",
     "params": {"a": "sma(px.close, 10)", "b": "sma(px.close, 20)", "direction": "down"}},
    {"id": "x2", "kind": "exit", "type": "time_stop", "params": {"max_hold_days": 5}},

    {"id": "s1", "kind": "size", "type": "fixed_fraction",
     "params": {"fraction": 0.10, "max_positions": 8, "min_notional": 500}}
  ],
  "edges": [["u1","u2"], ["u2","t1"], ["t1","c1"], ["c1","c2"],
            ["c2","v1"], ["v1","v2"], ["v2","s1"]],
  "costs": {"slippage_bps": 5}
}
```

Edges are always explicit, and describe the **entry** chain only — `exit` nodes are deliberately unwired, because they are evaluated per held position rather than as part of candidate selection (§4.5). The funnel editor generates the linear chain; the canvas editor writes arbitrary DAGs. Validated with Pydantic: cycles rejected, kind-ordering enforced, feature expressions parsed and checked against the declared sources, and AI-backed node types rejected in `trigger` (§5.2).

`spec_version: 2` is the graph form. Version 1 (flat slots) is not shipped; the POC's strategy is migrated directly to version 2.

### 4.7 Node interfaces

```python
@dataclass(frozen=True)
class NodeContext:
    features: FeatureView     # resolves expressions, as_of-bounded, never raw providers
    as_of: date
    ticker: str
    position: Position | None
    portfolio: PortfolioView

@dataclass(frozen=True)
class NodeResult:
    passed: bool
    reason: str               # machine key, e.g. "cross_up"
    explanation: str          # plain language, shown to the user
    missing: list[str]        # features that were unavailable
    metadata: dict

class UniverseNode(Protocol):
    def filter(self, candidates: list[str], as_of: date) -> list[str]: ...

class DecisionNode(Protocol):             # trigger, confirm, veto, exit
    def evaluate(self, ctx: NodeContext) -> NodeResult: ...

class SizeNode(Protocol):
    def size(self, ctx: NodeContext) -> float: ...    # cash amount, 0 = skip
```

Nodes are stateless and pure. `FeatureView` is the single chokepoint for data access — the successor to `DataProvider.get_bars` and the place the `as_of` invariant is enforced for *every* source, not just price.

### 4.8 Initial node and source library

**Sources (v1):** `price_bars`, `corporate_events`, `finviz_screen`. **Planned:** `x_firehose`, `polymarket`, `news_archive`.

**Generic nodes** — work over any feature expression, which is what makes new sources free: `cross`, `threshold`, `rank_top_n`, `percentile`, `sustained` (condition true for N days).

**Universe:** `index_membership`, `liquidity_filter`, `sector_filter`, `manual_list`, `finviz_screen`

**Composite convenience nodes:** `breakout`, `pullback`, `momentum`, `market_regime`, `volume_surge`, `earnings_blackout`, `recent_runup`

**Exit:** `cross`, `time_stop`, `stop_loss`, `trailing_stop`, `profit_target`

**Size:** `fixed_fraction`, `equal_weight`, `volatility_scaled`

**AI:** `ai_news_check`, `ai_regime_check` — `veto` and ranking only (§5.2)

Every node carries a maturity label — **standard**, **experimental**, or **AI** — shown alongside the trust class of the sources it reads.

### 4.9 Combining strategies

Three forms, deliberately not treated equally.

**Layering — supported, and the default meaning of "combine."** Two ideas become trigger plus confirm nodes in one graph. No new concepts; it is just the builder.

**Portfolio of strategies — supported via wallets.** Run momentum in one wallet and mean-reversion in another. They lose money at different times, which is the actual benefit. This falls out of the wallet model for free.

**Ensemble voting — deferred.** The `score` node kind reserves space for it, but it ships with the canvas, not v1. Weighted voting doubles the parameter count and makes the weights one more thing to overfit.

The rule the UI enforces: every added node is another knob. The complexity meter counts free parameters across the whole graph, and the holdout nag scales with it.

### 4.10 Two editors, one spec

**Funnel (v1 default).** A vertical stack rendered from the linear graph, with live candidate counts between nodes (`503 → 10 → 3 → 2 → 1`) and a missing-data count where relevant. Add, remove, and reorder within a kind. The counts are the teaching mechanism — a node that's too strict shows `2 → 0` immediately.

**Canvas (later milestone).** A node-graph view of the same spec, behind an advanced toggle: branching exits, `score` nodes, sub-strategies, multiple sources wired explicitly. Users who never open it lose nothing.

Because both editors read and write the same graph, a strategy built on the canvas still renders in the funnel whenever its topology happens to be linear — and shows a "this strategy uses the advanced view" notice when it doesn't.

## 5. The AI copilot

### 5.1 Two placements

**Copilot (outside the loop)** — the chat panel. Builds strategies, analyses runs, proposes and executes experiments, explains results. Its *output is a deterministic strategy graph*: nodes, parameters, and feature expressions — fully backtestable, fully inspectable. The AI never makes a trade decision; it shapes the design. **This is the primary AI surface and is built first.**

**Agent node (inside the loop)** — `ai_news_check` and friends, evaluated per-decision. Powerful but structurally compromised (§5.2). These are simply nodes whose source has trust class `live_only` (§4.2), restricted to `veto` and ranking kinds: an AI node may *prevent* a trade or *order* candidates, never originate one. A black box that can only make you trade less has bounded downside.

### 5.2 The look-ahead problem with AI nodes

**An LLM cannot be backtested.** Asked "should I buy NVDA on 2026-03-15," a model trained past that date knows the answer. The `as_of` invariant that the entire data layer is built around is worthless against memorised history.

This is not a special case — it is the `live_only` trust class (§4.2) taken to its extreme, and it uses the same machinery. Three mitigations, all required:

1. **Segregate and label.** Any strategy containing a `live_only` source is marked *not backtestable*. Lab runs either disable those nodes (and say so) or run both with and without, labelling the AI variant as indicative only. Such strategies can never satisfy a holdout unseal.
2. **Record, don't replay.** Every AI node judgment made in a live wallet is persisted to `ai_judgments` with its `as_of` date and full input context. After months of forward running these become real point-in-time decisions that *can* be replayed honestly — at which point the source is promoted from `live_only` to `reconstructable` for that date range. This is the same pattern the screener already uses to accumulate replayable history.
3. **Keep AI out of `trigger`.** Enforced by the spec validator, not by convention.

**Cost shape.** One LLM call per ticker per day over six months is 1,200+ calls. AI nodes sit near the bottom of the funnel by design, so they see 1–3 candidates rather than 500. Wallet-level AI budgets are enforced, and the estimated daily cost is shown before a wallet with AI nodes is started.

### 5.3 Tool surface

The copilot's tools are the same API the frontend uses. No privileged path.

| Tool | Notes |
|---|---|
| `list_strategies`, `get_strategy` | read |
| `create_strategy`, `update_strategy` | returns a diff for user review; never auto-saves over a user's strategy |
| `validate_strategy` | spec check + complexity score before running anything |
| `run_backtest` | requires a `hypothesis` and `expected_outcome` argument — enforced, not optional |
| `list_experiments`, `get_experiment` | read the notebook |
| `run_luck_test`, `run_neighbourhood_scan` | the honesty tools |
| `list_wallets`, `get_wallet`, `get_wallet_trades` | **read-only** |
| `propose_wallet` | queues a proposal; a human creates the wallet |
| `get_market_context` | point-in-time news/regime, for explanation |

The AI may not create, modify, or delete wallets, may not approve pending orders, and may not spend a holdout unseal. Those are human-only actions by design.

### 5.4 Unattended experiments

The AI can be given a goal and left to run. Two mechanics make this safe rather than an industrial overfitting machine:

**Predict before running.** Every experiment records the AI's hypothesis *and* its expected outcome before execution. The notebook then records whether it was right, producing a **calibration score for the copilot itself**: "Claude predicted 12 outcomes and got 4 right." The AI is held to the standard it holds strategies to.

**Report a digest, not a dump.** "I ran 12 experiments. Ten were dead ends. Two are worth your attention. The pattern: every version that helps the hit rate cuts trade count below 20, so none of them are conclusive yet." Dead ends stay in the notebook, searchable, but don't demand attention.

Supporting constraints: per-session experiment budget, per-user token budget with visible spend, every unattended run in the audit trail, and the search counter promoted from advisory to load-bearing — past a threshold the copilot must stop searching and recommend a holdout test.

### 5.5 Copilot behaviour

- **Renders strategies, not prose.** A proposal appears as the actual editable funnel card inline in chat — the same component as the builder.
- **Every change is a reviewable diff.** "slow: 20 → 50, removed earnings veto." One-click undo.
- **It must be willing to say no.** A copilot that builds whatever it's asked and calls it promising is worse than none. Pushing back on a doomed idea is the feature, not a failure to comply.
- **Context-aware.** The panel knows which wallet, strategy, or run is on screen, so "why did this lose money in March" needs no setup.

### 5.6 Provider abstraction

A `LLMProvider` interface with `complete(messages, tools) -> Response`. Anthropic is the first and only implementation in v1; OpenAI and a local (Ollama) adapter follow. Model tier is configurable per operation — cheap models for explanation and narration, stronger ones for strategy design and experiment planning. Keys are per-user, encrypted at rest, never logged.

---

## 6. Data model

Postgres, multi-user from day one. Key decisions:

**Source data is shared, not per-user.** Market data, sentiment, and prediction-market odds are public; duplicating them per user would be wasteful and slow. The cache warms once for everyone.

**Everything else is user-scoped**, with row-level isolation enforced in the data access layer.

```
users(id, email, password_hash, created_at)
api_keys(id, user_id, provider, encrypted_key)          -- BYO LLM keys

-- shared source data (no user_id)
instruments(id, ticker, name, sector, exchange)
price_bars(instrument_id, date, open, high, low, close, volume, PK(instrument_id, date))
corporate_events(instrument_id, date, type, payload)     -- earnings dates etc.
screen_results(id, screen_date, ticker, rank, source)    -- successor to watchlist_history

sources(id, source_type, trust_class, native_frequency, config_json, enabled)
observations(id, source_id, instrument_id, observed_at, valid_from, valid_to,
             feature, value, PK(source_id, instrument_id, feature, observed_at))

-- user-scoped
strategies(id, user_id, name, spec_json, spec_version, created_by, parent_id, created_at)
holdouts(id, user_id, start_date, end_date, unseals_total, unseals_used, created_at)

experiments(id, user_id, strategy_id, hypothesis, expected_outcome, actual_outcome,
            prediction_correct, period_start, period_end, initiated_by, status,
            result_json, created_at)

wallets(id, user_id, name, strategy_id, initial_cash, cash, start_date,
        status, is_benchmark, created_at, retired_at)
positions(id, wallet_id, ticker, shares, avg_entry_price, entry_date, entry_reason,
          UNIQUE(wallet_id, ticker))
orders(id, wallet_id, created_date, ticker, action, cash_amount, reason,
       metadata_json, status, user_decision)             -- approve/skip lives here
fills(id, wallet_id, timestamp, ticker, action, shares, fill_price,
      cost_bps_applied, reason, metadata_json)
skipped_signals(id, wallet_id, date, ticker, stage, reason, metadata_json)
equity_snapshots(id, wallet_id, date, cash, positions_value, total_equity)

ai_judgments(id, wallet_id, as_of, node_type, ticker, input_context_json,
             output_json, model, cost_usd)               -- for honest future replay
jobs(id, user_id, type, payload_json, status, progress, result_json,
     created_at, started_at, finished_at)
```

Notes:

- `observations` is the generic landing table for every non-price source. `observed_at` is when the value became knowable — the column the as-of join reads (§4.3). `valid_from`/`valid_to` carry event-shaped sources like earnings announcements. Price stays in its own narrow table for query performance.
- `sources.trust_class` propagates to every strategy that reads it, which is what drives the trust label in the UI (§4.2).
- `positions` unique constraint is `(wallet_id, ticker)` — the POC's global `ticker UNIQUE` is what blocks parallel wallets.
- `skipped_signals` is a first-class table, not a `SKIP` row in the trade log. The POC logged skips into `trade_log` and in practice never wrote any; a dedicated table with an explicit funnel `stage` — and a `missing_data` flag — fixes both the plumbing and the analysis.
- `orders` carries `user_decision` so the override ledger is a query, not a separate system.
- `strategies.parent_id` gives lineage, so the Lab can show how a strategy evolved.
- Benchmark equity is no longer a column on the snapshot table — the SPY wallet is a real wallet.
- All timestamps `timestamptz`, all dates `date`. Migrations via Alembic.

---

## 7. Architecture and stack

```
frontend/          React 18 + TypeScript + Vite + Tailwind + shadcn/ui
                   TanStack Query for server state, Recharts for charts
backend/
  api/             FastAPI — REST, auth, SSE for job progress and chat streaming
  engine/          node registry, graph validation + evaluation, sandbox, orchestrator
  sources/         source registry, adapters (yfinance, finviz, x, polymarket),
                   feature expression parser, as-of join, the single data chokepoint
  ai/              provider abstraction, tool definitions, copilot loop
  worker/          job runner: backtests, unattended experiments, daily wallet runs
  db/              SQLAlchemy models, Alembic migrations
```

`sources/` is deliberately its own package rather than living under `engine/`. It owns the `as_of` invariant for *every* source, and nothing outside it may query raw source data. Adding X or Polymarket means adding an adapter here and a registry entry — no engine changes.

**Database:** Postgres 16. SQLite is dropped — multi-user with concurrent background jobs is where it stops being appropriate.

**Job queue:** Postgres-backed using `SELECT ... FOR UPDATE SKIP LOCKED`. Battle-tested, and avoids running Redis for a workload this size. Swap to Redis + RQ if throughput ever demands it. The POC's single `threading.Lock` does not survive.

**Scheduling:** APScheduler in the worker process. Daily wallet runs fire after market close; each wallet is an independent job.

**Auth:** email + password (argon2), JWT sessions. OAuth later. Per-user row isolation enforced in a repository layer, not left to individual queries.

**Deployment:** Docker Compose — `postgres`, `api`, `worker`, `frontend`. Single command to run the whole thing locally; the same compose file is the basis for a hosted deployment.

**Config:** `.env` for secrets only (DB URL, JWT secret, encryption key). Runtime settings move into the database as user preferences. **The `config.yaml`-as-mutable-global-state pattern is removed entirely** — it was the source of the worst bug in the current codebase, where running a backtest silently rewrote the file.

**Testing:** pytest for the engine (the POC's tests port over as the transplant's proof), contract tests for every node against the spec registry, and an as-of conformance suite that every source adapter must pass before registration — the generalisation of `test_as_of_guard` to all sources. Vitest + Playwright for the frontend.

---

## 8. What survives from the POC

**Keep and port** — these encode subtle correctness that is easy to get wrong on a second attempt:

- `data_provider.py` — the `as_of` guard, cache-fill logic, per-ticker failure isolation. Becomes the `price_bars` adapter inside `sources/`, and its guard generalises into the shared as-of join every source inherits.
- `sandbox.py` — accounting, slippage direction, whole-share flooring, rejection paths. Refactored to be wallet-scoped.
- `screener.py` — Finviz retry/timeout handling and the as-of watchlist replay (recently corrected to `screen_date <= as_of`). Becomes the `finviz_screen` source adapter; its record-then-replay pattern is the template every `reconstructable` source follows.
- `orchestrator.py` — the `run_day` sequencing: pending orders at today's open, exits before entries, next-open fills, within-day idempotency. Refactored to evaluate a node graph rather than a single strategy object.
- `tests/` — all 22 tests. **They are the acceptance criteria for the transplant.** If `test_as_of_guard` and the crossover tests pass against the new engine, the invariants survived.

**Rewrite:**

- `strategy/` → the source registry and node graph. The monolithic `generate_signal` Protocol is the thing blocking the builder, multi-source data, combining, and AI.
- `api.py` → new FastAPI app. Its bugs are structural (mutable global config, no wallet concept, thread lock instead of a queue), so patching costs more than replacing.
- `static/index.html` → React. Vanilla JS will not carry a reorderable node builder with live counts, a chat panel rendering interactive cards, and diff views.
- Schema → Postgres with wallets, experiments, holdouts.

**Delete:**

- `dashboard.py` — Streamlit, redundant with the web UI.
- `config.yaml` as runtime state — see §7.
- The multi-DB-file scheme (`swing_paper.db`, `swing_backtest.db`, `backtests/*.db` + `registry.json`) — replaced by proper tables.

Known bugs from `REVIEW.md` that this design resolves structurally rather than by patch: config mutation on backtest (#1), per-run `initial_cash` (#2), `run_id` mismatch (#3), unbounded price lookup in the positions view (#4), watchlist as-of semantics (#5, already fixed), trade ordering (#6), and dead SKIP logging (#8).

---

## 9. Build order

Each milestone is independently demoable. No milestone requires the next one to be useful.

**M1 — Foundation.** Postgres schema, Alembic, auth, Docker Compose, engine ported behind the new data layer, all POC tests passing. *Demo: `pytest` green against Postgres; a backtest runs from the CLI.*

**M2 — Sources and features.** Source registry, `price_bars` and `finviz_screen` adapters, feature expression parser with derived warmup, the shared as-of join, trust classes, the as-of conformance suite. *Demo: `sma(px.close, 20)` resolves through the registry, and a deliberately broken adapter fails conformance.*

**M3 — Strategy graph.** Node registry, graph spec + validator (cycles, kind ordering, expression checks, AI-in-trigger rejection), topological evaluation in the orchestrator, initial node library, contract tests. *Demo: the POC's SMA strategy expressed as a graph produces identical backtest results — the strongest possible regression test.*

**M4 — Wallets.** Wallet CRUD, wallet-scoped sandbox, daily scheduled runs, default SPY benchmark wallet, retire-not-delete. *Demo: three wallets running different strategies forward in parallel.*

**M5 — Frontend shell.** React app, auth, Today and Wallets surfaces, order approve/skip, equity charts. *Demo: a usable daily-practice app.*

**M6 — Funnel builder.** The funnel UI with live counts and missing-data counts, node add/remove/reorder, plain-language sentences, trust labels, presets, complexity meter. *Demo: build a strategy without touching JSON.*

**M7 — Lab.** Experiments with mandatory hypotheses, search counter and luck baseline, sealed holdout with unseal budget, neighbourhood scan, luck test, report card. *Demo: get told your strategy is probably luck.*

**M8 — Copilot.** Provider abstraction, tool surface, chat panel with inline strategy cards and diffs, explanation and analysis. *Demo: describe a strategy in English and get an editable funnel.*

**M9 — Unattended experiments.** Background experiment jobs, predict-before-run, calibration scoring, digest reporting, budgets. *Demo: ask a question, walk away, come back to a digest.*

**M10 — AI nodes.** `ai_news_check`, `ai_regime_check`, the `live_only` labelling path, `ai_judgments` recording, per-wallet cost budgets.

**M11 — New sources.** `polymarket` first — it is `point_in_time`, so it is genuinely backtestable and proves the registry without the caveats. Then `x_firehose` and `news_archive` as `reconstructable`. *Demo: a strategy whose confirm node reads prediction-market odds, backtested honestly.*

**M12 — Canvas.** The node-graph editor behind the advanced toggle: branching, `score` nodes, explicit multi-source wiring. Reads and writes the same spec the funnel already produces.

Behavioural features (override ledger, flinch test, friction meter, replay scrubber) slot in alongside M5–M7 as their host surfaces land.

The ordering point worth noting: **M2 and M3 are the whole bet.** Once sources and the graph are decoupled, every later milestone is additive — a new data source is an adapter, a new capability is a node type, and neither touches the engine.

---

## 10. Open questions

1. **Naming.** "Markov Trader" describes an implementation detail that isn't in the code, and means nothing to the target user.
2. **Source availability.** yfinance and Finviz are scrapers and will break. An earnings calendar is needed for `earnings_blackout` and isn't currently sourced. X historical access is a paid API at meaningful cost; whether it's worth it should be decided before M11, not during.
3. **Index membership history.** Backtesting `index_membership` correctly requires knowing S&P 500 constituents *as of* each date. Currently unavailable — the documented survivorship-bias limitation from the POC persists until this is sourced.
4. **Polymarket ↔ equity mapping.** Prediction markets resolve on events, not tickers. Linking "will NVDA beat earnings" to the NVDA instrument needs an explicit mapping table, and most equities will have no market at all — making `on_missing` policy load-bearing rather than a detail.
5. **Feature expression scope.** The vocabulary (`sma`, `zscore`, …) is deliberately small. Where it stops is a real decision: too small and users hit walls, too large and it becomes a programming language the target user can't read.
6. **Holdout policy.** Is the sealed period fixed per user, per strategy, or rolling? Fixed per user is simplest and hardest to game.
7. **Wallet cadence.** Daily runs assume the user checks in daily. Weekly wallets may suit the target user better.
8. **Real money.** Explicitly out of scope, but the gate should be designed before anyone asks for it.
