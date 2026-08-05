"""FastAPI backend for the Markov Trader swing agent.

Run with:
    uvicorn api:app --reload --port 8000
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import threading
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import sys
sys.path.insert(0, str(Path(__file__).parent))

from config import load_config
from data_provider import DataProvider
from db import get_connection
from orchestrator import Orchestrator
from sandbox import Sandbox
from screener import Screener
from strategy import build_strategy

CONFIG_PATH      = Path(__file__).parent / "config.yaml"
STATIC_DIR       = Path(__file__).parent / "static"
RUNS_DIR         = Path(__file__).parent / "backtests"
RUNS_DIR.mkdir(exist_ok=True)
REGISTRY         = RUNS_DIR / "registry.json"
STRATEGIES_FILE  = Path(__file__).parent / "strategies.json"

app = FastAPI(title="Markov Trader API", version="0.1.0")

_jobs: dict[str, dict] = {}
_run_lock       = threading.Lock()
_registry_lock  = threading.Lock()


# ------------------------------------------------------------------ registry

def _load_registry() -> list:
    if not REGISTRY.exists():
        return []
    return json.loads(REGISTRY.read_text())


def _save_registry(runs: list) -> None:
    REGISTRY.write_text(json.dumps(runs, indent=2))


def _register_run(meta: dict) -> None:
    with _registry_lock:
        runs = _load_registry()
        runs.insert(0, meta)   # newest first
        _save_registry(runs)


# ------------------------------------------------------------------ helpers

def _db_path(mode: str) -> Path:
    raw = yaml.safe_load(CONFIG_PATH.read_text())
    key = "paper_path" if mode == "paper" else "backtest_path"
    return Path(__file__).parent / raw["database"][key]


def _resolve_conn(mode: str, run_id: Optional[str]) -> sqlite3.Connection:
    """Open the right DB: a saved run file, or the live mode DB."""
    if run_id:
        path = RUNS_DIR / f"{run_id}.db"
        if not path.exists():
            raise HTTPException(404, f"Run {run_id} not found")
        return get_connection(path)
    return get_connection(_db_path(mode))


def _initial_cash() -> float:
    return float(yaml.safe_load(CONFIG_PATH.read_text())["sizing"]["initial_cash"])


def _compute_kpis(conn: sqlite3.Connection, initial: float) -> dict:
    rows = conn.execute(
        "SELECT total_equity, benchmark_equity FROM performance_history ORDER BY date"
    ).fetchall()
    if not rows:
        return {
            "total_equity": initial, "cash": initial, "pnl": 0.0,
            "roi_pct": 0.0, "spy_roi_pct": 0.0, "vs_spy_pct": 0.0,
            "max_drawdown_pct": 0.0,
        }
    latest   = rows[-1]
    total_eq = float(latest["total_equity"])
    roi      = (total_eq / initial - 1) * 100
    bench    = latest["benchmark_equity"]
    bench_roi = ((float(bench) / initial - 1) * 100) if bench else 0.0
    peak, max_dd = 0.0, 0.0
    for r in rows:
        peak = max(peak, float(r["total_equity"]))
        if peak > 0:
            max_dd = max(max_dd, (peak - float(r["total_equity"])) / peak * 100)
    cash_row = conn.execute("SELECT cash FROM account LIMIT 1").fetchone()
    return {
        "total_equity":     total_eq,
        "cash":             float(cash_row["cash"]) if cash_row else initial,
        "pnl":              round(total_eq - initial, 2),
        "roi_pct":          round(roi, 3),
        "spy_roi_pct":      round(bench_roi, 3),
        "vs_spy_pct":       round(roi - bench_roi, 3),
        "max_drawdown_pct": round(max_dd, 3),
    }


def _compute_summary(conn: sqlite3.Connection, initial: float) -> dict:
    kpis   = _compute_kpis(conn, initial)
    n_fills = conn.execute(
        "SELECT COUNT(*) AS n FROM trade_log WHERE action IN ('BUY','SELL')"
    ).fetchone()["n"]
    sells = conn.execute(
        "SELECT ticker, fill_price AS exit_p, timestamp FROM trade_log "
        "WHERE action='SELL' ORDER BY id"
    ).fetchall()
    wins, closed, total_hold = 0, 0, 0
    for s in sells:
        b = conn.execute(
            "SELECT fill_price, timestamp FROM trade_log WHERE action='BUY' AND ticker=? "
            "AND timestamp<=? ORDER BY id DESC LIMIT 1",
            (s["ticker"], s["timestamp"]),
        ).fetchone()
        if b:
            closed += 1
            if float(s["exit_p"]) > float(b["fill_price"]):
                wins += 1
            total_hold += (
                date.fromisoformat(s["timestamp"]) - date.fromisoformat(b["timestamp"])
            ).days
    return {
        **kpis,
        "n_fills":       n_fills,
        "hit_rate_pct":  round(wins / closed * 100, 1) if closed else None,
        "avg_hold_days": round(total_hold / closed, 1) if closed else None,
    }


# ------------------------------------------------------------------ config

@app.get("/api/config")
def get_config():
    raw = yaml.safe_load(CONFIG_PATH.read_text())
    return {"strategy": raw["strategy"], "sizing": raw["sizing"], "costs": raw["costs"]}


class ConfigUpdate(BaseModel):
    strategy_params: Optional[Dict] = None
    sizing: Optional[Dict] = None
    costs: Optional[Dict] = None


@app.patch("/api/config")
def update_config(body: ConfigUpdate):
    raw = yaml.safe_load(CONFIG_PATH.read_text())
    if body.strategy_params:
        for k, v in body.strategy_params.items():
            if k in raw["strategy"]["params"]:
                raw["strategy"]["params"][k] = type(raw["strategy"]["params"][k])(v)
    if body.sizing:
        for k, v in body.sizing.items():
            if k in raw["sizing"]:
                raw["sizing"][k] = type(raw["sizing"][k])(v)
    if body.costs:
        for k, v in body.costs.items():
            if k in raw["costs"]:
                raw["costs"][k] = type(raw["costs"][k])(v)
    CONFIG_PATH.write_text(yaml.dump(raw, default_flow_style=False, sort_keys=False))
    return {"ok": True}


# ------------------------------------------------------------------ saved strategies

def _load_strategies() -> list:
    if not STRATEGIES_FILE.exists():
        return []
    return json.loads(STRATEGIES_FILE.read_text())


def _save_strategies(data: list) -> None:
    STRATEGIES_FILE.write_text(json.dumps(data, indent=2))


class SaveStrategyRequest(BaseModel):
    label: str
    strategy_params: Dict
    sizing: Dict
    costs: Dict


@app.get("/api/strategies")
def list_strategies():
    return _load_strategies()


@app.post("/api/strategies", status_code=201)
def create_strategy(req: SaveStrategyRequest):
    strategies = _load_strategies()
    entry = {
        "id":              str(uuid.uuid4()),
        "label":           req.label.strip(),
        "created_at":      datetime.now().isoformat(timespec="seconds"),
        "strategy_params": req.strategy_params,
        "sizing":          req.sizing,
        "costs":           req.costs,
    }
    strategies.append(entry)
    _save_strategies(strategies)
    return entry


@app.delete("/api/strategies/{strategy_id}", status_code=204)
def delete_strategy(strategy_id: str):
    strategies = _load_strategies()
    _save_strategies([s for s in strategies if s["id"] != strategy_id])


# ------------------------------------------------------------------ watchlist

@app.get("/api/watchlist")
def get_watchlist(mode: str = "paper"):
    conn = get_connection(_db_path(mode))
    try:
        row = conn.execute("SELECT MAX(screen_date) AS d FROM watchlist_history").fetchone()
        if not row or not row["d"]:
            return {"date": None, "tickers": []}
        rows = conn.execute(
            "SELECT ticker FROM watchlist_history WHERE screen_date=? ORDER BY rank",
            (row["d"],),
        ).fetchall()
        return {"date": row["d"], "tickers": [r["ticker"] for r in rows]}
    finally:
        conn.close()


class WatchlistBody(BaseModel):
    tickers: List[str]
    mode: str = "paper"


@app.post("/api/watchlist")
def set_watchlist(body: WatchlistBody):
    tickers = [t.upper().strip() for t in body.tickers if t.strip()]
    today   = date.today().isoformat()
    conn    = get_connection(_db_path(body.mode))
    try:
        rid = str(uuid.uuid4())
        conn.execute("DELETE FROM watchlist_history WHERE screen_date=?", (today,))
        conn.executemany(
            "INSERT INTO watchlist_history (run_id, screen_date, ticker, rank) VALUES (?,?,?,?)",
            [(rid, today, t, i + 1) for i, t in enumerate(tickers)],
        )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "date": today, "tickers": tickers}


# ------------------------------------------------------------------ data reads (run_id-aware)

@app.get("/api/performance")
def get_performance(mode: str = "paper", run_id: Optional[str] = None):
    conn = _resolve_conn(mode, run_id)
    try:
        rows = conn.execute(
            "SELECT date, cash, positions_value, total_equity, benchmark_equity "
            "FROM performance_history ORDER BY date"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@app.get("/api/kpis")
def get_kpis(mode: str = "paper", run_id: Optional[str] = None):
    conn = _resolve_conn(mode, run_id)
    try:
        return _compute_kpis(conn, _initial_cash())
    finally:
        conn.close()


@app.get("/api/summary")
def get_summary(mode: str = "paper", run_id: Optional[str] = None):
    conn = _resolve_conn(mode, run_id)
    try:
        return _compute_summary(conn, _initial_cash())
    finally:
        conn.close()


@app.get("/api/positions")
def get_positions(mode: str = "paper", run_id: Optional[str] = None):
    conn = _resolve_conn(mode, run_id)
    try:
        rows = conn.execute(
            "SELECT p.ticker, p.shares, p.avg_entry_price, p.entry_date, p.entry_signal, "
            "       pc.close AS current_price "
            "FROM positions p "
            "LEFT JOIN price_cache pc ON pc.ticker=p.ticker "
            "  AND pc.date=(SELECT MAX(date) FROM price_cache WHERE ticker=p.ticker)"
        ).fetchall()
        out = []
        for r in rows:
            entry = float(r["avg_entry_price"])
            cur   = float(r["current_price"]) if r["current_price"] else entry
            sh    = int(r["shares"])
            out.append({
                "ticker": r["ticker"], "shares": sh,
                "entry_price": entry, "current_price": cur,
                "market_value":      round(sh * cur, 2),
                "unrealized_pnl":    round(sh * (cur - entry), 2),
                "unrealized_pnl_pct": round((cur / entry - 1) * 100, 2),
                "entry_date": r["entry_date"],
            })
        return out
    finally:
        conn.close()


@app.get("/api/trades")
def get_trades(mode: str = "paper", run_id: Optional[str] = None,
               actions: str = "BUY,SELL", limit: int = 100):
    action_list = [a.strip() for a in actions.split(",")]
    ph   = ",".join("?" * len(action_list))
    conn = _resolve_conn(mode, run_id)
    try:
        rows = conn.execute(
            f"SELECT timestamp, ticker, action, shares, fill_price, cost_bps_applied, reason "
            f"FROM trade_log WHERE action IN ({ph}) ORDER BY timestamp DESC LIMIT ?",
            (*action_list, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@app.get("/api/pending-orders")
def get_pending_orders(mode: str = "paper"):
    conn = get_connection(_db_path(mode))
    try:
        rows = conn.execute(
            "SELECT id, created_date, ticker, action, cash_amount, reason "
            "FROM pending_orders WHERE status='pending' ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@app.post("/api/pending-orders/{order_id}/cancel")
def cancel_order(order_id: int, mode: str = "paper"):
    conn = get_connection(_db_path(mode))
    try:
        row = conn.execute("SELECT id FROM pending_orders WHERE id=?", (order_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Order not found")
        conn.execute("UPDATE pending_orders SET status='cancelled' WHERE id=?", (order_id,))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


# ------------------------------------------------------------------ saved runs

@app.get("/api/runs")
def list_runs():
    with _registry_lock:
        return _load_registry()


@app.delete("/api/runs/{run_id}")
def delete_run(run_id: str):
    with _registry_lock:
        runs = _load_registry()
        runs = [r for r in runs if r["run_id"] != run_id]
        _save_registry(runs)
    db_file = RUNS_DIR / f"{run_id}.db"
    if db_file.exists():
        db_file.unlink()
    return {"ok": True}


# ------------------------------------------------------------------ run execution

def _build_orch_at(db_path: Path, mode: str = "backtest"):
    cfg    = load_config(mode)
    conn   = get_connection(db_path)
    run_id = str(uuid.uuid4())
    return cfg, Orchestrator(
        cfg,
        DataProvider(conn, cfg.data),
        Screener(conn, cfg.screener, run_id, mode),
        Sandbox(conn, run_id, mode, cfg.sizing, cfg.costs),
        build_strategy(cfg.strategy.name, cfg.strategy.params),
        run_id,
    )


def _setup_run_db(job_id: str) -> Path:
    """Create a per-run DB, seeding from the main backtest DB (price cache reuse)."""
    run_db   = RUNS_DIR / f"{job_id}.db"
    main_db  = _db_path("backtest")
    if main_db.exists():
        shutil.copy2(str(main_db), str(run_db))
    # Ensure schema exists, then clear run-specific tables
    conn = get_connection(run_db)
    for tbl in ("account", "positions", "trade_log", "performance_history", "pending_orders"):
        conn.execute(f"DELETE FROM {tbl}")
    conn.commit()
    conn.close()
    return run_db


def _launch(target, *args) -> str:
    if not _run_lock.acquire(blocking=False):
        raise HTTPException(409, "A run is already in progress")
    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"status": "running", "progress": 0}

    def run():
        try:
            target(job_id, *args)
        finally:
            _run_lock.release()

    threading.Thread(target=run, daemon=True).start()
    return job_id


def _paper_job(job_id: str) -> None:
    try:
        cfg, orch = _build_orch_at(_db_path("paper"), "paper")
        orch.run_day(date.today(), quiet=True)
        conn   = get_connection(_db_path("paper"))
        result = _compute_summary(conn, _initial_cash())
        conn.close()
        _jobs[job_id] = {"status": "done", "result": result}
    except Exception as exc:
        _jobs[job_id] = {"status": "error", "error": str(exc)}


def _backtest_job(job_id: str, start: str, end: str) -> None:
    try:
        run_db     = _setup_run_db(job_id)
        cfg, orch  = _build_orch_at(run_db, "backtest")
        start_d, end_d = date.fromisoformat(start), date.fromisoformat(end)
        orch.provider.refresh([cfg.data.benchmark_ticker], end_d)
        orch.provider._refresh_one(  # noqa: SLF001
            cfg.data.benchmark_ticker,
            start_d - timedelta(days=cfg.data.fetch_window_days),
            end_d,
        )
        days = orch.provider.trading_days(cfg.data.benchmark_ticker, start_d, end_d)
        if not days:
            _jobs[job_id] = {"status": "error", "error": "No trading days in date range"}
            return
        for i, day in enumerate(days):
            _jobs[job_id]["progress"] = round((i + 1) / len(days) * 100)
            orch.run_day(day, quiet=True)

        conn   = get_connection(run_db)
        result = _compute_summary(conn, _initial_cash())
        conn.close()

        raw = yaml.safe_load(CONFIG_PATH.read_text())
        _register_run({
            "run_id":     job_id,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "start_date": start,
            "end_date":   end,
            "strategy":   raw["strategy"],
            "sizing":     raw["sizing"],
            "costs":      raw["costs"],
            "db_file":    f"{job_id}.db",
            **{k: result[k] for k in (
                "roi_pct", "vs_spy_pct", "spy_roi_pct", "max_drawdown_pct",
                "n_fills", "hit_rate_pct", "avg_hold_days",
            )},
        })
        _jobs[job_id] = {"status": "done", "result": result, "run_id": job_id}
    except Exception as exc:
        _jobs[job_id] = {"status": "error", "error": str(exc)}


@app.post("/api/paper/run")
def run_paper():
    return {"job_id": _launch(_paper_job)}


class BacktestParams(BaseModel):
    start: str
    end: str


@app.post("/api/backtest")
def run_backtest(params: BacktestParams):
    return {"job_id": _launch(_backtest_job, params.start, params.end)}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


# ------------------------------------------------------------------ static
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
