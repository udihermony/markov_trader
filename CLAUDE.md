# Working in this repo

A swing-trading **practice** environment for non-professional traders. Users assemble strategies from plain-language nodes over pluggable data sources, test them under discipline that keeps results honest, and run them forward in parallel paper wallets alongside an AI copilot.

Read `DESIGN.md` before making architectural decisions. `REVIEW.md` records what went wrong in the POC — those failures are why several rules below exist.

**Status:** rebuilding against `DESIGN.md`. The POC (`legacy/`) has been retired — its role as parity oracle is now `tests/test_backtest_golden.py`, a golden-file regression test against a committed, deterministic price fixture (`tests/golden/`).

---

## Non-negotiable invariants

Violating any of these silently corrupts every result the app produces. They are not style preferences.

**1. Never read data past `as_of`.**
Every component receives `as_of: date` and may not see anything timestamped after it. This is enforced in one place — `sources/` — and nothing outside that package may query raw source data. A convenient direct query against `price_bars` or `observations` from a route handler or UI endpoint is the exact bug that shipped in the POC (`REVIEW.md` #4).

**2. Signals compute on the close of day *t*; fills happen at the open of day *t+1*.**
Filling at the close used to generate the signal is look-ahead bias. Orders are persisted between the two, and the whole day must be idempotent — running it twice must not double-execute.

**3. Slippage always works against the trader.**
Buys fill higher, sells fill lower. If a test ever shows a round trip at an unchanged price making money, something is inverted.

**4. `sources/` is the only data chokepoint.**
Every source declares a trust class, an alignment policy, and a missing-data policy. Every adapter passes the as-of conformance suite before it is registered. No exceptions for "just this one lookup."

**5. AI nodes may never appear in `trigger`.**
Enforced by the spec validator, not by convention. An AI node may veto or rank; it may never originate a trade. Any strategy containing a `live_only` source is marked not-backtestable and can never satisfy a holdout unseal.

**6. The AI copilot cannot mutate wallets.**
It may read them and propose changes. Creating a wallet, approving an order, and spending a holdout unseal are human-only actions.

**7. Wallets are forward-only.**
A wallet cannot be backdated — that would be a backtest in disguise. Wallets are retired, never deleted; deleting losers reintroduces survivorship bias.

**8. Configuration is not mutable global state.**
`.env` holds secrets only. Runtime settings live in the database, scoped to a user. Never write to a config file as a side effect of running something — the POC did exactly this and silently corrupted the user's settings on every backtest (`REVIEW.md` #1).

---

## Tests are the contract

The POC's 22 legacy tests encoded the invariants above and were ported during the rebuild; they are the acceptance criteria, not leftovers — do not weaken what they check.

The M3 gate ("the POC's SMA crossover strategy, re-expressed as a node graph, produces byte-identical backtest results against the same data") is enforced permanently by `tests/test_backtest_golden.py`, not a one-off manual check: it runs the engine over a committed fixture and diffs the real output — fills and final equity/drawdown, not `reason` labels — against a committed golden file on every run. A change that legitimately shifts the numbers regenerates the golden file deliberately (see that file's docstring) and the diff gets reviewed like any other change to committed behavior.

When a test and an implementation disagree about an invariant, the test is right.

---

## Ground rules

- **One milestone at a time.** `DESIGN.md` §9 lists twelve. Build the one you were asked for; its stated demo is the definition of done. Don't scaffold ahead.
- **Honest results over impressive ones.** When a result is weak, small-sample, or contaminated by search, say so in the UI. The product's whole differentiator is being the tool that tells the user their strategy is probably luck. Never round a number in a flattering direction.
- **No jargon reaches the surface.** The user is not a trader. Every control needs a plain-language form. If a concept can't be explained in one sentence, it doesn't get a control.
- **Prediction markets are read-only signal sources.** Polymarket odds inform equity decisions. Nothing in this system places, holds, or settles a prediction-market position. The only tradeable instruments are equities, and only ever in a paper wallet.
- **No real-money trading, no leverage, no shorting.** Out of scope by design, not by omission.
- **Adding a data source should not touch the engine.** If it does, the source abstraction is wrong — fix that instead of special-casing.

## Conventions

- Python: type hints throughout, `from __future__ import annotations`, dataclasses for value types, Pydantic at boundaries.
- All dates `date`, all timestamps `timestamptz`. ISO-8601 everywhere in JSON.
- Every node returns a machine `reason` key *and* a plain-language `explanation`. Both are shown to users somewhere.
- Rejected and skipped candidates are recorded, not dropped. A decision not to trade is as informative as a trade — and in the POC this was specified but never actually wired up (`REVIEW.md` #8).
- Migrations via Alembic, always reversible.
