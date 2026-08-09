"""
journal.py — Append-only event journal med SQLite

Brugsmønster:
    journal = Journal("trading_dash.db")
    await journal.init()

    await journal.log_event(
        source="Momentum ORB",
        event_type="order_request",
        ibkr_account="DUxxxxxxx",
        symbol="GME",
        payload={"action": "BUY", "quantity": 100, "limit_price": 12.34, "reason": "ORB breakout"},
    )

Designprincipper:
  - Append-only: ingen UPDATE, ingen DELETE.
  - Skriver fejler aldrig algoritmen — fejl logges men kastes ikke videre.
  - ts_utc og ts_local sættes automatisk på skrive-tidspunktet.
  - payload er fri-form JSON — alt der ikke passer i de faste kolonner.

Placering: C:\\Projects\\Trading_Dash\\backend\\journal.py
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiosqlite
import pytz

import replication
from accounts import identity, aktiv_konto

# Tidszone-konstant — bruges af trade-helpers til at gemme entry_time_et og exit_time_et
ET = pytz.timezone("America/New_York")

logger = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).parent / "db_schema.sql"


# ── Hvor paper/live-mærket kommer fra ────────────────────────────────────────
# Ikke fra maskinens identitet. Paper og live skal kunne køre SAMTIDIG — algoerne
# videre på paper mens der handles live manuelt — så maskinen alene kan ikke
# afgøre hvad en given handel var. Kilden er den FORBINDELSE ordren gik igennem.
#
# main.py registrerer opslaget ved opstart (undgår cirkulær import: journal må
# ikke kende strategy_manager). Er intet registreret, falder vi tilbage på
# identity — korrekt i dag, hvor der kun findes én forbindelse.
_konto_kilde = None


def saet_konto_kilde(fn) -> None:
    """fn() -> (ibkr_account: str, paper: bool). Sættes af main ved opstart."""
    global _konto_kilde
    _konto_kilde = fn


def _paper_flag(ibkr_account: str | None, paper: bool | None) -> tuple[str, int]:
    """Løs (konto, paper) og krydstjek dem mod hinanden.

    ⚠ Krydstjekket er det eneste der kan fange at flaget og kontoen er uenige.
    IBKR's paper-konti begynder med D (DU/DF), live-konti med U. Et 0/1-flag kan
    ikke selv afsløre at det er forkert — men en live-konto stemplet som paper
    ville lægge rigtige penge oven i paper-statistikken, og det ville ingen
    opdage. Vi retter ikke automatisk: vi ved ikke hvilken af de to der lyver.
    """
    konto = ibkr_account
    flag = paper

    if (konto is None or flag is None) and _konto_kilde is not None:
        try:
            k, p = _konto_kilde()
            if konto is None:
                konto = k
            if flag is None:
                flag = p
        except Exception as e:
            logger.error(f"[Journal] Kunne ikke slå handelskonto op: {e}")

    if konto is None:
        konto = aktiv_konto()
    if flag is None:
        flag = identity.paper_trading

    k = (konto or "").upper()
    if k:
        ligner_paper = k.startswith("D")
        if ligner_paper != bool(flag):
            logger.error(
                f"[Journal] ⚠ KONTO OG PAPER-FLAG ER UENIGE: konto {k} "
                f"{'ligner paper' if ligner_paper else 'ligner LIVE'}, men flaget "
                f"siger {'paper' if flag else 'LIVE'}. Rækken skrives med flaget "
                f"som angivet — undersøg hvilken af de to der er forkert.")

    return konto, int(bool(flag))


class Journal:
    """Tynd wrapper omkring aiosqlite — append-only event log."""

    def __init__(self, db_path: str = "trading_dash.db"):
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    # ------------------------------------------------------------------
    # Livscyklus
    # ------------------------------------------------------------------

    async def init(self) -> None:
        """Åbn forbindelse og opret skema hvis det ikke findes."""
        self._db = await aiosqlite.connect(self.db_path)

        # WAL-mode: bedre samtidighed mellem skriv (algoritme) og læs
        # (analyse-vinduer der senere vil forespørge mens algoen kører).
        await self._db.execute("PRAGMA journal_mode = WAL")
        await self._db.execute("PRAGMA synchronous = NORMAL")

        schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
        await self._db.executescript(schema_sql)
        await self._db.commit()

        # Idempotent migration: skema-ændringer rammer kun NYE db'er (CREATE
        # TABLE IF NOT EXISTS), så eksisterende db'er får nye kolonner her.
        for col_ddl in ("current_price REAL", "paper INTEGER NOT NULL DEFAULT 1"):
            try:
                await self._db.execute(f"ALTER TABLE trades ADD COLUMN {col_ddl}")
                await self._db.commit()
            except Exception:
                pass  # kolonnen findes allerede
        for col_ddl in ("paper INTEGER",):
            try:
                await self._db.execute(f"ALTER TABLE events ADD COLUMN {col_ddl}")
                await self._db.commit()
            except Exception:
                pass

        # ⚠ Eksisterende rækker får paper=1 af DEFAULT. Det er korrekt for alt
        # hvad der findes i dag — der er aldrig handlet live — men det er en
        # ANTAGELSE, ikke en måling. Skulle den nogensinde være forkert, ville
        # live-handler blive talt med som paper. Derfor krydstjekket i
        # _paper_flag(): IBKR's paper-konti begynder med D, live med U.
        await self._db.execute(
            "UPDATE trades SET paper = 1 WHERE paper IS NULL")
        await self._db.commit()

        logger.info(f"[Journal] Klar — db: {self.db_path}")

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @property
    def db(self):
        """
        Tilgang til den underliggende aiosqlite-forbindelse.

        Returnerer None hvis journal ikke er initialiseret. Bruges af
        trade_queries-modulet til at lave read-queries der ikke passer
        i Journal-klassens API.
        """
        return self._db

    # ------------------------------------------------------------------
    # Skrivning
    # ------------------------------------------------------------------

    async def log_event(
        self,
        source:       str,
        event_type:   str,
        payload:      Optional[dict] = None,
        symbol:       Optional[str]  = None,
        ibkr_account: Optional[str]  = None,
        paper:        Optional[bool] = None,
    ) -> None:
        """
        Skriv ét event til journalen.

        account_id og instance_id sættes AUTOMATISK fra accounts.identity —
        kalderen kan ikke overstyre eller udelade dem. Det er den arkitektoniske
        garanti for at hver event entydigt tilhører én skattepligtig identitet.

        ibkr_account er valgfri og refererer til IBKR-kontonummeret for events
        knyttet til en specifik konto-transaktion (defaulter til instansens
        konfigurerede ibkr_account hvis ikke angivet).

        Fejler aldrig kalderen — selv hvis SQLite er nede skal handelsflowet
        fortsætte. Vi vil hellere miste et log-event end at miste en handel.
        """
        if self._db is None:
            logger.warning(f"[Journal] Ikke initialiseret — kasserer event {source}/{event_type}")
            return

        try:
            now_utc   = datetime.now(timezone.utc)
            now_local = datetime.now().astimezone()
            _konto, _paper = _paper_flag(ibkr_account, paper)

            await self._db.execute(
                """
                INSERT INTO events
                    (ts_utc, ts_local, account_id, instance_id, source, event_type,
                     ibkr_account, paper, symbol, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now_utc.isoformat(),
                    now_local.isoformat(),
                    identity.account_id,
                    identity.instance_role,
                    source,
                    event_type,
                    _konto,
                    _paper,
                    symbol,
                    json.dumps(payload or {}, default=str),
                ),
            )
            await self._db.commit()
            replication.notify_change()   # poke replikering — billigt flag-sæt

        except Exception as e:
            # Vi sluger fejlen bevidst — journalisering må aldrig
            # nedbryde handelsflowet.
            logger.error(f"[Journal] Skriv-fejl ({source}/{event_type}): {e}")

    # ------------------------------------------------------------------
    # Simpel læsning (til health/debug — analyse kommer senere)
    # ------------------------------------------------------------------

    async def count_events(self) -> int:
        if self._db is None:
            return 0
        async with self._db.execute("SELECT COUNT(*) FROM events") as cur:
            row = await cur.fetchone()
            return row[0] if row else 0

    async def get_events(
        self,
        from_utc:   Optional[str] = None,   # ISO UTC streng, inklusiv
        to_utc:     Optional[str] = None,   # ISO UTC streng, inklusiv
        source:     Optional[str] = None,   # fx "Momentum ORB" — None = alle
        event_type: Optional[str] = None,   # fx "trade_forensics" — None = alle
        limit:      int = 1000,
        db=None,
    ) -> list[dict]:
        """
        Hent events filtreret på tidsvindue og/eller source.

        from_utc/to_utc er ISO UTC-strenge (samme format som ts_utc gemmes i),
        så tekstuel sammenligning på ts_utc fungerer korrekt. Frontend udregner
        disse fra det ET-vindue brugeren vælger.

        Returnerer events sorteret kronologisk (ældste først) så de kan vises
        som en log. Tom liste hvis intet matcher eller journal ikke er klar.
        """
        conn = db if db is not None else self._db
        if conn is None:
            return []

        clauses = []
        params: list = []
        if from_utc:
            clauses.append("ts_utc >= ?")
            params.append(from_utc)
        if to_utc:
            clauses.append("ts_utc <= ?")
            params.append(to_utc)
        if source:
            clauses.append("source = ?")
            params.append(source)
        if event_type:
            clauses.append("event_type = ?")
            params.append(event_type)

        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = (
            "SELECT ts_utc, ts_local, source, event_type, symbol, payload_json, "
            "ibkr_account, COALESCE(paper, 1) AS paper "
            "FROM events" + where +
            " ORDER BY ts_utc ASC LIMIT ?"
        )
        params.append(limit)

        try:
            rows = []
            async with conn.execute(sql, params) as cur:
                async for r in cur:
                    payload = {}
                    try:
                        payload = json.loads(r[5] or "{}")
                    except (json.JSONDecodeError, TypeError):
                        payload = {}
                    rows.append({
                        "ts_utc":     r[0],
                        "ts_local":   r[1],
                        "source":     r[2],
                        "event_type": r[3],
                        "symbol":     r[4],
                        "payload":    payload,
                        # Forensikken skal kunne kende paper fra live uden at
                        # gaette ud fra maskinen den blev skrevet paa.
                        "ibkr_account": r[6],
                        "paper":        bool(r[7]),
                    })
            return rows
        except Exception as e:
            logger.error(f"[Journal] get_events fejl: {e}")
            return []

    # ------------------------------------------------------------------
    # Trades — mutable aggregeret view (entry + exit i én row)
    # ------------------------------------------------------------------
    #
    # I modsætning til events (append-only) opdaterer disse metoder
    # eksisterende rows. trade_id er en UUID genereret af backend ved
    # entry, så vi kan deduplere ved sync og opdatere specifikke rows.
    #
    # Designprincip: Disse fejler aldrig kalderen. Hvis SQLite er nede
    # eller skemaet er forkert, logger vi fejlen og fortsætter — det
    # er bedre at miste et trade-log end at nedbryde handelsflowet.
    # ------------------------------------------------------------------

    async def log_trade_open(
        self,
        source:         str,
        symbol:         str,
        side:           str,           # "long" eller "short"
        shares:         int,
        entry_price:    float,
        entry_time:     datetime,      # tz-aware, gerne ET
        variant:        Optional[str]  = None,
        entry_reason:   Optional[str]  = None,
        current_stop:   Optional[float] = None,
        current_target: Optional[float] = None,
        current_stage:  Optional[str]  = None,
        ibkr_account:   Optional[str]  = None,
        paper:          Optional[bool] = None,
        notes:          Optional[str]  = None,
        payload:        Optional[dict] = None,
    ) -> Optional[str]:
        """
        Åbn en ny trade-row. Returnerer trade_id (UUID) eller None ved fejl.

        Den returnerede trade_id SKAL gemmes af kalderen (typisk på
        position.metadata) så vi kan referere til samme row ved exit.

        account_id, instance_id og ibkr_account sættes automatisk fra
        accounts.identity — samme arkitektoniske garanti som log_event().
        """
        if self._db is None:
            logger.warning(f"[Journal] Ikke initialiseret — kasserer trade_open {source}/{symbol}")
            return None

        trade_id = str(uuid.uuid4())
        _konto, _paper = _paper_flag(ibkr_account, paper)

        try:
            # Tidsstempler: hvis entry_time er tz-aware konverteres det;
            # ellers behandles det som naivt UTC (best-effort)
            if entry_time.tzinfo is None:
                entry_utc = entry_time.replace(tzinfo=timezone.utc)
            else:
                entry_utc = entry_time.astimezone(timezone.utc)
            entry_et = entry_utc.astimezone(ET)

            capital_used = entry_price * shares

            await self._db.execute(
                """
                INSERT INTO trades (
                    trade_id, account_id, instance_id, ibkr_account, paper,
                    source, variant,
                    symbol, side, shares,
                    entry_time_utc, entry_time_et, entry_price, entry_reason,
                    capital_used,
                    current_stop, current_target, current_stage,
                    notes, payload_json
                )
                VALUES (?, ?, ?, ?, ?,
                        ?, ?,
                        ?, ?, ?,
                        ?, ?, ?, ?,
                        ?,
                        ?, ?, ?,
                        ?, ?)
                """,
                (
                    trade_id,
                    identity.account_id,
                    identity.instance_role,
                    _konto,
                    _paper,
                    source,
                    variant,
                    symbol,
                    side,
                    shares,
                    entry_utc.isoformat(),
                    entry_et.isoformat(),
                    entry_price,
                    entry_reason,
                    capital_used,
                    current_stop,
                    current_target,
                    current_stage,
                    notes,
                    json.dumps(payload or {}, default=str),
                ),
            )
            await self._db.commit()
            replication.notify_change()   # poke replikering — billigt flag-sæt
            return trade_id

        except Exception as e:
            logger.error(f"[Journal] log_trade_open fejl ({source}/{symbol}): {e}")
            return None

    async def log_trade_close(
        self,
        trade_id:     str,
        exit_price:   float,
        exit_time:    datetime,        # tz-aware, gerne ET
        exit_reason:  str,             # "stop", "target", "trail", "force_close", "manual"
        pnl:          float,           # realiseret USD (kalderen beregner)
        payload:      Optional[dict] = None,
    ) -> bool:
        """
        Luk en eksisterende trade-row med exit-info.

        Returnerer True ved succes, False ved fejl eller hvis trade_id
        ikke fandtes.

        pnl_pct og duration_sec beregnes her ud fra de eksisterende
        entry-felter — kalderen behøver ikke at sende dem.

        Hvis payload sendes, MERGES det ind i den eksisterende payload_json
        (entry-payload + exit-payload bevares begge).
        """
        if self._db is None:
            logger.warning(f"[Journal] Ikke initialiseret — kasserer trade_close {trade_id}")
            return False

        try:
            # Hent eksisterende entry-info så vi kan beregne pnl_pct og duration
            async with self._db.execute(
                "SELECT entry_price, entry_time_utc, side, shares, payload_json "
                "FROM trades WHERE trade_id = ?",
                (trade_id,)
            ) as cur:
                row = await cur.fetchone()

            if row is None:
                logger.warning(f"[Journal] log_trade_close: trade_id {trade_id} fandtes ikke")
                return False

            entry_price, entry_time_utc_str, side, shares, existing_payload_json = row

            # pnl_pct beregning — spejlvendes for short.
            # ⚠ NORMALISÉR CASE. Sammenligningen var case-følsom, og en kalder der
            # sendte "LONG" fik derfor SHORT-formlen: en vindende long kom ud med
            # negativ pnl_pct mens pnl var positiv. Ingen fejl, intet råb — bare et
            # forkert fortegn i journalen. Alle nuværende algoer sender "long", så
            # ingen historiske rækker er ramt (verificeret: nul rækker hvor pnl og
            # pnl_pct har modsat fortegn), men fælden lå der og bed den første nye
            # kalder — den manuelle forensik, 2026-08-04.
            side = (side or "long").strip().lower()
            if side == "long":
                pnl_pct = (exit_price - entry_price) / entry_price * 100
            else:
                pnl_pct = (entry_price - exit_price) / entry_price * 100

            # Tidsstempler
            if exit_time.tzinfo is None:
                exit_utc = exit_time.replace(tzinfo=timezone.utc)
            else:
                exit_utc = exit_time.astimezone(timezone.utc)
            exit_et = exit_utc.astimezone(ET)

            # Duration
            entry_utc = datetime.fromisoformat(entry_time_utc_str)
            duration_sec = int((exit_utc - entry_utc).total_seconds())

            # Merge payloads
            try:
                merged_payload = json.loads(existing_payload_json or "{}")
            except json.JSONDecodeError:
                merged_payload = {}
            if payload:
                merged_payload.update(payload)

            await self._db.execute(
                """
                UPDATE trades
                SET exit_time_utc = ?,
                    exit_time_et  = ?,
                    exit_price    = ?,
                    exit_reason   = ?,
                    pnl           = ?,
                    pnl_pct       = ?,
                    duration_sec  = ?,
                    payload_json  = ?
                WHERE trade_id = ?
                """,
                (
                    exit_utc.isoformat(),
                    exit_et.isoformat(),
                    exit_price,
                    exit_reason,
                    round(pnl, 2),
                    round(pnl_pct, 4),
                    duration_sec,
                    json.dumps(merged_payload, default=str),
                    trade_id,
                ),
            )
            await self._db.commit()
            replication.notify_change()   # poke replikering — billigt flag-sæt
            return True

        except Exception as e:
            logger.error(f"[Journal] log_trade_close fejl ({trade_id}): {e}")
            return False

    async def update_trade_state(
        self,
        trade_id:       str,
        current_stop:   Optional[float] = None,
        current_target: Optional[float] = None,
        current_stage:  Optional[str]  = None,
        trail_stop:     Optional[float] = None,
        current_price:  Optional[float] = None,
    ) -> bool:
        """
        Opdater live position-state mens en trade er åben.

        Kaldes hver gang ExitEngine ratcheter et stop, aktiverer BE-stage
        eller flytter trail-stop. Disse felter fryses automatisk ved exit
        (vi opdaterer dem ikke længere når exit_time_utc er sat).

        Returnerer True ved succes. Sender man None for en parameter,
        opdateres den IKKE (dvs. eksisterende værdi bevares).
        """
        if self._db is None:
            return False

        # Byg SET-clause dynamisk så vi kun opdaterer de felter der er sendt
        sets = []
        params = []
        if current_stop is not None:
            sets.append("current_stop = ?")
            params.append(current_stop)
        if current_target is not None:
            sets.append("current_target = ?")
            params.append(current_target)
        if current_stage is not None:
            sets.append("current_stage = ?")
            params.append(current_stage)
        if trail_stop is not None:
            sets.append("trail_stop = ?")
            params.append(trail_stop)
        if current_price is not None:
            sets.append("current_price = ?")
            params.append(current_price)

        if not sets:
            return True  # ingenting at opdatere — ikke en fejl

        params.append(trade_id)
        sql = (
            f"UPDATE trades SET {', '.join(sets)} "
            "WHERE trade_id = ? AND exit_time_utc IS NULL"
        )

        try:
            await self._db.execute(sql, params)
            await self._db.commit()
            replication.notify_change()   # poke replikering — billigt flag-sæt
            return True
        except Exception as e:
            logger.error(f"[Journal] update_trade_state fejl ({trade_id}): {e}")
            return False

    async def update_trade_notes(self, trade_id: str, notes: str) -> bool:
        """
        Opdater notes på en trade. Brugbart fra UI når Iben/Søren vil
        tilføje en kommentar til en eksisterende handel.

        Kan også opdatere lukkede trades (i modsætning til state-felter).

        ⚠ RETURNERER FALSE HVIS INGEN RAEKKE BLEV RAMT.

        Her stod `return True` ubetinget. En UPDATE der matcher nul raekker er ikke
        en fejl i SQL — den er bare en no-op — saa et forkert trade_id gav 200 OK,
        og Studio skiftede tilbage til visningstilstand som om noten var gemt.
        Noten var vaek, og der kom ingen fejl nogen steder.

        Det sker i praksis naar man i maskinvaelgeren ser en ANDEN maskines handler:
        raekkerne laeses fra dens replikerede arkiv, mens skrivningen gaar til den
        LOKALE database, hvor det trade_id ikke findes.

        rowcount skelner de to. Det er den samme sygdom som resten af projektets
        fund: et svar hvis udfald er afgjort paa forhaand.
        """
        if self._db is None:
            return False
        try:
            cur = await self._db.execute(
                "UPDATE trades SET notes = ? WHERE trade_id = ?",
                (notes, trade_id),
            )
            await self._db.commit()
            ramt = cur.rowcount
            if ramt < 1:
                logger.warning(
                    f"[Journal] update_trade_notes: trade_id {trade_id} findes ikke "
                    f"i DENNE database — intet gemt. Ses en anden maskines handler "
                    f"i maskinvaelgeren, skal noten skrives dér.")
                return False
            # En note ER handelsdata. Uden dette skub ville den foerst naa
            # algoserveren ved den periodiske replikering op til to minutter senere.
            replication.notify_change()
            return True
        except Exception as e:
            logger.error(f"[Journal] update_trade_notes fejl ({trade_id}): {e}")
            return False

    async def count_trades(self) -> int:
        """Antal trades i alt (åbne + lukkede). Bruges af /health."""
        if self._db is None:
            return 0
        async with self._db.execute("SELECT COUNT(*) FROM trades") as cur:
            row = await cur.fetchone()
            return row[0] if row else 0