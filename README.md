# Markov Trader

A swing-trading **practice** environment for people who are not professional traders. Users assemble strategies from plain-language nodes over pluggable data sources, test them under discipline that keeps results honest, and run them forward in parallel paper wallets alongside an AI copilot that uses the same building blocks they do.

No real money, no leverage, no shorting — by design.

## Status

Rebuilding against [`DESIGN.md`](DESIGN.md). Nothing in the new architecture is implemented yet.

| Document | What it is |
|---|---|
| [`DESIGN.md`](DESIGN.md) | The v2 design: principles, surfaces, source and node model, AI copilot, schema, stack, twelve milestones |
| [`CLAUDE.md`](CLAUDE.md) | Non-negotiable invariants and ground rules — read before changing anything |
| [`REVIEW.md`](REVIEW.md) | Code review of the POC; several design decisions exist because of these failures |
| `legacy/` | The working POC. Reference implementation and test oracle — not the codebase |

## About `legacy/`

The POC proved the engine: `as_of` enforcement, next-open fills, slippage accounting, and within-day idempotency all work and are covered by 22 tests. Those tests are the **acceptance criteria for the rebuild**, not leftovers.

The gate for milestone M3 is that the POC's SMA crossover strategy, re-expressed as a node graph, produces identical backtest results against the same data. `legacy/` is deleted once that passes.

To run the POC in the meantime:

```bash
cd legacy
pip install -r requirements.txt
python main.py backtest --start 2026-01-05 --end 2026-06-30
python -m pytest tests/
```

Tagged `v0-poc`.
