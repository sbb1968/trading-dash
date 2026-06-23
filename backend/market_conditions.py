"""
market_conditions.py
────────────────────
Analyserer markedsforhold ved handelsdagens start.
Bruges som del af pre-flight tjek i alle strategier.

Placering: C:\\Projects\\trading-dash\\backend\\market_conditions.py

Aktivitetsscore 0-100:
  > 60  → Aktiv dag    — normal handel
  40-60 → Moderat dag  — reduceret position size (50%)
  < 40  → Rolig dag    — ingen handel
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import pytz

logger = logging.getLogger(__name__)
ET = pytz.timezone("America/New_York")


# ---------------------------------------------------------------------------
# Data-klasse
# ---------------------------------------------------------------------------

@dataclass
class MarketConditions:
    vix:                  float = 0.0
    vix_status:           str   = "ukendt"
    spy_gap_pct:          float = 0.0
    spy_gap_status:       str   = "ukendt"
    spy_price:            float = 0.0
    spy_intraday_pct:     float = 0.0
    spy_intraday_status:  str   = "ukendt"
    spy_vwap:             float = 0.0
    spy_above_vwap:       bool | None = None
    iwm_price:            float = 0.0
    iwm_gap_pct:          float = 0.0
    iwm_gap_status:       str   = "ukendt"
    iwm_intraday_pct:     float = 0.0
    iwm_intraday_status:  str   = "ukendt"
    iwm_vwap:             float = 0.0
    iwm_above_vwap:       bool | None = None
    stocks_gap_over_10:   int   = 0
    stocks_relvol_over_5: int   = 0
    top_gainers:          list  = field(default_factory=list)
    score:                int   = 0
    score_label:          str   = "ukendt"
    position_size_pct:    float = 1.0
    skal_handle:          bool  = True
    checked_at:           str   = ""
    error:                str   = ""


# ---------------------------------------------------------------------------
# MarketConditionChecker
# ---------------------------------------------------------------------------

class MarketConditionChecker:

    def __init__(self, conn, journal=None):
        self.conn     = conn
        self._journal = journal

    async def check(self) -> MarketConditions:
        mc = MarketConditions(
            checked_at=datetime.now(ET).strftime("%H:%M:%S ET")
        )

        # Kør alle tjek — fejler aldrig
        await self._check_vix(mc)

        spy = await self._check_index("SPY")
        mc.spy_price, mc.spy_gap_pct, mc.spy_gap_status = spy["price"], spy["gap_pct"], spy["gap_status"]
        mc.spy_intraday_pct, mc.spy_intraday_status = spy["intraday_pct"], spy["intraday_status"]
        mc.spy_vwap, mc.spy_above_vwap = spy["vwap"], spy["above_vwap"]

        iwm = await self._check_index("IWM")
        mc.iwm_price, mc.iwm_gap_pct, mc.iwm_gap_status = iwm["price"], iwm["gap_pct"], iwm["gap_status"]
        mc.iwm_intraday_pct, mc.iwm_intraday_status = iwm["intraday_pct"], iwm["intraday_status"]
        mc.iwm_vwap, mc.iwm_above_vwap = iwm["vwap"], iwm["above_vwap"]

        await self._check_scanner(mc)
        self._calculate_score(mc)

        if self._journal:
            await self._journal.log_event(
                source     = "market_conditions",
                event_type = "market_conditions_snapshot",
                payload    = {
                    "vix":                  mc.vix,
                    "vix_status":           mc.vix_status,
                    "spy_price":            mc.spy_price,
                    "spy_gap_pct":          mc.spy_gap_pct,
                    "spy_gap_status":       mc.spy_gap_status,
                    "spy_intraday_pct":     mc.spy_intraday_pct,
                    "spy_above_vwap":       mc.spy_above_vwap,
                    "iwm_price":            mc.iwm_price,
                    "iwm_gap_pct":          mc.iwm_gap_pct,
                    "iwm_intraday_pct":     mc.iwm_intraday_pct,
                    "iwm_above_vwap":       mc.iwm_above_vwap,
                    "stocks_gap_over_10":   mc.stocks_gap_over_10,
                    "stocks_relvol_over_5": mc.stocks_relvol_over_5,
                    "score":                mc.score,
                    "score_label":          mc.score_label,
                    "position_size_pct":    mc.position_size_pct,
                    "skal_handle":          mc.skal_handle,
                    "checked_at_et":        mc.checked_at,
                    "error":                mc.error,
                },
            )

        return mc

    # -----------------------------------------------------------------------
    # VIX — via yfinance (ingen IBKR abonnement nødvendigt)
    # -----------------------------------------------------------------------

    async def _check_vix(self, mc: MarketConditions) -> None:
        try:
            import yfinance as yf
            loop = asyncio.get_event_loop()

            def fetch():
                try:
                    ticker = yf.Ticker("^VIX")
                    hist   = ticker.history(period="1d")
                    if not hist.empty:
                        return float(hist["Close"].iloc[-1])
                    info = ticker.fast_info
                    return float(info.last_price or 0)
                except Exception:
                    return 0.0

            mc.vix = await asyncio.wait_for(
                loop.run_in_executor(None, fetch),
                timeout=10.0
            )
            mc.vix = round(mc.vix, 2)

        except Exception as e:
            logger.warning(f"VIX fejl: {e}")
            mc.vix = 0.0

        # Status
        if mc.vix == 0:
            mc.vix_status = "ukendt"
        elif mc.vix < 15:
            mc.vix_status = "lav"
        elif mc.vix < 25:
            mc.vix_status = "normal"
        elif mc.vix < 40:
            mc.vix_status = "høj"
        else:
            mc.vix_status = "ekstrem"

        logger.info(f"VIX: {mc.vix} ({mc.vix_status})")

    # -----------------------------------------------------------------------
    # SPY — via IBKR historiske bars
    # -----------------------------------------------------------------------

    async def _check_index(self, symbol: str) -> dict:
        """Dagligt gap + live intradag-retning (vs open + VWAP) for eet indeks.
        IBKR foerst, yfinance-fallback for BAADE dagligt og intradag. Fejler aldrig
        kalderen — uden for RTH forbliver intradag-felter 0/ukendt/None."""
        out = {"price": 0.0, "gap_pct": 0.0, "gap_status": "ukendt",
               "intraday_pct": 0.0, "intraday_status": "ukendt",
               "vwap": 0.0, "above_vwap": None}

        from ib_async import Stock
        contract = Stock(symbol, "SMART", "USD")

        # 1) DAGLIGT gap (3 D / 1 day bars)
        try:
            await self.conn.ib.qualifyContractsAsync(contract)
            bars = await asyncio.wait_for(
                self.conn.ib.reqHistoricalDataAsync(
                    contract, endDateTime="", durationStr="3 D",
                    barSizeSetting="1 day", whatToShow="TRADES", useRTH=True, formatDate=1),
                timeout=15.0)
            if bars and len(bars) >= 2:
                prev_close     = bars[-2].close
                today_open     = bars[-1].open or bars[-1].close
                out["price"]   = round(bars[-1].close, 2)
                out["gap_pct"] = round((today_open - prev_close) / prev_close * 100, 2)
            elif bars:
                out["price"] = round(bars[-1].close, 2)
        except Exception as e:
            logger.warning(f"{symbol} IBKR dagligt fejl: {e} - proever yfinance")
            try:
                import yfinance as yf
                loop = asyncio.get_event_loop()

                def fetch_daily():
                    try:
                        hist = yf.Ticker(symbol).history(period="3d")
                        if len(hist) >= 2:
                            prev_close  = float(hist["Close"].iloc[-2])
                            today_open  = float(hist["Open"].iloc[-1])
                            today_close = float(hist["Close"].iloc[-1])
                            return today_close, round((today_open - prev_close) / prev_close * 100, 2)
                        return 0.0, 0.0
                    except Exception:
                        return 0.0, 0.0

                out["price"], out["gap_pct"] = await asyncio.wait_for(
                    loop.run_in_executor(None, fetch_daily), timeout=10.0)
            except Exception as e2:
                logger.warning(f"{symbol} yfinance dagligt fejl: {e2}")

        out["gap_status"] = ("neutral" if abs(out["gap_pct"]) < 0.3
                             else "gap op" if out["gap_pct"] > 0 else "gap ned")

        # 2) INTRADAG (i dags 5-min RTH-bars) — vs open + VWAP
        try:
            bars = await asyncio.wait_for(
                self.conn.ib.reqHistoricalDataAsync(
                    contract, endDateTime="", durationStr="1 D",
                    barSizeSetting="5 mins", whatToShow="TRADES", useRTH=True, formatDate=1),
                timeout=15.0)
            if bars:
                today_open = bars[0].open or bars[0].close
                current    = bars[-1].close
                if current:
                    out["price"] = round(current, 2)
                if today_open:
                    out["intraday_pct"] = round((current - today_open) / today_open * 100, 2)
                vol = sum(b.volume for b in bars) or 0
                if vol:
                    tp = sum(((b.high + b.low + b.close) / 3) * b.volume for b in bars)
                    out["vwap"]       = round(tp / vol, 2)
                    out["above_vwap"] = current >= out["vwap"]
        except Exception as e:
            logger.warning(f"{symbol} intradag-bars fejl: {e} - proever yfinance")
            try:
                import yfinance as yf
                loop = asyncio.get_event_loop()

                def fetch_intraday():
                    try:
                        hist = yf.Ticker(symbol).history(period="1d", interval="5m")
                        if hist is None or hist.empty:
                            return 0.0, 0.0, 0.0, None
                        today_open = float(hist["Open"].iloc[0])
                        current    = float(hist["Close"].iloc[-1])
                        ip   = round((current - today_open) / today_open * 100, 2) if today_open else 0.0
                        vol  = float(hist["Volume"].sum())
                        if vol:
                            tp   = float(((hist["High"] + hist["Low"] + hist["Close"]) / 3 * hist["Volume"]).sum())
                            vwap = round(tp / vol, 2)
                            return current, ip, vwap, bool(current >= vwap)
                        return current, ip, 0.0, None
                    except Exception:
                        return 0.0, 0.0, 0.0, None

                cur, ip, vwap, above = await asyncio.wait_for(
                    loop.run_in_executor(None, fetch_intraday), timeout=10.0)
                if cur:
                    out["price"] = round(cur, 2)
                out["intraday_pct"], out["vwap"], out["above_vwap"] = ip, vwap, above
            except Exception as e2:
                logger.warning(f"{symbol} yfinance intradag fejl: {e2}")

        # Ingen intradag-data (uden for RTH) -> "ukendt"; ellers retning vs open.
        if out["above_vwap"] is None and out["intraday_pct"] == 0:
            out["intraday_status"] = "ukendt"
        elif abs(out["intraday_pct"]) < 0.1:
            out["intraday_status"] = "neutral"
        else:
            out["intraday_status"] = "op fra open" if out["intraday_pct"] > 0 else "ned fra open"

        logger.info(f"{symbol}: ${out['price']} gap {out['gap_pct']:+.2f}% ({out['gap_status']}) "
                    f"intradag {out['intraday_pct']:+.2f}% ({out['intraday_status']}) vwap {out['vwap']}")
        return out

    # -----------------------------------------------------------------------
    # Scanner — top gainers fra IBKR
    # -----------------------------------------------------------------------

    async def _check_scanner(self, mc: MarketConditions) -> None:
        try:
            # Kandidater hentes fra TradingView — samme kilde som algoerne bruger.
            # Vi rører IKKE TWS her længere: tidligere hentede vi gap/relvol via
            # IBKR-snapshots, hvilket fik MarketOverview til at hænge når TWS var nede.
            from strategies.shared.tv_scanner import fetch_tv_top_gainer_symbols
            loop = asyncio.get_event_loop()
            gainers = await asyncio.wait_for(
                loop.run_in_executor(None, fetch_tv_top_gainer_symbols, 25),
                timeout=15.0
            )

            mc.top_gainers = gainers[:25]
            # gap/relvol-tælling er fjernet — krævede TWS og er ikke længere
            # nødvendig nu hvor kandidaterne kommer fra TradingView.
            mc.stocks_gap_over_10   = 0
            mc.stocks_relvol_over_5 = 0
            logger.info(f"Scanner (TV): {len(gainers)} gainers")

        except Exception as e:
            logger.warning(f"Scanner (TV) fejl: {e}")
            mc.top_gainers = []

    # -----------------------------------------------------------------------
    # Aktivitetsscore
    # -----------------------------------------------------------------------

    def _calculate_score(self, mc: MarketConditions) -> None:
        score = 0

        # VIX (max 30)
        if mc.vix == 0:
            score += 15
        elif mc.vix < 15:
            score += 0
        elif mc.vix < 20:
            score += 15
        elif mc.vix < 30:
            score += 25
        elif mc.vix < 40:
            score += 30
        else:
            score += 20

        # SPY gap (max 20)
        gap = abs(mc.spy_gap_pct)
        if gap > 1.5:
            score += 20
        elif gap > 0.5:
            score += 15
        elif gap > 0.2:
            score += 10
        else:
            score += 5

        # Gap-aktier (max 25)
        if mc.stocks_gap_over_10 >= 10:
            score += 25
        elif mc.stocks_gap_over_10 >= 5:
            score += 20
        elif mc.stocks_gap_over_10 >= 2:
            score += 10
        else:
            score += 5

        # Rel.vol aktier (max 25)
        if mc.stocks_relvol_over_5 >= 8:
            score += 25
        elif mc.stocks_relvol_over_5 >= 4:
            score += 18
        elif mc.stocks_relvol_over_5 >= 2:
            score += 10
        else:
            score += 5

        mc.score = min(100, score)

        # Afgørelser
        if mc.vix > 0 and mc.vix < 15:
            mc.score_label, mc.position_size_pct, mc.skal_handle = "rolig", 0.0, False
        elif mc.score >= 60:
            mc.score_label, mc.position_size_pct, mc.skal_handle = "aktiv", 1.0, True
        elif mc.score >= 10:
            mc.score_label, mc.position_size_pct, mc.skal_handle = "moderat", 0.5, True
        else:
            mc.score_label, mc.position_size_pct, mc.skal_handle = "rolig", 0.0, False

        if mc.spy_gap_pct < -1.5:
            mc.score_label, mc.position_size_pct, mc.skal_handle = "rolig", 0.0, False

        logger.info(f"Score: {mc.score}/100 ({mc.score_label}) pos_size: {mc.position_size_pct*100:.0f}%")

    # -----------------------------------------------------------------------
    # Frontend formatering
    # -----------------------------------------------------------------------

    def format_status_message(self, mc: MarketConditions) -> str:
        emoji   = "🟢" if mc.score_label == "aktiv" else "🟡" if mc.score_label == "moderat" else "🔴"
        vix_str = f"VIX {mc.vix:.1f}" if mc.vix > 0 else "VIX ukendt"
        spy_str = f"SPY {mc.spy_gap_pct:+.1f}%" if mc.spy_price > 0 else "SPY ukendt"
        scan_str= f"{len(mc.top_gainers)} gainers"
        return (
            f"{emoji} Markedsoverblik: {vix_str} | {spy_str} | {scan_str} | "
            f"Score {mc.score}/100 — {mc.score_label.upper()}"
        )

    def format_detailed(self, mc: MarketConditions) -> dict:
        return {
            "type":              "market_conditions",
            "checked_at":        mc.checked_at,
            "score":             mc.score,
            "score_label":       mc.score_label,
            "skal_handle":       mc.skal_handle,
            "position_size_pct": mc.position_size_pct,
            "vix":    {"value": mc.vix, "status": mc.vix_status},
            "spy":    {"price": mc.spy_price, "gap_pct": mc.spy_gap_pct, "gap_status": mc.spy_gap_status,
                       "intraday_pct": mc.spy_intraday_pct, "intraday_status": mc.spy_intraday_status,
                       "vwap": mc.spy_vwap, "above_vwap": mc.spy_above_vwap},
            "iwm":    {"price": mc.iwm_price, "gap_pct": mc.iwm_gap_pct, "gap_status": mc.iwm_gap_status,
                       "intraday_pct": mc.iwm_intraday_pct, "intraday_status": mc.iwm_intraday_status,
                       "vwap": mc.iwm_vwap, "above_vwap": mc.iwm_above_vwap},
            "scanner":{"top_gainers": mc.top_gainers[:10], "stocks_gap_over_10": mc.stocks_gap_over_10, "stocks_relvol_over_5": mc.stocks_relvol_over_5},
        }
