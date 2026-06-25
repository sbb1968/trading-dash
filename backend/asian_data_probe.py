#!/usr/bin/env python3
"""
asian_data_probe.py
═══════════════════
Moellen-trin 1 ("definer universet") for det asiatiske spor. Svarer EMPIRISK paa:
hvilke asiatiske instrumenter har kontoen (DUO509856) live-/historik-data til, hvad
koster de i mikrostruktur (spread/volumen), og hvor langt raekker 1-min-historikken.

Udvider data_access_probe.py-moenstret (REALTID vs FORSINKET) med tre ekstra tjek
pr. instrument, saa outputtet direkte afgoer hvilket (tidsvindue, aktiv)-par der er
tradebart. FIRE tjek pr. instrument:
  A. KVALIFICERING + OEKONOMI  (reqContractDetails: localSymbol/expiry/mult/minTick)
  B. SESSIONSTIDER             (liquidHours -> dansk tid FOERST, lokal i parentes)
  C. LIVE-FEED + spread/volumen (reqMktData: realtid/forsinket + mikrostruktur)
  D. 1-MIN HISTORIK-DYBDE + AARSAG (reqHeadTimeStamp + reqHistoricalData)

AENDRING (denne version): tjek D rapporterer nu den PRAECISE AARSAG, naar historik
fejler — i stedet for at sluge fejlen og bare sige "ingen bars". To tilfoejelser:
  * reqHeadTimeStamp: eet billigt kald der giver TIDLIGST tilgaengelige bar (hvor langt
    tilbage data raekker) OG renest afsloerer permission/availability (bruger HMDS,
    samme rettigheder som bars). Findes en head-timestamp -> data FINDES.
  * Fejl-destillering: 162-beskeden adskiller permission ("No market data permissions
    for OSE.JPN") vs pacing ("pacing violation") vs no-data. Timeout vs IBKR-fejl vs
     aegte-tom holdes nu adskilt. Lette pacing-pauser mellem historik-kald.

Koeber INTET. reqMktData/reqHistoricalData paa et uabonneret instrument giver bare en
fejl/delayed — ingen maanedsgebyrer udloeses. Koer mod Gateway paa algoserveren (ingen
Client Portal-login -> feeden bumpes ikke). Egen client-id 45 (distinkt fra 27/35/36/40).

Python 3.14: event-loop-fix oeverst. ib_async, alle *Async-kald. Laes-only.

TIMING: A/B/D virker NAAR SOM HELST; C (realtid-vs-frosset + spread/volumen) er kun
repraesentativ naar det asiatiske marked er AABENT. Koer i et asiatisk vindue, fx
~03:00-07:00 dansk (rammer Japan/Hongkong/ASX samtidigt).

VIGTIGT efter et nyt abonnement: rettigheder opdateres foerst ved en NY Gateway-
forbindelse. Re-login Gateway FOER genkoersel, ellers ser proben de gamle rettigheder.

Brug:
    python asian_data_probe.py
    python asian_data_probe.py --port 7497 --client-id 45

Placering: C:\\projects\\trading_dash\\backend\\asian_data_probe.py
"""

from __future__ import annotations

import asyncio
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import argparse
import math
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

OUTPUT_DIRNAME = "asian_data_probe_output"
GREEN, YELLOW, RED = "✅", "⚠️ ", "❌"

DK = ZoneInfo("Europe/Copenhagen")

# Pacing: lille pause foer hvert historik-kald (reqHeadTimeStamp/reqHistoricalData), saa
# en raekke kald ikke udloeser en 162 pacing-violation der forurener diagnosen.
PACE = 1.0

# IBKR timeZoneId -> IANA. Udvid efter behov fra hvad proben faktisk ser i kolonnen.
TZMAP = {
    "Japan": "Asia/Tokyo", "Hongkong": "Asia/Hong_Kong", "Hong_Kong": "Asia/Hong_Kong",
    "Singapore": "Asia/Singapore", "Australia/NSW": "Australia/Sydney",
    "Australia/ACT": "Australia/Sydney", "US/Eastern": "America/New_York", "GMT": "Etc/GMT",
}

# (label, art, kwargs).  art in {"future", "fx"}.
# future: lad reqContractDetails finde front-maaned via symbol+exchange+currency.
# Symbol-listen er bedste gaet — forkerte -> "KUNNE IKKE FINDE KONTRAKT", korrekte
# naboer afsloeres af de der resolver. Foerste koersel er en opdagelse; ret bagefter.
INSTRUMENTS = [
    # --- Japan (Tokyo-session, ~02:00-08:00 dansk) ---
    ("Nikkei225 mini (OSE)",   "future", dict(symbol="N225M", exchange="OSE.JPN", currency="JPY")),
    ("Nikkei225 micro (OSE)",  "future", dict(symbol="N225MC", exchange="OSE.JPN", currency="JPY")),
    ("Nikkei225 USD (CME)",    "future", dict(symbol="NKD",    exchange="CME",     currency="USD")),
    ("Nikkei225 JPY (CME)",    "future", dict(symbol="NIY",    exchange="CME",     currency="JPY")),
    ("Nikkei225 stor (OSE)",   "future", dict(symbol="N225",   exchange="OSE.JPN", currency="JPY")),
    ("TOPIX (OSE)",            "future", dict(symbol="TOPX",   exchange="OSE.JPN", currency="JPY")),
    # --- Kina/Hongkong (~03:00-09:00 dansk) ---
    ("FTSE China A50 (SGX)",   "future", dict(symbol="XINA50", exchange="SGX",     currency="USD")),
    ("Hang Seng (HKFE)",       "future", dict(symbol="HSI",    exchange="HKFE",    currency="HKD")),
    ("Mini Hang Seng (HKFE)",  "future", dict(symbol="MHI",    exchange="HKFE",    currency="HKD")),
    # --- Australien (Sydney, tidligst, ~01:00-07:00 dansk) ---
    ("ASX SPI 200 (SNFE)",     "future", dict(symbol="SPI",    exchange="SNFE",    currency="AUD")),
    # --- FX-baseline (24/5, data oftest inkluderet) ---
    ("USD/JPY",                "fx",     dict(pair="USDJPY")),
    ("AUD/USD",                "fx",     dict(pair="AUDUSD")),
]

# fejlkoder (kopieret fra data_access_probe — ret ikke)
NOT_SUB = {354, 10168}       # ikke abonneret, ingen/ingen-delayed
DELAYED_SHOWN = {10167}      # ikke abonneret — viser delayed
COMPETING = {10197}          # abonneret, men konkurrerende live-session
BENIGN = {2104, 2106, 2107, 2108, 2158, 2150, 2119}  # data-farm-status, ufarlige


def _isnan(v) -> bool:
    return isinstance(v, float) and math.isnan(v)


def classify(mdt, got_price, error_codes):
    """Ren klassificering — unit-testbar. Kopieret verbatim fra data_access_probe."""
    codes = set(error_codes)
    if codes & COMPETING:
        return "ABONNERET (men konkurrerende live-session er aktiv)"
    if codes & NOT_SUB:
        return "IKKE ABONNERET (ingen live, ingen delayed)"
    if codes & DELAYED_SHOWN:
        return "IKKE ABONNERET (kun delayed tilgaengelig)"
    if mdt == 1:
        return "REALTID LIVE" if got_price else "REALTID LIVE (frosset — marked lukket)"
    if mdt in (3, 4):
        return "KUN DELAYED (ikke abonneret paa live)"
    if mdt == 2:
        return "REALTID (frosset — marked lukket)"
    return "UAFKLARET (ingen ticks/fejl modtaget)"


def _front_month_cd(cds):
    """Vaelg ContractDetails for naermeste kontrakt-maaned >= i dag. Spejler
    data_access_probe._front_month, men returnerer HELE cd (vi skal bruge minTick +
    liquidHours fra ContractDetails, ikke kun kontrakten)."""
    today = date.today().strftime("%Y%m%d")
    best = None
    for cd in cds:
        c = cd.contract
        exp = (c.lastTradeDateOrContractMonth or "")
        key = (exp + "01")[:8] if len(exp) == 6 else exp[:8]
        if key and key >= today and (best is None or key < best[0]):
            best = (key, cd)
    if best is None and cds:  # alt udloebet — tag seneste
        best = ("", sorted(cds, key=lambda x: x.contract.lastTradeDateOrContractMonth or "")[-1])
    return best[1] if best else None


async def resolve_details(ib, art, kwargs):
    """Returner ContractDetails (front-maaned for futures, foerste for FX) eller None.
    cd.contract er den kvalificerede kontrakt; cd baerer minTick + sessionstider."""
    from ib_async import Future, Forex
    try:
        if art == "fx":
            cds = await asyncio.wait_for(
                ib.reqContractDetailsAsync(Forex(kwargs["pair"])), timeout=15)
            return cds[0] if cds else None
        cds = await asyncio.wait_for(
            ib.reqContractDetailsAsync(Future(**kwargs)), timeout=15)
        return _front_month_cd(cds) if cds else None
    except Exception:
        return None


# ── Tjek B: sessionstider ─────────────────────────────────────────
def _resolve_tz(tz_id):
    for cand in (TZMAP.get(tz_id), tz_id):
        if cand:
            try:
                return ZoneInfo(cand)
            except Exception:
                pass
    return ZoneInfo("UTC")


def next_liquid_session(liquid_hours: str, tz_id: str):
    """liquidHours: 'YYYYMMDD:HHMM-YYYYMMDD:HHMM;YYYYMMDD:CLOSED;...' (ogsaa aeldre
    'YYYYMMDD:HHMM-HHMM'). Returner (start, slut) for FOERSTE ikke-CLOSED segment som
    tz-aware datetimes i instrumentets tid. None hvis intet."""
    tz = _resolve_tz(tz_id)

    def _parse(part, fallback_date):
        if ":" in part:
            d, t = part.split(":", 1)
        else:
            d, t = fallback_date, part
        return datetime.strptime(f"{d}{t}", "%Y%m%d%H%M").replace(tzinfo=tz)

    for seg in (liquid_hours or "").split(";"):
        seg = seg.strip()
        if not seg or seg.upper().endswith("CLOSED"):
            continue
        try:
            a, b = seg.split("-", 1)
            s = _parse(a, None)
            e = _parse(b, a.split(":", 1)[0])
            return s, e
        except (ValueError, TypeError):
            continue
    return None


# ── Tjek D: 1-min historik-dybde + AARSAG ─────────────────────────
def _hist_error_reason(errors):
    """Destillér den mest relevante IBKR-fejlbesked fra historik-kaldet (ikke-benigne
    koder). 162-beskeden adskiller permission vs pacing vs no-data."""
    out = []
    for _, cc, m in errors:
        if cc in BENIGN:
            continue
        msg = m.split("message:", 1)[-1].strip() if "message:" in m else (m or "").strip()
        tag = f"{cc}: {msg[:90]}"
        if tag not in out:
            out.append(tag)
    return " | ".join(out) if out else None


async def hist_diag(ib, c, what_first, errors):
    """Diagnosér 1-min historik. Returnerer dict:
      ok        : bool (kunne hente 1-min bars)
      newest    : nyeste bar-ts (eller None)
      oldest    : aeldste i 2-dages sample (eller None)
      n         : antal bars i sample
      used_what : TRADES/MIDPOINT der virkede
      earliest  : reqHeadTimeStamp (tidligst tilgaengelige bar) eller None
      reason    : aarsags-streng hvis ok=False (permission/pacing/no-data/timeout/...)
    """
    res = {"ok": False, "newest": None, "oldest": None, "n": 0,
           "used_what": what_first, "earliest": None, "reason": None}
    errors.clear()

    # 1) headTimestamp: eet billigt kald -> tidligst tilgaengelige bar + ren
    #    permission/availability-probe (HMDS, samme rettigheder som bars).
    await asyncio.sleep(PACE)
    try:
        head = await asyncio.wait_for(
            ib.reqHeadTimeStampAsync(c, whatToShow=what_first, useRTH=False, formatDate=2),
            timeout=20)
        if head:
            res["earliest"] = head
    except asyncio.TimeoutError:
        res["reason"] = "timeout paa reqHeadTimeStamp (data-farm langsom?)"
    except Exception as e:
        res["reason"] = f"{type(e).__name__}: {str(e)[:80]}"

    # 2) sample-pull (2 D) -> bekraeft 1-min granularitet + nyeste bar.
    async def fetch(dur, what):
        await asyncio.sleep(PACE)
        try:
            return await asyncio.wait_for(ib.reqHistoricalDataAsync(
                c, endDateTime="", durationStr=dur, barSizeSetting="1 min",
                whatToShow=what, useRTH=False, formatDate=2), timeout=30)
        except asyncio.TimeoutError:
            if not res["reason"]:
                res["reason"] = "timeout paa reqHistoricalData (data-farm langsom / pacing?)"
            return []
        except Exception as e:
            if not res["reason"]:
                res["reason"] = f"{type(e).__name__}: {str(e)[:80]}"
            return []

    bars = await fetch("2 D", what_first)
    used = what_first
    if not bars and what_first == "TRADES":
        bars = await fetch("2 D", "MIDPOINT")
        if bars:
            used = "MIDPOINT"
    if bars:
        res["ok"] = True
        res["used_what"] = used
        res["newest"] = bars[-1].date
        res["oldest"] = bars[0].date
        res["n"] = len(bars)
        res["reason"] = None
        return res

    # 3) ingen bars -> destillér den faktiske aarsag fra IBKR-fejlene (162-besked osv.).
    res["reason"] = (_hist_error_reason(errors) or res["reason"]
                     or "ingen bars og ingen fejl (UAFKLARET)")
    return res


# ── Per-instrument: fire tjek ─────────────────────────────────────
async def check_instrument(ib, label, art, kwargs, emit, errors, check_hist=True):
    """Koerer A-D for ét instrument. Returnerer status-dict til opsummeringen.
    check_hist=False springer tjek D over (undgaar reqHistoricalData-pacing — fx naar
    en harvest koerer paa samme konto)."""
    emit("─" * 60)
    emit(f"  {label}")
    emit("─" * 60)
    st = {"label": label, "found": False, "feed": "", "hist_ok": False,
          "spread": "", "hist_reason": ""}

    # ── A. KVALIFICERING + OEKONOMI ──
    cd = await resolve_details(ib, art, kwargs)
    if cd is None:
        emit(f"   {RED} KVALIFICERING: KUNNE IKKE FINDE KONTRAKT (symbol/expiry) — ret i listen")
        return st
    c = cd.contract
    st["found"] = True
    min_tick = getattr(cd, "minTick", None)
    mult_raw = getattr(c, "multiplier", "") or ""
    try:
        mult = float(mult_raw) if mult_raw not in ("", None) else None
    except (TypeError, ValueError):
        mult = None
    local = c.localSymbol or "?"
    ccy = c.currency or "?"
    exch = c.exchange or kwargs.get("exchange", "?")
    if art == "fx":
        emit(f"   {GREEN} KVALIFICERING: {local}  {ccy}  minTick={min_tick}  ({exch})")
    else:
        expiry = c.lastTradeDateOrContractMonth or "?"
        mult_s = f"mult={mult:g}" if mult is not None else "mult=?"
        emit(f"   {GREEN} KVALIFICERING: {local}  expiry {expiry}  {mult_s}  {ccy}  "
             f"minTick={min_tick}  ({exch})")

    # ── B. SESSIONSTIDER ──
    tz_id = getattr(cd, "timeZoneId", "") or "?"
    liquid = getattr(cd, "liquidHours", "") or ""
    sess = next_liquid_session(liquid, tz_id)
    if sess:
        s, e = sess
        s_dk, e_dk = s.astimezone(DK), e.astimezone(DK)
        emit(f"   {GREEN} SESSION: {s_dk:%H:%M}-{e_dk:%H:%M} dansk "
             f"({s:%H:%M}-{e:%H:%M} {tz_id})  [liquid]")
    else:
        trading = getattr(cd, "tradingHours", "") or ""
        sess_t = next_liquid_session(trading, tz_id)
        if sess_t:
            s, e = sess_t
            s_dk, e_dk = s.astimezone(DK), e.astimezone(DK)
            emit(f"   {YELLOW}SESSION: ingen liquidHours — trading {s_dk:%H:%M}-{e_dk:%H:%M} dansk "
                 f"({s:%H:%M}-{e:%H:%M} {tz_id})")
        else:
            emit(f"   {YELLOW}SESSION: kunne ikke parse sessionstider (tz={tz_id})")

    # ── C. LIVE-FEED + spread/volumen ──
    errors.clear()
    try:
        ticker = ib.reqMktData(c, "", False, False)
        await asyncio.sleep(3.5)
        bid, ask = ticker.bid, ticker.ask
        vol = ticker.volume
        prices = [ticker.last, ticker.close, bid, ask]
        got_price = any(p is not None and not _isnan(p) for p in prices)
        codes = [code for _, code, _ in errors if code not in BENIGN]
        mdt = getattr(ticker, "marketDataType", None)
        verdict = classify(mdt, got_price, codes)
        st["feed"] = verdict
        spread_s = ""
        if bid is not None and ask is not None and not _isnan(bid) and not _isnan(ask):
            spread = ask - bid
            parts = []
            if min_tick:
                parts.append(f"{spread / min_tick:.0f} tick")
            if mult is not None:
                parts.append(f"{spread * mult:.2f} {ccy}")
            else:
                parts.append(f"{spread:g} {ccy}")
            spread_s = "spread=" + " (".join(parts) + (")" if len(parts) > 1 else "")
            st["spread"] = spread_s
        vol_s = f"vol={vol:.0f}" if (vol is not None and not _isnan(vol)) else "vol=?"
        emoji = GREEN if verdict.startswith(("REALTID", "ABONNERET")) else RED
        bits = "  ".join([b for b in (spread_s, vol_s) if b])
        emit(f"   {emoji} FEED: {verdict}" + (f" — {bits}" if bits else ""))
        if codes:
            msgs = "; ".join(sorted({m.split(':')[-1].strip()[:48] for _, cc, m in errors if cc in codes}))
            emit(f"        [{msgs}]")
    except Exception as e:
        emit(f"   {RED} FEED: probe-fejl: {e}")
    finally:
        try:
            ib.cancelMktData(c)
        except Exception:
            pass

    # ── D. 1-MIN HISTORIK-DYBDE + AARSAG ──
    if not check_hist:
        emit(f"   {YELLOW}HISTORIK: sprunget over (--no-hist)")
        return st
    what_first = "MIDPOINT" if art == "fx" else "TRADES"
    try:
        diag = await hist_diag(ib, c, what_first, errors)
    except Exception as e:
        emit(f"   {RED} HISTORIK: probe-fejl: {e}")
        st["hist_reason"] = f"probe-fejl: {e}"
        return st

    if diag["ok"]:
        st["hist_ok"] = True
        what_note = f" [{diag['used_what']}]" if diag["used_what"] != what_first else ""
        earliest_s = (f" · raekker tilbage til {_fmt_ts(diag['earliest'])}"
                      if diag["earliest"] else " · dybde ukendt (ingen headTimeStamp)")
        emit(f"   {GREEN} HISTORIK: 1-min OK{what_note} · nyeste {_fmt_ts(diag['newest'])} · "
             f"{diag['n']} bars i 2-dages sample{earliest_s}")
    elif diag["earliest"]:
        # headTimeStamp gav en dato -> data FINDES; sample-pull fejlede (pacing/timeout),
        # IKKE manglende rettighed. Vigtig skelnen: koeb ikke abonnement for dette.
        st["hist_reason"] = f"data FINDES (tidligst {_fmt_ts(diag['earliest'])}), pull fejlede: {diag['reason']}"
        emit(f"   {YELLOW}HISTORIK: data FINDES (tidligst {_fmt_ts(diag['earliest'])}), "
             f"men sample-pull fejlede — {diag['reason']}")
    else:
        st["hist_reason"] = diag["reason"]
        emit(f"   {RED} HISTORIK: ingen 1-min bars — {diag['reason']}")
    return st


def _fmt_ts(ts):
    """formatDate=2 giver typisk datetime; fald tilbage til str."""
    try:
        return ts.strftime("%Y-%m-%d %H:%M") if hasattr(ts, "strftime") else str(ts)
    except Exception:
        return str(ts)


async def probe(host, port, client_id, emit, check_hist=True):
    from ib_async import IB
    ib = IB()
    errors = []  # (reqId, code, msg)

    def on_error(reqId, code, msg, *_):
        errors.append((reqId, code, msg))

    ib.errorEvent += on_error
    await ib.connectAsync(host, port, clientId=client_id, timeout=15)
    ib.reqMarketDataType(1)  # bed om LIVE; ikke-abonnerede giver fejl (ingen auto-delayed)
    results = []
    try:
        for label, art, kwargs in INSTRUMENTS:
            st = await check_instrument(ib, label, art, kwargs, emit, errors, check_hist)
            results.append(st)
            emit("")
    finally:
        ib.disconnect()
    return results


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description="IBKR asiatisk data-adgangs-probe (koeber intet)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=7497)
    ap.add_argument("--client-id", type=int, default=45)
    ap.add_argument("--no-hist", action="store_true",
                    help="spring tjek D (1-min historik-dybde) over — undgaa reqHistoricalData-"
                         "pacing-konflikt med en koerende harvest paa samme konto")
    args = ap.parse_args()

    out_dir = Path.cwd() / OUTPUT_DIRNAME
    out_dir.mkdir(exist_ok=True)
    lines = []

    def emit(s=""):
        print(s, flush=True)
        lines.append(s)

    emit("=" * 78)
    emit("  IBKR ASIATISK DATA-ADGANGS-PROBE  (koeber intet)")
    emit("=" * 78)
    emit(f"Tid: {datetime.now():%Y-%m-%d %H:%M}   Gateway: {args.host}:{args.port}   "
         f"client-id {args.client_id}")
    emit("Bemaerk: marked lukket = 'frosset' men stadig REALTID HVIS abonneret "
         "(spread/volumen er saa ikke repraesentativt).")
    emit("Historik fejler? Linjen viser nu AARSAGEN: 162-permission (mangler abonnement) "
         "vs pacing/timeout (forbigaaende) vs 'data FINDES' (rettighed OK).")
    emit("")

    if args.no_hist:
        emit("MODE: --no-hist — tjek D (historik-dybde) springes over (pacing-venlig).")
        emit("")

    try:
        results = asyncio.run(probe(args.host, args.port, args.client_id, emit,
                                    check_hist=not args.no_hist))
    except Exception as e:
        emit(f"FORBINDELSESFEJL: {e}")
        emit(f"Tjek at Gateway koerer paa algoserveren og at API-port {args.port} er aaben "
             f"+ client-id {args.client_id} er ledigt.")
        (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
        return 1

    # ── Opsummering: lister ──
    realtid = [r for r in results if r["found"] and r["feed"].startswith(("REALTID", "ABONNERET"))]
    # Uden historik-tjek kan vi ikke afgoere "tradebar" fuldt — vis kun realtid-fundne.
    if args.no_hist:
        tradebar = [r["label"] for r in realtid]
        tradebar_label = "Realtid + fundet (historik IKKE tjekket, --no-hist)"
    else:
        tradebar = [r["label"] for r in realtid if r["hist_ok"]]
        tradebar_label = "Tradebar kandidat (realtid + 1-min-historik OK)"
    mangler = [r["label"] for r in results
               if r["found"] and ("IKKE ABONNERET" in r["feed"] or "KUN DELAYED" in r["feed"])]
    ikke_fundet = [r["label"] for r in results if not r["found"]]
    hist_fail = [(r["label"], r.get("hist_reason", "")) for r in results
                 if r["found"] and not r["hist_ok"] and r.get("hist_reason")]

    emit("─" * 78)
    emit("  OPSUMMERING")
    emit("─" * 78)
    emit(f"  {tradebar_label}: {', '.join(tradebar) if tradebar else '(ingen)'}")
    emit(f"  Mangler dataabonnement (ikke abonneret / kun delayed): "
         f"{', '.join(mangler) if mangler else '(ingen)'}")
    emit(f"  Kontrakt ikke fundet (ret symbol i listen): "
         f"{', '.join(ikke_fundet) if ikke_fundet else '(ingen)'}")
    if hist_fail and not args.no_hist:
        emit("  Historik utilgaengelig — AARSAG pr. instrument (skel permission fra pacing):")
        for lab, why in hist_fail:
            emit(f"      - {lab}: {why}")
    emit("  → Spread/volumen er kun repraesentativt naar det asiatiske marked er aabent "
         "(~03:00-07:00 dansk).")
    emit("")
    (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
    emit(f"Fil: {out_dir / 'summary.txt'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
