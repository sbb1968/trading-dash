"""
test_trendjoin_news.py — nyhedskatalysator-gaten i Trend Join Long
──────────────────────────────────────────────────────────────────
Baggrund: TJL handlede ALDRIG. Diagnosen (3/8-2026) var ikke datamangel, men en
type-fejl: ib_async 2.1 leverer HistoricalNews.time som et NAIVT datetime, mens
_news_ts_epoch var skrevet til en streng. datetime har ingen .strip(), saa hvert
kald returnerede 0.0 — aeldre end enhver cutoff — og ALLE nyheder blev filtreret
fra foer sentiment blev talt. Gaten svarede altid "ingen frisk positiv nyhed".

Sektioner:
  A — _news_ts_epoch accepterer BEGGE former (datetime og streng)
  B — check_positive_catalyst aabner paa en frisk bullish overskrift
  C — afvisningsgrundene kan skelnes fra hinanden (den gamle kode sagde det samme
      uanset aarsag, hvilket skjulte fejlen i over en maaned)
  D — opsamling af friske overskrifter (raamateriale til at kvalificere ordlisten)

Kør i backend-mappen:  python test_trendjoin_news.py
"""

import asyncio
import sys
from datetime import datetime, timedelta, timezone

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

import algo_trendjoin as tj


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        raise SystemExit(1)


class FakeNews:
    """Efterligner ib_async.objects.HistoricalNews — time ER et datetime."""

    def __init__(self, time, headline):
        self.time = time
        self.providerCode = "BRFG"
        self.articleId = "x"
        self.headline = headline


class FakeConn:
    def __init__(self, news):
        self._news = news
        self.ib = self

    async def qualifyContractsAsync(self, c):
        c.conId = 12345
        return [c]

    async def reqHistoricalNewsAsync(self, *a, **k):
        return self._news


def frisk(timer_siden: float) -> datetime:
    """Naivt UTC-datetime, som ib_async leverer det."""
    return (datetime.now(timezone.utc) - timedelta(hours=timer_siden)).replace(tzinfo=None)


def kald(news):
    conn = FakeConn(news)
    return asyncio.run(tj.check_positive_catalyst(conn, "TEST", "BRFG+DJNL"))


# ── A — tidsstempel-parsing ────────────────────────────────────
def section_A():
    print("\nSektion A — _news_ts_epoch accepterer begge former")
    d = frisk(2)
    ep_dt = tj._news_ts_epoch(d)
    check("A1 naivt datetime giver et rigtigt epoch (ikke 0.0)", ep_dt > 0, ep_dt)

    ep_str = tj._news_ts_epoch(d.strftime("%Y-%m-%d %H:%M:%S.0"))
    check("A2 streng virker stadig (bagudkompatibel)", ep_str > 0, ep_str)
    check("A3 de to former giver samme tid", abs(ep_dt - ep_str) < 1.5,
          (ep_dt, ep_str))

    aware = datetime.now(timezone.utc) - timedelta(hours=2)
    check("A4 tz-aware datetime haandteres ogsaa",
          abs(tj._news_ts_epoch(aware) - aware.timestamp()) < 1.0)
    check("A5 skrald giver 0.0 (ingen crash)", tj._news_ts_epoch(object()) == 0.0)


# ── B — gaten aabner ───────────────────────────────────────────
def section_B():
    print("\nSektion B — gaten aabner paa frisk bullish nyhed")
    ok, detail, ts = kald([FakeNews(frisk(2), "{A:1:L:en}Acme wins FDA approval for lead drug")])
    check("B1 frisk bullish overskrift → KATALYSATOR", ok is True, detail)
    check("B1 overskrift returneret uden metadata-praefiks",
          detail.startswith("Acme wins"), detail)
    check("B1 best_ts sat", ts > 0, ts)

    # Den praecise regression: netop denne kombination gav FALSE foer rettelsen.
    ok2, d2, _ = kald([
        FakeNews(frisk(1), "{A:1:L:en}Biotech surges after breakthrough trial data"),
        FakeNews(frisk(30), "{A:1:L:en}Old bearish headline about a lawsuit"),
    ])
    check("B2 frisk bullish + GAMMEL bearish → katalysator (gammel ignoreres)",
          ok2 is True, d2)

    ok3, d3, _ = kald([
        FakeNews(frisk(1), "{A:1:L:en}Company wins contract"),
        FakeNews(frisk(1), "{A:1:L:en}Company faces lawsuit and fraud probe"),
    ])
    check("B3 lige friske bull+bear → netto ikke bullish → afvist", ok3 is False, d3)
    check("B3 afvisning navngiver bull/bear", "bull=" in d3, d3)


# ── C — afvisningsgrundene kan skelnes ─────────────────────────
def section_C():
    print("\nSektion C — afvisningsgrunde er entydige")
    ok, d, _ = kald([FakeNews(frisk(40), "{A:1:L:en}Acme wins FDA approval")])
    check("C1 kun GAMLE nyheder → siger 'ingen friskere end'", ok is False and
          "friskere end" in d, d)

    ok, d, _ = kald([FakeNews(frisk(2), "{A:1:L:en}Acme announces pricing of public offering")])
    check("C2 friske men neutrale → siger '0 bullish' OG viser overskriften",
          ok is False and "0 bullish" in d and "pricing" in d.lower(), d)

    # Selve fejlen vi rettede: et uparsbart tidsstempel maa IKKE ligne datamangel.
    ok, d, _ = kald([FakeNews(object(), "{A:1:L:en}Acme wins FDA approval")])
    check("C3 uparsbart tidsstempel → siger PARSE-FEJL, ikke datamangel",
          ok is False and "PARSE-FEJL" in d, d)

    ok, d, _ = kald([])
    check("C4 tom nyhedsliste → 'ingen nyheder'", ok is False and d == "ingen nyheder", d)

    ok, d, _ = asyncio.run(tj.check_positive_catalyst(FakeConn([]), "TEST", "")), None, None
    check("C5 ingen providere → egen besked", ok[0] is False and
          ok[1] == "ingen nyheds-providere", ok)


# -- D — overskrift-opsamling (raamateriale til at kvalificere keyword-listen) --
def section_D():
    print("\nSektion D — opsamling af friske overskrifter")
    fangst = []
    ok, d, _ = kald_med([
        FakeNews(frisk(1), "{A:1:L:en}Acme wins FDA approval"),
        FakeNews(frisk(2), "{A:1:L:en}Acme to present at investor conference"),
        FakeNews(frisk(40), "{A:1:L:en}Gammel nyhed der skal IGNORERES"),
    ], fangst)
    check("D1 kun FRISKE overskrifter opsamles", len(fangst) == 2, fangst)
    check("D2 overskrift gemt UDEN metadata-praefiks",
          all(not o["headline"].startswith("{") for o in fangst), fangst)
    check("D3 vores klassifikation foelger med",
          {o["sentiment"] for o in fangst} == {"bullish", "neutral"},
          [o["sentiment"] for o in fangst])
    check("D4 tidsstempel er et rigtigt epoch", all(o["ts"] > 0 for o in fangst), fangst)
    check("D5 gaten paavirkes IKKE af opsamlingen", ok is True, d)

    # Uden collect skal adfaerden vaere bit-for-bit som foer.
    ok2, d2, _ = kald([FakeNews(frisk(1), "{A:1:L:en}Acme wins FDA approval")])
    check("D6 uden collect: uaendret 3-tuple-kontrakt", ok2 is True and d2.startswith("Acme"), d2)


def kald_med(news, fangst):
    conn = FakeConn(news)
    return asyncio.run(tj.check_positive_catalyst(conn, "TEST", "BRFG+DJNL", collect=fangst))


if __name__ == "__main__":
    section_A()
    section_B()
    section_C()
    section_D()
    print("\nALLE TESTS BESTÅET ✓")
