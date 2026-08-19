#!/usr/bin/env python3
r"""
test_feed_opstartskontrol.py — "Streaming startet" skal betyde at der er data
════════════════════════════════════════════════════════════════════════════════
PAA IBENS WORKSTATION 19-08 KL. 07:39 STOD DER I LOGGEN:

    07:39:45   34 x "No market data during competing live session" / "different IP"
    07:39:49   [LiveFeed] Streaming startet

Feedet meldte succes efter at hvert eneste af de 20 abonnementer var afvist.
Kontrollen i `start()` var `if not self.conn.connected` — og `connected` er ikke
det samme som "har markedsdata". Paa en konto uden abonnement er de to
systematisk forskellige.

⚠ OG DET KOSTEDE MERE END EN TOM KOLONNE. `_uden_feed()` i main.py indeholder en
faerdigbygget kursproxy mod algoserveren, skrevet med netop Ibens maskine som
navngivent eksempel. Den vej blev aldrig taget, fordi systemet troede den lokale
vej lykkedes. Faldskaermen udloeste ikke, fordi hovedskaermen meldte sig foldet ud.

⚠ BEGGE RETNINGER PROEVES, og den forkerte vej er den farligste:
  · et feed der FAAR data maa aldrig overdrage (saa havde vi bygget en ny fejl)
  · findes der ingen at overdrage til, skal feedet BLIVE KOERENDE — et tomt feed
    er ikke daarligere end intet feed, men et nedlagt feed kan ikke komme sig

    python test_feed_opstartskontrol.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ibkr_live_feed as lf

FEJL: list[str] = []
NAN = float("nan")


def kraev(betingelse, hvad: str) -> None:
    print(f"  {'OK  ' if betingelse else 'FEJL'}  {hvad}")
    if not betingelse:
        FEJL.append(hvad)


# ── Stubbe ──────────────────────────────────────────────────────────────────
class FakeTicker:
    """ib_async udfylder manglende data med NaN — det er praecis det tilfaelde
    der skal skelnes fra en rigtig pris."""

    def __init__(self, last=NAN, close=NAN, market=NAN):
        self.last = last
        self.close = close
        self.open = NAN
        self.volume = NAN
        # _build_ticks laeser ogsaa disse. En attrap der mangler dem, faar
        # loekken til at kaste AttributeError inde i sin egen except — og saa
        # ville testen "der sendes ticks ud" vaere roed af den forkerte grund.
        self.bid = self.ask = self.high = self.low = NAN
        self.halted = NAN
        self.contract = object()
        self._market = market

    def marketPrice(self):
        return self._market


class FakeIB:
    def __init__(self, priser: dict):
        self._priser = priser
        self.annulleret: list = []

    def reqMktData(self, contract, *a, **kw):
        return self._priser.get(contract, FakeTicker())

    def cancelMktData(self, contract):
        self.annulleret.append(contract)


class FakeConn:
    """Kun det feedet roerer."""

    def __init__(self, priser_pr_symbol: dict, connected=True):
        self.connected = connected
        self._pr_symbol = priser_pr_symbol
        self._kontrakter = {s: object() for s in priser_pr_symbol}
        self.ib = FakeIB({self._kontrakter[s]: t
                          for s, t in priser_pr_symbol.items()})

    async def _resolve_contract(self, sym):
        if sym not in self._kontrakter:
            raise ValueError(f"ukendt ticker {sym!r}")
        return self._kontrakter[sym]

    async def scan_top_gainers(self, max_results=20):
        return []

    async def get_historical_bars(self, *a, **kw):
        return []


class FakeAlerts:
    def process_ticks(self, ticks):
        return []


def byg(priser: dict, paa_doedt_feed=None, connected=True):
    async def broadcast(besked):
        broadcast.kaldt.append(besked)
    broadcast.kaldt = []
    conn = FakeConn(priser, connected=connected)
    feed = lf.IBKRLiveFeed(conn, broadcast, FakeAlerts(),
                           paa_doedt_feed=paa_doedt_feed)
    return feed, broadcast


async def _koer_start(feed, sekunder=1.5):
    """Start feedet og lad loekken snurre kort. Returnerer naar den er stoppet
    eller tiden er gaaet."""
    t = asyncio.create_task(feed.start())
    try:
        await asyncio.wait_for(asyncio.shield(t), timeout=sekunder)
    except asyncio.TimeoutError:
        pass                     # loekken koerer = feedet blev startet
    finally:
        if not t.done():
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
    return t


# ════════════════════════════════════════════════════════════════════════════
def test_pris_udtraek():
    """⚠ Kontrollen og loekken SKAL bruge samme udtraek. Gjorde de ikke det,
    ville kontrollen maale noget andet end det loekken sender ud — altsaa vaere
    blind for netop den fejl den er sat til at fange."""
    print("\n[1] Faelles pris-udtraek")
    kraev(lf._pris(FakeTicker(last=100.0)) == 100.0, "last bruges naar den findes")
    kraev(lf._pris(FakeTicker(close=99.5)) == 99.5, "close bruges naar last er NaN")
    kraev(lf._pris(FakeTicker(market=98.0)) == 98.0, "marketPrice() som sidste udvej")
    kraev(lf._pris(FakeTicker()) is None, "alt NaN -> None")
    kraev(lf._pris(FakeTicker(last=0.0, close=0.0)) is None, "nul er ikke en pris")
    # Rangordningen skal vaere last > close > market
    kraev(lf._pris(FakeTicker(last=1.0, close=2.0, market=3.0)) == 1.0,
          "last vinder over close og marketPrice")


def test_feed_med_data_starter_normalt():
    """Kendt-positiv: et feed der FAAR priser maa aldrig overdrage."""
    print("\n[2] Feed med data")
    overdraget = []

    async def paa_doedt(hvorfor, syms):
        overdraget.append(hvorfor)
        return True

    feed, bc = byg({"AAPL": FakeTicker(last=230.0),
                    "TSLA": FakeTicker(last=245.0)}, paa_doedt_feed=paa_doedt)
    lf.FALLBACK_UNIVERSE_GEM = lf.FALLBACK_UNIVERSE
    lf.FALLBACK_UNIVERSE = ["AAPL", "TSLA"]
    try:
        # 3 sekunder, ikke 1,5. Opstartskontrollen bruger 0,5 s og loekken sover
        # 1,0 s foer sin FOERSTE udsendelse -- et 1,5-sekunders vindue rammer
        # praecis kanten, og testen ville vaere roed af utaalmodighed frem for
        # af en fejl i koden.
        asyncio.run(_koer_start(feed, sekunder=3.0))
    finally:
        lf.FALLBACK_UNIVERSE = lf.FALLBACK_UNIVERSE_GEM
    kraev(not overdraget, "feed med priser overdrager IKKE")
    kraev(feed._antal_med_pris() == 2, "begge symboler har en pris")
    kraev(any(b.get("type") == "ticks" for b in bc.kaldt),
          "og der bliver faktisk sendt ticks ud")


def test_delvis_daekning_er_hverdag():
    """Kendt-positiv 2: nogle symboler uden pris er normalt (doede tickere,
    tynde papirer). Kun NUL af N er en fejl."""
    print("\n[3] Delvis daekning")
    overdraget = []

    async def paa_doedt(hvorfor, syms):
        overdraget.append(hvorfor)
        return True

    feed, _ = byg({"AAPL": FakeTicker(last=230.0),
                   "SKLZ": FakeTicker(),          # afnoteret, ingen pris
                   "SPRT": FakeTicker()}, paa_doedt_feed=paa_doedt)
    gem = lf.FALLBACK_UNIVERSE
    lf.FALLBACK_UNIVERSE = ["AAPL", "SKLZ", "SPRT"]
    try:
        asyncio.run(_koer_start(feed))
    finally:
        lf.FALLBACK_UNIVERSE = gem
    kraev(not overdraget, "1 af 3 med pris -> ingen overdragelse")
    kraev(feed._antal_med_pris() == 1, "og tallet er rigtigt")


def test_marked_lukket_er_ikke_et_doedt_feed():
    """⚠ DEN FARLIGSTE FALSKE POSITIV, og den blev fundet af falsifikationen.

    Uden for handelstid er `ticker.last` tom (NaN) mens `close` staar fra sidste
    session. Maalte opstartskontrollen paa `last` alene, ville ETHVERT feed se
    doedt ud hver nat og hver weekend — og maskiner MED abonnement ville
    overdrage til kursproxyen uden grund.

    Det er derfor `_pris()` er ÉN funktion delt af kontrollen og loekken. Denne
    proeve er det eneste sted forskellen kan ses: fjernes delingen, bliver den
    roed, og det goer ingen af de andre.
    """
    print("\n[3b] Marked lukket — kun close")
    overdraget = []

    async def paa_doedt(hvorfor, syms):
        overdraget.append(hvorfor)
        return True

    feed, _ = byg({"AAPL": FakeTicker(close=230.0),      # last = NaN
                   "TSLA": FakeTicker(close=245.0)}, paa_doedt_feed=paa_doedt)
    gem_u, gem_t = lf.FALLBACK_UNIVERSE, lf.OPSTART_TAALMODIGHED_SEC
    lf.FALLBACK_UNIVERSE = ["AAPL", "TSLA"]
    lf.OPSTART_TAALMODIGHED_SEC = 2.0
    try:
        asyncio.run(_koer_start(feed, sekunder=3.0))
    finally:
        lf.FALLBACK_UNIVERSE, lf.OPSTART_TAALMODIGHED_SEC = gem_u, gem_t
    kraev(not overdraget, "kun close (marked lukket) -> overdrager IKKE")
    kraev(feed._antal_med_pris() == 2, "begge taelles som havende en pris")
    kraev(feed._running is True, "feedet starter normalt om natten")


def test_doedt_feed_overdrager():
    """⚠ KENDT-NEGATIV: 19-08 genspillet. Nul priser -> overdrag."""
    print("\n[4] Doedt feed (Ibens maskine, 19-08)")
    modtaget = {}

    async def paa_doedt(hvorfor, syms):
        modtaget["hvorfor"] = hvorfor
        modtaget["syms"] = syms
        return True

    feed, bc = byg({"AAPL": FakeTicker(), "TSLA": FakeTicker(),
                    "MES": FakeTicker()}, paa_doedt_feed=paa_doedt)
    gem_u, gem_t = lf.FALLBACK_UNIVERSE, lf.OPSTART_TAALMODIGHED_SEC
    lf.FALLBACK_UNIVERSE = ["AAPL", "TSLA"]
    lf.OPSTART_TAALMODIGHED_SEC = 1.5          # testen skal ikke tage 20 sekunder
    try:
        asyncio.run(feed.add_symbols(["MES"]))     # watchlisten bad om MES
        t = asyncio.run(_koer_start(feed, sekunder=6.0))
    finally:
        lf.FALLBACK_UNIVERSE, lf.OPSTART_TAALMODIGHED_SEC = gem_u, gem_t

    kraev("hvorfor" in modtaget, "nul priser -> overdragelsen kaldes")
    kraev("0 priser" in modtaget.get("hvorfor", ""), "og begrundelsen siger hvorfor")
    # ⚠ Watchlistens symboler skal MED over. Fallback-universet maa IKKE — at
    # proxy'e 20 tickere ingen ser, ville koste et kald hvert 5. sekund.
    kraev(modtaget.get("syms") == ["MES"],
          "watchlist-symbolet foelger med, fallback-universet goer ikke")
    kraev(feed._running is False, "feedet gaar ikke i gang")
    kraev(feed.conn.ib.annulleret, "og abonnementerne bliver nedlagt")
    kraev(not any(b.get("type") == "ticks" for b in bc.kaldt),
          "der sendes ingen ticks fra et doedt feed")


def test_ingen_at_overdrage_til_bliver_koerende():
    """⚠ KENDT-NEGATIV DEN ANDEN VEJ: at lukke feedet ned uden et alternativ
    ville vaere strengt vaerre end at lade det staa. Det er samme afvejning som
    salgsvagtens 'upaalideligt opslag afviser ikke'."""
    print("\n[5] Ingen at overdrage til")

    async def paa_doedt(hvorfor, syms):
        return False                    # main.py: algoserveren har intet alternativ

    feed, _ = byg({"AAPL": FakeTicker(), "TSLA": FakeTicker()},
                  paa_doedt_feed=paa_doedt)
    gem_u, gem_t = lf.FALLBACK_UNIVERSE, lf.OPSTART_TAALMODIGHED_SEC
    lf.FALLBACK_UNIVERSE = ["AAPL", "TSLA"]
    lf.OPSTART_TAALMODIGHED_SEC = 1.0
    try:
        asyncio.run(_koer_start(feed, sekunder=4.0))
    finally:
        lf.FALLBACK_UNIVERSE, lf.OPSTART_TAALMODIGHED_SEC = gem_u, gem_t

    kraev(feed._running is True, "feedet BLIVER koerende naar ingen kan overtage")
    kraev(not feed.conn.ib.annulleret, "og abonnementerne rives IKKE ned")


def test_uden_callback_er_bagudkompatibelt():
    """Et feed uden overdragelses-krog maa opfoere sig som foer."""
    print("\n[6] Uden callback")
    feed, _ = byg({"AAPL": FakeTicker()})
    gem_u, gem_t = lf.FALLBACK_UNIVERSE, lf.OPSTART_TAALMODIGHED_SEC
    lf.FALLBACK_UNIVERSE = ["AAPL"]
    lf.OPSTART_TAALMODIGHED_SEC = 1.0
    try:
        asyncio.run(_koer_start(feed, sekunder=4.0))
    finally:
        lf.FALLBACK_UNIVERSE, lf.OPSTART_TAALMODIGHED_SEC = gem_u, gem_t
    kraev(feed._running is True, "ingen callback -> feedet koerer videre som foer")


def test_taalmodighed():
    """En langsom TWS maa ikke forveksles med en tom. Prisen kommer efter et
    sekund, og feedet skal vente paa den — 2 sekunders fast sleep (som foer) var
    for kort til at skelne."""
    print("\n[7] Taalmodighed med en langsom TWS")
    overdraget = []

    async def paa_doedt(hvorfor, syms):
        overdraget.append(hvorfor)
        return True

    tk = FakeTicker()
    feed, _ = byg({"AAPL": tk}, paa_doedt_feed=paa_doedt)

    async def scenarie():
        async def forsinket_pris():
            await asyncio.sleep(1.0)
            tk.last = 230.0
        asyncio.create_task(forsinket_pris())
        await _koer_start(feed, sekunder=5.0)

    gem_u, gem_t = lf.FALLBACK_UNIVERSE, lf.OPSTART_TAALMODIGHED_SEC
    lf.FALLBACK_UNIVERSE = ["AAPL"]
    lf.OPSTART_TAALMODIGHED_SEC = 4.0
    try:
        asyncio.run(scenarie())
    finally:
        lf.FALLBACK_UNIVERSE, lf.OPSTART_TAALMODIGHED_SEC = gem_u, gem_t
    kraev(not overdraget, "pris efter 1 sekund -> ingen overdragelse")
    kraev(feed._running is True, "feedet starter normalt")


# ════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("test_feed_opstartskontrol — begge retninger proeves")
    for f in (test_pris_udtraek, test_feed_med_data_starter_normalt,
              test_delvis_daekning_er_hverdag,
              test_marked_lukket_er_ikke_et_doedt_feed, test_doedt_feed_overdrager,
              test_ingen_at_overdrage_til_bliver_koerende,
              test_uden_callback_er_bagudkompatibelt, test_taalmodighed):
        f()
    print("\n" + "=" * 70)
    if FEJL:
        print(f"{len(FEJL)} FEJL:")
        for x in FEJL:
            print(f"  · {x}")
        sys.exit(1)
    print("ALLE KONTROLLER BESTAAET")
    print("  · et feed MED data overdrager aldrig")
    print("  · et feed UDEN data overdrager — og tager watchlisten med")
    print("  · findes der ingen at overdrage til, bliver det koerende")
