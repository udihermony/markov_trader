"""Structural diff between two strategy spec dicts — extracted from
backend/api/routers/experiments.py so backend/ai/tools.py's `update_strategy`
tool can produce the same diff text without importing router internals."""
from __future__ import annotations


def diff_summary(prev_spec: dict | None, spec: dict) -> str:
    if prev_spec is None:
        return "baseline"
    prev_nodes = {n["id"]: n for n in prev_spec.get("nodes", [])}
    nodes = {n["id"]: n for n in spec.get("nodes", [])}
    changes: list[str] = []
    for node_id, node in nodes.items():
        prev_node = prev_nodes.get(node_id)
        if prev_node is None:
            changes.append(f"added {node['type']} ({node['kind']})")
            continue
        for key, value in node.get("params", {}).items():
            old = prev_node.get("params", {}).get(key)
            if old != value:
                changes.append(f"{key}: {old}→{value}")
    for node_id, prev_node in prev_nodes.items():
        if node_id not in nodes:
            changes.append(f"removed {prev_node['type']} ({prev_node['kind']})")
    return ", ".join(changes) if changes else "no changes"
