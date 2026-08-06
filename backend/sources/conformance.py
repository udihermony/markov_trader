"""The as-of conformance suite (CLAUDE.md rule 4, DESIGN.md §7).

`assert_as_of_conformant` is a plain function, not a pytest mixin — each
adapter's test supplies concrete seed/read closures using the project's
existing `db_session` fixture.

"Every adapter passes the as-of conformance suite before it is registered"
(CLAUDE.md) is interpreted as a *test-suite gate*: a new adapter's
conformance test must be green in the standard `pytest` run before a
`register_*_source()` call is wired into anything that runs for real — not a
runtime check inside `SourceRegistry.register()`, which has no DB session
available at registration time and would need to fabricate one. Nothing
outside tests calls a `register_*_source()` yet in M2 (M3's orchestrator
setup is the first real caller), so this is a convention enforced by the
test suite, not app-startup code.
"""
from __future__ import annotations

from datetime import date
from typing import Callable, TypeVar

T = TypeVar("T")


class AsOfViolation(AssertionError):
    pass


def assert_as_of_conformant(
    *,
    seed_prior: Callable[[], None],
    seed_future: Callable[[], None],
    read_as_of: Callable[[date], T],
    cutoff: date,
    extract_max_date: Callable[[T], date | None],
) -> None:
    seed_prior()
    seed_future()
    result = read_as_of(cutoff)
    max_date = extract_max_date(result)
    if max_date is not None and max_date > cutoff:
        raise AsOfViolation(f"leaked data dated {max_date} past cutoff {cutoff}")
