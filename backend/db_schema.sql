-- ─────────────────────────────────────────────────────────────
-- Trading Dash — SQLite skema
-- ─────────────────────────────────────────────────────────────
-- Filosofi:
--   `events` er en append-only "black box recorder". Alt der
--   sker i systemet skrives hertil. Ingen UPDATE, ingen DELETE.
--   Hvis vi senere vil have aggregerede views (trades,
--   strategi-statistik, kontostyring) bygger vi dem oven på
--   denne tabel — eller som separate tabeller der ALDRIG
--   modsiger events.
-- ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc       TEXT    NOT NULL,           -- ISO 8601 UTC, fx "2026-05-06T13:45:12.123456+00:00"
    ts_local     TEXT    NOT NULL,           -- ISO 8601 lokal tid (København)
    source       TEXT    NOT NULL,           -- "system", "risk_manager", "Momentum ORB", "manual", osv.
    event_type   TEXT    NOT NULL,           -- "order_request", "order_approved", "order_rejected",
                                             -- "fill", "exit", "strategy_started", "strategy_stopped",
                                             -- "emergency_stop", "market_conditions", "config_change",
                                             -- "account_snapshot", "system_startup", osv.
    account      TEXT,                       -- "DUNXXXXXXX", "live-konto", eller NULL for system-events
    symbol       TEXT,                       -- "AAPL", "GME", eller NULL hvis ikke ticker-relateret
    payload_json TEXT    NOT NULL DEFAULT '{}'  -- Resten — alt strukturen der ikke passer i kolonnerne
);

-- Indeks der gør de typiske forespørgsler hurtige
CREATE INDEX IF NOT EXISTS idx_events_ts_utc     ON events(ts_utc);
CREATE INDEX IF NOT EXISTS idx_events_source     ON events(source);
CREATE INDEX IF NOT EXISTS idx_events_event_type ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_symbol     ON events(symbol);