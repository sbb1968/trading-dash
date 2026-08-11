"""
test_krypto_kurs.py — crypto må prissættes, men ikke handles
════════════════════════════════════════════════════════════════════════════════
`BINANCE:LINKUSDT` skal kunne stå i watchlisten med en kurs. Den må **ikke**
kunne handles gennem Trading Dash: IBKR handler ikke Binance-spot, og crypto
lægges manuelt (spec §11).

De to krav trækker hver sin vej, og det er dér fejl opstår — et symbol der kan
prissættes, ser handlebart ud. Testen holder dem adskilt.

⚠ Nogle tests rammer TradingView over nettet. De springes over hvis der ikke er
svar, frem for at fejle: en rød test der kun betyder "ingen internetforbindelse",
lærer man at ignorere.
"""
from __future__ import annotations

import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import krypto_kurs as kk


# ── genkendelse ─────────────────────────────────────────────────────────────
def test_krypto_genkendes():
    for t in ["BINANCE:LINKUSDT", "binance:linkusdt", "  BINANCE:BTCUSDT  ",
              "CRYPTOCAP:BTC.D", "COINBASE:ETHUSD", "BINANCE:LINKUSDT.P"]:
        assert kk.er_krypto(t), t


def test_aktier_og_futures_rammes_ikke():
    """⚠ Kernen i afgrænsningen.

    Reglen er en hvidliste over børser, ikke "indeholder et kolon". `BATS:AAPL`
    har også et kolon — og en regel der matcher bredere end sin påstand, svarer
    på noget andet end den siger.
    """
    for t in ["MES", "AAPL", "M2K", "SPY", "BATS:AAPL", "NASDAQ:TSLA",
              "NYSE:GME", "", "   "]:
        assert not kk.er_krypto(t), t


def test_endpointet_vaelges_efter_praefiks():
    """Børs-par og beregnede mål bor ikke samme sted.

    ⚠ Målt 11-08: /crypto/scan returnerede NUL rækker for CRYPTOCAP:BTC.D. Stod
    de på samme endpoint, ville de tavst mangle.
    """
    assert "BINANCE" in kk.BOERS_PAR and "BINANCE" not in kk.BEREGNEDE
    assert "CRYPTOCAP" in kk.BEREGNEDE
    assert kk.TV_ENDPOINT["krypto"].endswith("/crypto/scan")
    assert kk.TV_ENDPOINT["maal"].endswith("/global/scan")


# ── hentning (netværk) ──────────────────────────────────────────────────────
def test_henter_link_og_dominans():
    d = asyncio.run(kk.hent(["BINANCE:LINKUSDT", "CRYPTOCAP:BTC.D"]))
    if not d:
        print("      (sprunget over — TradingView svarede ikke)")
        return
    assert "BINANCE:LINKUSDT" in d, d.keys()
    link = d["BINANCE:LINKUSDT"]
    assert link["price"] > 0
    assert "ChainLink" in (link["navn"] or "")
    # Begge endpoints skal have svaret i samme kald.
    assert "CRYPTOCAP:BTC.D" in d, "det beregnede maal kom ikke med — kun ét endpoint kaldt?"
    assert 0 < d["CRYPTOCAP:BTC.D"]["price"] < 100, "dominans er en procent"


def test_ukendt_symbol_udelades_frem_for_at_blive_nul():
    """⚠ En kurs vi ikke har, er ikke nul.

    Samme regel som mock-feedet brød: et 0 står i grænsefladen som en påstand,
    ikke som et hul.
    """
    d = asyncio.run(kk.hent(["BINANCE:FINDESHELTSIKKERTIKKEUSDT"]))
    assert d == {}, d


def test_ikke_krypto_filtreres_fra_foer_kaldet():
    d = asyncio.run(kk.hent(["AAPL", "MES"]))
    assert d == {}, d


if __name__ == "__main__":
    for navn, fn in sorted(globals().items()):
        if navn.startswith("test_"):
            fn()
            print(f"  OK  {navn}")
    print("\nAlle bestod.")
