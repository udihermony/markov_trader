"""The strategy spec (DESIGN.md §4.6) — the shared contract between the UI,
the AI tool surface, and the engine. `spec_version: 2` is the graph form;
version 1 (flat slots) is not shipped."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SourceRef(BaseModel):
    id: str
    type: str
    params: dict = Field(default_factory=dict)


class NodeSpec(BaseModel):
    id: str
    kind: Literal["universe", "trigger", "confirm", "veto", "exit", "size"]
    # "score" (canvas-only, weighted combination) is deliberately omitted —
    # DESIGN.md §4.9 ships it with the canvas editor, not v1.
    type: str
    params: dict = Field(default_factory=dict)
    on_missing: Literal["fail_open", "fail_closed"] | None = None


class StrategySpec(BaseModel):
    spec_version: Literal[2] = 2
    name: str
    sources: list[SourceRef]
    nodes: list[NodeSpec]
    edges: list[tuple[str, str]] = Field(default_factory=list)
    # Entry chain only — exit nodes are deliberately unwired (§4.6): they're
    # evaluated independently for held positions, not part of candidate
    # selection.
    costs: dict = Field(default_factory=dict)
