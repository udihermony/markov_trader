-- Swing Trading Agent POC — SQLite schema.
-- Designed for a mechanical later migration to PostgreSQL:
--   * integer primary keys, no SQLite-only types
--   * all timestamps/dates stored as ISO-8601 TEXT

CREATE TABLE IF NOT EXISTS price_cache (
    ticker  TEXT NOT NULL,
    date    TEXT NOT NULL,             -- ISO-8601 date
    open    REAL NOT NULL,
    high    REAL NOT NULL,
    low     REAL NOT NULL,
    close   REAL NOT NULL,
    volume  INTEGER NOT NULL,
    PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS watchlist_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL,
    screen_date TEXT NOT NULL,         -- ISO-8601 date
    ticker      TEXT NOT NULL,
    rank        INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_watchlist_date ON watchlist_history (screen_date);

CREATE TABLE IF NOT EXISTS account (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    mode   TEXT NOT NULL CHECK (mode IN ('backtest', 'paper')),
    cash   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS positions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT NOT NULL,
    ticker          TEXT NOT NULL UNIQUE,
    shares          INTEGER NOT NULL,
    avg_entry_price REAL NOT NULL,
    entry_date      TEXT NOT NULL,     -- ISO-8601 date; required for the time stop
    entry_signal    TEXT
);

CREATE TABLE IF NOT EXISTS trade_log (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id               TEXT NOT NULL,
    mode                 TEXT NOT NULL CHECK (mode IN ('backtest', 'paper')),
    timestamp            TEXT NOT NULL,  -- ISO-8601
    ticker               TEXT NOT NULL,
    action               TEXT NOT NULL CHECK (action IN ('BUY', 'SELL', 'SKIP')),
    shares               INTEGER,
    fill_price           REAL,
    cost_bps_applied     REAL,
    reason               TEXT NOT NULL,
    signal_metadata_json TEXT
);

CREATE TABLE IF NOT EXISTS performance_history (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id           TEXT NOT NULL,
    date             TEXT NOT NULL,     -- ISO-8601 date
    cash             REAL NOT NULL,
    positions_value  REAL NOT NULL,
    total_equity     REAL NOT NULL,
    benchmark_equity REAL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_perf_date ON performance_history (date);

-- Orders queued at close of day t, executed at open of day t+1.
CREATE TABLE IF NOT EXISTS pending_orders (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id               TEXT NOT NULL,
    created_date         TEXT NOT NULL, -- signal date (day t), ISO-8601
    ticker               TEXT NOT NULL,
    action               TEXT NOT NULL CHECK (action IN ('BUY', 'SELL')),
    cash_amount          REAL,          -- BUY sizing (fraction of cash at queue time)
    reason               TEXT NOT NULL,
    signal_metadata_json TEXT,
    status               TEXT NOT NULL DEFAULT 'pending'
                         CHECK (status IN ('pending', 'executed', 'cancelled'))
);
