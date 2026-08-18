#!/usr/bin/env python3
r"""
oekonomisk_kalender.py — planlagte begivenheder der kan flytte MES
════════════════════════════════════════════════════════════════════════════════
Daglig drift: hvad sker der i dag, der kan flytte S&P 500.

KILDE: ForexFactory. To veje ind, med hver sin begrænsning — begge målt 18-08:

  · `nfs.faireconomy.media/ff_calendar_thisweek.json`
      Let, ren JSON, ET-stemplet. **Kun indeværende uge** — `lastweek` og
      `nextweek` giver 404.
      ⚠ RATE-LIMITER. Fire kald på et par minutter gav HTTP 429. Derfor cache
      og ét kald om dagen; ellers er kalenderen nede præcis den morgen den
      betyder noget.

  · `forexfactory.com/calendar?week=aug9.2026`
      391 KB HTML med kalenderen indlejret som JS-objekt. Kan hente VILKÅRLIGE
      uger, også bagud. Bruges kun når historik skal høstes — ikke dagligt.

⚠ TIDEN LÆSES AF `dateline` (UNIX-sekunder), ALDRIG af de viste klokkeslæt.
Siden gengiver tider i browserens zone. Alle tre kilder i Sørens skærmbillede
viste 14:30 for retail sales; det er dansk tid, og feedet siger 08:30 ET.
Samme øjeblik — men en aflæsning af det VISTE ville arve en forskydning man
ikke kan se, og den ville flytte sig to gange om året når sommertiden skifter
forskudt i EU og USA.

VERIFICERET mod Sørens skærmbillede (ForexFactory + TradingEconomics +
Investing.com, ugen 9.–15. august 2026): 11 af 11 events stemte på navn, dato
OG klokkeslæt. Se `kalender_verifikation.md`.

CACHEN er idempotent: nøglet på (dateline, ebase_id), så et gentaget kald ikke
lægger dubletter ind. `ebase_id` er ForexFactorys stabile id pr. BEGIVENHEDSTYPE
— "CPI m/m" beholder sit id over år, så en serie kan følges uden at matche på
navnestrenge der ændrer sig.

    python oekonomisk_kalender.py --hent          # dagens uge → cache
    python oekonomisk_kalender.py --vis 2026-08-14
    python oekonomisk_kalender.py --vis 2026-08-12 --alle
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import urllib.request
from pathlib import Path

HER = Path(__file__).resolve().parent
CACHE = HER / "kalender_cache"
FIL = CACHE / "events.json"

FEED = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
UGE_HTML = "https://www.forexfactory.com/calendar?week={uge}"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

# ⚠ MES er S&P 500. USD-begivenheder er dem der flytter den; de øvrige valutaer
# hentes med, men mærkes, så en fremtidig bruger kan vælge selv frem for at
# opdage at de blev kasseret i stilhed.
VIGTIG_VALUTA = "USD"


def _et(unix: int) -> dt.datetime:
    import pytz
    return dt.datetime.fromtimestamp(unix, tz=dt.timezone.utc).astimezone(
        pytz.timezone("America/New_York"))


# ⚠ ForexFactory skriver "High impact expected", ikke "High" — og HTML-vejen og
# JSON-feedet skriver det FORSKELLIGT (feedet siger bare "High"). Uden en fælles
# oversættelse ville de samme events få to forskellige impact-værdier alt efter
# hvilken vej de kom ind, og et filter på "Medium+" ville tie om halvdelen.
def _impact(raa) -> str | None:
    s = (raa or "").lower()
    for niveau in ("high", "medium", "low"):
        if niveau in s:
            return niveau.capitalize()
    if "non-economic" in s or "holiday" in s:
        return "Ikke-oekonomisk"
    return None


def normalisér(navn, valuta, dateline, impact, forecast, previous, actual,
               ebase_id=None) -> dict:
    t = _et(int(dateline))
    return {
        "dateline": int(dateline),
        "dato_et": t.strftime("%Y-%m-%d"),
        "tid_et": t.strftime("%H:%M"),
        "valuta": valuta,
        "navn": navn,
        "ebase_id": ebase_id,
        "impact": _impact(impact),
        "forecast": forecast or None,
        "previous": previous or None,
        "actual": actual or None,
    }


# ── Kilde 1: JSON-feedet (dagligt) ──────────────────────────────────────────
def fra_feed() -> list[dict]:
    """Indeværende uge. ⚠ Ét kald — feedet rate-limiter."""
    r = urllib.request.Request(FEED, headers={"User-Agent": UA})
    raa = json.load(urllib.request.urlopen(r, timeout=30))
    ud = []
    for x in raa:
        # feedets `date` er ISO med ET-offset; laves om til unix så nøglen er
        # den samme som HTML-vejens `dateline`
        t = dt.datetime.fromisoformat(x["date"])
        ud.append(normalisér(x["title"], x["country"], t.timestamp(),
                             x.get("impact"), x.get("forecast"),
                             x.get("previous"), None))
    return ud


# ── Kilde 2: uge-HTML (historik, kun når det behøves) ───────────────────────
def fra_uge_html(uge: str, html: str | None = None) -> list[dict]:
    """`uge` som ForexFactory skriver den, fx 'aug9.2026'."""
    if html is None:
        r = urllib.request.Request(UGE_HTML.format(uge=uge), headers={"User-Agent": UA})
        html = urllib.request.urlopen(r, timeout=45).read().decode("utf-8", "replace")

    # ⚠ KLAMMEMATCHNING, IKKE REGEX. Det omgivende er et JS-objekt med
    # unoterede nøgler; et regex-"fix" af dem brækker strenge der selv
    # indeholder ':' — og der er mange (URL'er, klokkeslæt).
    # `days`-arrayet ER derimod gyldig JSON, så kun det skæres ud.
    i = html.index("calendarComponentStates[1]")
    i = html.index("days:", i)
    j = html.index("[", i)
    dyb, k, inde, esc = 0, j, False, False
    while k < len(html):
        c = html[k]
        if inde:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                inde = False
        else:
            if c == '"':
                inde = True
            elif c == "[":
                dyb += 1
            elif c == "]":
                dyb -= 1
                if dyb == 0:
                    break
        k += 1
    dage = json.loads(html[j:k + 1])

    ud = []
    for d in dage:
        for e in d.get("events", []):
            ud.append(normalisér(
                e.get("name"), e.get("currency"), e.get("dateline"),
                e.get("impactTitle") or e.get("impactClass"),
                e.get("forecast"), e.get("previous"), e.get("actual"),
                e.get("ebaseId")))
    return ud


# ── Cache ───────────────────────────────────────────────────────────────────
def laes_cache() -> dict[str, dict]:
    if not FIL.exists():
        return {}
    return {n["_noegle"]: n for n in json.loads(FIL.read_text(encoding="utf-8"))}


def flet(nye: list[dict]) -> tuple[int, int]:
    """Læg nye events i cachen. (tilføjet, opdateret).

    ⚠ IDEMPOTENT. Nøglen er (dateline, ebase_id eller navn) — kør to gange og
    få samme resultat. Uden det ville en daglig hentning lægge den samme uge
    ind igen og igen, og cachen ville vokse uden at der kom ny viden. Det er
    præcis den fejl vol-harvesten blev bygget for at undgå.
    """
    CACHE.mkdir(parents=True, exist_ok=True)
    haves = laes_cache()
    ny, opd = 0, 0
    for e in nye:
        n = f"{e['dateline']}|{e['ebase_id'] or e['navn']}"
        e = {**e, "_noegle": n}
        if n not in haves:
            haves[n] = e
            ny += 1
        elif haves[n] != e:
            # ⚠ `actual` kommer FØRST efter offentliggørelsen, så en opdatering
            # af en kendt række er normal og skal ikke tælle som en ny.
            haves[n] = {**haves[n], **{k: v for k, v in e.items() if v is not None}}
            opd += 1
    FIL.write_text(json.dumps(sorted(haves.values(), key=lambda x: x["dateline"]),
                              ensure_ascii=False, indent=1), encoding="utf-8")
    return ny, opd


def vis(dato: str, kun_usd: bool = True, mindst: str = "Medium") -> None:
    RANG = {"High": 3, "Medium": 2, "Low": 1, None: 0}
    e = [x for x in laes_cache().values() if x["dato_et"] == dato]
    if kun_usd:
        e = [x for x in e if x["valuta"] == VIGTIG_VALUTA]
    if mindst:
        e = [x for x in e if RANG.get(x["impact"], 0) >= RANG[mindst]]
    e.sort(key=lambda x: x["dateline"])
    if not e:
        print(f"  (ingen events {dato} med de filtre)")
        return
    ugedag = dt.date.fromisoformat(dato).strftime("%A")
    print(f"\n{ugedag} {dato}   ({len(e)} events)")
    print(f"  {'ET':6}{'DK':6}{'impact':8}{'begivenhed':40}{'prog.':>9}{'forrige':>9}{'faktisk':>9}")
    print("  " + "─" * 87)
    for x in e:
        # ⚠ DK udledes af ET-tidsstemplet, ikke af et fast +6. EU og USA
        # skifter sommertid på FORSKELLIGE datoer, så forskellen er 6 timer i
        # 48 uger om året og 5 i de øvrige fire.
        import pytz
        t = _et(x["dateline"])
        dk = t.astimezone(pytz.timezone("Europe/Copenhagen")).strftime("%H:%M")
        print(f"  {x['tid_et']:6}{dk:6}{(x['impact'] or '—'):8}{x['navn'][:38]:40}"
              f"{(x['forecast'] or '—'):>9}{(x['previous'] or '—'):>9}"
              f"{(x['actual'] or '—'):>9}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--hent", action="store_true", help="indeværende uge fra JSON-feedet")
    ap.add_argument("--uge", help="historik fra uge-HTML, fx aug9.2026")
    ap.add_argument("--fra-fil", help="parse en allerede hentet uge-HTML")
    ap.add_argument("--vis", help="vis en dato (YYYY-MM-DD)")
    ap.add_argument("--alle", action="store_true", help="ikke kun USD, ikke kun Medium+")
    a = ap.parse_args()

    if a.hent:
        n, o = flet(fra_feed())
        print(f"feed: {n} nye, {o} opdateret · cache: {len(laes_cache())} events")
    if a.uge or a.fra_fil:
        html = Path(a.fra_fil).read_text(encoding="utf-8", errors="replace") if a.fra_fil else None
        n, o = flet(fra_uge_html(a.uge or "", html))
        print(f"uge {a.uge or a.fra_fil}: {n} nye, {o} opdateret · "
              f"cache: {len(laes_cache())} events")
    if a.vis:
        vis(a.vis, kun_usd=not a.alle, mindst=None if a.alle else "Medium")
    if not any((a.hent, a.uge, a.fra_fil, a.vis)):
        ap.print_help()
