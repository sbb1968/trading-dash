import { useState, useEffect } from "react";
import type { Portfolio, Position, Trade } from "./useMarketData";

interface Props {
  portfolio: Portfolio;
  selectedTicker: string;
  currentPrice: number;
  onBuy: (ticker: string, shares: number, price: number) => void;
  onSell: (ticker: string, shares: number, price: number) => void;
  onReset: () => void;
  onSelectTicker: (ticker: string) => void;
}

function formatUSD(val: number) {
  return val.toLocaleString("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2 });
}

function PnLCell({ value, pct }: { value: number; pct: number }) {
  const cls = value >= 0 ? "positive" : "negative";
  return (
    <span className={cls}>
      {value >= 0 ? "+" : ""}{formatUSD(value)} ({pct >= 0 ? "+" : ""}{pct.toFixed(2)}%)
    </span>
  );
}

export function PaperTradingPanel({ portfolio, selectedTicker, currentPrice, onBuy, onSell, onReset, onSelectTicker }: Props) {
  const [activeTab, setActiveTab]           = useState<"overview" | "positions" | "trades">("overview");
  const [shares, setShares]                 = useState<string>("10");
  const [orderType, setOrderType]           = useState<"market" | "shares" | "dollars">("shares");
  const [dollars, setDollars]               = useState<string>("1000");
  const [tradeError, setTradeError]         = useState<string>("");
  const [tradeSuccess, setTradeSuccess]     = useState<string>("");
  const [showResetConfirm, setShowResetConfirm] = useState(false);

  const currentPosition = portfolio.positions.find(p => p.ticker === selectedTicker);

  function calculateShares(): number {
    if (orderType === "dollars") return parseFloat(dollars) / currentPrice;
    return parseFloat(shares) || 0;
  }

  function handleBuy() {
    setTradeError(""); setTradeSuccess("");
    const s = calculateShares();
    if (s <= 0) { setTradeError("Angiv antal aktier eller beløb"); return; }
    const cost = s * currentPrice;
    if (cost > portfolio.balance) { setTradeError(`Ikke nok kapital. Kræver ${formatUSD(cost)}`); return; }
    onBuy(selectedTicker, s, currentPrice);
    setTradeSuccess(`Købt ${s.toFixed(4)} ${selectedTicker} @ ${formatUSD(currentPrice)}`);
    setTimeout(() => setTradeSuccess(""), 3000);
  }

  function handleSell() {
    setTradeError(""); setTradeSuccess("");
    if (!currentPosition) { setTradeError(`Ingen position i ${selectedTicker}`); return; }
    const s = orderType === "market" ? currentPosition.shares : calculateShares();
    if (s <= 0) { setTradeError("Angiv antal aktier eller beløb"); return; }
    if (s > currentPosition.shares) {
      setTradeError(`Kan ikke sælge ${s.toFixed(4)} — har kun ${currentPosition.shares.toFixed(4)}`);
      return;
    }
    onSell(selectedTicker, s, currentPrice);
    setTradeSuccess(`Solgt ${s.toFixed(4)} ${selectedTicker} @ ${formatUSD(currentPrice)}`);
    setTimeout(() => setTradeSuccess(""), 3000);
  }

  function handleReset() {
    if (showResetConfirm) {
      onReset();
      setShowResetConfirm(false);
      setTradeSuccess("Portfolio nulstillet til $100.000");
      setTimeout(() => setTradeSuccess(""), 3000);
    } else {
      setShowResetConfirm(true);
      setTimeout(() => setShowResetConfirm(false), 5000);
    }
  }

  // ── ALT+K = Køb, ALT+S = Sælg ────────────────────────────
  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if (!e.altKey) return;
      if (e.key.toUpperCase() === "K") { e.preventDefault(); handleBuy(); }
      if (e.key.toUpperCase() === "S") { e.preventDefault(); handleSell(); }
    }
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [selectedTicker, currentPrice, orderType, shares, dollars, portfolio]);

  const estimatedShares = calculateShares();
  const estimatedCost   = estimatedShares * currentPrice;

  return (
    <div className="pt-container">
      {/* Header med balance */}
      <div className="pt-header">
        <div className="pt-equity">
          <span className="pt-equity-label">Total Equity</span>
          <span className="pt-equity-value">{formatUSD(portfolio.total_equity)}</span>
        </div>
        <div className="pt-pnl">
          <PnLCell value={portfolio.total_pnl} pct={portfolio.total_pnl_pct} />
        </div>
        <button
          className={`pt-reset-btn ${showResetConfirm ? "pt-reset-confirm" : ""}`}
          onClick={handleReset}
          title="Nulstil portfolio"
        >
          {showResetConfirm ? "Bekræft nulstil?" : "⟳ Nulstil"}
        </button>
      </div>

      {/* Balance info */}
      <div className="pt-balance-row">
        <div className="pt-balance-item">
          <span className="pt-balance-label">Cash</span>
          <span className="pt-balance-value">{formatUSD(portfolio.balance)}</span>
        </div>
        <div className="pt-balance-item">
          <span className="pt-balance-label">Positioner</span>
          <span className="pt-balance-value">{formatUSD(portfolio.total_position_value)}</span>
        </div>
        <div className="pt-balance-item">
          <span className="pt-balance-label">Realiseret P&L</span>
          <span className={portfolio.realized_pnl >= 0 ? "positive" : "negative"}>
            {portfolio.realized_pnl >= 0 ? "+" : ""}{formatUSD(portfolio.realized_pnl)}
          </span>
        </div>
      </div>

      {/* Order panel */}
      <div className="pt-order-panel">
        <div className="pt-order-ticker">
          <span className="pt-order-ticker-name">{selectedTicker}</span>
          <span className="pt-order-ticker-price">{formatUSD(currentPrice)}</span>
          {currentPosition && (
            <span className="pt-position-badge">
              {currentPosition.shares.toFixed(2)} aktier
            </span>
          )}
        </div>

        {/* Order type */}
        <div className="pt-order-type">
          <button className={`pt-type-btn ${orderType === "shares"  ? "active" : ""}`} onClick={() => setOrderType("shares")}>Antal</button>
          <button className={`pt-type-btn ${orderType === "dollars" ? "active" : ""}`} onClick={() => setOrderType("dollars")}>Beløb $</button>
          <button className={`pt-type-btn ${orderType === "market"  ? "active" : ""}`} onClick={() => setOrderType("market")}>Alle</button>
        </div>

        {/* Input */}
        {orderType === "shares" && (
          <div className="pt-order-input-row">
            <input type="number" className="pt-order-input" value={shares}
              onChange={e => setShares(e.target.value)} min="0" step="1" placeholder="Antal aktier" />
            <span className="pt-order-estimate">≈ {formatUSD(estimatedCost)}</span>
          </div>
        )}
        {orderType === "dollars" && (
          <div className="pt-order-input-row">
            <input type="number" className="pt-order-input" value={dollars}
              onChange={e => setDollars(e.target.value)} min="0" step="100" placeholder="Beløb i $" />
            <span className="pt-order-estimate">≈ {estimatedShares.toFixed(4)} aktier</span>
          </div>
        )}
        {orderType === "market" && currentPosition && (
          <div className="pt-order-input-row">
            <span className="pt-order-estimate">Sælger alle {currentPosition.shares.toFixed(4)} aktier</span>
          </div>
        )}

        {/* Køb/Salg knapper med shortcut-hint */}
        <div className="pt-order-buttons">
          <button className="pt-buy-btn" onClick={handleBuy} title="ALT+K">
            📈 <u>K</u>ØB {selectedTicker}
          </button>
          <button
            className={`pt-sell-btn ${!currentPosition ? "pt-btn-disabled" : ""}`}
            onClick={handleSell}
            disabled={!currentPosition}
            title="ALT+S"
          >
            📉 <u>S</u>ÆLG {selectedTicker}
          </button>
        </div>

        {tradeError   && <div className="pt-trade-error">{tradeError}</div>}
        {tradeSuccess && <div className="pt-trade-success">{tradeSuccess}</div>}
      </div>

      {/* Tabs */}
      <div className="pt-tabs">
        <button className={`pt-tab ${activeTab === "overview"   ? "active" : ""}`} onClick={() => setActiveTab("overview")}>Oversigt</button>
        <button className={`pt-tab ${activeTab === "positions"  ? "active" : ""}`} onClick={() => setActiveTab("positions")}>Positioner ({portfolio.num_positions})</button>
        <button className={`pt-tab ${activeTab === "trades"     ? "active" : ""}`} onClick={() => setActiveTab("trades")}>Trades ({portfolio.num_trades})</button>
      </div>

      {/* Tab indhold */}
      <div className="pt-tab-content">

        {activeTab === "overview" && (
          <div className="pt-overview">
            <div className="pt-overview-row"><span className="pt-overview-label">Start kapital</span>      <span className="pt-overview-value">{formatUSD(portfolio.starting_balance)}</span></div>
            <div className="pt-overview-row"><span className="pt-overview-label">Total Equity</span>       <span className="pt-overview-value">{formatUSD(portfolio.total_equity)}</span></div>
            <div className="pt-overview-row"><span className="pt-overview-label">Total P&L</span>          <span className="pt-overview-value"><PnLCell value={portfolio.total_pnl} pct={portfolio.total_pnl_pct} /></span></div>
            <div className="pt-overview-row"><span className="pt-overview-label">Urealiseret P&L</span>    <span className="pt-overview-value"><PnLCell value={portfolio.unrealized_pnl} pct={portfolio.total_position_value > 0 ? (portfolio.unrealized_pnl / portfolio.total_position_value * 100) : 0} /></span></div>
            <div className="pt-overview-row"><span className="pt-overview-label">Realiseret P&L</span>     <span className="pt-overview-value"><PnLCell value={portfolio.realized_pnl} pct={portfolio.realized_pnl / portfolio.starting_balance * 100} /></span></div>
            <div className="pt-overview-row"><span className="pt-overview-label">Antal positioner</span>   <span className="pt-overview-value">{portfolio.num_positions}</span></div>
            <div className="pt-overview-row"><span className="pt-overview-label">Antal trades</span>       <span className="pt-overview-value">{portfolio.num_trades}</span></div>
          </div>
        )}

        {activeTab === "positions" && (
          <div className="pt-scroll">
            {portfolio.positions.length === 0 && <div className="pt-empty">Ingen aktive positioner</div>}
            {portfolio.positions.map((pos: Position) => (
              <div key={pos.ticker}
                className={`pt-position-card ${pos.ticker === selectedTicker ? "pt-position-selected" : ""}`}
                onClick={() => onSelectTicker(pos.ticker)}
              >
                <div className="pt-pos-header">
                  <span className="pt-pos-ticker">{pos.ticker}</span>
                  <span className="pt-pos-value">{formatUSD(pos.position_value)}</span>
                </div>
                <div className="pt-pos-details">
                  <span>{pos.shares.toFixed(4)} aktier @ {formatUSD(pos.avg_price)}</span>
                  <PnLCell value={pos.unrealized_pnl} pct={pos.unrealized_pnl_pct} />
                </div>
                <div className="pt-pos-price">Nuværende: {formatUSD(pos.current_price)}</div>
              </div>
            ))}
          </div>
        )}

        {activeTab === "trades" && (
          <div className="pt-scroll">
            {portfolio.trades.length === 0 && <div className="pt-empty">Ingen afsluttede trades endnu</div>}
            {[...portfolio.trades].reverse().map((trade: Trade, i) => (
              <div key={i} className="pt-trade-card">
                <div className="pt-trade-header">
                  <span className="pt-pos-ticker">{trade.ticker}</span>
                  <span className={trade.pnl >= 0 ? "positive" : "negative"}>
                    {trade.pnl >= 0 ? "+" : ""}{formatUSD(trade.pnl)}
                  </span>
                </div>
                <div className="pt-trade-details">
                  <span>Ind: {formatUSD(trade.entry_price)} → Ud: {formatUSD(trade.exit_price)}</span>
                  <span className={trade.pnl_pct >= 0 ? "positive" : "negative"}>
                    {trade.pnl_pct >= 0 ? "+" : ""}{trade.pnl_pct.toFixed(2)}%
                  </span>
                </div>
                <div className="pt-trade-time">{new Date(trade.closed_at).toLocaleTimeString("da-DK")}</div>
              </div>
            ))}
          </div>
        )}

      </div>
    </div>
  );
}
