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

## About the POC

The POC (formerly `legacy/`) proved the engine: `as_of` enforcement, next-open fills, slippage accounting, and within-day idempotency all worked and were covered by 22 tests, ported as the acceptance criteria for the rebuild. Its role as parity oracle for the M3 gate — the POC's SMA crossover strategy, re-expressed as a node graph, producing identical backtest results against the same data — is now `tests/test_backtest_golden.py`, a permanent golden-file regression test, so `legacy/` has been removed. The POC itself is still available at the `v0-poc` tag.
