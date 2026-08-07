"""Spec validation (DESIGN.md §4.6): cycles rejected, kind-ordering
enforced, feature expressions parsed and checked against declared sources,
AI-backed node types restricted to `veto`."""
from __future__ import annotations

from backend.engine.graph.registry import get_node_type
from backend.engine.graph.spec import NodeSpec, SourceRef, StrategySpec
from backend.sources.expressions import parse_feature_expression

ENTRY_KIND_ORDER = ["universe", "trigger", "confirm", "veto", "size"]
REQUIRED_KINDS = ["universe", "trigger", "exit", "size"]


class GraphValidationError(ValueError):
    pass


def validate_spec(spec: StrategySpec) -> None:
    nodes_by_id: dict[str, NodeSpec] = {n.id: n for n in spec.nodes}
    if len(nodes_by_id) != len(spec.nodes):
        raise GraphValidationError("duplicate node ids")
    source_ids = {s.id for s in spec.sources}

    _check_registered_types_and_kinds(spec.nodes)
    _check_required_kinds_present(spec.nodes)
    _check_exit_nodes_unwired(spec.nodes, spec.edges, nodes_by_id)
    _check_kind_ordering(spec.edges, nodes_by_id)
    _check_no_cycles(spec.nodes, spec.edges, nodes_by_id)
    _check_size_node_terminal(spec.edges, nodes_by_id)
    _check_feature_expressions(spec.nodes, source_ids)
    _check_ai_kind_restricted(spec.nodes)
    _check_ai_source_declared(spec.nodes, spec.sources)


def _check_registered_types_and_kinds(nodes: list[NodeSpec]) -> None:
    for node in nodes:
        try:
            info = get_node_type(node.type)
        except KeyError:
            raise GraphValidationError(f"node {node.id!r}: unknown node type {node.type!r}") from None
        if node.kind not in info.allowed_kinds:
            raise GraphValidationError(
                f"node {node.id!r}: type {node.type!r} is not allowed in kind {node.kind!r} "
                f"(allowed: {sorted(info.allowed_kinds)})"
            )


def _check_required_kinds_present(nodes: list[NodeSpec]) -> None:
    kinds_present = {n.kind for n in nodes}
    for kind in REQUIRED_KINDS:
        if kind not in kinds_present:
            raise GraphValidationError(f"spec is missing a required {kind!r} node")
    size_nodes = [n for n in nodes if n.kind == "size"]
    if len(size_nodes) != 1:
        raise GraphValidationError(f"spec must have exactly one 'size' node, found {len(size_nodes)}")


def _check_exit_nodes_unwired(
    nodes: list[NodeSpec], edges: list[tuple[str, str]], nodes_by_id: dict[str, NodeSpec]
) -> None:
    exit_ids = {n.id for n in nodes if n.kind == "exit"}
    for a, b in edges:
        if a in exit_ids or b in exit_ids:
            raise GraphValidationError(
                f"edge ({a!r}, {b!r}): exit nodes are deliberately unwired, evaluated "
                "independently for held positions"
            )


def _check_kind_ordering(edges: list[tuple[str, str]], nodes_by_id: dict[str, NodeSpec]) -> None:
    for a, b in edges:
        if a not in nodes_by_id:
            raise GraphValidationError(f"edge references unknown node id {a!r}")
        if b not in nodes_by_id:
            raise GraphValidationError(f"edge references unknown node id {b!r}")
        from_kind, to_kind = nodes_by_id[a].kind, nodes_by_id[b].kind
        if from_kind not in ENTRY_KIND_ORDER or to_kind not in ENTRY_KIND_ORDER:
            continue  # exit already rejected above; anything else is a registry-level error
        from_idx, to_idx = ENTRY_KIND_ORDER.index(from_kind), ENTRY_KIND_ORDER.index(to_kind)
        if to_idx < from_idx:
            raise GraphValidationError(
                f"edge ({a!r}: {from_kind!r} -> {b!r}: {to_kind!r}) goes backward in kind order "
                f"{ENTRY_KIND_ORDER}"
            )


def _check_no_cycles(
    nodes: list[NodeSpec], edges: list[tuple[str, str]], nodes_by_id: dict[str, NodeSpec]
) -> None:
    entry_ids = [n.id for n in nodes if n.kind != "exit"]
    in_degree = {nid: 0 for nid in entry_ids}
    adjacency: dict[str, list[str]] = {nid: [] for nid in entry_ids}
    for a, b in edges:
        if a in adjacency and b in in_degree:
            adjacency[a].append(b)
            in_degree[b] += 1

    queue = [nid for nid, deg in in_degree.items() if deg == 0]
    visited = 0
    while queue:
        current = queue.pop()
        visited += 1
        for nxt in adjacency[current]:
            in_degree[nxt] -= 1
            if in_degree[nxt] == 0:
                queue.append(nxt)
    if visited != len(entry_ids):
        raise GraphValidationError("cycle detected in the entry chain")


def _check_size_node_terminal(edges: list[tuple[str, str]], nodes_by_id: dict[str, NodeSpec]) -> None:
    for a, b in edges:
        if nodes_by_id.get(a) and nodes_by_id[a].kind == "size":
            raise GraphValidationError(f"the size node {a!r} must be terminal (no outgoing edges)")


def _check_feature_expressions(nodes: list[NodeSpec], source_ids: set[str]) -> None:
    for node in nodes:
        for key, value in node.params.items():
            if not isinstance(value, str):
                continue
            try:
                expr = parse_feature_expression(value)
            except ValueError:
                continue  # not an expression (e.g. "direction": "up") — not an error
            if expr.alias not in source_ids:
                raise GraphValidationError(
                    f"node {node.id!r} param {key!r}={value!r}: unknown source alias {expr.alias!r} "
                    f"(declared sources: {sorted(source_ids)})"
                )


def _check_ai_kind_restricted(nodes: list[NodeSpec]) -> None:
    """CLAUDE.md rule 5 / DESIGN.md §5.1: AI nodes may veto, never
    originate a trade. Checked independently of a node type's
    `allowed_kinds` (M10's `ai_news_check`/`ai_regime_check` only declare
    `veto` there anyway) so a future AI node type with a looser
    `allowed_kinds` still can't be placed anywhere but `veto` — this is
    the single place the restriction is enforced, not a convention."""
    for node in nodes:
        info = get_node_type(node.type)
        if info.maturity == "AI" and node.kind != "veto":
            raise GraphValidationError(
                f"node {node.id!r}: AI-backed node type {node.type!r} may not appear in "
                f"`{node.kind}` — AI nodes may only veto (DESIGN.md §5.1/§5.2)"
            )


def _check_ai_source_declared(nodes: list[NodeSpec], sources: list[SourceRef]) -> None:
    """An AI node's factory pulls its adapter straight out of the registry
    (backend/engine/graph/nodes.py, same shape as `finviz_screen`) rather
    than through a declared `px.foo`-style expression alias, so nothing
    else would otherwise force a spec to declare it. Requiring the
    declaration here is what makes `CompiledGraph.trust_label` (which only
    inspects declared `spec.sources`) actually see the ai_judgment source
    and report `live_only` — without this check a strategy could silently
    call a live LLM while still claiming to be backtestable, exactly the
    dishonest-labelling failure DESIGN.md §5.2 exists to prevent."""
    has_ai_node = any(get_node_type(n.type).maturity == "AI" for n in nodes)
    if has_ai_node and not any(s.type == "ai_judgment" for s in sources):
        raise GraphValidationError(
            "spec has an AI-backed node but does not declare an `ai_judgment` source — "
            "add one so the strategy is correctly labelled live_only"
        )
