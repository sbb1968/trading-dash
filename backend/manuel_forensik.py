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

import json
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
        # fetch_trade_bars returnerer kolonner med STORT begyndelsesbogstav.
        # Foerste udgave laeste dem med smaat: r.get("open") gav None, og _byg_bar
        # gjorde None til 0.0. Alle 44 bars blev bygget som nuller. Indikatorerne
        # regnede paa dem uden at klage (rsi_14=100, ema=0, macd=0) og chart_bars
        # blev tom. Intet i logfilen, ingen fejl — bare et forkert facit.
        # Derfor tjekkes kolonnerne nu EKSPLICIT foer vi bygger noget.
        manglende = [k for k in ("Open", "High", "Low", "Close")
                     if k not in df.columns]
        if manglende:
            logger.error(
                f"[ManuelForensik] {symbol}: bar-kolonner mangler {manglende} — "
                f"fik {list(df.columns)}. Bygger INGEN bars frem for nul-bars.")
            return []
        ud = []
        for ts, r in df.iterrows():
            ud.append(_byg_bar(ts, r["Open"], r["High"], r["Low"],
                               r["Close"], r.get("Volume")))
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

    ⚠ Priser maa IKKE falde tilbage til 0.0. Det gjorde de foer, og da kolonnerne
    hed "Open" og ikke "open", blev hver eneste bar til en nul-bar som indikatorerne
    regnede videre paa uden at klage. En manglende pris er en FEJL, ikke et nul —
    kun volumen maa mangle (nogle feeds giver den ikke).
    """
    from strategies.base import Bar
    return Bar(timestamp=ts,
               open=float(o), high=float(h), low=float(l), close=float(c),
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
# AFSTEMNING MOD ORDRE-TRACKEREN — kontrollen uden deadline
# ═══════════════════════════════════════════════════════════════════════════════
# ⚠ ANLEDNINGEN: TRE ORDRER AFSKREVET SOM "IKKE FYLDT", ALLE FYLDT
#
#   13-08 16:45  ordre 29  ordre_ikke_fyldt (PreSubmitted)  ->  Filled @ 7827,25
#   17-08 15:31  ordre 67  ordre_ikke_fyldt (PreSubmitted)  ->  Filled @ 7808,75
#   18-08 16:30  ordre 76  ordre_ikke_fyldt (PendingSubmit) ->  Filled @ 7730,75
#
# Journalen kiggede paa ordrestatus ÉN gang, ~1 sekund efter afsendelsen, og
# skrev ordren af. Ordre-trackeren fulgte op og fik alle tre bekraeftet af IBKR.
# De to systemer stod side om side paa samme maskine med hvert sit svar, og
# INGEN sammenlignede dem.
#
# ⚠ AT VENTE LAENGERE ER IKKE LOESNINGEN — DET FLYTTER KLIPPEKANTEN.
# `place_paper_order(await_fill_sec=...)` gaar fra ét kig til femten sekunders
# kig, og det havde fanget alle tre. Men en ordre kan fylde paa det sekstende.
# Det er praecis fejlen fra reconcile-timeouten: budgettet var 30 sekunder, K2
# brugte 32, og konsekvensen af at loebe toer var at fortsaette som om man bestod.
#
# Derfor TO ting, og den anden er den strukturelle:
#   1. vent paa fyldningen i stedet for at gaette (await_fill_sec i main.py)
#   2. DENNE afstemning, som ingen deadline har: den spoerger bagefter, uanset
#      hvor lang tid der gik, om trackeren kender en bekraeftet fyldning som
#      journalen ikke har et spor af.
#
# ⚠ DEN RETTER IKKE NOGET. Den rapporterer. At bogfoere en trade-raekke ud fra en
# loes fill kraever at man parrer entry med exit — og det er netop dér journalen
# allerede kom galt afsted. En kontrol der ogsaa reparerer, skjuler hvor slem
# skaden var.
#
# ⚠ MATCHET ER DETERMINISTISK, IKKE FUZZY. `ibkr_order_id` staar i trade-raekkens
# payload (skrevet af registrer_entry/registrer_exit), saa der sammenlignes paa
# id — ikke paa "symbol + pris + nogenlunde samme tid". Et fuzzy match ville
# parre to MES-handler til samme pris paa samme minut, og fejlen ville vaere
# usynlig indtil den kostede noget.

async def _bogfoerte_ordre_ider(journal) -> set:
    """Ordre-id'er journalen HAR et spor af (entry eller exit)."""
    ider: set = set()
    try:
        db = journal.db
        if db is None:
            return ider
        async with db.execute(
            "SELECT payload FROM trades WHERE source = ? AND payload IS NOT NULL",
            (KILDE,)) as cur:
            raekker = await cur.fetchall()
    except Exception as e:
        logger.error(f"[ManuelForensik] kunne ikke laese trades: {e}")
        return ider
    for (raa,) in raekker:
        try:
            p = json.loads(raa) if isinstance(raa, str) else (raa or {})
        except Exception:
            continue
        for noegle in ("ibkr_order_id", "ibkr_order_id_exit"):
            v = p.get(noegle)
            if v is not None:
                ider.add(str(v))
    return ider


async def afstem_mod_tracker(journal, tracker_ordrer: list) -> dict:
    """Bekraeftede fyldninger uden spor i journalen.

    `tracker_ordrer` er OrdersTracker.get_all_orders()-formatet. Kun manuelle
    kilder er relevante — algoernes handler bogfoeres ad en anden vej.
    """
    bogfoerte = await _bogfoerte_ordre_ider(journal)
    ubogfoerte, set_i_alt = [], 0
    for o in tracker_ordrer or []:
        if o.get("source") not in ("manual_watchlist", KILDE):
            continue
        if not o.get("bekraeftet") or float(o.get("filled") or 0) <= 0:
            continue
        set_i_alt += 1
        if str(o.get("order_id")) not in bogfoerte:
            ubogfoerte.append({
                "order_id": o.get("order_id"),
                "tid": o.get("placed_at"),
                "ticker": o.get("ticker"),
                "action": o.get("action"),
                "filled": o.get("filled"),
                "avg_fill": o.get("avg_fill"),
                "status": o.get("status"),
            })
    return {"bekraeftede_fills": set_i_alt,
            "bogfoerte_ordre_ider": len(bogfoerte),
            "ubogfoerte": sorted(ubogfoerte, key=lambda x: str(x["tid"]))}


async def alarmer_om_ubogfoerte(journal, tracker_ordrer: list) -> dict:
    """Koer afstemningen og RAAB OP hvis der er huller.

    ⚠ En afstemning ingen laeser, er ikke en kontrol. Derfor samme alarmvej som
    exit_uden_aaben_entry — og hændelsen i journalen, saa den kan findes bagefter.
    """
    r = await afstem_mod_tracker(journal, tracker_ordrer)
    if not r["ubogfoerte"]:
        return r
    await journal.log_event(
        source=KILDE, event_type="fills_uden_journalspor",
        symbol=(r["ubogfoerte"][0].get("ticker") or None),
        payload=r)
    try:
        import notifier
        linjer = ", ".join(f"{u['action']} {u['filled']:g} {u['ticker']} @ "
                           f"{u['avg_fill']} (ordre {u['order_id']})"
                           for u in r["ubogfoerte"][:5])
        await notifier.alert_backend_error(
            f"{len(r['ubogfoerte'])} bekraeftede fyldning(er) uden spor i "
            f"journalen: {linjer}")
    except Exception as e:
        logger.error(f"[ManuelForensik] alarm om ubogfoerte fills fejlede: {e}")
        await journal.log_event(
            source=KILDE, event_type="alarm_fejlede",
            payload={"anledning": "fills_uden_journalspor", "fejl": str(e)})
    return r


# ═══════════════════════════════════════════════════════════════════════════════
# SALGSVAGT — kontrollen skal ligge FOER ordren, ikke efter fyldningen
# ═══════════════════════════════════════════════════════════════════════════════
# ⚠ ANLEDNINGEN, MAALT PAA DUQ441063 17.-19. AUGUST 2026
#
#   17-08 13:34:37  SELL 1 MES @ 7797,25  ->  "exit_uden_aaben_entry"
#
# Salget fyldte. Der var ingen aaben raekke. Systemet NAVNGAV problemet korrekt i
# loggen — og gik videre. Fra det sekund var journalen én kontrakt ude af fase med
# brokeren, og alt derefter blev maerket forkert:
#
#   17-08 16:22  koeb bogfoert som NY long   ->  lukkede i virkeligheden shorten
#   18-08 15:01  salg bogfoert som exit -406,25  ->  aabnede i virkeligheden en short
#   19-08 06:39  Soeren koeber manuelt for at flade ud  ->  slet ikke i journalen
#
# Journalen sagde -493,75 hvor brokeren sagde -33,75. PRAECIS 460 DOLLAR forkert,
# og ingen af de fire hændelser var en kodefejl i sig selv — de var alle
# foelgevirkninger af den ene kontrol der loggede i stedet for at stoppe.
#
# Det er forekomst nr. 12 af projektets faste fejlklasse: EN KONTROL HVIS FEJL
# BEHANDLES SOM EN BESTAAELSE. `exit_uden_aaben_entry` fyrer EFTER fyldningen —
# pengene er allerede flyttet, og en hændelse i loggen kan ikke tage dem tilbage.
#
# ⚠ HVORFOR VAGTEN SPOERGER BROKEREN OG IKKE JOURNALEN
# De to spoergsmaal er forskellige, og kun det ene er farligt:
#   · journalen kender den ikke, brokeren HAR den   -> et lovligt salg af en
#     ujournaliseret position. SKAL tillades; det er netop oprydning.
#   · brokeren har den IKKE                         -> salget AABNER en short.
# Den gamle kontrol stillede det foerste spoergsmaal. Kun det andet betyder noget.
#
# ⚠ HVORFOR AFVIS OG IKKE BESKAER
# En beskaeret ordre ville stiltiende goere noget andet end det brugeren bad om;
# en afvisning siger det.
#
# ⚠ RETTET 25-08: her stod at vinduet var "long-only ved konstruktion", og at
# short derfor maatte afvises. Det var sandt om bogfoeringen den dag, men det
# gjorde en MANGEL til et FORBUD — og et forbud mod noget helt normalt. Iben stod
# midt i en handelsdag og kunne ikke aabne en short paa MES. Manglen er lukket i
# stedet: vinduet bogfoerer nu begge retninger.

SALGSVAGT_EVENT = "ordre_afvist_ville_vende_position"
SALGSVAGT_UKONTROLLERET = "salg_uden_positionskontrol"


async def kontroller_ordre(ibkr, symbol: str, action: str,
                           shares: int) -> tuple[bool, str, dict]:
    """Maa dette salg sendes? -> (ok, besked, detaljer).

    ⚠ ET UPAALIDELIGT OPSLAG AFVISER IKKE. `get_positions_reliable()` siger selv i
    sin docstring at reliable=False ikke maa tolkes som "fladt", og den regel gaelder
    ogsaa den anden vej: kan vi ikke laese positionen, ved vi ikke at salget er
    forkert.

    Afvejningen er ikke symmetrisk, og det er derfor den falder saadan ud:
      · at forhindre nogen i at lukke en position de FAKTISK har = ubegraenset risiko
      · at komme til at aabne en 1-lot short ved et uheld  = begraenset og opdageligt
    Derfor: tillad, men MAERK det. Et hul man kan se i loggen er ikke det samme som
    det hul vi lukker her.
    """
    if ibkr is None or not getattr(ibkr, "connected", False):
        return True, "", {"kontrolleret": False, "grund": "ikke forbundet"}
    try:
        poss, paalideligt = await ibkr.get_positions_reliable()
    except Exception as e:
        return True, "", {"kontrolleret": False, "grund": f"opslag fejlede: {e}"}
    if not paalideligt:
        return True, "", {"kontrolleret": False, "grund": "positionsopslag upaalideligt"}

    netto = sum(float(p.get("position") or 0)
                for p in poss if p.get("ticker") == symbol)
    salg = (action or "").upper() == "SELL"
    detaljer = {"kontrolleret": True, "netto_hos_broker": netto,
                "action": action, "antal": shares}

    # ⚠ REGLEN ER "VEND IKKE POSITIONEN I ÉN ORDRE", ikke "aabn aldrig en short".
    #
    # Den foerste udgave (19-08) afviste ethvert salg der ville aabne en short,
    # med den begrundelse at watchlist-vinduet ikke kunne BOGFOERE en short. Det
    # var sandt om koden — men det gjorde en bogfoeringsmangel til et forbud mod
    # en helt normal handling. Paa MES er short lige saa almindelig som long, og
    # 25-08 stod Iben og kunne ikke handle.
    #
    # Nu kan shorten bogfoeres (se routingen i main.py), og forbuddet er
    # overfloedigt. Tilbage staar den beskyttelse der faktisk betoed noget:
    # vinduet kan holde ÉN position ad gangen, saa en ordre der baade lukker den
    # ene vej og aabner den anden, kan ikke repraesenteres. Praecis dét var
    # over-salget 31-07: en lang position solgt for meget, som endte som en
    # ejerloes short.
    if salg and netto > 0 and shares > netto:
        return False, (f"Brokeren har {netto:g} {symbol}. Et salg paa {shares} ville "
                       f"lukke den OG aabne en short paa {shares - netto:g} i samme "
                       f"ordre — det kan vinduet ikke bogfoere. Luk foerst, "
                       f"aabn saa."), detaljer
    if not salg and netto < 0 and shares > -netto:
        return False, (f"Brokeren er short {-netto:g} {symbol}. Et koeb paa {shares} "
                       f"ville daekke den OG aabne en long paa {shares + netto:g} i "
                       f"samme ordre — det kan vinduet ikke bogfoere. Luk foerst, "
                       f"aabn saa."), detaljer
    return True, "", detaljer


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
        # ⚠ DET HER STOD FOER SOM "ikke en fejl". Det var forkert, og det kostede
        # 460 dollar i fejlbogfoering paa DUQ441063 — se noten ved kontroller_salg.
        #
        # Naar vi naar hertil, ER handlen sket. Salgsvagten skulle have stoppet den
        # foer ordren blev sendt; naar den alligevel slap igennem, er der to
        # muligheder, og BEGGE kraever at et menneske kigger:
        #   · positionen blev aabnet et andet sted (algo eller direkte i TWS)
        #     -> journalen mangler en raekke, og parringen bagefter bliver forkert
        #   · der var slet ingen position -> vi har lige aabnet en short
        # Derfor en ALARM og ikke kun en linje i loggen. En hændelse ingen laeser,
        # er ikke en kontrol.
        payload = {"shares": shares, "fill": fill_pris, "ibkr_order_id": ordre_id,
                   "note": "salg uden en aaben manuel entry — positionen kan vaere "
                           "aabnet af en algo eller direkte i TWS, ELLER salget har "
                           "aabnet en short"}
        await journal.log_event(
            source=KILDE, event_type="exit_uden_aaben_entry", symbol=symbol,
            payload=payload,
        )
        try:
            import notifier
            await notifier.alert_backend_error(
                f"{symbol}: salg paa {shares} fyldt @ {fill_pris} UDEN aaben manuel "
                f"entry (ordre {ordre_id}). Journalen er nu muligvis ude af fase med "
                f"brokeren — tjek positionen.")
        except Exception as e:
            # Alarmen maa aldrig vaelte ordre-stien. Men at den fejlede skal ogsaa
            # kunne ses, ellers er vi tilbage ved tavshed.
            logger.error(f"[ManuelForensik] alarm om {symbol} kunne ikke sendes: {e}")
            await journal.log_event(
                source=KILDE, event_type="alarm_fejlede", symbol=symbol,
                payload={"anledning": "exit_uden_aaben_entry", "fejl": str(e)})
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

    Hentes fra futures_katalog, som er ÉN sandhedskilde for MES/M2K (bekraeftet live
    mod reqPositions-avgCost). Laeses dér frem for at skrive tallet op igen, saa en
    fremtidig aendring kun skal ét sted hen.
    """
    try:
        from futures_katalog import multiplikator
        return multiplikator(symbol)
    except Exception:
        return 1.0


def _som_utc(v) -> datetime:
    """ISO-streng eller datetime -> tz-aware UTC. Naive stempler antages UTC."""
    if isinstance(v, datetime):
        d = v
    else:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
