from __future__ import annotations

from fastapi import FastAPI

from backend.api.routers import auth

app = FastAPI(title="Markov Trader API")
app.include_router(auth.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
