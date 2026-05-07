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
        await self._check_spy(mc)
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

    async def _check_spy(self, mc: MarketConditions) -> None:
        try:
            from ib_async import Stock
            contract = Stock("SPY", "SMART", "USD")
            await self.conn.ib.qualifyContractsAsync(contract)

            bars = await asyncio.wait_for(
                self.conn.ib.reqHistoricalDataAsync(
                    contract,
                    endDateTime    = "",
                    durationStr    = "3 D",
                    barSizeSetting = "1 day",
                    whatToShow     = "TRADES",
                    useRTH         = True,
                    formatDate     = 1,
                ),
                timeout=15.0
            )

            if bars and len(bars) >= 2:
                prev_close     = bars[-2].close
                today_open     = bars[-1].open or bars[-1].close
                mc.spy_price   = round(bars[-1].close, 2)
                mc.spy_gap_pct = round((today_open - prev_close) / prev_close * 100, 2)
            elif bars:
                mc.spy_price   = round(bars[-1].close, 2)
                mc.spy_gap_pct = 0.0

        except Exception as e:
            logger.warning(f"SPY IBKR fejl: {e} — prøver yfinance")
            try:
                import yfinance as yf
                loop = asyncio.get_event_loop()

                def fetch_spy():
                    try:
                        hist = yf.Ticker("SPY").history(period="3d")
                        if len(hist) >= 2:
                            prev_close = float(hist["Close"].iloc[-2])
                            today_open = float(hist["Open"].iloc[-1])
                            today_close= float(hist["Close"].iloc[-1])
                            return today_close, round((today_open - prev_close) / prev_close * 100, 2)
                        return 0.0, 0.0
                    except Exception:
                        return 0.0, 0.0

                mc.spy_price, mc.spy_gap_pct = await asyncio.wait_for(
                    loop.run_in_executor(None, fetch_spy),
                    timeout=10.0
                )
            except Exception as e2:
                logger.warning(f"SPY yfinance fejl: {e2}")

        if abs(mc.spy_gap_pct) < 0.3:
            mc.spy_gap_status = "neutral"
        elif mc.spy_gap_pct > 0:
            mc.spy_gap_status = "gap op"
        else:
            mc.spy_gap_status = "gap ned"

        logger.info(f"SPY: ${mc.spy_price} gap {mc.spy_gap_pct:+.2f}% ({mc.spy_gap_status})")

    # -----------------------------------------------------------------------
    # Scanner — top gainers fra IBKR
    # -----------------------------------------------------------------------

    async def _check_scanner(self, mc: MarketConditions) -> None:
        try:
            from ib_async import ScannerSubscription
            sub = ScannerSubscription(
                instrument   = "STK",
                locationCode = "STK.US.MAJOR",
                scanCode     = "TOP_PERC_GAIN",
            )
            data = await asyncio.wait_for(
                self.conn.ib.reqScannerDataAsync(sub),
                timeout=15.0
            )

            gainers = []
            for item in data:
                symbol = item.contractDetails.contract.symbol
                if len(symbol) <= 5:
                    gainers.append(symbol)

            mc.top_gainers = gainers[:25]

            # Tæl gap og volumen for top 10
            gap_count = relvol_count = 0
            for symbol in gainers[:10]:
                try:
                    snap = await asyncio.wait_for(
                        self.conn.get_snapshot(symbol),
                        timeout=5.0
                    )
                    if not snap:
                        continue
                    if snap.get("open") and snap.get("close") and snap["close"] > 0:
                        gap = (snap["open"] - snap["close"]) / snap["close"] * 100
                        if gap > 10:
                            gap_count += 1
                    if snap.get("volume") and snap["volume"] > 500000:
                        relvol_count += 1
                except Exception:
                    continue

            mc.stocks_gap_over_10   = gap_count
            mc.stocks_relvol_over_5 = relvol_count
            logger.info(f"Scanner: {len(gainers)} gainers")

        except Exception as e:
            logger.warning(f"Scanner fejl: {e}")
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
        elif mc.score >= 40:
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
            "spy":    {"price": mc.spy_price, "gap_pct": mc.spy_gap_pct, "gap_status": mc.spy_gap_status},
            "scanner":{"top_gainers": mc.top_gainers[:10], "stocks_gap_over_10": mc.stocks_gap_over_10, "stocks_relvol_over_5": mc.stocks_relvol_over_5},
        }
