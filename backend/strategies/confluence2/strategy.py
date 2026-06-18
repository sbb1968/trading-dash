"""
strategies/confluence2/strategy.py
────────────────────────────────────
Konfluens 2 — impuls-strategi på 1-min bars.

Implementerer projektets Strategy/EntryEngine/ExitEngine-kontrakt (strategies.base),
så den kan køres af den eksisterende live-algo og backtest-engine uden ændringer
i dem — på samme måde som de øvrige OHLC-strategier.

PRINCIP:
  Entry  = 2 obligatoriske impuls-kriterier (volumen-spike + range/stærk-grøn)
           OG mindst context_threshold af 4 kontekst-filtre.
  Exit   = valgt arketype (impulse_low / trail_hl / momentum / target_r),
           altid med session-luk som backstop.

Alle indikatorer pre-computes over hele bar-serien i build_session_context,
præcis som K1 — så live og backtest giver samme resultat, og vi UNDGÅR den
intrabar-fejl K1 led under (vi evaluerer kun FÆRDIGE bars; caller leverer dem).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time as dtime
from typing import Any, Optional

import pandas as pd

from strategies.base import Bar, EntrySignal, ExitDecision, Position
# Delte, validerede indikator-beregninger fra strategies.shared (ingen duplikering)
from strategies.shared.indicators import ema, rsi, atr, sma

from strategies.confluence2.config import (
    Confluence2VariantConfig,
    VARIANTS,
    LIVE_VARIANT_KEY,
    SESSION_START_HHMM,
    SESSION_END_HHMM,
)

SESSION_START = dtime(*SESSION_START_HHMM)
SESSION_END   = dtime(*SESSION_END_HHMM)


# ─────────────────────────────────────────────────────────────────
# Per-position state
# ─────────────────────────────────────────────────────────────────
@dataclass
class Confluence2ExitState:
    entry_price: float
    impulse_low: float            # impuls-candlens low = naturligt katastrofe-niveau
    risk_per_share: float         # R = entry − impulse_low
    target_price: Optional[float] # for target_r-mode
    trail_stop: float             # for trail_hl-mode (starter = impulse_low)
    variant_key: str


# ─────────────────────────────────────────────────────────────────
# Entry-engine
# ─────────────────────────────────────────────────────────────────
class Confluence2Entry:
    """Vurderer impuls-entry på færdige 1-min bars."""

    def __init__(self):
        self._ctx: Optional[dict] = None

    def reset_for_day(self, date, context: dict) -> None:
        self._ctx = context

    def load_session_context(self, context: dict) -> None:
        """Backtest-hjælper: indlæs pre-computet context (som K1)."""
        self._ctx = context

    def evaluate(self, ticker: str, bar: Bar, context: dict) -> dict:
        """
        Returnér diagnostik for denne bar: score, bricks, status, reason.
        Bricks: V=volumen-spike, B=krop/range, G=stærk grøn (obligatoriske)
                E=ikke-overudvidet, K=break, R=rsi-momentum, T=trend-kontekst
        """
        cfg: Confluence2VariantConfig = context["config"]
        ind: pd.DataFrame = context["ind_df"]

        try:
            row = ind.loc[bar.timestamp]
            if isinstance(row, pd.DataFrame):
                row = row.iloc[0]
        except KeyError:
            return {"status": "skip", "reason": "ingen indikator-række", "score": None}

        # ── Obligatoriske impuls-kriterier ──
        prev_vol = row.get("prev_vol")
        vol_ma   = row.get("vol_ma")
        body_ma  = row.get("body_ma")
        body     = abs(bar.close - bar.open)
        rng      = bar.high - bar.low
        close_pos = ((bar.close - bar.low) / rng) if rng > 0 else 0.0

        c_vol = (prev_vol and prev_vol > 0 and bar.volume >= cfg.vol_mult * prev_vol
                 and vol_ma and vol_ma > 0 and bar.volume >= cfg.vol_ma_mult * vol_ma)
        c_body = (body_ma and body_ma > 0 and body >= cfg.body_mult * body_ma)
        c_green = (bar.close > bar.open and close_pos >= cfg.close_pos_min)

        impulse_ok = bool(c_vol and c_body and c_green)

        # ── Kontekst-kriterier (mindst context_threshold) ──
        ema_fast = row.get("ema_fast")
        ema_slow = row.get("ema_slow")
        rsi_val  = row.get("rsi")
        atr_val  = row.get("atr")
        prev_high = row.get("prev_high")

        # E: ikke overudvidet (close ikke for langt over ema_fast)
        c_notext = (ema_fast is not None and not pd.isna(ema_fast) and atr_val
                    and not pd.isna(atr_val)
                    and bar.close <= ema_fast + cfg.overext_atr_mult * atr_val)
        # K: break af forrige bars high (et reelt, friskt break)
        c_break = (prev_high is not None and not pd.isna(prev_high)
                   and bar.high > prev_high)
        # R: RSI har momentum men er ikke udmattet
        c_rsi = (rsi_val is not None and not pd.isna(rsi_val)
                 and cfg.rsi_min <= rsi_val <= cfg.rsi_max)
        # T: bred trend ikke imod (close over langsommere EMA)
        c_trend = (ema_slow is not None and not pd.isna(ema_slow)
                   and bar.close > ema_slow)

        context_hits = sum([bool(c_notext), bool(c_break), bool(c_rsi), bool(c_trend)])

        bricks = "".join([
            "V" if c_vol else "·",
            "B" if c_body else "·",
            "G" if c_green else "·",
            "E" if c_notext else "·",
            "K" if c_break else "·",
            "R" if c_rsi else "·",
            "T" if c_trend else "·",
        ])

        if not impulse_ok:
            return {"status": "rejected", "reason": "ingen impuls",
                    "score": context_hits, "short_form": bricks,
                    "atr": atr_val, "close_pos": close_pos}

        if context_hits < cfg.context_threshold:
            return {"status": "rejected",
                    "reason": f"impuls OK men kun {context_hits}/{cfg.context_threshold} kontekst",
                    "score": context_hits, "short_form": bricks,
                    "atr": atr_val, "close_pos": close_pos}

        return {"status": "signal", "reason": "impuls + kontekst opfyldt",
                "score": context_hits, "short_form": bricks,
                "atr": atr_val, "close_pos": close_pos}

    def check_entry(self, ticker: str, bar: Bar, context: dict,
                    evaluation: Optional[dict] = None) -> Optional[EntrySignal]:
        cfg: Confluence2VariantConfig = context["config"]

        # Rammevilkår: entry-cutoff
        cutoff = dtime(*cfg.entry_cutoff_hhmm)
        if bar.time_et >= cutoff:
            return None

        ev = evaluation or self.evaluate(ticker, bar, context)
        if ev.get("status") != "signal":
            return None

        # min-R-gate (deep-dive 2026-06-12): spring setups over hvor impuls-low
        # ligger for tæt på entry — de skrabes ellers af ren støj. R = entry −
        # impuls-low = bar.close − bar.low. Default min_r_pct=0.0 → ingen effekt.
        if cfg.min_r_pct > 0.0 and bar.close > 0:
            if (bar.close - bar.low) / bar.close < cfg.min_r_pct:
                return None

        return EntrySignal(
            ticker=ticker,
            entry_price=bar.close,          # færdig bars close — IKKE intrabar
            entry_time=bar.timestamp,
            side="long",
            metadata={
                "impulse_low": bar.low,
                "impulse_high": bar.high,
                "impulse_close": bar.close,
                "atr": ev.get("atr"),       # til ATR-gulv i open_position
                "score": ev.get("score"),
                "bricks": ev.get("short_form"),
            },
        )


# ─────────────────────────────────────────────────────────────────
# Exit-engine
# ─────────────────────────────────────────────────────────────────
class Confluence2Exit:
    """Fire exit-arketyper, valgt via variant.exit_mode."""

    def __init__(self):
        self._ctx: Optional[dict] = None

    def load_session_context(self, context: dict) -> None:
        self._ctx = context

    def open_position(self, signal: EntrySignal, shares: int,
                      variant_key: str) -> Position:
        cfg = VARIANTS[variant_key]
        entry = signal.entry_price
        impulse_low = float(signal.metadata.get("impulse_low", entry))

        # ATR-gulv (deep-dive 2026-06-12): udvid et for tæt stop, så det aldrig er
        # tættere på entry end stop_atr_floor_mult × ATR. For en long er et LAVERE
        # stop bredere → vi tager det laveste (videste) af de to. Default 0.0 → FRA.
        if cfg.stop_atr_floor_mult > 0.0:
            atr = signal.metadata.get("atr")
            if atr and atr > 0:
                impulse_low = min(impulse_low, entry - cfg.stop_atr_floor_mult * atr)

        R = max(entry - impulse_low, 0.0)
        target = (entry + cfg.target_r * R) if (cfg.exit_mode == "target_r" and R > 0) else None

        state = Confluence2ExitState(
            entry_price=entry,
            impulse_low=impulse_low,
            risk_per_share=R,
            target_price=target,
            trail_stop=impulse_low,
            variant_key=variant_key,
        )
        return Position(
            ticker=signal.ticker, entry_price=entry, entry_time=signal.entry_time,
            shares=shares, state=state, side="long", metadata=dict(signal.metadata),
        )

    def update(self, position: Position, high_seen: float, variant_key: str,
               low_seen: Optional[float] = None) -> None:
        """For trail_hl: løft trail-stop til nye higher lows.
        For breakeven_r: løft stop til entry når high ≥ entry + breakeven_r × R."""
        cfg = VARIANTS[variant_key]
        st: Confluence2ExitState = position.state
        if cfg.exit_mode == "trail_hl" and low_seen is not None:
            if low_seen > st.trail_stop:
                st.trail_stop = low_seen
        be_r = getattr(cfg, "breakeven_r", None)
        if be_r and st.risk_per_share > 0:
            if high_seen >= st.entry_price + be_r * st.risk_per_share:
                if st.entry_price > st.trail_stop:
                    st.trail_stop = st.entry_price

    def check_exit_bar(self, position: Position, bar: Bar, variant_key: str,
                       indicator_row: Optional[Any] = None) -> Optional[ExitDecision]:
        cfg = VARIANTS[variant_key]
        st: Confluence2ExitState = position.state
        entry = st.entry_price

        # ── Lag 0: session-luk (backstop, matcher K1/ORB 15:45) ──
        if bar.time_et >= dtime(*cfg.force_close_hhmm):
            return ExitDecision(exit_price=bar.close, reason="session_close")

        mode = cfg.exit_mode

        if mode == "impulse_low":
            # trail_stop starter = impulse_low; breakeven_r kan have løftet den til entry
            if bar.low <= st.trail_stop:
                reason = "breakeven" if st.trail_stop > st.impulse_low else "stop"
                return ExitDecision(exit_price=st.trail_stop, reason=reason)
            return None

        if mode == "trail_hl":
            if bar.low <= st.trail_stop:
                reason = "trail" if st.trail_stop > st.impulse_low else "stop"
                return ExitDecision(exit_price=st.trail_stop, reason=reason)
            return None

        if mode == "momentum":
            # katastrofe-stop først
            if cfg.catastrophe_stop and bar.low <= st.impulse_low:
                return ExitDecision(exit_price=st.impulse_low, reason="stop")
            # rød volumen-candle eller close under EMA fast
            ema_fast = None
            if indicator_row is not None:
                ema_fast = indicator_row.get("ema_fast") if hasattr(indicator_row, "get") else None
            prev_close_below = (ema_fast is not None and not pd.isna(ema_fast)
                                and bar.close < ema_fast)
            red = (bar.close < bar.open)
            if prev_close_below or red:
                return ExitDecision(exit_price=bar.close, reason="signal_exit")
            return None

        if mode == "target_r":
            # katastrofe-stop
            if cfg.catastrophe_stop and bar.low <= st.impulse_low:
                return ExitDecision(exit_price=st.impulse_low, reason="stop")
            # target
            if st.target_price is not None and bar.high >= st.target_price:
                return ExitDecision(exit_price=st.target_price, reason="target")
            return None

        return None

    def check_exit_live(self, position: Position, current_price: float,
                        current_time_et: dtime, variant_key: str) -> Optional[ExitDecision]:
        """Live-sti: konservativ — kun pris/tid, ingen intrabar candle-mønstre."""
        cfg = VARIANTS[variant_key]
        st: Confluence2ExitState = position.state
        if current_time_et >= dtime(*cfg.force_close_hhmm):
            return ExitDecision(exit_price=current_price, reason="session_close")
        if current_price <= st.impulse_low:
            return ExitDecision(exit_price=st.impulse_low, reason="stop")
        if st.target_price is not None and current_price >= st.target_price:
            return ExitDecision(exit_price=st.target_price, reason="target")
        return None


# ─────────────────────────────────────────────────────────────────
# Strategi-facade
# ─────────────────────────────────────────────────────────────────
class Confluence2Strategy:
    def __init__(self):
        self._entry = Confluence2Entry()
        self._exit = Confluence2Exit()

    @property
    def name(self) -> str:
        return "Konfluens 2"

    @property
    def description(self) -> str:
        return "Impuls-strategi på 1-min: volumen-spike + range + stærk grøn, plads i exit."

    @property
    def variants(self) -> dict[str, Any]:
        return VARIANTS

    @property
    def live_variant_key(self) -> str:
        return LIVE_VARIANT_KEY

    @property
    def entry(self) -> Confluence2Entry:
        return self._entry

    @property
    def exit(self) -> Confluence2Exit:
        return self._exit

    def build_session_context(self, ticker: str, bars: list[Bar],
                              config: Optional[Confluence2VariantConfig] = None
                              ) -> Optional[dict]:
        """
        Pre-compute alle indikatorer over HELE bar-serien (warmup + target),
        præcis som K1, så live == backtest. Returnér None hvis for få bars.
        """
        cfg = config or VARIANTS[LIVE_VARIANT_KEY]
        if not bars or len(bars) < cfg.min_warmup_bars:
            return None

        df = pd.DataFrame({
            "open":   [b.open for b in bars],
            "high":   [b.high for b in bars],
            "low":    [b.low for b in bars],
            "close":  [b.close for b in bars],
            "volume": [b.volume for b in bars],
        }, index=[b.timestamp for b in bars])

        body = (df["close"] - df["open"]).abs()

        ind = pd.DataFrame(index=df.index)
        ind["prev_vol"]  = df["volume"].shift(1)
        ind["vol_ma"]    = df["volume"].rolling(cfg.vol_ma_len, min_periods=1).mean().shift(1)
        ind["body_ma"]   = body.rolling(cfg.body_ma_len, min_periods=1).mean().shift(1)
        ind["prev_high"] = df["high"].shift(1)
        ind["ema_fast"]  = ema(df["close"], cfg.ema_fast_len)
        ind["ema_slow"]  = ema(df["close"], cfg.ema_slow_len)
        ind["rsi"]       = rsi(df["close"], cfg.rsi_len)
        ind["atr"]       = atr(df["high"], df["low"], df["close"], cfg.atr_len)

        return {"ticker": ticker, "config": cfg, "ind_df": ind, "bars": bars}

    # Alias for kompatibilitet med kode der kalder build_day_context
    build_day_context = build_session_context