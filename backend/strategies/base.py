"""
strategies/base.py
──────────────────
Kontrakter (Protocols) og fælles datatyper for alle trading-strategier.

En strategi består af tre dele:
  1. config.py     — variant-konfigurationer (parameter-sweep dimensioner)
  2. entry.py      — EntryEngine: state-machine der returnerer EntrySignal
  3. exit.py       — ExitEngine: position-state + exit-detektion

Strategien selv (strategy.py) er en tynd klasse der samler disse tre dele
og overholder Strategy-protokollen.

Hvorfor Protocols og ikke abstrakte klasser?
  - Duck typing: alt der har de rigtige metoder fungerer som strategi
  - Ingen tvang om at arve fra én base-klasse
  - Test-mocks bliver trivielle (kun protokollens metoder skal implementeres)
  - Backtest-engine kan tage enhver strategi uden at kende dens type
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time as dtime
from typing import Protocol, Optional, Any


# ─────────────────────────────────────────────────────────────────────
# Fælles dataklasser — bruges af alle strategier
# ─────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Bar:
    """
    Én OHLCV-bar med ET-tidsstempel.

    Bruges identisk af både live (én bar ad gangen fra IBKR) og backtest
    (iterer gennem alle bars fra CSV).
    """
    timestamp: datetime    # tz-aware (America/New_York)
    open:   float
    high:   float
    low:    float
    close:  float
    volume: float

    @property
    def time_et(self) -> dtime:
        """Tid på dagen i ET — bruges af exit-logik (force-close kl. 10:30)."""
        return self.timestamp.time()

    @property
    def date(self):
        """Dato — bruges til at gruppere bars pr. handelsdag."""
        return self.timestamp.date()


@dataclass
class EntrySignal:
    """
    Resultatet af en entry-detektion.

    Returneres af EntryEngine.check_entry() når strategien vil åbne en position.

    side:     "long" (køb) eller "short" (sælg-først). Default "long" så
              eksisterende strategier ikke skal opdateres.
    metadata: strategi-specifikke felter der skal følge positionen indtil exit.
              En strategi lægger fx sine niveauer (high/low/retest) her.
              Mean reversion ville lægge mean_price, std_dev osv. her.
    """
    ticker: str
    entry_price: float
    entry_time: datetime
    side: str = "long"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExitDecision:
    """
    Resultatet af en exit-detektion.

    reason: en fri tekststreng (fx "stop", "target", "trail", "force_close").
            Forskellige strategier kan have forskellige årsager — backtesten
            tæller dem automatisk i sin sammenligningstabel.
    """
    exit_price: float
    reason: str


@dataclass
class Position:
    """
    En åben position — opdateres af ExitEngine på hver pris-observation.

    state: strategi-specifik state (fx en PositionState med exit-tilstand)
           med stage, stop, target, highest_high. For andre strategier kan det
           være hvad de har brug for. ExitEngine ejer den, ingen andre rører
           den.
    """
    ticker: str
    entry_price: float
    entry_time: datetime
    shares: int
    state: Any                          # strategi-specifik state
    side: str = "long"                  # "long" eller "short"
    metadata: dict[str, Any] = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────
# Protocols — hvert strategi-modul implementerer disse
# ─────────────────────────────────────────────────────────────────────

class EntryEngine(Protocol):
    """
    En entry-engine vurderer om en ticker skal åbnes som position.

    Den ejer sin egen interne state-machine (waiting → breakout → retest osv.)
    Caller skal blot:
      1. Kalde reset_for_day(date, context) ved hver ny handelsdag
      2. Kalde check_entry(ticker, bar, context) på hver ny bar
      3. Hvis return-værdi ikke er None → åbn position med signalet
    """

    def reset_for_day(self, date, context: dict) -> None:
        """
        Kaldes én gang pr. ticker pr. dag (eller ved start på live algo).

        context: alt strategien behøver vide om dagen — fx ORB-niveauer,
                 gennemsnitsvolumen, dagsforhold. Beregnes udenfor entry-engine
                 og leveres som dict for at undgå tight coupling.
        """
        ...

    def check_entry(
        self,
        ticker: str,
        bar: Bar,
        context: dict,
    ) -> Optional[EntrySignal]:
        """
        Returnér et EntrySignal hvis denne bar trigger entry, ellers None.

        OBS: caller står for at filtrere bars væk udenfor handelsvinduet.
        Entry-engine fokuserer kun på at vurdere signalet.
        """
        ...


class ExitEngine(Protocol):
    """
    En exit-engine ejer alt om åbne positioner og deres exit-betingelser.

    Den får én pris ad gangen (live: snapshot) eller én bar ad gangen (backtest)
    og afgør om en exit skal udløses.
    """

    def open_position(
        self,
        signal: EntrySignal,
        shares: int,
        variant_key: str,
    ) -> Position:
        """
        Opret en ny Position med initial stop/target baseret på signal og variant.

        Returnerer en Position som caller gemmer i sit position-register.
        """
        ...

    def update(
        self,
        position: Position,
        high_seen: float,
        variant_key: str,
    ) -> None:
        """
        Opdatér position-state med den højeste pris vi har set siden sidst.

        I live er high_seen typisk snapshot-prisen.
        I backtest er det bar_high.

        Skal altid kaldes FØR check_exit_live / check_exit_bar.
        """
        ...

    def check_exit_live(
        self,
        position: Position,
        current_price: float,
        current_time_et: dtime,
        variant_key: str,
    ) -> Optional[ExitDecision]:
        """Tjek exit i live (snapshot-pris). Returnér ExitDecision eller None."""
        ...

    def check_exit_bar(
        self,
        position: Position,
        bar: Bar,
        variant_key: str,
    ) -> Optional[ExitDecision]:
        """Tjek exit i backtest (OHLC-bar). Returnér ExitDecision eller None."""
        ...


class Strategy(Protocol):
    """
    En komplet strategi: navn, beskrivelse, varianter, entry, exit.

    Live-algo'en og backtest-engine'en bruger denne protokol til at handle
    strategi-agnostisk: de kalder bare strategy.entry.check_entry(...) og
    strategy.exit.check_exit_bar(...) uden at vide hvad strategien faktisk er.
    """

    @property
    def name(self) -> str:
        """Læsbart strategi-navn — vises i UI."""
        ...

    @property
    def description(self) -> str:
        """Kort one-liner — vises i AlgoHub-kort."""
        ...

    @property
    def variants(self) -> dict[str, Any]:
        """
        Map fra variant-nøgle (fx "A", "baseline") til VariantConfig.
        Bruges af backtest-engine til at iterere parameter-sweep.
        """
        ...

    @property
    def live_variant_key(self) -> str:
        """Hvilken variant kører live? Default-værdi som backend bruger."""
        ...

    @property
    def entry(self) -> EntryEngine:
        """Entry-engine instans (typisk én pr. strategi-instans)."""
        ...

    @property
    def exit(self) -> ExitEngine:
        """Exit-engine instans."""
        ...

    def build_day_context(
        self,
        ticker: str,
        bars: list[Bar],
        config: Optional[Any] = None,
    ) -> Optional[dict]:
        """
        Bygger den 'kontekst' entry-engine skal bruge på dagen — fx ORB-niveauer,
        gennemsnitsvolumen. Returnerer None hvis dagen skal springes over
        (fx for få bars, manglende data).

        bars:   alle bars for dagen for denne ticker, sorteret kronologisk.
        config: optional variant-config. Hvis givet, kan strategien bruge
                den til at justere ORB-vindue, vol_mult osv. pr. variant.
        """
        ...
