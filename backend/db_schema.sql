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
    ts_utc       TEXT    NOT NULL,
    ts_local     TEXT    NOT NULL,
    account_id   TEXT    NOT NULL,           -- "soren", "iben" — skattepligtig identitet
    instance_id  TEXT    NOT NULL,           -- "workstation", "algoserver" — fysisk maskine-rolle
    source       TEXT    NOT NULL,
    event_type   TEXT    NOT NULL,
    ibkr_account TEXT,                       -- IBKR konto-nummer (kan være NULL for system-events)
    symbol       TEXT,
    payload_json TEXT    NOT NULL DEFAULT '{}'
);

-- Indeks der gør de typiske forespørgsler hurtige
CREATE INDEX IF NOT EXISTS idx_events_ts_utc     ON events(ts_utc);
CREATE INDEX IF NOT EXISTS idx_events_source     ON events(source);
CREATE INDEX IF NOT EXISTS idx_events_event_type ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_symbol     ON events(symbol);
CREATE INDEX IF NOT EXISTS idx_events_account_id  ON events(account_id);
CREATE INDEX IF NOT EXISTS idx_events_instance_id ON events(instance_id);