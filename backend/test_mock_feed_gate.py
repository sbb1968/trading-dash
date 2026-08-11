"""
test_mock_feed_gate.py — opdigtede priser kun når nogen har bedt om dem
════════════════════════════════════════════════════════════════════════════════
Målt 11-08-2026 på Ibens workstation. Backenden startede uden lokal
IBKR-forbindelse og skrev:

    [LiveFeed] Ingen IBKR-forbindelse — bruger mock data
    [MockFeed] Starter (IBKR ikke tilgængelig)

⚠ `mock_data` digter kurser for **rigtige, handlebare tickere**: AAPL 189.50,
TSLA 245.30, NVDA 875.20, GME 18.40. Intet i grænsefladen markerer dem.

Prisen når ikke selve ordren — `ibkrBuy` lægger en markedsordre. Men den står i
**bekræftelsesdialogen**, altså præcis dér hvor mennesket beslutter. Viser den
AAPL 189.50 mens AAPL koster 230, godkendes et køb 21 % dyrere end vist.

⚠ Og tavshed er ikke et tab på sådan en maskine: watchlisten falder tilbage til
`/quote`, som henter ægte kurser fra algoserveren (App.tsx:440-447). **En tom
pris udløser den vej; en opdigtet pris blokerer den.** Fabrikationen kostede os
altså den rigtige kilde.

Testen viser porten både LUKKE og ÅBNE. En port der kun er set lukke, kunne
lukke alt.
"""
from __future__ import annotations

import dataclasses
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import accounts
import main


class _Optaeller:
    """Stub for asyncio-modulet i main — tæller create_task uden at køre noget."""

    def __init__(self):
        self.startede: list[str] = []

    def create_task(self, coro):
        self.startede.append(getattr(coro, "__name__", "?"))
        coro.close()          # ingen "never awaited"-advarsel
        return None


def _koer(mock_feed: bool) -> list[str]:
    aegte_asyncio, aegte_id = main.asyncio, accounts.identity
    taeller = _Optaeller()
    try:
        main.asyncio = taeller
        accounts.identity = dataclasses.replace(aegte_id, mock_feed=mock_feed)
        main._uden_feed("test")
        return taeller.startede
    finally:
        main.asyncio, accounts.identity = aegte_asyncio, aegte_id


def test_uden_flag_digtes_der_ikke():
    """⚠ Kernen. Default må aldrig fabrikere."""
    assert _koer(False) == [], (
        "mock_data_loop blev startet uden at nogen bad om det — watchlisten "
        "ville vise opdigtede priser for rigtige tickere, og bekræftelses-"
        "dialogen ville bygge på dem")


def test_med_flag_digtes_der():
    """Porten må ikke bare lukke alt."""
    assert "mock_data_loop" in _koer(True), (
        "instance.mock_feed: true blev sat, men mock startede ikke")


def test_kun_ét_sted_kan_starte_mock():
    """⚠ En port hjælper ikke hvis der findes en vej udenom.

    Før rettelsen stod `asyncio.create_task(mock_data_loop())` to steder i
    start_ibkr_feed — én for "ingen forbindelse" og én i except-grenen. Havde vi
    kun lukket den ene, ville en undtagelse under opstart stadig have fabrikeret.
    """
    kilde = (pathlib.Path(__file__).parent / "main.py").read_text(encoding="utf-8")
    kald = kilde.count("create_task(mock_data_loop())")
    assert kald == 1, (
        f"mock_data_loop startes {kald} steder — porten i _uden_feed daekker "
        f"kun ét af dem")


def test_standard_er_slukket():
    """Feltet skal defaulte til False, ikke arve fra en tilfældig maskine."""
    felt = {f.name: f for f in dataclasses.fields(accounts.AccountIdentity)}["mock_feed"]
    assert felt.default is False, felt.default


if __name__ == "__main__":
    for navn, fn in sorted(globals().items()):
        if navn.startswith("test_"):
            fn()
            print(f"  OK  {navn}")
    print("\nAlle bestod.")
