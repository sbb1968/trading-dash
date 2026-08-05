"""
manuel_forensik.py — manuelle handler skal efterlade samme spor som algoernes
═══════════════════════════════════════════════════════════════════════════════════
FOER dette modul efterlod en manuel handel fra watchlist-vinduet PRAECIS dette:

    {"action": "BUY", "shares": 100, "order_id": 123,
     "status": "Inactive", "filled": 0.0, "avg_fill": 0.0}

Seks felter i ét `events`-rowe. Ingen raekke i `trades`, ingen entry/exit-parring,
ingen P&L, ingen indikatorer, intet chart. Databasen viste det entydigt: 92 handler i
`trades`, fordelt paa tre algoer — og NUL fra manuel handel.

Der fandtes ganske vist et REST-endpoint `/journal/manual-trade` der skrev en
trades-raekke, men INGEN frontend kaldte det. Watchlist-knapperne gik ad
WebSocket-stien (`ibkr_buy`/`ibkr_sell`), som kun journaliserede ordren.

Dette modul lukker hullet, og gaar gennem PRAECIS de samme funktioner som algoerne:

    journal.log_trade_open / log_trade_close     — samme ene skrivepunkt til `trades`
    trade_forensics.build_entry_snapshot         — samme indikator-snapshot
    trade_forensics.build_exit_snapshot          — samme exit-snapshot
    trade_chart.fetch_trade_bars                 — samme bar-hentning, samme roll-logik

⚠ TRE STEDER HVOR MANUEL ER AERLIGT ANDERLEDES, og hvor det staar i data frem for
kun her:

 1. `chart_bars` hentes VED EXIT, ikke undervejs. En algo gemmer de bars den faktisk
    evaluerede (`self._bar_history`); et menneske har ingen saadan buffer. Barerne er
    stadig hentet paa handelsdagen og gemt — ikke rekonstrueret maaneder senere — men
    paastanden er en anden, og derfor staar `chart_bars_kilde` i payloadet.

 2. Ingen stop eller target. Watchlist-vinduet har ingen felter til dem, saa
    `stop_trajectory` er tom og charten tegner ingen stop-linje. Det er ikke en
    mangel i logningen; der ER ingen stop at logge.

 3. Ingen `context` og ingen tape. Det deler manuel med fire af de seks algoer —
    kun Konfluens 2 sender ægte context og tape. Indikatorerne kommer fra bars, og
    det er de samme indikatorer.

⚠ ÉT STED HVOR MANUEL ER BEDRE END ALGOERNE, og det boer rettes dér ogsaa:
`trade_forensics`-eventet baerer her et **trade_id**. Algoernes gør ikke, saa deres
forensik-snapshots kan kun parres med handlen heuristisk paa ticker+tid
(`show_forensics.pair_entries_and_exits`). Her er koblingen deterministisk.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

KILDE = "manual"                 # `source` i trades — samme vaerdi som REST-endpointet
ENTRY_REASON = "Manuel handel (watchlist)"


# ═══════════════════════════════════════════════════════════════════════════════
# Bars — hentes gennem SAMME funktion som Handels-charten bruger
# ═══════════════════════════════════════════════════════════════════════════════
async def _hent_bars(ibkr, symbol: str, entry_utc: datetime,
                     exit_utc: datetime) -> list:
    """Bars for handels-vinduet, som liste af Bar-agtige objekter.

    Genbruger `trade_chart.fetch_trade_bars`, saa manuel faar samme kontrakt-roll-
    haandtering og samme bar-parametre som algoerne. Fejl-sikker: en tom liste
    betyder "ingen bars", aldrig et nedbrud i ordre-stien.
    """
    try:
        from trade_chart import fetch_trade_bars
        # Foerste argument er forbindelses-MANAGEREN (har .resolve_contract_asof og
        # .ib), ikke ib_async-objektet selv.
        df = await fetch_trade_bars(ibkr, symbol, KILDE, entry_utc, exit_utc)
        if df is None or df.empty:
            return []
        ud = []
        for ts, r in df.iterrows():
            ud.append(_byg_bar(ts, r.get("open"), r.get("high"), r.get("low"),
                               r.get("close"), r.get("volume")))
        return ud
    except Exception as e:
        logger.warning(f"[ManuelForensik] kunne ikke hente bars for {symbol}: {e}")
        return []


def _byg_bar(ts, o, h, l, c, v):
    """Byg kodebasens EGEN Bar frem for en look-alike.

    Foerste udgave var en egen `_Bar` med feltet `date`. Indikatorerne blev beregnet
    fint (de laeser open/high/low/close/volume), men `bars_to_chart_payload` bruger
    `b.timestamp` — saa hver eneste bar kastede i dens try/except og blev sprunget
    over. Resultatet var `chart_bars: []` uden en eneste fejlmeddelelse: chartet ville
    bare have vaeret tomt, og ingen ville have vidst hvorfor.

    Ved at bruge den rigtige dataclass kan feltnavnene ikke divergere igen.
    """
    from strategies.base import Bar
    return Bar(timestamp=ts,
               open=float(o) if o is not None else 0.0,
               high=float(h) if h is not None else 0.0,
               low=float(l) if l is not None else 0.0,
               close=float(c) if c is not None else 0.0,
               volume=float(v) if v is not None else 0.0)


# ═══════════════════════════════════════════════════════════════════════════════
# Opslag: den aabne manuelle handel for en ticker
# ═══════════════════════════════════════════════════════════════════════════════
async def find_aaben(journal, symbol: str) -> Optional[dict]:
    """Aeldste aabne manuelle handel for tickeren (FIFO), eller None.

    FIFO fordi det er den konvention en handelsjournal normalt foelger, og fordi
    valget skal vaere forudsigeligt frem for smart.
    """
    try:
        db = journal.db
        if db is None:
            return None
        async with db.execute(
            "SELECT trade_id, symbol, side, shares, entry_price, entry_time_utc "
            "FROM trades "
            "WHERE source = ? AND symbol = ? AND exit_time_utc IS NULL "
            "ORDER BY entry_time_utc ASC LIMIT 1",
            (KILDE, symbol.upper()),
        ) as cur:
            r = await cur.fetchone()
        if r is None:
            return None
        return {"trade_id": r[0], "symbol": r[1], "side": r[2], "shares": r[3],
                "entry_price": r[4], "entry_time_utc": r[5]}
    except Exception as e:
        logger.warning(f"[ManuelForensik] opslag af aaben handel fejlede: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# ENTRY
# ═══════════════════════════════════════════════════════════════════════════════
async def registrer_entry(journal, ibkr, *, symbol: str, side: str, shares: int,
                          fill_pris: float, ordre_id: Any, ordre_status: str,
                          et_tz) -> Optional[str]:
    """Skriv trades-raekken OG forensik-snapshottet for en manuel entry.

    Returnerer trade_id, eller None hvis raekken ikke kunne skrives.
    Kaldes EFTER at ordre-resultatet er sendt til frontend, saa et langsomt
    bar-kald aldrig forsinker klikket.
    """
    entry_time = datetime.now(et_tz)

    trade_id = await journal.log_trade_open(
        source=KILDE,
        symbol=symbol,
        side=side,
        shares=shares,
        entry_price=fill_pris,
        entry_time=entry_time,
        variant=None,
        entry_reason=ENTRY_REASON,
        notes=None,
        payload={
            "ibkr_order_id": ordre_id,
            "ibkr_status": ordre_status,
            "indgang": "watchlist",
        },
    )
    if trade_id is None:
        logger.error(f"[ManuelForensik] {symbol}: trades-raekke kunne IKKE skrives")
        return None

    # ── Forensik-snapshot, samme builder som algoerne ─────────────────────────
    try:
        from trade_forensics import build_entry_snapshot
        entry_utc = entry_time.astimezone(timezone.utc)
        bars = await _hent_bars(ibkr, symbol, entry_utc, entry_utc)
        snap = build_entry_snapshot(
            ticker=symbol,
            entry_price=fill_pris,
            entry_time=entry_time,
            shares=shares,
            bars=bars,
            context={},              # som fire af de seks algoer
            tape_buffer=None,        # kun Konfluens 2 har tape
            variant_name=KILDE,
        )
        # DET DER MANGLER HOS ALGOERNE: en deterministisk noegle til handlen.
        snap["trade_id"] = trade_id
        snap["manuel"] = True
        await journal.log_event(
            source=KILDE, event_type="trade_forensics",
            symbol=symbol, payload=snap,
        )
    except Exception as e:
        # Handlen er registreret; kun snapshottet mangler. Det maa aldrig vaelte
        # ordre-stien, men det skal kunne ses at det skete.
        logger.warning(f"[ManuelForensik] {symbol}: entry-snapshot fejlede: {e}")
        await journal.log_event(
            source=KILDE, event_type="forensik_fejl", symbol=symbol,
            payload={"fase": "entry", "trade_id": trade_id, "fejl": str(e)},
        )
    return trade_id


# ═══════════════════════════════════════════════════════════════════════════════
# EXIT
# ═══════════════════════════════════════════════════════════════════════════════
async def registrer_exit(journal, ibkr, *, symbol: str, shares: int,
                         fill_pris: float, ordre_id: Any, ordre_status: str,
                         et_tz) -> Optional[str]:
    """Luk den aabne manuelle handel og skriv exit-forensikken.

    Returnerer trade_id, eller None hvis der ikke var en aaben manuel handel —
    fx hvis positionen blev aabnet et andet sted end watchlist-vinduet.
    """
    aaben = await find_aaben(journal, symbol)
    if aaben is None:
        # Ikke en fejl: et salg uden en aaben manuel entry er et salg af noget vi
        # ikke har logget. Det skal registreres, ikke skjules.
        await journal.log_event(
            source=KILDE, event_type="exit_uden_aaben_entry", symbol=symbol,
            payload={"shares": shares, "fill": fill_pris, "ibkr_order_id": ordre_id,
                     "note": "salg uden en aaben manuel entry — positionen kan vaere "
                             "aabnet af en algo eller direkte i TWS"},
        )
        return None

    trade_id = aaben["trade_id"]
    entry_pris = float(aaben["entry_price"] or 0.0)
    entry_antal = int(aaben["shares"] or 0)
    side = (aaben["side"] or "LONG").upper()
    exit_time = datetime.now(et_tz)

    # P&L paa det antal der FAKTISK blev lukket.
    #
    # ⚠ MULTIPLIKATOREN. Watchlist-vinduet kan handle MES og M2K, og en future
    # afregnes i $ PR. PRISPOINT — ikke i $ pr. aktie. MES er $5/point, saa 2
    # kontrakter der bevaeger sig 6,50 point giver 65 USD, ikke 13.
    #
    # Foerste udgave regnede (exit-entry) x antal og gav 13. Tallet ser rimeligt ud,
    # journalen ville have set rigtig ud, og fejlen var foerst dukket op naar Ibens
    # kontoudtog ikke stemte med hendes egen handelsjournal. Europa-reversion har
    # formlen rigtigt (algo_europa_reversion.py:1031) — manuel havde den ikke.
    lukket = min(shares, entry_antal) if entry_antal else shares
    retning = 1.0 if side.lower() in ("long", "buy") else -1.0
    mult = _multiplikator(symbol)
    pnl = (fill_pris - entry_pris) * lukket * retning * mult

    payload: dict[str, Any] = {
        "ibkr_order_id_exit": ordre_id,
        "ibkr_status_exit": ordre_status,
        "chart_bars_kilde": "hentet ved exit (manuel handel har ingen bar-buffer)",
        "stop_trajectory": [],       # manuel har ingen stop — se modulets docstring
    }
    if shares != entry_antal:
        # Delvis luk eller for stort salg. Vi lukker raekken helt, men tallene skal
        # staa der, saa en senere analyse kan se at de ikke stemte.
        payload["antal_uoverensstemmelse"] = {
            "entry_shares": entry_antal, "exit_shares": shares,
            "pnl_beregnet_paa": lukket,
            "note": "raekken lukkes helt; delvise lukninger foeres ikke som "
                    "separate raekker",
        }

    # ── Bars + exit-snapshot, samme buildere som algoerne ─────────────────────
    bars: list = []
    try:
        entry_utc = _som_utc(aaben["entry_time_utc"])
        exit_utc = exit_time.astimezone(timezone.utc)
        bars = await _hent_bars(ibkr, symbol, entry_utc, exit_utc)
        if bars:
            from trade_forensics import bars_to_chart_payload
            payload["chart_bars"] = bars_to_chart_payload(
                bars, entry_time=entry_utc)
    except Exception as e:
        logger.warning(f"[ManuelForensik] {symbol}: bar-hentning ved exit fejlede: {e}")

    ok = await journal.log_trade_close(
        trade_id=trade_id, exit_price=fill_pris, exit_time=exit_time,
        exit_reason="manuel_salg", pnl=pnl, payload=payload,
    )
    if not ok:
        logger.error(f"[ManuelForensik] {symbol}: kunne ikke lukke trade {trade_id}")

    try:
        from trade_forensics import build_exit_snapshot
        snap = build_exit_snapshot(
            ticker=symbol, entry_price=entry_pris, exit_price=fill_pris,
            entry_time=_som_utc(aaben["entry_time_utc"]), exit_time=exit_time,
            shares=lukket, pnl=pnl, reason="manuel_salg",
            bars=bars, context={}, tape_buffer=None, variant_name=KILDE,
        )
        snap["trade_id"] = trade_id
        snap["manuel"] = True
        await journal.log_event(source=KILDE, event_type="trade_forensics",
                                symbol=symbol, payload=snap)
    except Exception as e:
        logger.warning(f"[ManuelForensik] {symbol}: exit-snapshot fejlede: {e}")
        await journal.log_event(
            source=KILDE, event_type="forensik_fejl", symbol=symbol,
            payload={"fase": "exit", "trade_id": trade_id, "fejl": str(e)},
        )
    return trade_id


def _multiplikator(symbol: str) -> float:
    """$ pr. prispoint. 1,0 for aktier, kontraktens multiplier for futures.

    Hentes fra Europa-reversions config, som er den eksisterende sandhedskilde for
    MES/M2K (og som er bekraeftet live mod reqPositions-avgCost). At laese den dér
    frem for at skrive tallet op igen betyder at en fremtidig aendring kun skal ét
    sted hen.
    """
    try:
        from strategies.europa_reversion.config import MULTIPLIER
        return float(MULTIPLIER.get(symbol.upper().strip(), 1.0))
    except Exception:
        return 1.0


def _som_utc(v) -> datetime:
    """ISO-streng eller datetime -> tz-aware UTC. Naive stempler antages UTC."""
    if isinstance(v, datetime):
        d = v
    else:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
