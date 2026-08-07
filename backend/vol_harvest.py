r"""
vol_harvest.py — FASE V1: byg vol_cache for spor 1 (ETF og indeks)
═══════════════════════════════════════════════════════════════════════════════════
Henter det laaste instrumentsaet fra Revision A og laegger det i `vol_cache/`.
Futures-delen (MES/M2K) er allerede i hus i `data_harvest/` og roeres ikke herfra —
se vol_harvest_plan.md afsnit 0 for hvor data ligger.

SPECENS KRAV TIL V1, og hvor de er loest:

  · Inkrementel opdatering — et dagligt kald henter kun det nye  ->  `siden_sidst()`
  · Manifest pr. serie med IBKR-parametrene der blev brugt      ->  `skriv_manifest()`
  · Idempotent — koer to gange, faa samme resultat               ->  dedup paa bar-tid
  · Noegles paa DATA-dato, aldrig paa koerselsdato               ->  `by_ts`-noeglen

Det sidste er ikke bogholderi. Det forrige projekt noeglede paa koerselsdato og fik
nye raekker med identiske maalinger ved hver genkoersel — historikken saa ud til at
vokse uden at der kom ny viden.

KONTROLFIKSTUR I BEGGE RETNINGER (E2), permanent og foerst i koerslen:
  · kendt-POSITIV : SPY dagligt de sidste dage SKAL give barer. Goer den ikke det,
    er intet af det der foelger et datafund — det er en doed forbindelse.
  · kendt-NEGATIV : et symbol der umuligt kan findes maa IKKE kvalificere. Goer det
    det, svarer IBKR ja til alt, og hele koerslen kasseres.

Uden begge retninger er "vi fik ingen data" og "der er ingen data" ikke til at skelne
— og den forveksling har allerede kostet os tid én gang i dette projekt.

AFSLUTNING: fuldstaendighedsrevision mod NYSE-kalenderen (B3), saa den effektive
udviklingsstart fastlaegges af hvor sammenhaengen faktisk begynder — ikke af hvor den
foerste bar tilfaeldigvis ligger.

KOERSEL (workstation, TWS aabent):
    python vol_harvest.py --hvad dagligt         # alle dagsserier — minutter
    python vol_harvest.py --hvad 1min            # SPY/IWM/VIX 1-min — TIMER
    python vol_harvest.py --hvad alt
    python vol_harvest.py --hvad dagligt --toerloeb   # vis plan, hent intet

⚠ 1-MIN-DELEN ER LANG. Tre instrumenter × fjorten aar. Den er resumerbar pr. serie —
afbryd trygt, koer igen. Start med `--hvad dagligt`; den aabner lag 1 og 2 med det
samme, mens 1-min kun bruges af lag 3.
"""
from __future__ import annotations

import asyncio

# Python 3.14: skal staa FOER ib_async importeres
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import argparse
import csv
import json
import sys
import time as _ur
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HOST, PORT, CLIENT_ID = "127.0.0.1", 7497, 79
CACHE = "vol_cache"
SLEEP = 1.2

# Hvor ofte 1-min-hentningen melder fremdrift. 1-min gaar i 2-dages bidder over
# 14½ aar — flere tusinde kald pr. serie — og skrev tidligere IKKE en eneste
# linje undervejs. Et job der koerer i timer uden output kan ikke skelnes fra et
# haengt job, og saa er den eneste tilgaengelige handling at afbryde det.
# Tidsbaseret frem for pr. bid, saa kadencen er den samme uanset hvor hurtigt
# IBKR svarer.
STATUS_HVER_SEK = 20
REQ_TIMEOUT = 90

# Genforsoeg ved FEJL (pacing, kortvarigt forbindelsestab). En fejl maa ikke
# tolkes som "ingen data" — se hent_skive(). 3 forsoeg med voksende ventetid
# daekker en pacing-pause uden at hoelde koerslen laenge op.
HENT_FORSOEG = 3
HENT_BACKOFF = 8.0

# ── Det laaste saet (Revision A, afsnit 2.0) ──────────────────────────────────
# `start` er den dybeste dato det giver mening at bede om. For lag 1's serier er
# den sat af percentilreferencens start (B4: 2009-08-17, bundet af VIX3M), for
# 1-min-serierne af hvad A9 verificerede med rigtige barer (2012).
REFERENCE_START = date(2009, 8, 17)
INTRADAG_START = date(2012, 1, 1)

# `forvent_fra` er den foerste bar V0 MAALTE at IBKR har. Ligger den faktisk hentede
# start markant senere, er hentningen afkortet — og det skal raabes op om frem for at
# ende i manifestet som et tal ingen ser paa. Se `for_kort()`.
SERIER = [
    # navn, art, boers, barstoerrelser, hvorfor
    dict(navn="SPY",   art="stk", boers="SMART", bars=["1 day", "1 min"],
         forvent_fra=REFERENCE_START,
         hvorfor="lag 1 realiseret vol (ingen roll-gap) + lag 3 udviklingsproxy for MES"),
    dict(navn="IWM",   art="stk", boers="SMART", bars=["1 day", "1 min"],
         forvent_fra=REFERENCE_START,
         hvorfor="lag 3 udviklingsproxy for M2K (A8) — Russell-benet"),
    dict(navn="VIX",   art="ind", boers="CBOE",  bars=["1 day", "1 min"],
         forvent_fra=REFERENCE_START,
         hvorfor="lag 1 implicit vol-niveau + lag 3"),
    dict(navn="VIX3M", art="ind", boers="CBOE",  bars=["1 day"],
         forvent_fra=REFERENCE_START,
         hvorfor="lag 1 terminsstruktur sammen med VIX"),
    dict(navn="VIX9D", art="ind", boers="CBOE",  bars=["1 day"],
         forvent_fra=date(2018, 6, 22),   # V0: vendor-onboarding, ikke retention
         hvorfor="lag 2 kort ende — begivenhedsrisiko"),
    dict(navn="RVX",   art="ind", boers="CBOE",  bars=["1 day"],
         forvent_fra=REFERENCE_START,
         hvorfor="lag 1 small-cap-vol (A2) — den population vi handler i"),
    dict(navn="VX",    art="contfut", sym="VIX", boers="CFE", bars=["1 day"],
         forvent_fra=date(2023, 11, 24),  # V0: ContFuture-graensen
         hvorfor="lag 2 FRISK implicit vol — spot-VIX opdaterer kun i RTH"),
]

# En serie der starter mere end saa mange dage efter det forventede, regnes som
# afkortet. Rundt tal med vilje: helligdagsklynger og rul flytter en start nogle uger.
AFKORTET_TOLERANCE_DAGE = 45

# Barer pr. aar en DAGSSERIE mindst skal have. NYSE har ~252 handelsdage;
# 230 giver luft til helligdagsklynger og enkelte hul-dage uden at
# acceptere en serie der er tynd inde i perioden.
MIN_DAGSBARER_PR_AAR = 230

# ⚠ CONTFUTURE AFVISER endDateTime (IBKR-fejl 10339). Den bagudgaaende gang der virker
# for alt andet, giver derfor kun det FOERSTE vindue — og resten falder stille paa
# gulvet. Fundet 2026-08-04, da VX kom hjem med 252 barer i stedet for ~677.
# Loesningen er en varighedsstige med TOM endDateTime: bed om det laengste der virker,
# i én forespoergsel.
CONTFUT_STIGE = ["5 Y", "3 Y", "2 Y", "1 Y", "6 M"]

# whatToShow pr. art. Indeks har ingen volumen; TRADES virker for CBOE-indeksene
# (verificeret i V0), ellers falder vi tilbage paa MIDPOINT.
WHAT = {"stk": ["TRADES"], "ind": ["TRADES", "MIDPOINT"], "contfut": ["TRADES"]}

# Hentevinduer. IBKR's graenser er strammest paa 1-min.
CHUNK = {"1 day": "1 Y", "1 min": "2 D"}

# ── Kontrolfikstur, begge retninger (E2) ──────────────────────────────────────
KENDT_POSITIV = dict(navn="SPY", art="stk", boers="SMART", bar="1 day")
KENDT_NEGATIV_SYM = "ZZQQXXNOTREAL"   # kan ikke findes hos nogen boers


def barkode(bar: str) -> str:
    return bar.replace(" ", "").replace("day", "dag").replace("mins", "min")


def sti(rod: Path, navn: str, bar: str) -> Path:
    return rod / CACHE / f"{navn}_{barkode(bar)}.csv"


# ═══════════════════════════════════════════════════════════════════════════════
# Cache-IO — noeglet paa DATA-dato, aldrig paa koerselsdato
# ═══════════════════════════════════════════════════════════════════════════════
def laes_cache(p: Path) -> dict[str, list]:
    """{iso-tidsstempel: raekke}. Tidsstemplet ER noeglen — derfor er genkoersler gratis."""
    ud: dict[str, list] = {}
    if not p.exists():
        return ud
    try:
        with p.open(newline="", encoding="utf-8") as f:
            for r in csv.reader(f):
                if r and r[0] != "timestamp":
                    ud[r[0]] = r
    except Exception:
        return {}
    return ud


def skriv_cache(p: Path, by_ts: dict[str, list]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for k in sorted(by_ts):
            w.writerow(by_ts[k])
    tmp.replace(p)


def sidste_bar(by_ts: dict) -> datetime | None:
    if not by_ts:
        return None
    try:
        return datetime.fromisoformat(max(by_ts))
    except ValueError:
        return None


def siden_sidst(by_ts: dict, dybeste: date) -> date:
    """Hvorfra skal der hentes? Inkrementel hvis der er noget, ellers helt tilbage.

    Overlap paa nogle dage er med vilje: dedup paa tidsstempel goer det gratis, og
    det fjerner risikoen for et hul i soemmen mellem to koersler.

    ⚠ Bruges KUN til at beskrive FRONT-hullet (nye dage siden sidste koersel).
    Til at genoptage en afbrudt dyb hentning er den forkert — se segmenter().
    """
    s = sidste_bar(by_ts)
    if s is None:
        return dybeste
    return max(dybeste, s.date() - timedelta(days=3))


def segmenter(by_ts: dict, dybeste: date, i_dag: date) -> list[tuple]:
    """Hvilke (slut, fra)-stroekninger mangler? Loekken gaar BAGUD fra `slut`
    indtil den rammer `fra`.

    ⚠ HER LAA EN ALVORLIG FEJL. Startpunktet blev beregnet alene af cachens
    NYESTE bar. Det er rigtigt til daglig opdatering, men forkert naar en dyb
    hentning er blevet afbrudt:

        cache efter afbrydelse : 2025-02-14 → 2026-08-04
        siden_sidst() sagde    : 2026-08-01
        loekken stoppede altsaa: efter 3 dage

    Genoptagelsen ville hente tre dage, erklaere sig faerdig og skrive et
    manifest der meldte serien hjemme — med 4.793 af 5.329 dage aldrig hentet.
    Et "genoptag" der ALTID konkluderer "faerdig" er samme sygdom som resten:
    en kontrol hvis udfald er afgjort paa forhaand (Revision G).

    Der er TO huller, og de skal begge lukkes:
      FRONT  nye dage siden sidste koersel (cachens nyeste → i dag)
      BAG    den uafsluttede dybde        (cachens aeldste → dybeste)

    Tom cache giver ét segment der daekker det hele.
    """
    if not by_ts:
        return [("", dybeste)]                      # "" = start ved i dag

    nyeste = sidste_bar(by_ts)
    aeldste_ts = min(by_ts)
    try:
        aeldste = datetime.fromisoformat(aeldste_ts)
    except ValueError:
        return [("", dybeste)]

    ud = []
    # FRONT: kun hvis der faktisk er nye dage. 3 dages overlap som foer.
    if nyeste is not None and nyeste.date() < i_dag:
        ud.append(("", max(dybeste, nyeste.date() - timedelta(days=3))))
    # BAG: kun hvis dybden ikke er naaet. Start lidt SENERE end cachens
    # aeldste, saa soemmen overlapper frem for at stoede op (samme grund som
    # overlappet i loekken).
    if aeldste.date() > dybeste:
        ud.append((aeldste + timedelta(days=3), dybeste))
    return ud


# ═══════════════════════════════════════════════════════════════════════════════
# IBKR
# ═══════════════════════════════════════════════════════════════════════════════
def byg_kontrakt(spec: dict):
    from ib_async import ContFuture, Index, Stock
    art = spec["art"]
    sym = spec.get("sym", spec["navn"])
    if art == "ind":
        return Index(sym, spec["boers"], "USD")
    if art == "contfut":
        return ContFuture(sym, spec["boers"], "USD")
    return Stock(sym, spec["boers"], "USD")


async def hent_skive(ib, contract, slut: datetime | str, bar: str, what: str,
                     varighed: str, emit=None) -> list:
    """Ét vindue barer. Tom liste betyder "IBKR svarede, men havde intet".

    ⚠ EN FEJL MAA IKKE SE UD SOM ET TOMT SVAR. Foer returnerede baade timeout og
    enhver undtagelse en tom liste — og kalderen tolker to tomme svar i traek som
    "serien er udtoemt" og stopper. En pacing-begraensning eller et kortvarigt
    forbindelsestab ville dermed afkorte hentningen i stilhed og skrive et
    manifest der meldte serien hjemme.

    Det er samme sygdom som genoptagelsen havde: en tilstand hvis udfald er
    afgjort paa forhaand, her ved at fejl og tomhed ikke kan skelnes.

    Nu genforsoeges en FEJL med voksende ventetid. Kun naar IBKR faktisk svarer
    uden barer, returneres tom liste — og saa betyder den hvad kalderen tror.
    """
    from ib_async import util  # noqa: F401  (sikrer at ib_async er initialiseret)
    for forsoeg in range(1, HENT_FORSOEG + 1):
        try:
            bars = await asyncio.wait_for(
                ib.reqHistoricalDataAsync(
                    contract,
                    endDateTime=slut if isinstance(slut, str) else slut.replace(tzinfo=timezone.utc),
                    durationStr=varighed, barSizeSetting=bar, whatToShow=what,
                    useRTH=(bar == "1 min"), formatDate=2),
                timeout=REQ_TIMEOUT)
            return list(bars or [])
        except Exception as e:
            if forsoeg >= HENT_FORSOEG:
                # Opgivet efter alle forsoeg. Sig det HOEJT — ellers forsvinder
                # forskellen paa "ingen data" og "kunne ikke hente" igen.
                if emit:
                    emit(f"   ⚠ hentning fejlede {HENT_FORSOEG} gange "
                         f"({type(e).__name__}) — behandles som tomt svar")
                return []
            _vent = HENT_BACKOFF * forsoeg
            if emit and forsoeg == 1:
                emit(f"   (fejl: {type(e).__name__} — genforsoeger om {_vent:.0f}s)")
            await asyncio.sleep(_vent)
    return []


def som_raekke(b) -> tuple[str, list] | None:
    d = getattr(b, "date", None)
    if isinstance(d, datetime):
        ts = (d.replace(tzinfo=None) if d.tzinfo else d).isoformat()
    elif isinstance(d, date):
        ts = d.isoformat()
    else:
        try:
            ts = datetime.fromisoformat(str(d)[:19]).isoformat()
        except ValueError:
            return None
    return ts, [ts, b.open, b.high, b.low, b.close, int(getattr(b, "volume", 0) or 0)]


async def kontrolfikstur(ib, emit) -> bool:
    """Begge retninger, foer noget hentes. Fejler én af dem, er koerslen vaerdiloes."""
    from ibkr_kvalificer import kvalificer_eller_none
    from ib_async import Stock

    # KENDT-POSITIV: SPY dagligt SKAL give barer.
    c = await kvalificer_eller_none(ib, byg_kontrakt(KENDT_POSITIV))
    bars = await hent_skive(ib, c, "", "1 day", "TRADES", "5 D") if c else []
    if len(bars) < 2:
        emit("KONTROLFIKSTUR DUMPET (positiv): SPY dagligt gav ingen barer.")
        emit("  Alt der foelger ville vaere 'ingen data' — men det er forbindelsen,")
        emit("  ikke markedet. Koerslen kasseres.")
        return False
    emit(f"Kontrolfikstur OK (positiv): SPY dagligt gav {len(bars)} barer.")

    # KENDT-NEGATIV: et symbol der umuligt findes maa IKKE kvalificere.
    c = await kvalificer_eller_none(ib, Stock(KENDT_NEGATIV_SYM, "SMART", "USD"))
    if c is not None:
        emit(f"KONTROLFIKSTUR DUMPET (negativ): {KENDT_NEGATIV_SYM} kvalificerede "
             f"(conId {getattr(c, 'conId', '?')}). IBKR svarer ja til alt — kasseres.")
        return False
    emit(f"Kontrolfikstur OK (negativ): {KENDT_NEGATIV_SYM} kvalificerer ikke.\n")
    return True


async def hent_serie(ib, spec: dict, bar: str, rod: Path, emit,
                     toerloeb: bool = False, nr: int = 0, af: int = 0) -> dict:
    """Hent én serie inkrementelt. Returnerer manifestposten."""
    from ibkr_kvalificer import kvalificer_eller_none

    # Maerkatet baerer serie-nummeret. Uden det laeste man "[SPY 1 min] ca. 2t 13m
    # tilbage" som hele jobbets resttid — men det er ÉN serie ud af tre, og
    # forskellen er over fire timer.
    maerkat = f"{spec['navn']} {bar}" + (f" · {nr}/{af}" if af else "")
    p = sti(rod, spec["navn"], bar)
    by_ts = laes_cache(p)
    foer = len(by_ts)
    dybeste = INTRADAG_START if bar == "1 min" else REFERENCE_START
    i_dag = date.today()
    segs = segmenter(by_ts, dybeste, i_dag)
    fra = segs[0][1] if segs else dybeste      # kun til toerloebs-teksten

    post = {"instrument": spec["navn"], "barstoerrelse": bar,
            "hvorfor": spec["hvorfor"], "fil": str(p.relative_to(rod)),
            "ibkr": {"art": spec["art"], "boers": spec["boers"],
                     "useRTH": bar == "1 min", "formatDate": 2,
                     "varighed_pr_kald": CHUNK.get(bar, "1 Y")}}

    if toerloeb:
        emit(f"   [{maerkat}] {foer} barer i cache · ville hente fra {fra}")
        post.update(barer_foer=foer, toerloeb=True)
        return post

    c = await kvalificer_eller_none(ib, byg_kontrakt(spec))
    if c is None:
        emit(f"   [{maerkat}] KUNNE IKKE KVALIFICERES — springes over")
        post.update(fejl="ikke kvalificeret", barer=foer)
        return post
    post["ibkr"]["conId"] = getattr(c, "conId", 0)

    # ── ContFuture: ÉN forespoergsel, aldrig endDateTime (se CONTFUT_STIGE) ────
    if spec["art"] == "contfut":
        brugt = None
        for varighed in CONTFUT_STIGE:
            got = []
            for what in WHAT["contfut"]:
                got = await hent_skive(ib, c, "", bar, what, varighed)
                if got:
                    post["what_to_show"] = what
                    break
            await asyncio.sleep(SLEEP)
            if got:
                brugt = varighed
                for b in got:
                    r = som_raekke(b)
                    if r:
                        by_ts[r[0]] = r[1]
                break
        post["ibkr"]["varighed_pr_kald"] = brugt or "(ingen virkede)"
        post["ibkr"]["endDateTime"] = "(tom — ContFuture afviser den, fejl 10339)"
        skriv_cache(p, by_ts)
        s0, s1 = (min(by_ts) if by_ts else None), (max(by_ts) if by_ts else None)
        post.update(barer=len(by_ts), barer_foer=foer, nye_barer=len(by_ts) - foer,
                    foerste=s0, sidste=s1,
                    hentet=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        emit(f"   [{maerkat}] {len(by_ts)} barer (+{len(by_ts) - foer} nye) "
             f"· {str(s0)[:10]} .. {str(s1)[:10]} · varighed {brugt}")
        return post

    varighed = CHUNK.get(bar, "1 Y")
    what_valgt = None
    hentede = 0

    # Fremdrift. Vi gaar BAGUD fra i dag mod `fra`, saa andelen af tidsspændet
    # vi har passeret er den aerlige maalestok — ikke antal kald, som varierer
    # med helligdage og tomme svar.
    _t0 = _ur.monotonic()
    _sidst_meldt = _t0

    # ÉT segment pr. hul. Se segmenter(): front (nye dage) og bag (uafsluttet
    # dybde) er to forskellige stroekninger, og en afbrudt koersel har typisk
    # begge.
    for _seg_slut, fra in segs:
      slut = _seg_slut
      tomme_i_traek = 0
      forrige_aeldste = None
      _nyeste_dato = None
      while True:
        got = []
        for what in ([what_valgt] if what_valgt else WHAT[spec["art"]]):
            got = await hent_skive(ib, c, slut, bar, what, varighed, emit)
            if got:
                what_valgt = what
                break
        await asyncio.sleep(SLEEP)
        if not got:
            tomme_i_traek += 1
            if tomme_i_traek >= 2:
                break
            # Spring et vindue laengere tilbage og proev igen — enkelte tomme
            # svar er normale omkring helligdagsklynger.
            if isinstance(slut, str) and not slut:
                break
            slut = slut - timedelta(days=30)
            continue
        tomme_i_traek = 0

        aeldste = None
        for b in got:
            r = som_raekke(b)
            if r is None:
                continue
            if r[0] not in by_ts:
                hentede += 1
            by_ts[r[0]] = r[1]
            t = datetime.fromisoformat(r[0])
            aeldste = t if aeldste is None or t < aeldste else aeldste
        if aeldste is None:
            break

        # ── Fremdriftsmelding ────────────────────────────────────────────
        if _nyeste_dato is None:
            _nyeste_dato = aeldste.date()
        _naa = _ur.monotonic()
        if _naa - _sidst_meldt >= STATUS_HVER_SEK:
            _spaend = max((_nyeste_dato - fra).days, 1)
            _gaaet  = max((_nyeste_dato - aeldste.date()).days, 0)
            _andel  = min(_gaaet / _spaend, 1.0)
            _brugt  = _naa - _t0
            # Resttiden er lineaer fremskrivning. Den er upraecis tidligt i
            # koerslen og god senere — derfor vises den foerst ved 3 %, hvor
            # den holder nogenlunde, frem for at give et vildt tal i starten.
            _rest = ""
            if _andel >= 0.03:
                _sek = _brugt * (1 - _andel) / _andel
                _rest = f" · ca. {int(_sek // 3600)}t {int(_sek % 3600 // 60)}m tilbage"
            # Dansk talformat: punktum som tusindskilletegn, komma som decimal.
            # Bygges hver for sig — en global replace paa hele linjen ramte
            # baade tusindtallet og procentens decimal og gav "18.720 · 0.5%".
            _antal = f"{len(by_ts):,}".replace(",", ".")
            _pct   = f"{_andel * 100:.1f}".replace(".", ",")
            emit(f"   [{maerkat}] naaet {aeldste.date()} "
                 f"(maal {fra}) · {_antal} barer · {_pct}%{_rest}")
            _sidst_meldt = _naa

        if aeldste.date() <= fra:
            break
        # ⚠ SOEMMEN SKAL OVERLAPPE, IKKE STOEDE OP.
        # Foerste udgave satte `aeldste - 1 minut`, og saa faldt dagsbaren PAA
        # graensen i sprækken: SPY og IWM manglede praecis én dag hvert aar, altid
        # den foerste handelsdag i august — dér hvor 1-aars-vinduerne moedtes.
        # Et bevidst overlap er gratis (dedup paa tidsstempel) og fjerner hele
        # klassen af soem-fejl. Fremskridtet sikres i stedet af `forrige_aeldste`.
        if forrige_aeldste is not None and aeldste >= forrige_aeldste:
            break                      # ingen fremdrift — undgaa evig loekke
        forrige_aeldste = aeldste
        slut = aeldste
        if hentede and hentede % 20000 == 0:
            skriv_cache(p, by_ts)      # loebende, saa en afbrydelse ikke koster alt

    skriv_cache(p, by_ts)
    s0, s1 = (min(by_ts) if by_ts else None), (max(by_ts) if by_ts else None)
    post.update(barer=len(by_ts), barer_foer=foer, nye_barer=len(by_ts) - foer,
                hentede_svar=hentede, foerste=s0, sidste=s1,
                what_to_show=what_valgt,
                hentet=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    emit(f"   [{maerkat}] {len(by_ts)} barer "
         f"(+{len(by_ts) - foer} nye) · {str(s0)[:10]} .. {str(s1)[:10]}")
    return post


def for_kort(post: dict, forvent_fra: date, min_barer_pr_aar: int | None = None) -> str | None:
    """Holder serien hvad V0 MAALTE? Returnér grunden hvis ikke.

    ⚠ L3: V0's MAALINGER SKAL VAERE V1'S ASSERTIONS.
    VX-fejlen opstod fordi jeg kendte ContFuture-begraensningen — den staar i
    V0-rapporten — og alligevel byggede en hoester der forudsatte det modsatte. Det
    sker naar viden staar i prosa frem for i kode. En probe der kun producerer en
    rapport, beskytter ingenting mod at naeste script antager noget andet.

    Derfor tjekkes to ting her, begge maalt i V0:
      · foerste tilgaengelige bar   (fangede VX: 2025-08 mod 2023-11)
      · barer pr. aar               (fanger den halve fejl: rigtig start, tynd serie)

    ⚠ DEN KONTROL DER MANGLEDE. Ved foerste koersel kom VX hjem med 252 barer i
    stedet for ~677, fordi ContFuture afviser endDateTime — og hoesten meldte exit 0.
    Et afkortet resultat der rapporterer som faerdigt, er samme sygdom som et tomt
    arkiv der melder "intakt": udfaldet var afgjort af noget andet end det man troede
    man maalte. Uden dette tjek ville tallet vaere endt i manifestet og ingen havde
    set paa det foer lag 2 gav noget vroevl.
    """
    if post.get("fejl") or not post.get("foerste"):
        return "ingen barer hentet"
    try:
        faktisk = datetime.fromisoformat(post["foerste"]).date()
    except ValueError:
        return None
    slup = (faktisk - forvent_fra).days
    if slup > AFKORTET_TOLERANCE_DAGE:
        return (f"starter {faktisk} men V0 maalte at data findes fra {forvent_fra} "
                f"— {slup} dage for sent, altsaa AFKORTET")

    # Rigtig start, men tynd serie: hullerne ligger inde i perioden frem for i enden.
    if min_barer_pr_aar and post.get("sidste") and post.get("barer"):
        try:
            sidst = datetime.fromisoformat(post["sidste"]).date()
        except ValueError:
            return None
        aar = max((sidst - faktisk).days / 365.25, 0.1)
        pr_aar = post["barer"] / aar
        if pr_aar < min_barer_pr_aar:
            return (f"{post['barer']} barer over {aar:.1f} aar = {pr_aar:.0f} pr. aar, "
                    f"men V0 maalte mindst {min_barer_pr_aar} — serien er TYND")
    return None


def skriv_manifest(rod: Path, poster: list[dict]) -> Path:
    p = rod / CACHE / "manifest.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    tidligere = {}
    if p.exists():
        try:
            for x in json.loads(p.read_text(encoding="utf-8")).get("serier", []):
                tidligere[(x["instrument"], x["barstoerrelse"])] = x
        except Exception:
            pass
    for x in poster:
        tidligere[(x["instrument"], x["barstoerrelse"])] = x
    man = {"skema_version": "1.0",
           "opdateret": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "reference_start": REFERENCE_START.isoformat(),
           "intradag_start": INTRADAG_START.isoformat(),
           "serier": [tidligere[k] for k in sorted(tidligere)]}
    p.write_text(json.dumps(man, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


async def koer(args, emit) -> int:
    from ib_async import IB
    rod = Path(__file__).resolve().parent

    valgte = []
    for s in SERIER:
        for bar in s["bars"]:
            if args.hvad == "dagligt" and bar != "1 day":
                continue
            if args.hvad == "1min" and bar != "1 min":
                continue
            if args.kun and s["navn"] not in args.kun.upper().split(","):
                continue
            valgte.append((s, bar))
    if not valgte:
        emit("Intet valgt.")
        return 2

    emit(f"{len(valgte)} serier: " + ", ".join(f"{s['navn']}/{b}" for s, b in valgte))
    if args.hvad in ("1min", "alt"):
        emit("⚠ 1-min-delen tager TIMER. Den er resumerbar — afbryd trygt.")
    emit("")

    if args.toerloeb:
        poster = [await hent_serie(None, s, b, rod, emit, toerloeb=True,
                                   nr=i, af=len(valgte))
                  for i, (s, b) in enumerate(valgte, 1)]
        emit("\nToerloeb — intet hentet, intet skrevet.")
        return 0

    ib = IB()
    emit(f"Forbinder TWS {HOST}:{args.port} (clientId={args.client_id}) …")
    try:
        await ib.connectAsync(HOST, args.port, clientId=args.client_id, timeout=20)
    except Exception as e:
        emit(f"KUNNE IKKE FORBINDE: {e}")
        return 1
    emit("Forbundet.\n")

    poster = []
    try:
        if not await kontrolfikstur(ib, emit):
            return 1
        for i, (s, bar) in enumerate(valgte, 1):
            poster.append(await hent_serie(ib, s, bar, rod, emit,
                                           nr=i, af=len(valgte)))
    finally:
        try:
            ib.disconnect()
        except Exception:
            pass

    # ── Afkortnings-tjek FOER manifestet skrives ──────────────────────────────
    afkortede = []
    for (s, bar), post in zip(valgte, poster):
        grund = for_kort(post, s["forvent_fra"],
                         MIN_DAGSBARER_PR_AAR if bar == "1 day" else None)
        if grund:
            post["advarsel"] = grund
            afkortede.append((s["navn"], bar, grund))

    p = skriv_manifest(rod, poster)
    emit(f"\nManifest: {p}")
    nye = sum(x.get("nye_barer", 0) for x in poster)
    emit(f"{nye} nye barer i alt denne koersel.")

    if afkortede:
        emit(f"\n!! {len(afkortede)} SERIER ER AFKORTEDE — koerslen er IKKE faerdig:")
        for navn, bar, grund in afkortede:
            emit(f"   {navn} {bar}: {grund}")
        emit("\nEn afkortet serie der rapporterer som faerdig, er vaerre end en der")
        emit("fejler: den ender i manifestet som et tal ingen ser paa igen.")
        return 1

    emit("\nNaeste skridt — fuldstaendighedsrevision (B3):")
    emit("   python sessions_revision.py --mappe vol_cache --moenster \"*_1min.csv\" --streng")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="FASE V1: byg vol_cache for spor 1")
    ap.add_argument("--hvad", choices=["dagligt", "1min", "alt"], default="dagligt")
    ap.add_argument("--kun", default=None, help="kommasepareret, fx SPY,IWM")
    ap.add_argument("--toerloeb", action="store_true", help="vis plan, hent intet")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--client-id", dest="client_id", type=int, default=CLIENT_ID)
    args = ap.parse_args()

    def emit(s=""):
        try:
            print(s, flush=True)
        except UnicodeEncodeError:
            enc = sys.stdout.encoding or "ascii"
            print(s.encode(enc, "replace").decode(enc), flush=True)

    try:
        return asyncio.run(koer(args, emit))
    except KeyboardInterrupt:
        emit("\nAfbrudt. Cachen er skrevet loebende — koer igen for at fortsaette.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
