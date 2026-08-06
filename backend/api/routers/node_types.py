from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

import backend.engine.graph.nodes  # noqa: F401  registers the node type library
from backend.engine.graph.registry import all_node_types

router = APIRouter(prefix="/node-types", tags=["node-types"])


class ParamFieldResponse(BaseModel):
    name: str
    type: str
    label: str
    options: list[str] | None = None
    default: object | None = None


class NodeTypeResponse(BaseModel):
    type: str
    allowed_kinds: list[str]
    maturity: str
    params_schema: list[ParamFieldResponse]


@router.get("", response_model=list[NodeTypeResponse])
def list_node_types() -> list[NodeTypeResponse]:
    return [
        NodeTypeResponse(
            type=info.type,
            allowed_kinds=sorted(info.allowed_kinds),
            maturity=info.maturity,
            params_schema=[
                ParamFieldResponse(
                    name=f.name, type=f.type, label=f.label, options=f.options, default=f.default
                )
                for f in info.params_schema
            ],
        )
        for info in sorted(all_node_types().values(), key=lambda i: i.type)
    ]
