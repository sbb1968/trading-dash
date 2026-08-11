"""
futures_katalog.py — ÉN sandhedskilde for hvilke futures vi handler
═══════════════════════════════════════════════════════════════════════════════════
Foer denne fil stod oplysningerne om et futures-symbol tre forskellige steder, og de
blev holdt i sync i haanden:

    ibkr_connect.FUTURES_EXCHANGE          er det en future, og paa hvilken boers
    src/App.tsx FUTURES_SYMBOLS            UI-gate + standardantal i ordrefeltet
    europa_reversion/config.MULTIPLIER     $ pr. prispoint, til P&L

Kommentaren i App.tsx sagde ligefrem "Tilfoejes et symbol dér, skal det ogsaa
tilfoejes her" — en instruks til et menneske, og dermed en fejl der venter.

Konsekvensen af at glemme ét sted er forskellig hver gang, og INGEN af dem raaber op:

  · glemt i FUTURES_EXCHANGE   -> symbolet handles som en AKTIE. Ordren afvises.
  · glemt i App.tsx            -> UI bruger aktiekalenderen. Blokeret uden for RTH,
                                  og ordrefeltet foreslaar 100 kontrakter.
  · glemt i MULTIPLIER         -> P&L regnes med 1,0 i stedet for fx 2,0. Journalen
                                  ser rigtig ud og er forkert med faktor to.

Den sidste er den vaerste: den opdages foerst naar kontoudtoget ikke stemmer.

TILFOEJ ET NYT FUTURES-SYMBOL HER, og kun her. `test_futures_katalog.py` fejler hvis
de oevrige lister ikke foelger med.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FuturesInstrument:
    symbol: str          # det RENE symbol — det er dét der skrives i watchlist
    exchange: str        # IBKR-boers, til kontrakt-kvalificering
    multiplier: float    # $ pr. prispoint
    navn: str
    tradingview: str     # TradingViews symbol — se noten nedenfor


# Multiplikatorerne er bekraeftet live mod reqPositions-avgCost (= pris x multiplier)
# OG mod IBKRs egen kontrakt-spec (MESU6 har multiplier='5').
#
# TradingView-symbolet SKAL vaere boers-kvalificeret. Widget'en fik tidligere det bare
# "MES", og TradingView vaelger da selv boers: den fandt GETTEX:MES (Mitsubishi Estate
# Company, japansk ejendom) foer CME. Charten viste et helt andet papir under titlen
# "MES · MICRO E-MINI S&P 500" — forkert paa den farlige maade, for den saa rigtig ud.
# "1!" er TradingViews kontinuerte front-maaned.
KATALOG: dict[str, FuturesInstrument] = {
    i.symbol: i for i in [
        FuturesInstrument("MES", "CME", 5.0, "Micro E-mini S&P 500",
                          "CME_MINI:MES1!"),
        FuturesInstrument("M2K", "CME", 5.0, "Micro E-mini Russell 2000",
                          "CME_MINI:M2K1!"),
        # ⚠ MULTIPLIER 2, IKKE 5. MNQ er den eneste af de tre mikroer med en anden
        # multiplikator, og forskellen er praecis den fejl filens indledning
        # advarer imod: P&L ville vaere regnet 2,5 gange for stort, journalen ville
        # se rigtig ud, og det ville foerst blive opdaget paa kontoudtoget.
        # Aflaest 11-08-2026 fra IBKR's egen kontrakt-spec (reqContractDetails,
        # front MNQU6): multiplier='2', minTick=0.25.
        FuturesInstrument("MNQ", "CME", 2.0, "Micro E-mini Nasdaq-100",
                          "CME_MINI:MNQ1!"),
    ]
}


def er_future(ticker: str) -> bool:
    return _norm(ticker) in KATALOG


def boers(ticker: str, standard: str = "CME") -> str:
    i = KATALOG.get(_norm(ticker))
    return i.exchange if i else standard


def multiplikator(ticker: str) -> float:
    """$ pr. prispoint. 1,0 for alt der ikke er en future i kataloget.

    ⚠ 1,0 er det RIGTIGE svar for en aktie, men et FARLIGT svar for en future der
    ikke staar i kataloget: P&L bliver stille forkert. Derfor findes
    `manglende_i_katalog()` og testen, saa et halvt tilfoejet symbol fanges.
    """
    i = KATALOG.get(_norm(ticker))
    return i.multiplier if i else 1.0


def symboler() -> list[str]:
    return sorted(KATALOG)


def _norm(ticker: str) -> str:
    return (ticker or "").upper().strip()


def manglende_i_katalog(symboler_der_bruges) -> list[str]:
    """Hvilke af disse symboler mangler i kataloget? Til opstartstjek og test."""
    return sorted(s for s in {_norm(x) for x in symboler_der_bruges} if s not in KATALOG)
