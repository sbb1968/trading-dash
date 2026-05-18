//@version=5
// ═══════════════════════════════════════════════════════════════════════════
//  KONFLUENS STRATEGI v3 — Long-only, US aktier, 5m primær
//  Entry: 4+ af 6 bullish konfluens-betingelser
//  Exit:  3-lags hybrid (ATR hard SL + trailing + signal-konfluens)
//  Sizing: 1% risk pr. trade af equity
//  Nyt v3: Tooltips + optional detail-labels med hvilke betingelser var opfyldt
//          Udvidede comments i List of Trades
// ═══════════════════════════════════════════════════════════════════════════
strategy("Konfluens Strategi v3 [Long]",
     overlay=true,
     initial_capital=10000,
     default_qty_type=strategy.fixed,
     default_qty_value=0,
     pyramiding=0,
     commission_type=strategy.commission.percent,
     commission_value=0.05,
     slippage=2,
     calc_on_every_tick=false,
     process_orders_on_close=true,
     max_labels_count=500)

// ─── INPUT GRUPPER ─────────────────────────────────────────────────────────
grpDate   = "📅 Backtest periode"
grpRisk   = "💰 Risiko & Position"
grpEntry  = "▶ Entry konfluens"
grpExit   = "◀ Exit konfluens"
grpStop   = "⛔ Stop loss & Trailing"
grpTrend  = "Trend & HTF"
grpRSI    = "RSI parametre"
grpVol    = "Volume parametre"
grpStruct = "Struktur (pivots)"
grpAnalyse = "🔍 Analyse & detaljer"

// Dato-filter
useStartDate = input.bool(true,  "Brug startdato", group=grpDate)
startDate    = input.time(timestamp("2026-05-15 06:30 +0000"), "Backtest start", group=grpDate)
useEndDate   = input.bool(false, "Brug slutdato (ellers til nu)", group=grpDate)
endDate      = input.time(timestamp("2026-05-17 19:30 +0000"), "Backtest slut", group=grpDate)
inDateRange  = (not useStartDate or time >= startDate) and (not useEndDate or time <= endDate)

// Risk
riskPercent = input.float(1.0, "Risk pr. trade (% af equity)", minval=0.1, maxval=5.0, step=0.1, group=grpRisk)

// Tærskler
entryThreshold = input.int(4, "Min. entry konfluens (af 6)", minval=3, maxval=6, group=grpEntry)
exitThreshold  = input.int(3, "Min. exit konfluens (af 5)",  minval=2, maxval=5, group=grpExit)

// Stop loss & trailing
atrLen       = input.int(14, "ATR længde", minval=2, group=grpStop)
atrSLmult    = input.float(1.2, "Hard SL = entry − X × ATR", minval=0.3, step=0.1, group=grpStop)
trailActivR  = input.float(1.0, "Trailing aktiveres ved +X R", minval=0.3, step=0.1, group=grpStop)
trailType    = input.string("Swing Low", "Trailing type", options=["Swing Low", "EMA Fast", "ATR"], group=grpStop)
trailAtrMult = input.float(1.5, "ATR trail multiplum (hvis ATR-type)", minval=0.5, step=0.1, group=grpStop)

// Trend / HTF
htfTimeframe = input.timeframe("15", "HTF for trendfilter", group=grpTrend)
htfEmaLen    = input.int(50, "HTF EMA længde", minval=10, group=grpTrend)
emaFastLen   = input.int(9,  "Fast EMA (kort)", minval=2, group=grpTrend)
emaSlowLen   = input.int(21, "Slow EMA (kort)", minval=5, group=grpTrend)
useVWAP      = input.bool(true, "Brug VWAP-kontekst", group=grpTrend)
vwapBandMult = input.float(1.5, "VWAP std.dev. multiplum", minval=0.5, step=0.1, group=grpTrend)

// RSI
rsiLen          = input.int(14, "RSI længde", minval=2, group=grpRSI)
rsiOversold     = input.int(35, "RSI oversold tærskel", minval=10, maxval=50, group=grpRSI)
rsiCrossLevel   = input.int(40, "RSI cross-up tærskel", minval=20, maxval=60, group=grpRSI)
rsiLookback     = input.int(5,  "RSI oversold lookback (bars)", minval=1, group=grpRSI)
rsiOverbought   = input.int(65, "RSI overbought tærskel", minval=50, maxval=90, group=grpRSI)
rsiCrossDnLevel = input.int(60, "RSI cross-down tærskel", minval=40, maxval=80, group=grpRSI)

// Volume
volMaLen = input.int(20, "Volume MA længde", minval=5, group=grpVol)
volMult  = input.float(1.2, "Volume spike multiplum", minval=1.0, step=0.05, group=grpVol)

// Pivots
pivotLeft  = input.int(3, "Pivot lookback venstre", minval=1, group=grpStruct)
pivotRight = input.int(3, "Pivot lookback højre",  minval=1, group=grpStruct)

// Analyse
showDetailLabels = input.bool(false, "Vis permanente detail-labels ved entries/exits", group=grpAnalyse, tooltip="Når aktiv vises en lille tekstboks ved hver trade med præcis hvilke betingelser var opfyldt. Slå fra for renere chart.")

// ─── SESSION FILTER ────────────────────────────────────────────────────────
inSession = not na(time(timeframe.period, "0930-1600", "America/New_York"))

// ─── KERNE BEREGNINGER ────────────────────────────────────────────────────
emaFast = ta.ema(close, emaFastLen)
emaSlow = ta.ema(close, emaSlowLen)
htfEma  = request.security(syminfo.tickerid, htfTimeframe, ta.ema(close, htfEmaLen), lookahead=barmerge.lookahead_off)

[vwapVal, vwapUpper, vwapLower] = ta.vwap(hlc3, anchor=timeframe.change("D"), stdev_mult=vwapBandMult)

rsiVal = ta.rsi(close, rsiLen)
atrVal = ta.atr(atrLen)

volMA = ta.sma(volume, volMaLen)
volSpike = volume > volMA * volMult

// Pivots
pivotHigh = ta.pivothigh(high, pivotLeft, pivotRight)
pivotLow  = ta.pivotlow(low, pivotLeft, pivotRight)

var float lastSwingHigh = na
var float prevSwingHigh = na
var float lastSwingLow  = na
var float prevSwingLow  = na

if not na(pivotHigh)
    prevSwingHigh := lastSwingHigh
    lastSwingHigh := pivotHigh
if not na(pivotLow)
    prevSwingLow := lastSwingLow
    lastSwingLow := pivotLow

higherLow = not na(lastSwingLow) and not na(prevSwingLow) and lastSwingLow > prevSwingLow
lowerHigh = not na(lastSwingHigh) and not na(prevSwingHigh) and lastSwingHigh < prevSwingHigh

// ─── ENTRY KONFLUENS ──────────────────────────────────────────────────────
cond1 = close > htfEma
cond2 = not useVWAP or (close > vwapVal) or (low <= vwapLower and close > open)
rsiWasOversold = ta.lowest(rsiVal, rsiLookback) < rsiOversold
cond3 = rsiWasOversold and ta.crossover(rsiVal, rsiCrossLevel)
cond4 = higherLow

body      = math.abs(close - open)
rangeBar  = high - low
upperWick = high - math.max(close, open)
lowerWick = math.min(close, open) - low
isBullEng = close > open and close[1] < open[1] and close > open[1] and open < close[1]
isHammer  = lowerWick > body * 2 and upperWick < body and close > open
strongClose = close > open and (close - low) / (rangeBar + 0.000001) > 0.66 and low < low[1]
cond5 = isBullEng or isHammer or strongClose

cond6 = volSpike and close > open

entryScore  = (cond1 ? 1 : 0) + (cond2 ? 1 : 0) + (cond3 ? 1 : 0) + (cond4 ? 1 : 0) + (cond5 ? 1 : 0) + (cond6 ? 1 : 0)
entrySignal = entryScore >= entryThreshold and inSession and inDateRange

// Byg detalje-tekst for entry (kompakt + læselig)
// Kort form til comments: HTF=T,VWAP=V,RSI=R,HL=H,Candle=C,Vol=L
f_entryShort() =>
    s = ""
    s := s + (cond1 ? "T" : "·")
    s := s + (cond2 ? "V" : "·")
    s := s + (cond3 ? "R" : "·")
    s := s + (cond4 ? "H" : "·")
    s := s + (cond5 ? "C" : "·")
    s := s + (cond6 ? "L" : "·")
    s

// Lang form til tooltip og labels
f_entryDetail() =>
    candleType = isBullEng ? "Engulf" : isHammer ? "Hammer" : strongClose ? "Strong" : "—"
    s = "ENTRY @ " + str.tostring(close, "#.##") + "\n"
    s := s + "Score: " + str.tostring(entryScore) + "/6\n"
    s := s + "──────────────\n"
    s := s + (cond1 ? "✓" : "✗") + " HTF trend (50EMA " + str.tostring(htfEma, "#.##") + ")\n"
    s := s + (cond2 ? "✓" : "✗") + " VWAP (" + str.tostring(vwapVal, "#.##") + ")\n"
    s := s + (cond3 ? "✓" : "✗") + " RSI reset (" + str.tostring(rsiVal, "#.#") + ")\n"
    s := s + (cond4 ? "✓" : "✗") + " Higher Low\n"
    s := s + (cond5 ? "✓" : "✗") + " Reversal candle (" + candleType + ")\n"
    s := s + (cond6 ? "✓" : "✗") + " Volume spike (" + str.tostring(volume / volMA, "#.##") + "x)"
    s

// ─── EXIT KONFLUENS ───────────────────────────────────────────────────────
rsiWasOverbought = ta.highest(rsiVal, rsiLookback) > rsiOverbought
exit1 = rsiWasOverbought and ta.crossunder(rsiVal, rsiCrossDnLevel)
exit2 = lowerHigh
isBearEng   = close < open and close[1] > open[1] and close < open[1] and open > close[1]
isShootStar = upperWick > body * 2 and lowerWick < body and close < open
weakClose   = close < open and (high - close) / (rangeBar + 0.000001) > 0.66 and high > high[1]
exit3 = isBearEng or isShootStar or weakClose
exit4 = close < emaFast and close[1] >= emaFast[1]

var float lastPriceHigh = na
var float prevPriceHigh = na
var float lastRsiAtHigh = na
var float prevRsiAtHigh = na
if not na(pivotHigh)
    prevPriceHigh := lastPriceHigh
    lastPriceHigh := pivotHigh
    prevRsiAtHigh := lastRsiAtHigh
    lastRsiAtHigh := rsiVal[pivotRight]
bearDiv = not na(lastPriceHigh) and not na(prevPriceHigh) and not na(lastRsiAtHigh) and not na(prevRsiAtHigh) and lastPriceHigh > prevPriceHigh and lastRsiAtHigh < prevRsiAtHigh
exit5 = (volSpike and close < open) or bearDiv

exitScore  = (exit1 ? 1 : 0) + (exit2 ? 1 : 0) + (exit3 ? 1 : 0) + (exit4 ? 1 : 0) + (exit5 ? 1 : 0)
exitSignal = exitScore >= exitThreshold and inSession

// Byg detalje-tekst for exit
// Kort form: RSI=O, LowerHigh=L, BearCandle=C, UnderEMA=E, Vol/Div=V
f_exitShort() =>
    s = ""
    s := s + (exit1 ? "O" : "·")
    s := s + (exit2 ? "L" : "·")
    s := s + (exit3 ? "C" : "·")
    s := s + (exit4 ? "E" : "·")
    s := s + (exit5 ? "V" : "·")
    s

f_exitDetail() =>
    candleType = isBearEng ? "Engulf" : isShootStar ? "ShootSt" : weakClose ? "Weak" : "—"
    volDetail = bearDiv ? "DIV" : (volSpike and close < open) ? "VOL" : "—"
    s = "SIGNAL EXIT @ " + str.tostring(close, "#.##") + "\n"
    s := s + "Score: " + str.tostring(exitScore) + "/5\n"
    s := s + "──────────────\n"
    s := s + (exit1 ? "✓" : "✗") + " RSI overbought reversal (" + str.tostring(rsiVal, "#.#") + ")\n"
    s := s + (exit2 ? "✓" : "✗") + " Lower High\n"
    s := s + (exit3 ? "✓" : "✗") + " Bearish candle (" + candleType + ")\n"
    s := s + (exit4 ? "✓" : "✗") + " Under fast EMA (" + str.tostring(emaFast, "#.##") + ")\n"
    s := s + (exit5 ? "✓" : "✗") + " Bearish vol/div (" + volDetail + ")"
    s

// ─── POSITION & STOP MANAGEMENT ───────────────────────────────────────────
var float entryPrice   = na
var float initialStop  = na
var float trailingStop = na
var float riskPerShare = na
var bool  trailActive  = false
var string lastEntryShort = ""
var string lastEntryDetail = ""

calcQty(entryPx, stopPx) =>
    riskAmount = strategy.equity * (riskPercent / 100.0)
    perShareRisk = math.max(entryPx - stopPx, syminfo.mintick)
    math.floor(riskAmount / perShareRisk)

// ENTRY
if entrySignal and strategy.position_size == 0
    entryPx = close
    stopPx  = entryPx - atrSLmult * atrVal
    qty     = calcQty(entryPx, stopPx)
    if qty > 0
        entryPrice   := entryPx
        initialStop  := stopPx
        trailingStop := stopPx
        riskPerShare := entryPx - stopPx
        trailActive  := false
        // Gem detaljer til exit-label
        lastEntryShort := f_entryShort()
        lastEntryDetail := f_entryDetail()
        // Udvidet comment
        entryComment = "S=" + str.tostring(entryScore) + " [" + lastEntryShort + "]"
        strategy.entry("LONG", strategy.long, qty=qty, comment=entryComment)
        // Permanent detail-label (kun hvis aktiveret)
        if showDetailLabels
            label.new(bar_index, low, text=f_entryDetail(), style=label.style_label_up, color=color.new(color.green, 20), textcolor=color.white, size=size.small, yloc=yloc.belowbar, tooltip=f_entryDetail())

// TRAILING
if strategy.position_size > 0
    currentR = (close - entryPrice) / riskPerShare
    if not trailActive and currentR >= trailActivR
        trailActive := true
    if trailActive
        newTrail = trailingStop
        if trailType == "Swing Low" and not na(lastSwingLow)
            newTrail := math.max(trailingStop, lastSwingLow)
        else if trailType == "EMA Fast"
            newTrail := math.max(trailingStop, emaFast)
        else if trailType == "ATR"
            newTrail := math.max(trailingStop, close - trailAtrMult * atrVal)
        trailingStop := math.max(trailingStop, newTrail)

// EXIT
if strategy.position_size > 0
    activeStop = trailActive ? math.max(initialStop, trailingStop) : initialStop
    strategy.exit("SL/Trail", from_entry="LONG", stop=activeStop)
    if exitSignal
        exitComment = "SX=" + str.tostring(exitScore) + " [" + f_exitShort() + "]"
        strategy.close("LONG", comment=exitComment)
        if showDetailLabels
            label.new(bar_index, high, text=f_exitDetail(), style=label.style_label_down, color=color.new(color.purple, 20), textcolor=color.white, size=size.small, yloc=yloc.abovebar, tooltip=f_exitDetail())

if not inDateRange and strategy.position_size > 0
    strategy.close("LONG", comment="Date filter exit")

// Reset state
if strategy.position_size == 0 and strategy.position_size[1] > 0
    entryPrice   := na
    initialStop  := na
    trailingStop := na
    riskPerShare := na
    trailActive  := false

// ─── VISUALS ──────────────────────────────────────────────────────────────
bgcolor(not inDateRange ? color.new(color.gray, 90) : na, title="Uden for backtest")

plot(strategy.position_size > 0 ? entryPrice   : na, "Entry",        color=color.new(color.blue, 0),   style=plot.style_linebr, linewidth=1)
plot(strategy.position_size > 0 ? initialStop  : na, "Initial SL",   color=color.new(color.red, 0),    style=plot.style_linebr, linewidth=1)
plot(strategy.position_size > 0 and trailActive ? trailingStop : na, "Trailing SL", color=color.new(color.orange, 0), style=plot.style_linebr, linewidth=2)

plot(emaFast, "EMA Fast", color=color.new(color.orange, 50))
plot(emaSlow, "EMA Slow", color=color.new(color.blue, 50))
plot(htfEma,  "HTF EMA",  color=color.new(color.purple, 30), linewidth=2)
plot(vwapVal, "VWAP",     color=color.new(color.yellow, 30), linewidth=2)
