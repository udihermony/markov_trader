from __future__ import annotations

from fastapi import FastAPI

import backend.engine.graph.nodes  # noqa: F401  registers the node type library
from backend.api.routers import auth, strategies, wallets

app = FastAPI(title="Markov Trader API")
app.include_router(auth.router)
app.include_router(strategies.router)
app.include_router(wallets.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
