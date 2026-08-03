#!/usr/bin/env python3
"""
vol_data_probe.py — FASE V0 i regime-byggeklods 1 (VOLATILITET)
═══════════════════════════════════════════════════════════════════════════════════
Svarer paa ét spoergsmaal: HVAD kan vi faktisk faa fra IBKR, hvor langt tilbage, i
hvilke barstoerrelser, og med hvilken kvalitet? Ingen volatilitetsmaal beregnes her
— det er fase V1/V2. Denne fase er ingenioerarbejde, ikke statistik.

KERNEN: reqHeadTimeStamp OVERLOVER systematisk paa intradag. Paastanden alene er
ikke data. Derfor verificeres hver paastand med smaa hentninger lige INDENFOR og
lige UDENFOR den paastaaede graense, og hver begraensning klassificeres:

    IBKR-RETENTION     haard graense (typisk ~2 aar intradag) — kan ikke omgaas
    KONTRAKTLEVETID    instrumentet/expiry fandtes ikke foer dato X — loeses ved stitching
    HARVEST-PARAMETER  vi har simpelthen aldrig bedt om mere — gratis at rette

SPY og VIX er KONTROLGRUPPE: de har eksisteret i aartier, saa naar DERES historik
stopper, ER det retention. Uden den kontrol kan man ikke skelne "IBKR vil ikke" fra
"instrumentet fandtes ikke endnu".

KOERSEL (paa workstation med TWS aabent):
    python vol_data_probe.py --niveau A          # kerne — ca. 15-20 min
    python vol_data_probe.py --niveau AB         # + staerkt oenskede — ca. 40 min
    python vol_data_probe.py --niveau ALL        # alt — ca. 60-75 min

Den er GENOPTAGELIG: resultater skrives loebende, og en ny koersel springer det
allerede maalte over. Afbryd trygt med Ctrl+C.

Output: vol_probe_output/vol_data_probe.{json,md}

Placering: C:\\Projects\\trading_dash\\backend\\vol_data_probe.py
"""
from __future__ import annotations

import asyncio

# Python 3.14: skal staa FOER ib_async importeres
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import argparse
import json
import statistics as stat
import sys
from collections import defaultdict
from datetime import date as date_cls, datetime, timedelta, timezone
from pathlib import Path

# ── Konfiguration ───────────────────────────────────────────────────────────────
IBKR_HOST     = "127.0.0.1"
IBKR_PORT     = 7497
CLIENT_ID     = 52          # eget id — kolliderer ikke med strategier (34/47/49/51)

OUT_DIRNAME   = "vol_probe_output"

SLEEP_BETWEEN = 1.5         # sek mellem historiske kald — IBKR-pacing
PACING_WAIT   = 65          # sek at vente ved en pacing-advarsel
REQ_TIMEOUT   = 45
HEAD_TIMEOUT  = 25
QUALIFY_TIMEOUT = 20

# Barstoerrelser der probes. Rakkefoelge = billigst foerst.
BARS_ALLE = ["1 day", "1 hour", "30 mins", "5 mins", "1 min"]

# Smaa skiver til VERIFIKATION — vi vil kun vide OM der er data, ikke hente meget.
SKIVE = {
    "1 day":   "5 D",
    "1 hour":  "2 D",
    "30 mins": "2 D",
    "5 mins":  "1 D",
    "1 min":   "1 D",
}

# Kvalitetstjek paa dagsbarer: én stor hentning pr. instrument (billigt).
KVALITET_VARIGHED = "20 Y"

# ── Instrumenter ────────────────────────────────────────────────────────────────
# niveau: A = kerne (uden disse er byggeklodsen umulig)
#         B = staerkt oensket
#         C = kontekst (medtag kun hvis det tilfoejer noget A/B ikke fanger)
INSTRUMENTER = [
    # ── Niveau A — kerne ────────────────────────────────────────────────
    dict(navn="VIX",   niveau="A", art="ind", sym="VIX",   boers="CBOE",
         hvorfor="S&P 30-dages implicit vol — eneste FREMADSKUENDE kilde"),
    dict(navn="VIX3M", niveau="A", art="ind", sym="VIX3M", boers="CBOE",
         hvorfor="3-maaneders implicit — sammen med VIX giver den terminsstrukturen"),
    dict(navn="ES",    niveau="A", art="contfut", sym="ES", boers="CME",
         hvorfor="Realiseret vol paa det vi faktisk handler (via MES)"),
    dict(navn="RTY",   niveau="A", art="contfut", sym="RTY", boers="CME",
         hvorfor="Realiseret vol paa Russell — M2K-benet"),
    dict(navn="SPY",   niveau="A", art="stk", sym="SPY", boers="SMART",
         hvorfor="KONTROLGRUPPE: lang ubrudt dagshistorik. Afgoer om en graense er retention"),

    # ── Niveau B — staerkt oensket ──────────────────────────────────────
    dict(navn="VIX9D", niveau="B", art="ind", sym="VIX9D", boers="CBOE",
         hvorfor="9-dages implicit — fanger begivenhedsrisiko (FOMC/CPI)"),
    dict(navn="RVX",   niveau="B", art="ind", sym="RVX",   boers="CBOE",
         hvorfor="Russell 2000-vol — small-cap-vol afviger systematisk fra large-cap"),
    dict(navn="VXN",   niveau="B", art="ind", sym="VXN",   boers="CBOE",
         hvorfor="Nasdaq 100-vol — tech-dimensionen"),
    dict(navn="NQ",    niveau="B", art="contfut", sym="NQ", boers="CME",
         hvorfor="Realiseret vol, tech"),
    dict(navn="VX",    niveau="B", art="contfut", sym="VIX", boers="CFE",
         hvorfor="VIX-FUTURE: terminsstruktur paa tradeable kontrakter. Kritisk for lag 2 "
                 "— spot-VIX opdaterer KUN i RTH, futures handler naesten doegnet rundt"),

    # ── Niveau C — kontekst ─────────────────────────────────────────────
    dict(navn="VVIX", niveau="C", art="ind", sym="VVIX", boers="CBOE",
         hvorfor="Vol-of-vol — stiger ofte foer VIX selv"),
    dict(navn="HYG",  niveau="C", art="stk", sym="HYG", boers="SMART",
         hvorfor="Kreditstress — volregimer starter tit i kredit"),
    dict(navn="LQD",  niveau="C", art="stk", sym="LQD", boers="SMART",
         hvorfor="Investment grade-kredit"),
    dict(navn="TLT",  niveau="C", art="stk", sym="TLT", boers="SMART",
         hvorfor="Realiseret rentevol som MOVE-proxy"),
    dict(navn="UUP",  niveau="C", art="stk", sym="UUP", boers="SMART",
         hvorfor="Dollar — dollarstyrke og aktievol haenger sammen i stress"),
    dict(navn="GLD",  niveau="C", art="stk", sym="GLD", boers="SMART",
         hvorfor="Guld — sikker-havn-dimensionen"),
]

# whatToShow. Indeks har ingen volumen; TRADES virker for de fleste CBOE-indeks,
# men nogle kraever MIDPOINT. Vi proever i raekkefoelge og noterer hvad der virkede.
WHAT_KANDIDATER = {
    "ind":     ["TRADES", "MIDPOINT"],
    "stk":     ["TRADES"],
    "contfut": ["TRADES"],
}


# ── Halve handelsdage (uden eksternt bibliotek) ────────────────────────────────
def halve_dage(aar: int) -> set[date_cls]:
    """Kendte halve handelsdage paa NYSE. De giver SMAA ranges der ligner et
    lavvolatilitetsregime uden at vaere det — skal flages, ikke stiltiende indgaa."""
    ud: set[date_cls] = set()
    # Dagen efter Thanksgiving (4. torsdag i november + 1)
    d = date_cls(aar, 11, 1)
    torsdage = [d + timedelta(days=i) for i in range(30)
                if (d + timedelta(days=i)).month == 11
                and (d + timedelta(days=i)).weekday() == 3]
    if len(torsdage) >= 4:
        ud.add(torsdage[3] + timedelta(days=1))
    # Juleaftensdag naar den er en hverdag
    jul = date_cls(aar, 12, 24)
    if jul.weekday() < 5:
        ud.add(jul)
    # 3. juli naar 4. juli er en hverdag
    fjerde = date_cls(aar, 7, 4)
    if fjerde.weekday() < 5:
        tredje = date_cls(aar, 7, 3)
        if tredje.weekday() < 5:
            ud.add(tredje)
    return ud


# ── IBKR-primitiver ─────────────────────────────────────────────────────────────
def byg_kontrakt(inst: dict):
    from ib_async import ContFuture, Index, Stock
    art = inst["art"]
    if art == "ind":
        return Index(inst["sym"], inst["boers"], "USD")
    if art == "stk":
        return Stock(inst["sym"], inst["boers"], "USD")
    return ContFuture(symbol=inst["sym"], exchange=inst["boers"], currency="USD")


async def kvalificer(ib, inst):
    c = byg_kontrakt(inst)
    try:
        q = await asyncio.wait_for(ib.qualifyContractsAsync(c), timeout=QUALIFY_TIMEOUT)
        return (q[0] if q else None), None
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:100]}"


async def hent_head(ib, contract, what: str):
    """IBKR's PAASTAND om foerste tilgaengelige bar. Skal verificeres."""
    try:
        ts = await asyncio.wait_for(
            ib.reqHeadTimeStampAsync(contract, whatToShow=what, useRTH=False, formatDate=1),
            timeout=HEAD_TIMEOUT)
        if isinstance(ts, datetime):
            return (ts.replace(tzinfo=None) if ts.tzinfo else ts), None
        if ts:
            try:
                return datetime.fromisoformat(str(ts)[:19]), None
            except Exception:
                return None, f"ulaeselig headstamp: {ts!r}"
        return None, "tom headstamp"
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:100]}"


async def hent_skive(ib, contract, bar: str, slut: datetime | None, what: str,
                     rth: bool, varighed: str | None = None):
    """Lille hentning. Returnerer (antal_bars, foerste_dt, sidste_dt, fejl)."""
    v = varighed or SKIVE.get(bar, "2 D")
    end = ""
    if slut is not None:
        end = slut.replace(tzinfo=timezone.utc) if slut.tzinfo is None else slut
    try:
        bars = await asyncio.wait_for(
            ib.reqHistoricalDataAsync(
                contract, endDateTime=end, durationStr=v, barSizeSetting=bar,
                whatToShow=what, useRTH=rth, formatDate=2),
            timeout=REQ_TIMEOUT)
    except asyncio.TimeoutError:
        return 0, None, None, "timeout"
    except Exception as e:
        return 0, None, None, f"{type(e).__name__}: {str(e)[:110]}"
    if not bars:
        return 0, None, None, None
    def dt(b):
        d = getattr(b, "date", None)
        if isinstance(d, datetime):
            return d.replace(tzinfo=None) if d.tzinfo else d
        if isinstance(d, date_cls):
            return datetime(d.year, d.month, d.day)
        try:
            return datetime.fromisoformat(str(d)[:19])
        except Exception:
            return None
    return len(bars), dt(bars[0]), dt(bars[-1]), None


# Stige til at finde den FAKTISK aeldste bar naar headstampen overlover.
# Dage tilbage fra i dag, aeldst foerst. Foerste trin der giver bars, giver os en
# aegte observeret bardato — og den ligger mellem det trin og det forrige tomme.
AELDSTE_STIGE_DAGE = [9125, 5475, 3650, 2555, 1825, 1100, 730, 550, 365, 180, 90]


# ── Futures maales ANDERLEDES — og det er ikke en detalje ──────────────────────
# ContFuture UNDERSTOETTER IKKE historik med angivet endDateTime. Verificeret
# 3/8-2026: ES ContFuture kvalificerer til Sep-2026-kontrakten (conId 649180671)
# og returnerer 0 bars ved BAADE 30 og 90 dage tilbage, mens tom slutdato virker.
#
# Foerste probe-koersel maalte derfor ES og RTY med den samme anker-metode som
# aktier/indeks og fik "ingen data" paa alle 11 stigetrin — hvilket saa ud som en
# haard graense, men var et artefakt af maalemetoden. Kvalitetstjekket (tom
# slutdato) hentede samtidig 1054 dagsbarer for ES. Samme fejl ville have
# forgiftet enhver konklusion om futures-dybde.
#
# Korrekt metode: tom slutdato + stigende durationStr. Det stoerste trin der giver
# bars fortaeller hvor langt ContFuture'ens SAMMENSATTE serie raekker.
VARIGHEDS_STIGE = {
    "1 day":   ["20 Y", "10 Y", "5 Y", "2 Y", "1 Y"],
    "1 hour":  ["2 Y", "1 Y", "6 M", "1 M"],
    "30 mins": ["1 Y", "6 M", "1 M", "1 W"],
    "5 mins":  ["6 M", "1 M", "1 W", "1 D"],
    "1 min":   ["1 M", "1 W", "1 D"],
}


async def probe_contfut_serie(ib, contract, bar, what, emit, navn):
    """Raekkevidde for en continuous future. Tom slutdato, faldende varighed.

    Returnerer (foerste_dt, sidste_dt, n, brugt_varighed, kald).
    """
    kald = 0
    for v in VARIGHEDS_STIGE.get(bar, ["1 Y", "6 M", "1 M"]):
        n, foerste, sidste, fejl = await hent_skive(ib, contract, bar, None, what,
                                                    False, v)
        kald += 1
        await asyncio.sleep(SLEEP_BETWEEN)
        if n > 0 and foerste is not None:
            return foerste, sidste, n, v, kald
        if fejl and "pacing" in (fejl or "").lower():
            emit(f"    pacing — venter {PACING_WAIT}s")
            await asyncio.sleep(PACING_WAIT)
    return None, None, 0, None, kald


async def find_faktisk_aeldste(ib, contract, bar, what, emit, navn):
    """Naar reqHeadTimeStamp overlover, siger den intet om hvor data FAKTISK
    begynder. Vi gaar en stige bagfra og tager det foerste trin der giver bars.

    Returnerer (foerste_observerede_dt, oevre_graense_dage, antal_kald).
    Tidligt exit — typisk 3-5 kald, hoejst len(AELDSTE_STIGE_DAGE).
    """
    kald = 0
    forrige_tom = None
    for dage in AELDSTE_STIGE_DAGE:
        slut = datetime.now() - timedelta(days=dage)
        n, foerste, _sidste, _fejl = await hent_skive(ib, contract, bar, slut, what, False)
        kald += 1
        await asyncio.sleep(SLEEP_BETWEEN)
        if n > 0 and foerste is not None:
            emit(f"    {navn} {bar}: faktisk data fundet {dage} dage tilbage "
                 f"({foerste.date()}) efter {kald} soegekald")
            return foerste, forrige_tom, kald
        forrige_tom = dage
    return None, forrige_tom, kald


def klassificer(bar: str, bekraeftet_aeldste: datetime | None,
                head: datetime | None, kontrol_aeldste: datetime | None) -> str:
    """Hvorfor stopper historikken her? De tre aarsager forveksles konstant."""
    if bekraeftet_aeldste is None:
        return "INGEN DATA — hverken headstamp eller verifikation gav bars"
    alder_dage = (datetime.now() - bekraeftet_aeldste).days
    if bar == "1 day":
        if alder_dage > 3650:
            return "HARVEST-PARAMETER — dyb dagshistorik findes, vi skal bare bede om den"
        if kontrol_aeldste and bekraeftet_aeldste > kontrol_aeldste + timedelta(days=365):
            return ("KONTRAKTLEVETID — kontrolgruppen (SPY) naar laengere tilbage, "
                    "saa graensen er instrumentets egen alder")
        return "IBKR-RETENTION — dagsbarer stopper her ogsaa for kontrolgruppen"
    # Intradag
    if alder_dage < 400:
        return "IBKR-RETENTION — intradag er haardt begraenset (~1-2 aar) uanset instrument"
    if alder_dage < 900:
        return "IBKR-RETENTION — intradag naar ~2 aar; det er graensen"
    return "HARVEST-PARAMETER — mere intradag er tilgaengeligt end vi plejer at hente"


# ── V0.1 — raekkevidde pr. instrument pr. barstoerrelse ─────────────────────────
async def probe_instrument(ib, inst, bars, kontrol_aeldste, emit, ventet):
    contract, fejl = await kvalificer(ib, inst)
    if contract is None:
        emit(f"  {inst['navn']:<7} KUNNE IKKE KVALIFICERES — {fejl}")
        return {"navn": inst["navn"], "niveau": inst["niveau"], "art": inst["art"],
                "hvorfor": inst["hvorfor"], "kvalificeret": False, "fejl": fejl,
                "serier": []}

    # Find et whatToShow der virker (indeks kraever undertiden MIDPOINT).
    what_valgt, what_fejl = None, []
    for w in WHAT_KANDIDATER[inst["art"]]:
        n, _f, _l, e = await hent_skive(ib, contract, "1 day", None, w, False, "5 D")
        await asyncio.sleep(SLEEP_BETWEEN)
        if n > 0:
            what_valgt = w
            break
        what_fejl.append(f"{w}: {e or 'tom'}")
    if what_valgt is None:
        emit(f"  {inst['navn']:<7} INGEN BRUGBAR whatToShow — {'; '.join(what_fejl)}")
        return {"navn": inst["navn"], "niveau": inst["niveau"], "art": inst["art"],
                "hvorfor": inst["hvorfor"], "kvalificeret": True,
                "fejl": "; ".join(what_fejl), "serier": []}

    serier = []
    for bar in bars:
        head, head_fejl = await hent_head(ib, contract, what_valgt)
        await asyncio.sleep(SLEEP_BETWEEN)

        # ── Futures: egen metode (se VARIGHEDS_STIGE ovenfor) ──
        if inst["art"] == "contfut":
            foerste, sidste, n, brugt, kald = await probe_contfut_serie(
                ib, contract, bar, what_valgt, emit, inst["navn"])
            aar = round((datetime.now() - foerste).days / 365.25, 1) if foerste else None
            aarsag = ("CONTFUTURE-GRAENSE — IBKR's sammensatte serie raekker ikke laengere; "
                      "dybere historik kraever PR-EXPIRY-kontrakter + stitching"
                      if foerste else
                      "INGEN DATA — heller ikke med tom slutdato")
            serier.append({
                "bar": bar, "what": what_valgt,
                "headstamp_paastand": head.isoformat() if head else None,
                "headstamp_fejl": head_fejl,
                "bekraeftet_aeldste": foerste.isoformat() if foerste else None,
                "nyeste": sidste.isoformat() if sidste else None,
                "n_i_nyeste_skive": n,
                "nyeste_fejl": None,
                "metode": f"tom slutdato + durationStr={brugt}",
                "verifikation_indenfor": None, "verifikation_udenfor": None,
                "headstamp_overlover": None,
                "soegte_ekstra_kald": kald, "soegning_tom_indtil_dage": None,
                "aar_tilbage": aar, "aarsag": aarsag,
            })
            emit(f"  {inst['navn']:<7} {bar:<8} {brugt or '-':<6} "
                 f"foerste={str(foerste)[:10]:<10} n={n:<6} ~{aar} aar")
            continue

        # Nyeste bar — hvor frisk er serien?
        n_ny, _f, sidste, e_ny = await hent_skive(ib, contract, bar, None, what_valgt, False)
        await asyncio.sleep(SLEEP_BETWEEN)

        # VERIFIKATION af paastanden: lige indenfor og lige udenfor.
        indenfor = udenfor = None
        if head:
            marg = timedelta(days=10 if bar == "1 day" else 3)
            n_i, f_i, _l, _e = await hent_skive(ib, contract, bar, head + marg,
                                                what_valgt, False)
            await asyncio.sleep(SLEEP_BETWEEN)
            indenfor = {"n": n_i, "foerste": f_i.isoformat() if f_i else None}
            n_u, _f2, _l2, _e2 = await hent_skive(ib, contract, bar, head - marg,
                                                  what_valgt, False)
            await asyncio.sleep(SLEEP_BETWEEN)
            udenfor = {"n": n_u}

        # Bekraeftet aeldste = det tidligste vi FAKTISK har set bars fra.
        bekraeftet = None
        if indenfor and indenfor.get("foerste"):
            bekraeftet = datetime.fromisoformat(indenfor["foerste"])

        # Overlover headstampen? (paastand men INGEN bars nogen af stederne)
        overlover = None
        if head and udenfor is not None:
            overlover = (udenfor["n"] == 0 and (indenfor or {}).get("n", 0) == 0)

        # Overlover den — eller mangler vi helt en headstamp — saa ved vi endnu
        # ikke hvor data FAKTISK begynder. Det er hele pointen med denne fase, saa
        # vi soeger efter det i stedet for at rapportere "ukendt".
        soegte_kald, soege_graense = 0, None
        if bekraeftet is None:
            fundet, soege_graense, soegte_kald = await find_faktisk_aeldste(
                ib, contract, bar, what_valgt, emit, inst["navn"])
            if fundet is not None:
                bekraeftet = fundet

        aarsag = klassificer(bar, bekraeftet or head, head, kontrol_aeldste)
        aar = None
        if bekraeftet or head:
            aar = round((datetime.now() - (bekraeftet or head)).days / 365.25, 1)

        serier.append({
            "bar": bar,
            "what": what_valgt,
            "headstamp_paastand": head.isoformat() if head else None,
            "headstamp_fejl": head_fejl,
            "bekraeftet_aeldste": bekraeftet.isoformat() if bekraeftet else None,
            "nyeste": sidste.isoformat() if sidste else None,
            "n_i_nyeste_skive": n_ny,
            "nyeste_fejl": e_ny,
            "verifikation_indenfor": indenfor,
            "verifikation_udenfor": udenfor,
            "headstamp_overlover": overlover,
            "soegte_ekstra_kald": soegte_kald,
            "soegning_tom_indtil_dage": soege_graense,
            "aar_tilbage": aar,
            "aarsag": aarsag,
        })
        emit(f"  {inst['navn']:<7} {bar:<8} head={str(head)[:10]:<10} "
             f"bekr={str(bekraeftet)[:10]:<10} nyeste={str(sidste)[:10]:<10} "
             f"~{aar if aar is not None else '?'} aar")

    return {"navn": inst["navn"], "niveau": inst["niveau"], "art": inst["art"],
            "hvorfor": inst["hvorfor"], "kvalificeret": True, "what": what_valgt,
            "fejl": None, "serier": serier}


# ── V0.2 — kvalitet paa dagsserien (én stor hentning pr. instrument) ────────────
async def kvalitet_dagsserie(ib, inst, what, emit):
    contract, _f = await kvalificer(ib, inst)
    if contract is None:
        return None
    n, foerste, sidste, fejl = await hent_skive(
        ib, contract, "1 day", None, what, False, KVALITET_VARIGHED)
    await asyncio.sleep(SLEEP_BETWEEN)
    if fejl or n == 0:
        return {"navn": inst["navn"], "fejl": fejl or "tom"}
    # Hent igen for at faa selve baerne (hent_skive returnerer kun tal)
    try:
        bars = await asyncio.wait_for(
            ib.reqHistoricalDataAsync(contract, endDateTime="",
                                      durationStr=KVALITET_VARIGHED, barSizeSetting="1 day",
                                      whatToShow=what, useRTH=False, formatDate=2),
            timeout=REQ_TIMEOUT)
    except Exception as e:
        return {"navn": inst["navn"], "fejl": f"{type(e).__name__}: {e}"}
    await asyncio.sleep(SLEEP_BETWEEN)

    raekker = []
    for b in bars:
        d = getattr(b, "date", None)
        if isinstance(d, datetime):
            dd = d.date()
        elif isinstance(d, date_cls):
            dd = d
        else:
            continue
        raekker.append((dd, float(b.open), float(b.high), float(b.low), float(b.close)))
    raekker.sort()
    if not raekker:
        return {"navn": inst["navn"], "fejl": "ingen parsbare bars"}

    datoer = [r[0] for r in raekker]
    closes = [r[4] for r in raekker]
    highs  = [r[2] for r in raekker]
    lows   = [r[3] for r in raekker]

    # Stillestaaende: samme close flere dage i traek
    stille, loeb = [], 1
    for i in range(1, len(closes)):
        if closes[i] == closes[i - 1]:
            loeb += 1
        else:
            if loeb >= 3:
                stille.append({"til": datoer[i - 1].isoformat(), "dage": loeb})
            loeb = 1
    if loeb >= 3:
        stille.append({"til": datoer[-1].isoformat(), "dage": loeb})

    # Absurde vaerdier
    absurde = [datoer[i].isoformat() for i in range(len(closes))
               if closes[i] <= 0 or highs[i] < lows[i]]

    # Halve dage der faktisk optraeder i serien
    hd = set()
    for aar in range(datoer[0].year, datoer[-1].year + 1):
        hd |= halve_dage(aar)
    halve_i_serie = sorted(d.isoformat() for d in datoer if d in hd)

    # Range-fordeling + hvor foelsom en percentil er over for marts 2020
    rng = [(h - l) / c * 100.0 for _d, _o, h, l, c in raekker if c > 0]
    uden_2020 = [(h - l) / c * 100.0 for d, _o, h, l, c in raekker
                 if c > 0 and not (date_cls(2020, 2, 15) <= d <= date_cls(2020, 4, 30))]

    def p(xs, q):
        if not xs:
            return None
        s = sorted(xs)
        return round(s[min(len(s) - 1, int(len(s) * q))], 3)

    return {
        "navn": inst["navn"],
        "n_dage": len(raekker),
        "foerste": datoer[0].isoformat(),
        "sidste": datoer[-1].isoformat(),
        "stillestaaende_loeb": stille[:10],
        "n_stillestaaende_loeb": len(stille),
        "absurde_vaerdier": absurde[:10],
        "n_absurde": len(absurde),
        "halve_dage_i_serie": halve_i_serie[-10:],
        "n_halve_dage": len(halve_i_serie),
        "range_pct_p50": p(rng, 0.50), "range_pct_p90": p(rng, 0.90),
        "range_pct_p99": p(rng, 0.99),
        "range_pct_p90_uden_marts2020": p(uden_2020, 0.90),
        "range_pct_p99_uden_marts2020": p(uden_2020, 0.99),
    }


# ── Rapport ─────────────────────────────────────────────────────────────────────
def skriv_md(res: dict, sti: Path) -> None:
    L = []
    a = L.append
    a("# vol_data_probe — FASE V0: hvad kan vi faktisk faa fra IBKR?\n")
    a(f"Koert: {res['koert']}  ·  niveauer: {res['niveauer']}  ·  "
      f"barstoerrelser: {', '.join(res['bars'])}\n")
    a("Regime-byggeklods 1 (volatilitet), fase V0. Ingen volatilitetsmaal beregnes her.\n")
    a("\n## Sammenfatning\n")

    ok = [i for i in res["instrumenter"] if i.get("kvalificeret") and i.get("serier")]
    daarlige = [i for i in res["instrumenter"] if not i.get("serier")]
    a(f"- {len(ok)} instrumenter gav data, {len(daarlige)} gjorde ikke.\n")
    if daarlige:
        a("- **Uden data:** " + ", ".join(
            f"{i['navn']} ({(i.get('fejl') or 'ukendt')[:60]})" for i in daarlige) + "\n")

    a("\n## V0.1 — raekkevidde pr. instrument pr. barstoerrelse\n")
    for inst in res["instrumenter"]:
        a(f"\n### {inst['navn']}  ·  niveau {inst['niveau']}  ·  {inst['art']}\n")
        a(f"*{inst['hvorfor']}*\n\n")
        if not inst.get("serier"):
            a(f"**Ingen data.** {inst.get('fejl') or ''}\n")
            continue
        a(f"whatToShow der virkede: `{inst.get('what')}`\n\n")
        a("| bar | headstamp (paastand) | bekraeftet aeldste | nyeste | aar | overlover? | aarsag |\n")
        a("|---|---|---|---|---|---|---|\n")
        for s in inst["serier"]:
            over = {True: "**JA**", False: "nej", None: "?"}[s.get("headstamp_overlover")]
            a(f"| {s['bar']} | {str(s['headstamp_paastand'])[:10]} | "
              f"{str(s['bekraeftet_aeldste'])[:10]} | {str(s['nyeste'])[:10]} | "
              f"{s['aar_tilbage']} | {over} | {s['aarsag']} |\n")

    if res.get("kvalitet"):
        a("\n## V0.2 — kvalitet paa dagsserien\n")
        a("| instrument | dage | foerste | sidste | stille-loeb | absurde | halve dage | "
          "range% p50 | p90 | p99 | p99 u. marts-2020 |\n")
        a("|---|---|---|---|---|---|---|---|---|---|---|\n")
        for k in res["kvalitet"]:
            if k.get("fejl"):
                a(f"| {k['navn']} | — | — | — | — | — | — | — | — | — | {k['fejl'][:40]} |\n")
                continue
            a(f"| {k['navn']} | {k['n_dage']} | {k['foerste']} | {k['sidste']} | "
              f"{k['n_stillestaaende_loeb']} | {k['n_absurde']} | {k['n_halve_dage']} | "
              f"{k['range_pct_p50']} | {k['range_pct_p90']} | {k['range_pct_p99']} | "
              f"{k['range_pct_p99_uden_marts2020']} |\n")
        a("\n**Marts-2020-foelsomhed:** forskellen mellem `p99` og `p99 u. marts-2020` viser "
          "hvor meget én periode dominerer percentilreferencen. Er forskellen stor, skal "
          "referencevinduet i V2 vaelges med det for oeje.\n")

    a("\n## Anbefaling\n")
    a("*(udfyldes efter gennemlaesning — se statusnotatet)*\n")
    sti.write_text("".join(L), encoding="utf-8")


# ── Main ────────────────────────────────────────────────────────────────────────
async def koer(args, emit):
    from ib_async import IB
    ud_dir = Path(__file__).resolve().parent / OUT_DIRNAME
    ud_dir.mkdir(exist_ok=True)
    json_sti = ud_dir / "vol_data_probe.json"
    md_sti = ud_dir / "vol_data_probe.md"

    niveauer = {"A": ["A"], "AB": ["A", "B"], "ALL": ["A", "B", "C"]}[args.niveau]
    valgte = [i for i in INSTRUMENTER if i["niveau"] in niveauer]
    if args.kun:
        oensket = {x.strip().upper() for x in args.kun.split(",") if x.strip()}
        valgte = [i for i in INSTRUMENTER if i["navn"].upper() in oensket]
    bars = [b.strip() for b in args.bars.split(",") if b.strip()]

    # Genoptagelse
    tidligere = {}
    if json_sti.exists() and not args.forfra:
        try:
            gl = json.loads(json_sti.read_text(encoding="utf-8"))
            gen = {x.strip().upper() for x in (args.kun or "").split(",") if x.strip()}
            tidligere = {i["navn"]: i for i in gl.get("instrumenter", [])
                         if i.get("serier") and i["navn"].upper() not in gen}
            if tidligere:
                emit(f"Genoptager — {len(tidligere)} instrumenter er allerede maalt "
                     f"(brug --forfra for at maale alt igen).")
        except Exception:
            pass

    tidligere_kvalitet = []
    if json_sti.exists() and not args.forfra:
        try:
            tidligere_kvalitet = json.loads(json_sti.read_text(encoding="utf-8")).get("kvalitet", [])
        except Exception:
            pass

    ib = IB()
    emit(f"Forbinder til TWS {IBKR_HOST}:{args.port} (clientId={args.client_id}) ...")
    try:
        await ib.connectAsync(IBKR_HOST, args.port, clientId=args.client_id, timeout=20)
    except Exception as e:
        emit(f"KUNNE IKKE FORBINDE: {e}")
        emit("Er TWS aabent, og er API'et slaaet til (port 7497)?")
        return 1
    emit("Forbundet.\n")

    # 4 faste kald pr. (instrument, bar) + ~4 soegekald naar headstampen overlover
    kald = len([i for i in valgte if i['navn'] not in tidligere]) * (len(bars) * 8 + 2)
    emit(f"{len(valgte)} instrumenter x {len(bars)} barstoerrelser "
         f"= ca. {kald} historiske kald, ~{kald * SLEEP_BETWEEN / 60:.0f} min "
         f"(pacing-venligt med vilje).\n")

    # Kontrolgruppe foerst: SPY's dagsdybde afgoer om andres graenser er retention.
    kontrol_aeldste = None
    resultater = []
    rest = [i for i in valgte if i["navn"] not in tidligere]
    rest.sort(key=lambda i: (i["navn"] != "SPY", i["niveau"], i["navn"]))

    # Baer ALLE tidligere maalte instrumenter med over — ogsaa dem der ikke staar i
    # --kun. Foer itererede denne loekke over `valgte`, som --kun havde skaaret ned,
    # saa en genmaaling af ES+RTY SLETTEDE SPY/VIX/VIX3M fra JSON'en. Fejlen kostede
    # en probe-koersel 3/8-2026; .md'en reddede tallene.
    for inst in INSTRUMENTER:
        if inst["navn"] in tidligere:
            resultater.append(tidligere[inst["navn"]])
    try:
        for inst in rest:
            emit(f"\n[{inst['niveau']}] {inst['navn']} — {inst['hvorfor'][:70]}")
            r = await probe_instrument(ib, inst, bars, kontrol_aeldste, emit,
                                       args.vent_pacing)
            resultater.append(r)
            if inst["navn"] == "SPY":
                dag = next((s for s in r.get("serier", []) if s["bar"] == "1 day"), None)
                if dag and dag.get("bekraeftet_aeldste"):
                    kontrol_aeldste = datetime.fromisoformat(dag["bekraeftet_aeldste"])
                    emit(f"  >> KONTROLGRUPPE sat: SPY dagsbarer tilbage til "
                         f"{kontrol_aeldste.date()}")
            # Skriv loebende, saa en afbrydelse ikke koster arbejdet
            json_sti.write_text(json.dumps(
                {"koert": datetime.now().isoformat(timespec="seconds"),
                 "niveauer": args.niveau, "bars": bars,
                 "instrumenter": resultater, "kvalitet": tidligere_kvalitet},
                ensure_ascii=False, indent=2), encoding="utf-8")

        kvalitet = []
        if not args.spring_kvalitet:
            emit("\n── V0.2 kvalitetstjek paa dagsserier ──")
            for r in resultater:
                if not r.get("serier") or not r.get("what"):
                    continue
                inst = next(i for i in INSTRUMENTER if i["navn"] == r["navn"])
                k = await kvalitet_dagsserie(ib, inst, r["what"], emit)
                if k:
                    kvalitet.append(k)
                    emit(f"  {k['navn']:<7} {k.get('n_dage', '?')} dage  "
                         f"stille-loeb={k.get('n_stillestaaende_loeb', '?')}  "
                         f"absurde={k.get('n_absurde', '?')}")
    finally:
        try:
            ib.disconnect()
        except Exception:
            pass

    res = {"koert": datetime.now().isoformat(timespec="seconds"),
           "niveauer": args.niveau, "bars": bars,
           "instrumenter": resultater, "kvalitet": kvalitet}
    json_sti.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    skriv_md(res, md_sti)
    emit(f"\nSkrevet:\n  {json_sti}\n  {md_sti}")
    emit("\n→ Send mig vol_data_probe.md, saa skriver jeg anbefalingen og statusnotatet.")
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="FASE V0 — probe af IBKR-raekkevidde og datakvalitet til volatilitets-byggeklodsen")
    ap.add_argument("--niveau", choices=["A", "AB", "ALL"], default="A",
                    help="A=kerne (~15-20 min), AB=+staerkt oensket (~40 min), ALL=alt (~60-75 min)")
    ap.add_argument("--bars", default=",".join(BARS_ALLE),
                    help="komma-separerede barstoerrelser")
    ap.add_argument("--port", type=int, default=IBKR_PORT)
    ap.add_argument("--client-id", type=int, default=CLIENT_ID)
    ap.add_argument("--kun", default=None,
                    help="komma-separeret liste af instrumenter der skal (gen)maales, "
                         "fx ES,RTY — resten beholdes fra tidligere koersel")
    ap.add_argument("--forfra", action="store_true",
                    help="ignorer tidligere resultater og maal alt igen")
    ap.add_argument("--spring-kvalitet", action="store_true",
                    help="spring V0.2 over (kun raekkevidde)")
    ap.add_argument("--vent-pacing", type=int, default=PACING_WAIT)
    args = ap.parse_args()

    def emit(s=""):
        print(s, flush=True)

    try:
        sys.exit(asyncio.run(koer(args, emit)))
    except KeyboardInterrupt:
        emit("\nAfbrudt — det allerede maalte er gemt. Koer igen for at fortsaette.")
        sys.exit(130)


if __name__ == "__main__":
    main()
