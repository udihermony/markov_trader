"""The node-type registry — maps a spec node's `type` string to a factory
that constructs the concrete node instance. Node factories receive the
node's `params` dict plus the `SourceRegistry` (only `finviz_screen`-style
universe nodes that need direct adapter access at construction time use the
second argument; decision/size nodes get all their data through
`ctx.features` at evaluate() time instead).

Unlike M2's source adapters (which bind to a live DB session and therefore
must NOT self-register at import time — see sources/price_bars.py), node
type factories are stateless pure functions with no session to bind, so
self-registration at import time here is safe and conventional."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal

from backend.engine.graph.spec import NodeSpec
from backend.sources.registry import SourceRegistry

Maturity = Literal["standard", "experimental", "AI"]


@dataclass(frozen=True)
class NodeTypeInfo:
    type: str
    allowed_kinds: frozenset[str]
    maturity: Maturity
    factory: Callable[[dict, SourceRegistry], object]


_NODE_TYPES: dict[str, NodeTypeInfo] = {}


def register_node_type(info: NodeTypeInfo) -> None:
    _NODE_TYPES[info.type] = info


def get_node_type(type_name: str) -> NodeTypeInfo:
    return _NODE_TYPES[type_name]


def all_node_types() -> dict[str, NodeTypeInfo]:
    return dict(_NODE_TYPES)


def build_node(node_spec: NodeSpec, registry: SourceRegistry) -> object:
    info = get_node_type(node_spec.type)
    return info.factory(node_spec.params, registry)
