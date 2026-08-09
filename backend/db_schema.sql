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
    paper        INTEGER,                    -- 1 = paper, 0 = LIVE. Se trades.paper.
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

-- ─────────────────────────────────────────────────────────────────
-- `trades`-tabel — aggregeret view af handler (entry + exit i én row)
-- ─────────────────────────────────────────────────────────────────
-- I modsætning til events (append-only black box) er trades en
-- mutable tabel: rows opdateres når en åben position lukkes, og
-- notes kan opdateres af brugeren via Studio UI.
--
-- En åben position = row med exit_time_utc IS NULL.
-- En lukket trade = row med exit_time_utc IS NOT NULL.
--
-- trade_id er en UUID (string) genereret af backend ved entry, så
-- vi kan deduplere ved sync til central database og opdatere
-- specifikke rows uden at slå på naturlige nøgler (ticker+time).
-- ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS trades (
    -- Identitet
    trade_id        TEXT    PRIMARY KEY,         -- UUID v4
    account_id      TEXT    NOT NULL,            -- "soren", "iben"
    instance_id     TEXT    NOT NULL,            -- "workstation", "algoserver"
    ibkr_account    TEXT    NOT NULL,            -- "DUO509856" osv
    -- 1 = paper, 0 = LIVE. Kommer fra den FORBINDELSE ordren gik igennem, ikke
    -- fra maskinens identitet: paper og live skal kunne koere samtidig, saa
    -- maskinen alene kan ikke afgoere hvad en given handel var.
    --
    -- ⚠ Uden denne kolonne ville live-resultater blive lagt oven i maanedsvis af
    -- paper-handler i enhver statistik — win rate, P&L, top-lister — uden at
    -- noget fejlede. Det er den slags fejl der ikke goer vroevl.
    paper           INTEGER NOT NULL DEFAULT 1,

    -- Strategi
    source          TEXT    NOT NULL,            -- "Momentum ORB", "Konfluens", "manual"
    variant         TEXT,                        -- "all_winner", "baseline", NULL for manual

    -- Position
    symbol          TEXT    NOT NULL,
    side            TEXT    NOT NULL,            -- "long" eller "short"
    shares          INTEGER NOT NULL,

    -- Entry
    entry_time_utc  TEXT    NOT NULL,            -- ISO datetime UTC
    entry_time_et   TEXT    NOT NULL,            -- ISO datetime America/New_York
    entry_price     REAL    NOT NULL,
    entry_reason    TEXT,                        -- fri-form

    -- Exit (NULL mens position er åben)
    exit_time_utc   TEXT,
    exit_time_et    TEXT,
    exit_price      REAL,
    exit_reason     TEXT,                        -- "stop", "target", "trail", "force_close", "manual"

    -- Resultat (NULL mens åben)
    pnl             REAL,                        -- realiseret USD
    pnl_pct         REAL,                        -- % af entry-kapital
    duration_sec    INTEGER,                     -- sekunder fra entry til exit

    -- Kapital
    capital_used    REAL    NOT NULL,            -- entry_price × shares

    -- Live position-state (opdateres mens åben, fryses ved exit)
    current_stop    REAL,                        -- aktuelt stop-niveau
    current_target  REAL,                        -- aktuelt target-niveau (NULL i trailing-stage)
    current_stage   TEXT,                        -- "initial", "breakeven", "trailing"
    trail_stop      REAL,                        -- trail-stop niveau hvis stage=trailing
    current_price   REAL,                        -- seneste pris strategien har set (live urealiseret P&L)

    -- Metadata
    notes           TEXT,                        -- fri tekst, manuelle handler bruger primært dette
    payload_json    TEXT    NOT NULL DEFAULT '{}'  -- forensics, indikatorer, alt fri-form

    -- Bemærk: ingen foreign keys til events. Trades og events er
    -- to uafhængige views af samme virkelighed. Events er sandhed
    -- (append-only), trades er bekvemt aggregat.
);

CREATE INDEX IF NOT EXISTS idx_trades_entry_et    ON trades(entry_time_et);
CREATE INDEX IF NOT EXISTS idx_trades_account_id  ON trades(account_id);
CREATE INDEX IF NOT EXISTS idx_trades_instance_id ON trades(instance_id);
CREATE INDEX IF NOT EXISTS idx_trades_source      ON trades(source);
CREATE INDEX IF NOT EXISTS idx_trades_symbol      ON trades(symbol);
CREATE INDEX IF NOT EXISTS idx_trades_open
    ON trades(exit_time_utc) WHERE exit_time_utc IS NULL;

-- ── Dagsnoter ────────────────────────────────────────────────────────────────
-- Hvorfor der IKKE er handlet en dag. Ferie, sygdom, manglende lyst.
--
-- Fraværet af en handel er også information, men det har ingen række at hænge
-- på: intet trade_id, ingen ejermaskine. Derfor sin egen tabel, nøglet på dato.
--
-- Præcis én note pr. dag. Ikke pr. uge, ikke pr. periode — "ferie i uge 30" er
-- ikke én note, det er fem, én pr. handelsdag. PRIMARY KEY håndhæver det.
CREATE TABLE IF NOT EXISTS dagsnoter (
    dato      TEXT PRIMARY KEY,   -- ISO "YYYY-MM-DD", ET-handelsdag
    note      TEXT NOT NULL,
    oprettet  TEXT NOT NULL,      -- ISO UTC
    aendret   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_trades_paper ON trades(paper);
CREATE INDEX IF NOT EXISTS idx_events_paper ON events(paper);
