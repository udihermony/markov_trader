"""A validated `StrategySpec` compiled into something the orchestrator can
evaluate. Evaluation is a topological sort over the entry chain (DESIGN.md
§4.4); exit nodes are evaluated independently, unwired, first-fire-wins."""
from __future__ import annotations

from dataclasses import replace
from datetime import date

from backend.engine.graph.registry import build_node
from backend.engine.graph.spec import NodeSpec, StrategySpec
from backend.engine.graph.types import FeatureView, NodeContext, NodeResult, Position, PortfolioView
from backend.engine.graph.validator import validate_spec
from backend.sources.registry import SourceRegistry


def _topological_order(node_ids: list[str], edges: list[tuple[str, str]]) -> list[str]:
    in_degree = {nid: 0 for nid in node_ids}
    adjacency: dict[str, list[str]] = {nid: [] for nid in node_ids}
    for a, b in edges:
        if a in adjacency and b in in_degree:
            adjacency[a].append(b)
            in_degree[b] += 1
    queue = [nid for nid, deg in in_degree.items() if deg == 0]
    order: list[str] = []
    while queue:
        current = queue.pop(0)
        order.append(current)
        for nxt in adjacency[current]:
            in_degree[nxt] -= 1
            if in_degree[nxt] == 0:
                queue.append(nxt)
    return order


def _apply_on_missing(result: NodeResult, node_spec: NodeSpec) -> NodeResult:
    """DESIGN.md §4.3's node-level missing-data policy: if the node reported
    missing features and declared an on_missing policy, that policy
    overrides the raw passed/failed outcome."""
    if result.missing and node_spec.on_missing:
        return replace(result, passed=(node_spec.on_missing == "fail_open"))
    return result


class CompiledGraph:
    def __init__(self, spec: StrategySpec, registry: SourceRegistry):
        validate_spec(spec)
        self.spec = spec
        self.registry = registry
        self.source_aliases = {s.id: s.type for s in spec.sources}
        self._node_specs = {n.id: n for n in spec.nodes}
        self._nodes = {n.id: build_node(n, registry) for n in spec.nodes}

        entry_ids = [n.id for n in spec.nodes if n.kind != "exit"]
        order = _topological_order(entry_ids, spec.edges)

        self._universe_order = [nid for nid in order if self._node_specs[nid].kind == "universe"]
        self._decision_order = [
            nid for nid in order if self._node_specs[nid].kind in ("trigger", "confirm", "veto")
        ]
        size_ids = [nid for nid in order if self._node_specs[nid].kind == "size"]
        self._size_id = size_ids[0]  # validate_spec guarantees exactly one

        self._exit_ids = [n.id for n in spec.nodes if n.kind == "exit"]

    def _ctx(self, ticker: str, as_of: date, position: Position | None, portfolio: PortfolioView) -> NodeContext:
        return NodeContext(
            features=FeatureView(self.registry, self.source_aliases, ticker, as_of),
            as_of=as_of, ticker=ticker, position=position, portfolio=portfolio,
        )

    def candidates(self, as_of: date) -> list[str]:
        result: list[str] = []
        for nid in self._universe_order:
            result = self._nodes[nid].filter(result, as_of)
        return result

    def evaluate_entry(self, ticker: str, as_of: date, portfolio: PortfolioView) -> NodeResult:
        """Walks trigger/confirm/veto nodes in topological order,
        short-circuiting on the first failure — for a strictly linear graph
        (the only shape M3 supports) this is exactly equivalent to
        confirm's AND-combination and veto's OR-combination."""
        result = NodeResult(passed=True, reason="no_decision_nodes", explanation="No trigger/confirm/veto nodes.")
        for nid in self._decision_order:
            ctx = self._ctx(ticker, as_of, None, portfolio)
            result = _apply_on_missing(self._nodes[nid].evaluate(ctx), self._node_specs[nid])
            if not result.passed:
                return result
        return result

    def evaluate_exit(
        self, ticker: str, position: Position, as_of: date, portfolio: PortfolioView
    ) -> NodeResult | None:
        for nid in self._exit_ids:
            ctx = self._ctx(ticker, as_of, position, portfolio)
            result = _apply_on_missing(self._nodes[nid].evaluate(ctx), self._node_specs[nid])
            if result.passed:
                return result
        return None

    def size(self, ticker: str, as_of: date, portfolio: PortfolioView) -> float:
        ctx = self._ctx(ticker, as_of, None, portfolio)
        return self._nodes[self._size_id].size(ctx)
