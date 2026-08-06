from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import backend.engine.graph.nodes  # noqa: F401  registers the node type library
from backend.api.routers import auth, experiments, holdouts, node_types, orders, strategies, wallets

app = FastAPI(title="Markov Trader API")
app.add_middleware(
    CORSMiddleware,
    # Local dev origins only: the Vite dev server (run directly) and the
    # docker-compose `frontend` service, both published on the host at 5173.
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth.router)
app.include_router(strategies.router)
app.include_router(wallets.router)
app.include_router(orders.router)
app.include_router(node_types.router)
app.include_router(experiments.router)
app.include_router(holdouts.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
