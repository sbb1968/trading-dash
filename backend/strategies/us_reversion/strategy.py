"""
strategies/us_reversion/strategy.py
────────────────────────────────────
Facade for US-reversion — samler regel + config under ét strategi-objekt, så
den er registrerbar på linje med de øvrige strategier.

Samme strukturelle valg som Europa-reversion: reglen er en (næsten) ren
funktion frem for stateful entry/exit-ENGINES, fordi beslutningerne er
matematik på et rullende vindue snarere end en state-maskine. Den tilstand der
FINDES — armering og HH — holdes af live-wrapperen og backtesten hver for sig,
men beregnes med de samme rene funktioner fra rule.py.

Placering: C:\\Projects\\trading_dash\\backend\\strategies\\us_reversion\\strategy.py
"""

from __future__ import annotations

from typing import Any

from strategies.us_reversion import config
from strategies.us_reversion import rule


class UsReversionStrategy:
    """Tynd facade: navn/beskrivelse + adgang til den delte regel og config."""

    # Modul-referencer, så kaldere kan nå sandhedskilden gennem strategien.
    config = config
    rule = rule

    def __init__(self, variant_key: str = config.LIVE_VARIANT_KEY):
        if variant_key not in config.VARIANTS:
            raise ValueError(
                f"Ukendt variant '{variant_key}'. Gyldige: {sorted(config.VARIANTS)}")
        self._variant_key = variant_key

    @property
    def name(self) -> str:
        return "US-reversion"

    @property
    def description(self) -> str:
        return ("Long-only mean-reversion på MES i den amerikanske session "
                "(09:30–15:00 ET). 15m-bånd armerer, 5m-reversal bekræfter.")

    @property
    def asset_class(self) -> str:
        return "futures"

    @property
    def variants(self) -> dict[str, Any]:
        return config.VARIANTS

    @property
    def live_variant_key(self) -> str:
        return self._variant_key

    @property
    def cfg(self) -> config.UsReversionVariantConfig:
        return config.VARIANTS[self._variant_key]

    # ── Regel-passthroughs (samme matematik overalt) ──────────
    @staticmethod
    def compute_z(closes):
        return rule.compute_z(closes)

    def bands(self, closes):
        return rule.bands(closes, self.cfg.entry_z)

    # `retning` foeres igennem her, saa wrapperen kun skal kende sin egen
    # armerings-tilstand og ikke gentage baand-matematikken. Default LONG holder
    # alle eksisterende kald (backtest, tests) uaendrede.
    def is_break(self, z, retning=rule.LONG):
        """Armerer denne retning paa dette z? long: brud ned. short: brud op."""
        return rule.is_break(z, self.cfg.entry_z, retning)

    def is_break_below(self, z):
        """Long-brud. Bevares — backtesten kalder den."""
        return rule.is_break(z, self.cfg.entry_z, rule.LONG)

    def is_back_inside(self, z, retning=rule.LONG):
        return rule.is_back_inside(z, self.cfg.entry_z, retning)

    def check_entry(self, bars5, macd_now, macd_prev, cmf_now, cmf_prev,
                    retning=rule.LONG):
        return rule.check_entry(bars5, macd_now, macd_prev,
                                cmf_now, cmf_prev, self.cfg, retning)

    def check_exit(self, entry_price, ekstrem_close, last_close, z,
                   retning=rule.LONG):
        return rule.check_exit(entry_price, ekstrem_close, last_close, z,
                               self.cfg, retning)

    def stop_price(self, entry_price, retning=rule.LONG):
        return rule.stop_price(entry_price, self.cfg, retning)

    def update_ekstrem(self, ekstrem_close, last_close, retning=rule.LONG):
        return rule.update_ekstrem(ekstrem_close, last_close, retning)
