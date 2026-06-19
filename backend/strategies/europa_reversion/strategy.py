"""
strategies/europa_reversion/strategy.py
───────────────────────────────────────
Facade for Europa-reversion — samler regel + config under ét strategi-objekt,
så den er registrerbar i STRATEGY_REGISTRY på linje med de øvrige strategier.

BEMÆRK — bevidst strukturel forskel fra de OHLC-engine-baserede strategier:
  De har stateful entry/exit-ENGINES (state-maskiner, pris-stops). Europa-
  reversions regel er derimod en ren funktion af et rullende z-score-vindue
  (rule.py), og dens validerede backtest er den fritstående eureversion_backtest.py
  (15-min futures fra data_harvest/). Derfor eksponerer denne facade reglen +
  config i stedet for OHLC-engines — men reglen er DELT med live-wrapperen, så
  live og backtest aldrig divergerer.
"""

from __future__ import annotations

from strategies.europa_reversion import config
from strategies.europa_reversion import rule


class EuropaReversionStrategy:
    """Tynd facade: navn/beskrivelse + adgang til den delte regel og config."""

    # Modul-referencer, så kaldere kan nå den delte sandhedskilde via strategien.
    config = config
    rule = rule

    @property
    def name(self) -> str:
        return "Europa-reversion"

    @property
    def description(self) -> str:
        return ("Mean-reversion på index-micro futures (MES/M2K) i den "
                "europæiske session (02–08 ET)")

    @property
    def asset_class(self) -> str:
        return "futures"

    # ── Regel-passthroughs (samme matematik overalt) ──────────
    @staticmethod
    def compute_z(closes):
        return rule.compute_z(closes)

    @staticmethod
    def entry_side(z):
        return rule.entry_side(z, config.ENTRY_Z)

    @staticmethod
    def exit_reason(side, z):
        return rule.exit_reason(side, z, config.EXIT_Z, config.STOP_Z)

    @staticmethod
    def stop_distance(std):
        return rule.stop_distance(std, config.ENTRY_Z, config.STOP_Z)
