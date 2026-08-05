"""Streamlit dashboard for the Swing Trading Agent POC.

Run with:
    streamlit run dashboard.py
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yaml

# ------------------------------------------------------------------ config
CONFIG_PATH = Path(__file__).parent / "config.yaml"


def _db_path(mode: str) -> Path:
    raw = yaml.safe_load(CONFIG_PATH.read_text())
    key = "paper_path" if mode == "paper" else "backtest_path"
    return Path(__file__).parent / raw["database"][key]


def _initial_cash() -> float:
    raw = yaml.safe_load(CONFIG_PATH.read_text())
    return float(raw["sizing"]["initial_cash"])


# ------------------------------------------------------------------ db helpers
def _connect(db_path: Path) -> sqlite3.Connection | None:
    if not db_path.exists():
        return None
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _q(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> pd.DataFrame:
    return pd.read_sql_query(sql, conn, params=params)


# ------------------------------------------------------------------ page
st.set_page_config(page_title="Markov Trader", layout="wide")
st.title("Markov Trader — Swing Agent Dashboard")

mode = st.sidebar.selectbox("Mode", ["paper", "backtest"], index=0)
db_path = _db_path(mode)

if not db_path.exists():
    st.info(
        f"No **{mode}** database found at `{db_path}`.\n\n"
        f"Run `python3 main.py {mode}`"
        + (" `--start YYYY-MM-DD --end YYYY-MM-DD`" if mode == "backtest" else "")
        + " to generate data."
    )
    st.stop()

conn = _connect(db_path)

# ------------------------------------------------------------------ performance
perf = _q(conn, "SELECT date, cash, positions_value, total_equity, benchmark_equity "
                 "FROM performance_history ORDER BY date")

initial_cash = _initial_cash()

if perf.empty:
    st.warning("No performance snapshots yet — run the agent to populate data.")
else:
    perf["date"] = pd.to_datetime(perf["date"])
    latest = perf.iloc[-1]
    total_equity = latest["total_equity"]
    pnl = total_equity - initial_cash
    roi_pct = pnl / initial_cash * 100
    bench_eq = latest["benchmark_equity"]
    bench_roi = (bench_eq - initial_cash) / initial_cash * 100 if bench_eq else None

    # max drawdown on total_equity
    rolling_max = perf["total_equity"].cummax()
    drawdown = (perf["total_equity"] - rolling_max) / rolling_max * 100
    max_dd = drawdown.min()

    # ---------------------------------------------------------------- KPI row
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total Equity", f"${total_equity:,.0f}")
    c2.metric("Cash", f"${latest['cash']:,.0f}")
    c3.metric("ROI", f"{roi_pct:+.2f}%", delta=f"${pnl:+,.0f}")
    if bench_roi is not None:
        c4.metric("vs SPY", f"{roi_pct - bench_roi:+.2f}%", delta=f"SPY {bench_roi:+.2f}%")
    else:
        c4.metric("vs SPY", "—")
    c5.metric("Max Drawdown", f"{max_dd:.2f}%")

    st.divider()

    # ---------------------------------------------------------------- equity curve
    st.subheader("Equity Curve")
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=perf["date"], y=perf["total_equity"],
        name="Portfolio", line=dict(color="#4C9BE8", width=2),
    ))
    if perf["benchmark_equity"].notna().any():
        fig.add_trace(go.Scatter(
            x=perf["date"], y=perf["benchmark_equity"],
            name="SPY (buy & hold)", line=dict(color="#F5A623", width=2, dash="dash"),
        ))
    fig.add_hline(y=initial_cash, line_dash="dot", line_color="grey",
                  annotation_text="Starting capital")
    fig.update_layout(
        height=380, margin=dict(l=0, r=0, t=10, b=0),
        xaxis_title=None, yaxis_title="Equity ($)",
        legend=dict(orientation="h", y=1.05),
        hovermode="x unified",
    )
    st.plotly_chart(fig, use_container_width=True)

st.divider()

# ------------------------------------------------------------------ open positions
st.subheader("Open Positions")
positions = _q(conn,
    "SELECT p.ticker, p.shares, p.avg_entry_price, p.entry_date, p.entry_signal, "
    "       pc.close AS current_price "
    "FROM positions p "
    "LEFT JOIN price_cache pc ON pc.ticker = p.ticker "
    "   AND pc.date = (SELECT MAX(date) FROM price_cache WHERE ticker = p.ticker)"
)

if positions.empty:
    st.write("No open positions.")
else:
    positions["market_value"] = positions["shares"] * positions["current_price"].fillna(positions["avg_entry_price"])
    positions["cost_basis"] = positions["shares"] * positions["avg_entry_price"]
    positions["unrealized_pnl"] = positions["market_value"] - positions["cost_basis"]
    positions["unrealized_pnl_pct"] = positions["unrealized_pnl"] / positions["cost_basis"] * 100

    display = positions[[
        "ticker", "shares", "avg_entry_price", "current_price",
        "market_value", "unrealized_pnl", "unrealized_pnl_pct", "entry_date", "entry_signal",
    ]].copy()
    display.columns = [
        "Ticker", "Shares", "Entry Price", "Current Price",
        "Market Value", "Unrealized P&L", "P&L %", "Entry Date", "Signal",
    ]

    def _color_pnl(val):
        if isinstance(val, float):
            color = "color: #2ecc71" if val >= 0 else "color: #e74c3c"
            return color
        return ""

    st.dataframe(
        display.style
            .format({
                "Entry Price": "${:.2f}",
                "Current Price": "${:.2f}",
                "Market Value": "${:,.0f}",
                "Unrealized P&L": "${:+,.2f}",
                "P&L %": "{:+.2f}%",
            })
            .applymap(_color_pnl, subset=["Unrealized P&L", "P&L %"]),
        use_container_width=True,
        hide_index=True,
    )

st.divider()

# ------------------------------------------------------------------ trade log
st.subheader("Trade Log")

action_filter = st.multiselect(
    "Filter by action", ["BUY", "SELL", "SKIP"],
    default=["BUY", "SELL"],
)

placeholder = ",".join("?" * len(action_filter)) if action_filter else "''"
trades = _q(conn,
    f"SELECT timestamp, ticker, action, shares, fill_price, cost_bps_applied, reason, signal_metadata_json "
    f"FROM trade_log WHERE action IN ({placeholder}) ORDER BY timestamp DESC LIMIT 200",
    tuple(action_filter),
) if action_filter else pd.DataFrame()

if trades.empty:
    st.write("No trades match the filter.")
else:
    trades["signal_metadata_json"] = trades["signal_metadata_json"].apply(
        lambda s: json.loads(s) if s else {}
    )
    trades["timestamp"] = pd.to_datetime(trades["timestamp"])

    display_trades = trades.drop(columns=["signal_metadata_json"])
    display_trades.columns = ["Date", "Ticker", "Action", "Shares", "Fill Price", "Slippage (bps)", "Reason"]

    def _color_action(val):
        if val == "BUY":
            return "color: #2ecc71"
        if val == "SELL":
            return "color: #e74c3c"
        return "color: #aaaaaa"

    st.dataframe(
        display_trades.style
            .format({"Fill Price": lambda v: f"${v:.2f}" if pd.notna(v) else "—"})
            .applymap(_color_action, subset=["Action"]),
        use_container_width=True,
        hide_index=True,
    )

st.divider()

# ------------------------------------------------------------------ watchlist
with st.expander("Screener Watchlist History"):
    watchlist = _q(conn,
        "SELECT screen_date, ticker, rank FROM watchlist_history ORDER BY screen_date DESC, rank LIMIT 200"
    )
    if watchlist.empty:
        st.write("No screener history yet.")
    else:
        st.dataframe(watchlist, use_container_width=True, hide_index=True)
