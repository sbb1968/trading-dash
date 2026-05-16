import { useState, useEffect, useRef } from "react";
import { openUrl } from "@tauri-apps/plugin-opener";
import "./App.css";
import { useMarketData } from "./useMarketData";
import type { NewsData } from "./useMarketData";
import { TradingViewWidget } from "./TradingViewWidget";
import { FloatingWindow } from "./FloatingWindow";
import { Menubar } from "./Menubar";
import { PaperTradingPanel } from "./PaperTrading";
import {
  Layout, WindowConfig, WindowId, WINDOW_LABELS,
  loadLayouts, saveLayouts, getActiveLayoutId, setActiveLayoutId,
  saveCurrentAsLayout, deleteLayout,
  WATCHLIST_COLUMNS, LEVEL2_COLUMNS, TIMESALES_COLUMNS,
  DEFAULT_WATCHLIST_COLUMNS, DEFAULT_LEVEL2_COLUMNS, DEFAULT_TIMESALES_COLUMNS,
} from "./layouts";
import { TradeJournal } from "./TradeJournal";
import { LiveAlgo } from "./LiveAlgo";
import { AlertsWindow } from "./Alertspanel";
import { MarketOverview } from "./MarketOverview";
import { AccountPanel } from "./AccountPanel";
import { OrdersWindow } from "./OrdersWindow";
import { useTickerName } from "./useTickerName";


// ── Konstanter ────────────────────────────────────────────────
const ALERT_THRESHOLD_DEFAULT = 0.5;
type ActiveView = "scanners" | "watchlist" | "charting" | "newsroom" | "konfigurator" | "papertrading";

const ALL_COLUMNS = [
  { id: "price",      label: "Price"        },
  { id: "change",     label: "Change %"     },
  { id: "volume",     label: "Volume"       },
  { id: "float",      label: "Float"        },
  { id: "relvol",     label: "RelVol Daily" },
  { id: "relvol5",    label: "RelVol 5min"  },
  { id: "gap",        label: "Gap %"        },
  { id: "prevclose",  label: "Prev Close"   },
  { id: "high",       label: "High"         },
  { id: "low",        label: "Low"          },
];

const DEFAULT_COLUMNS = ["price","change","volume","float","relvol","relvol5","gap"];

// ── Font-størrelse system ─────────────────────────────────────
const FONT_WINDOW_TYPES = [
  { id: "menubar",   label: "Menubar" },
  { id: "scanner",   label: "Scannere" },
  { id: "watchlist", label: "Watchlist" },
  { id: "newsroom",  label: "Newsroom" },
  { id: "chart",     label: "Charts" },
  { id: "level2",    label: "Level 2" },
  { id: "timesales", label: "Time & Sales" },
  { id: "paper",     label: "Paper Trading" },
  { id: "journal",   label: "Trade Journal" },
  { id: "livealgo",  label: "Live Algo" },
  { id: "alerts",    label: "Pris/Nyheds Alerts" },
  { id: "algohub", label: "Algo Hub" },
  { id: "marketoverview", label: "Markedsoverblik" },
  { id: "account",   label: "Konto" },
];

const FONT_DEFAULTS = { title: 14, header: 11, content: 13 };

function getFontKey(type: string, part: "title" | "header" | "content") {
  return `font_${type}_${part}`;
}

function loadFontSize(type: string, part: "title" | "header" | "content"): number {
  const saved = localStorage.getItem(getFontKey(type, part));
  return saved ? parseInt(saved) : FONT_DEFAULTS[part];
}

function applyAllFonts() {
  FONT_WINDOW_TYPES.forEach(({ id }) => {
    (["title","header","content"] as const).forEach(part => {
      const size = loadFontSize(id, part);
      document.documentElement.style.setProperty(`--fs-${part}-${id}`, `${size}px`);
    });
  });
}

function getWindowType(id: WindowId): string {
  if (id === "scanner1" || id === "scanner2") return "scanner";
  if (id === "watchlist")    return "watchlist";
  if (id === "newsroom")     return "newsroom";
  if (id.startsWith("chart")) return "chart";
  if (id === "level2")       return "level2";
  if (id === "timesales")    return "timesales";
  if (id === "papertrading") return "paper";
  if (id === "alerts")       return "alerts";
  if (id === "algohub")      return "algohub";
  if (id === "marketoverview") return "marketoverview";
  if (id === "account") return "account";
  return "scanner";
}

// ── Clock ─────────────────────────────────────────────────────
function Clock() {
  const [time, setTime] = useState(new Date());
  useEffect(() => {
    const interval = setInterval(() => setTime(new Date()), 1000);
    return () => clearInterval(interval);
  }, []);
  return (
    <div className="status-item">
      <span className="status-label">🕐</span>
      <span className="status-value">
        {time.toLocaleTimeString("da-DK", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
      </span>
    </div>
  );
}

// ── Market Status ─────────────────────────────────────────────
function MarketStatus() {
  const [status, setStatus] = useState<"open" | "pre" | "after" | "closed">("closed");
  useEffect(() => {
    function update() {
      const now = new Date(), day = now.getDay();
      const et  = new Date(now.toLocaleString("en-US", { timeZone: "America/New_York" }));
      const mins = et.getHours() * 60 + et.getMinutes();
      if (day === 0 || day === 6)     { setStatus("closed"); return; }
      if (mins >= 240 && mins < 570)  { setStatus("pre");    return; }
      if (mins >= 570 && mins < 960)  { setStatus("open");   return; }
      if (mins >= 960 && mins < 1200) { setStatus("after");  return; }
      setStatus("closed");
    }
    update();
    const interval = setInterval(update, 10000);
    return () => clearInterval(interval);
  }, []);
  const labels = {
    open:   { text: "● MARKED ÅBENT",  cls: "market-open"   },
    pre:    { text: "● PRE-MARKET",     cls: "market-pre"    },
    after:  { text: "● AFTER-HOURS",    cls: "market-after"  },
    closed: { text: "● MARKED LUKKET",  cls: "market-closed" },
  };
  return <div className={`status-item market-status ${labels[status].cls}`}>{labels[status].text}</div>;
}

// ── Alert Counter ─────────────────────────────────────────────
function AlertCounter({ count }: { count: number }) {
  const [flash, setFlash] = useState(false);
  const prevCount = useRef(count);
  useEffect(() => {
    if (count > prevCount.current) { setFlash(true); setTimeout(() => setFlash(false), 600); }
    prevCount.current = count;
  }, [count]);
  return (
    <div className={`status-item alert-counter ${flash ? "alert-flash" : ""}`}>
      <span className="status-label">🔔</span>
      <span className="status-value">{count} alerts</span>
    </div>
  );
}

// ── Connection Status ─────────────────────────────────────────
function ConnectionStatus({ status }: { status: string }) {
  const labels: Record<string, { text: string; cls: string }> = {
    connected:    { text: "● BACKEND TILSLUTTET",  cls: "conn-connected"    },
    connecting:   { text: "◌ FORBINDER...",        cls: "conn-connecting"   },
    disconnected: { text: "● IBKR IKKE FORBUNDET", cls: "conn-disconnected" },
  };
  const label = labels[status] || labels.disconnected;
  return <div className={`status-item ${label.cls}`}>{label.text}</div>;
}

// ── TickerCell — viser ticker + firma-navn nedenunder ─────────
function TickerCell({ ticker }: { ticker: string }) {
  const name = useTickerName(ticker);
  return (
    <td className="sym-cell">
      <div style={{ fontWeight: 700 }}>{ticker}</div>
      <div style={{
        fontSize: "calc(var(--fs-content-scanner, 13px) * 0.78)",
        color: "var(--text-muted)",
        fontWeight: 400,
        marginTop: 1,
        whiteSpace: "nowrap",
        overflow: "hidden",
        textOverflow: "ellipsis",
        maxWidth: "180px",
      }}>
        {name || "\u00A0"}
      </div>
    </td>
  );
}

// ── Scanner Table ─────────────────────────────────────────────
function ScannerTable({ stocks, sortBy, selectedTicker, onSelectTicker, scannerId }: {
  stocks: any[]; sortBy: "momentum" | "gainers"; selectedTicker: string;
  onSelectTicker: (ticker: string) => void; scannerId: string;
}) {
  const [visibleCols, setVisibleCols] = useState<string[]>(() => {
    const saved = localStorage.getItem(`columns_${scannerId}`);
    return saved ? JSON.parse(saved) : DEFAULT_COLUMNS;
  });
  useEffect(() => {
    function onStorage(e: StorageEvent) {
      if (e.key === `columns_${scannerId}` && e.newValue) setVisibleCols(JSON.parse(e.newValue));
    }
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, [scannerId]);

  const sortedRef = useRef<StockData[]>([]);
  const lastSortTime = useRef(0);

  const now = Date.now();
  if (now - lastSortTime.current > 30000 || sortedRef.current.length === 0) {
    sortedRef.current = [...stocks].sort((a, b) => {
      if (sortBy === "momentum") {
        const volDiff = Math.abs(b.rel_vol_5min) - Math.abs(a.rel_vol_5min);
        if (volDiff !== 0) return volDiff;
        return b.change_percent - a.change_percent;  // ← fjern Math.abs
      }
      return b.change_percent - a.change_percent;
    }).slice(0, 20);
    lastSortTime.current = now;
  }

  const sorted = sortedRef.current.map(s => stocks.find(x => x.ticker === s.ticker) || s);

  function renderCell(stock: any, colId: string) {
    switch(colId) {
      case "price":     return <td key={colId}>${stock.price.toFixed(2)}</td>;
      case "change":    return <td key={colId} className={stock.change_percent >= 0 ? "positive" : "negative"}>{stock.change_percent >= 0 ? "+" : ""}{stock.change_percent.toFixed(2)}%</td>;
      case "volume":    return <td key={colId}>{(stock.volume / 1000000).toFixed(1)}M</td>;
      case "float":     return <td key={colId}>{stock.float}</td>;
      case "relvol":    return <td key={colId} className={stock.rel_vol_daily > 2 ? "highlight" : ""}>{stock.rel_vol_daily}x</td>;
      case "relvol5":   return <td key={colId} className={stock.rel_vol_5min > 3 ? "positive" : ""}>{stock.rel_vol_5min}x</td>;
      case "gap":       return <td key={colId} className={stock.gap_percent >= 0 ? "positive" : "negative"}>{stock.gap_percent >= 0 ? "+" : ""}{stock.gap_percent.toFixed(1)}%</td>;
      case "prevclose": return <td key={colId}>${(stock.price * (1 - stock.change_percent/100)).toFixed(2)}</td>;
      case "high":      return <td key={colId} className="positive">${(stock.price * 1.005).toFixed(2)}</td>;
      case "low":       return <td key={colId} className="negative">${(stock.price * 0.995).toFixed(2)}</td>;
      default:          return <td key={colId}>—</td>;
    }
  }
  const [isMarketClosed, setIsMarketClosed] = useState(false);
  useEffect(() => {
    function update() {
      const now = new Date();
      const day = now.getDay();
      const et  = new Date(now.toLocaleString("en-US", { timeZone: "America/New_York" }));
      const mins = et.getHours() * 60 + et.getMinutes();
      if (day === 0 || day === 6) { setIsMarketClosed(true); return; }
      if (mins < 240 || mins >= 1200) { setIsMarketClosed(true); return; }
      setIsMarketClosed(false);
    }
    update();
    const interval = setInterval(update, 30000);
    return () => clearInterval(interval);
  }, []);
  return (
    <div className="scanner-panel" style={{ position: "relative" }}>
      {isMarketClosed && (
        <div style={{
          position: "absolute",
          top: 0, left: 0, right: 0, bottom: 0,
          background: "rgba(0, 0, 0, 0.75)",
          zIndex: 10,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          textAlign: "center",
          padding: 20,
          pointerEvents: "none",
        }}>
          <div style={{
            fontSize: 16,
            fontWeight: 600,
            color: "var(--text-primary)",
            marginBottom: 6,
          }}>
            🌙 Marked lukket
          </div>
          <div style={{
            fontSize: 12,
            color: "var(--text-muted)",
          }}>
            Scanner aktiveres når NYSE/NASDAQ åbner
          </div>
          <div style={{
            fontSize: 11,
            color: "var(--text-muted)",
            marginTop: 4,
          }}>
            (15:30 dansk tid på hverdage)
          </div>
        </div>
      )}
      <div className="scanner-scroll scanner-scroll-both">
        <table className="scanner-table">
          <thead>
            <tr>
              <th>Symbol</th>
              {visibleCols.map(colId => { const col = ALL_COLUMNS.find(c => c.id === colId); return <th key={colId}>{col?.label}</th>; })}
            </tr>
          </thead>
          <tbody>
            {sorted.map(stock => (
              <tr key={stock.ticker}
                className={[stock.change_percent >= 0 ? "row-up" : "row-down", stock.ticker === selectedTicker ? "row-selected" : ""].join(" ")}
                onClick={() => onSelectTicker(stock.ticker)}
              >
                <TickerCell ticker={stock.ticker} />
                {visibleCols.map(colId => renderCell(stock, colId))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── Watchlist Panel ───────────────────────────────────────────
function WatchlistPanel({ stocks, selectedTicker, onSelectTicker, watchlist, onAddTicker, onRemoveTicker, onRequestOrder }: {
  stocks: any[]; selectedTicker: string; onSelectTicker: (ticker: string) => void;
  watchlist: string[]; onAddTicker: (ticker: string) => void; onRemoveTicker: (ticker: string) => void;
  onRequestOrder: (action: "BUY" | "SELL", ticker: string, shares: number, price: number) => void;
}) {
  // Mængde pr. ticker — default 100 (Ross Cameron standard)
  const [orderShares, setOrderShares] = useState<Record<string, string>>({});

  function getShares(ticker: string): string {
    return orderShares[ticker] ?? "100";
  }

  function setShares(ticker: string, value: string) {
    // Tillad kun cifre (max 6 = op til 999.999)
    const clean = value.replace(/\D/g, "").slice(0, 6);
    setOrderShares(prev => ({ ...prev, [ticker]: clean }));
  }

  function handleOrder(action: "BUY" | "SELL", stock: any) {
    const shares = parseInt(getShares(stock.ticker), 10);
    if (!shares || shares <= 0) {
      alert(`Angiv en gyldig mængde for ${stock.ticker}`);
      return;
    }
    if (!stock.price || stock.price <= 0) {
      alert(
        `Ingen live pris for ${stock.ticker} — kan ikke sende ordre.\n\n` +
        `Mulige årsager:\n` +
        `• Markedet er lukket (priser opdateres når NYSE/NASDAQ åbner)\n` +
        `• Tickeren findes ikke eller er forkert stavet\n` +
        `• IBKR-feedet er ikke aktivt for denne aktie`
      );
      return;
    }
    onRequestOrder(action, stock.ticker, shares, stock.price);
  }
  const [input, setInput] = useState("");
  const [error, setError] = useState("");
  const [visibleCols, setVisibleCols] = useState<string[]>(() => {
    const saved = localStorage.getItem("columns_watchlist");
    return saved ? JSON.parse(saved) : DEFAULT_WATCHLIST_COLUMNS;
  });
  useEffect(() => {
    function onStorage(e: StorageEvent) {
      if (e.key === "columns_watchlist" && e.newValue) setVisibleCols(JSON.parse(e.newValue));
    }
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  function handleAdd() {
    const t = input.trim().toUpperCase();
    if (!t) return;
    if (watchlist.includes(t)) { setError(`${t} er allerede på listen`); return; }
    onAddTicker(t); setInput(""); setError("");
  }

    // Vis alle watchlist-tickers, også dem uden live data
    const watchedStocks = watchlist.map(ticker => {
      const live = stocks.find(s => s.ticker === ticker);
      return live ?? {
        ticker,
        price: 0,
        change_percent: 0,
        volume: 0,
        float: "—",
        rel_vol_daily: 0,
        gap_percent: 0,
      };
    });

  function renderCell(stock: any, colId: string) {
    switch(colId) {
      case "price":   return <td key={colId}>${stock.price.toFixed(2)}</td>;
      case "change":  return <td key={colId} className={stock.change_percent >= 0 ? "positive" : "negative"}>{stock.change_percent >= 0 ? "+" : ""}{stock.change_percent.toFixed(2)}%</td>;
      case "volume":  return <td key={colId}>{(stock.volume / 1000000).toFixed(1)}M</td>;
      case "float":   return <td key={colId}>{stock.float}</td>;
      case "relvol":  return <td key={colId}>{stock.rel_vol_daily}x</td>;
      case "gap":     return <td key={colId} className={stock.gap_percent >= 0 ? "positive" : "negative"}>{stock.gap_percent >= 0 ? "+" : ""}{stock.gap_percent.toFixed(1)}%</td>;
      default:        return <td key={colId}>—</td>;
    }
  }

  return (
    <div className="watchlist-container">
      <div className="watchlist-add">
        <input className="watchlist-input" type="text" placeholder="Tilføj ticker (tryk Enter)" value={input}
          onChange={e => { setInput(e.target.value.toUpperCase()); setError(""); }}
          onKeyDown={e => e.key === "Enter" && handleAdd()} maxLength={6} /> 
      </div>
      {error && <div className="watchlist-error">{error}</div>}
      <div className="watchlist-scroll">
        <table className="scanner-table">
          <thead>
            <tr>
              <th>Symbol</th>
              {visibleCols.map(colId => { const col = WATCHLIST_COLUMNS.find(c => c.id === colId); return <th key={colId}>{col?.label}</th>; })}
              <th style={{ textAlign: "center", width: 60 }}>Stk</th>
              <th style={{ textAlign: "center", width: 130 }}>Handel</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {watchedStocks.length === 0 && <tr><td colSpan={visibleCols.length + 4} className="watchlist-empty">Ingen aktier endnu</td></tr>}
            {watchedStocks.map(stock => (
              <tr key={stock.ticker}
                className={[stock.change_percent >= 0 ? "row-up" : "row-down", stock.ticker === selectedTicker ? "row-selected" : ""].join(" ")}
                onClick={() => onSelectTicker(stock.ticker)}
              >
                <td className="sym-cell">{stock.ticker}</td>
                {visibleCols.map(colId => renderCell(stock, colId))}
                <td onClick={e => e.stopPropagation()} style={{ textAlign: "center" }}>
                  <input
                    type="text"
                    inputMode="numeric"
                    value={getShares(stock.ticker)}
                    onChange={e => setShares(stock.ticker, e.target.value)}
                    onClick={e => e.stopPropagation()}
                    style={{
                      width: 50,
                      background: "var(--bg-input)",
                      border: "1px solid var(--border-default)",
                      borderRadius: 3,
                      color: "var(--text-primary)",
                      fontSize: 12,
                      padding: "3px 6px",
                      textAlign: "right",
                      fontFamily: "inherit",
                      outline: "none",
                    }}
                  />
                </td>
                <td onClick={e => e.stopPropagation()} style={{ textAlign: "center" }}>
                  <button
                    onClick={e => { e.stopPropagation(); handleOrder("BUY", stock); }}
                    title={`Køb ${getShares(stock.ticker)} ${stock.ticker} @ market`}
                    style={{
                      background: "var(--bull-muted)",
                      border: "1px solid var(--bull)",
                      color: "var(--bull)",
                      borderRadius: 3,
                      fontSize: 11,
                      fontWeight: 700,
                      padding: "3px 10px",
                      marginRight: 4,
                      cursor: "pointer",
                    }}
                  >
                    KØB
                  </button>
                  <button
                    onClick={e => { e.stopPropagation(); handleOrder("SELL", stock); }}
                    title={`Sælg ${getShares(stock.ticker)} ${stock.ticker} @ market`}
                    style={{
                      background: "var(--bear-muted)",
                      border: "1px solid var(--bear)",
                      color: "var(--bear)",
                      borderRadius: 3,
                      fontSize: 11,
                      fontWeight: 700,
                      padding: "3px 10px",
                      cursor: "pointer",
                    }}
                  >
                    SÆLG
                  </button>
                </td>
                <td><button className="watchlist-remove" onClick={e => { e.stopPropagation(); onRemoveTicker(stock.ticker); }}>✕</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function NewsTickerName({ ticker }: { ticker: string }) {
  const name = useTickerName(ticker);
  return (
    <span>
      <strong>{ticker}</strong>
      {name && <span style={{ marginLeft: 4, color: "var(--text-muted)", fontSize: 10 }}>· {name}</span>}
    </span>
  );
}

// ── News Room ─────────────────────────────────────────────────
function NewsRoom({ news, selectedTicker, onSelectTicker, watchlist }: {
  news: (NewsData & { isNew?: boolean })[]; selectedTicker: string;
  onSelectTicker: (ticker: string) => void; watchlist: string[];
}) {
  const [filter, setFilter] = useState<"all" | "watchlist">("all");
  const filtered = news.filter(n => filter === "all" || watchlist.includes(n.ticker));
  const grouped  = filter === "watchlist"
    ? watchlist.map(ticker => ({ ticker, items: filtered.filter(n => n.ticker === ticker) })).filter(g => g.items.length > 0)
    : null;

  // Tjek om der findes nogen forsinkede nyheder — hvis ja vis advarsel
  const hasDelayed = filtered.some(n => (n as any).delayed);

  function handleNewsClick(item: any) {
    if (item.url) {
      openUrl(item.url);
    } else {
      // Fallback — søgning hvis ingen direkte URL
      const query = encodeURIComponent(`${item.ticker} ${item.headline}`);
      openUrl(`https://www.google.com/search?q=${query}+stock+news`);
    }
  }

  return (
    <div className="newsroom-container">
      {/* ── Forsinket-data advarsel ── */}
      {hasDelayed && (
        <div style={{
          padding: "6px 10px",
          background: "rgba(245, 158, 11, 0.15)",
          borderBottom: "1px solid rgba(245, 158, 11, 0.5)",
          color: "#f59e0b",
          fontSize: 11,
          fontWeight: 600,
          display: "flex",
          alignItems: "center",
          gap: 6,
          flexShrink: 0,
        }}>
          <span style={{ fontSize: 13 }}>⚠</span>
          <span>
            FORSINKET DATA — Finnhub gratis tier · nyheder er typisk 15-30 min gamle ·
            ikke egnet til live momentum trading
          </span>
        </div>
      )}

      <div className="newsroom-toolbar">
        <div className="newsroom-filters">
          <button className={`news-filter-btn ${filter === "all" ? "active" : ""}`} onClick={() => setFilter("all")}>Alle</button>
          <button className={`news-filter-btn ${filter === "watchlist" ? "active" : ""}`} onClick={() => setFilter("watchlist")}>Watchlist</button>
        </div>
        <span className="newsroom-count">{filtered.length} nyheder</span>
      </div>
      <div className="newsroom-scroll">
        {grouped && (
          <div className="newsroom-grouped">
            {grouped.map(group => (
              <div key={group.ticker} className="newsroom-group">
                <div className={`newsroom-group-header ${group.ticker === selectedTicker ? "newsroom-group-selected" : ""}`} onClick={() => onSelectTicker(group.ticker)}>
                  <span className="newsroom-group-ticker">{group.ticker}</span>
                  <span className="newsroom-group-count">{group.items.length} nyheder</span>
                </div>
                <table className="newsroom-table">
                  <tbody>
                    {group.items.map(item => (
                      <tr 
                        key={item.id} 
                        className={[`news-row-${item.sentiment}`, item.isNew ? "news-row-new" : ""].join(" ")} 
                        onClick={() => onSelectTicker(item.ticker)}
                        onDoubleClick={() => handleNewsClick(item)}
                        style={{ cursor: "pointer" }}
                      >
                        <td className="news-time" style={{ width: "60px" }}>
                          {item.time}
                          {(item as any).delayed && <span style={{ marginLeft: 3, color: "#f59e0b", fontSize: 9 }} title="Forsinket data">⏱</span>}
                        </td>
                        <td className="news-headline">{item.headline}</td>
                        <td className="news-source" style={{ width: "100px" }}>{item.source}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ))}
          </div>
        )}
        {!grouped && (
          <table className="newsroom-table">
            <thead>
              <tr><th style={{ width: "60px" }}>Tid</th><th>Overskrift</th><th style={{ width: "70px" }}>Ticker</th><th style={{ width: "100px" }}>Kilde</th></tr>
            </thead>
            <tbody>
              {filtered.length === 0 && <tr><td colSpan={4} className="watchlist-empty">Ingen nyheder</td></tr>}
              {filtered.map(item => (
                <tr
                  key={item.id}
                  className={[`news-row-${item.sentiment}`, item.ticker === selectedTicker ? "row-selected" : "", item.isNew ? "news-row-new" : ""].join(" ")}
                  onClick={() => onSelectTicker(item.ticker)}
                  onDoubleClick={() => handleNewsClick(item)}
                  style={{ cursor: "pointer" }}
                >
                  <td className="news-time">
                    {item.time}
                    {(item as any).delayed && <span style={{ marginLeft: 3, color: "#f59e0b", fontSize: 9 }} title="Forsinket data">⏱</span>}
                  </td>
                  <td className="news-headline">{item.headline}</td>
                  <td className="news-ticker-cell">
                    <span className={`news-sentiment-dot sentiment-${item.sentiment}`}>●</span>
                    <NewsTickerName ticker={item.ticker} />
                  </td>
                  <td className="news-source">{item.source}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

// ── Konfigurator ──────────────────────────────────────────────
function Konfigurator({ onClose }: { onClose: () => void }) {
  const originals = useRef({
    cols1:         JSON.parse(localStorage.getItem("columns_scanner1")  || JSON.stringify(DEFAULT_COLUMNS)),
    cols2:         JSON.parse(localStorage.getItem("columns_scanner2")  || JSON.stringify(DEFAULT_COLUMNS)),
    colsWatchlist: JSON.parse(localStorage.getItem("columns_watchlist") || JSON.stringify(DEFAULT_WATCHLIST_COLUMNS)),
    colsLevel2:    JSON.parse(localStorage.getItem("columns_level2")    || JSON.stringify(DEFAULT_LEVEL2_COLUMNS)),
    colsTimeSales: JSON.parse(localStorage.getItem("columns_timesales") || JSON.stringify(DEFAULT_TIMESALES_COLUMNS)),
    fonts: FONT_WINDOW_TYPES.map(({ id }) => ({
      id,
      title:   loadFontSize(id, "title"),
      header:  loadFontSize(id, "header"),
      content: loadFontSize(id, "content"),
    })),
  });

  const [cols1,         setCols1]         = useState<string[]>(originals.current.cols1);
  const [cols2,         setCols2]         = useState<string[]>(originals.current.cols2);
  const [colsWatchlist, setColsWatchlist] = useState<string[]>(originals.current.colsWatchlist);
  const [colsLevel2,    setColsLevel2]    = useState<string[]>(originals.current.colsLevel2);
  const [colsTimeSales, setColsTimeSales] = useState<string[]>(originals.current.colsTimeSales);

  const [fonts, setFonts] = useState<Record<string, { title: number; header: number; content: number }>>(() => {
    const obj: Record<string, { title: number; header: number; content: number }> = {};
    FONT_WINDOW_TYPES.forEach(({ id }) => {
      obj[id] = { title: loadFontSize(id, "title"), header: loadFontSize(id, "header"), content: loadFontSize(id, "content") };
    });
    return obj;
  });

  useEffect(() => { localStorage.setItem("columns_scanner1",  JSON.stringify(cols1)); }, [cols1]);
  useEffect(() => { localStorage.setItem("columns_scanner2",  JSON.stringify(cols2)); }, [cols2]);
  useEffect(() => { localStorage.setItem("columns_watchlist", JSON.stringify(colsWatchlist)); }, [colsWatchlist]);
  useEffect(() => { localStorage.setItem("columns_level2",    JSON.stringify(colsLevel2)); }, [colsLevel2]);
  useEffect(() => { localStorage.setItem("columns_timesales", JSON.stringify(colsTimeSales)); }, [colsTimeSales]);

  // Anvend font-ændringer live
  useEffect(() => {
    FONT_WINDOW_TYPES.forEach(({ id }) => {
      const f = fonts[id];
      if (!f) return;
      (["title","header","content"] as const).forEach(part => {
        document.documentElement.style.setProperty(`--fs-${part}-${id}`, `${f[part]}px`);
        localStorage.setItem(getFontKey(id, part), String(f[part]));
      });
    });
  }, [fonts]);

  function setFont(typeId: string, part: "title" | "header" | "content", value: number) {
    setFonts(prev => ({ ...prev, [typeId]: { ...prev[typeId], [part]: value } }));
  }

  function handleCancel() {
    localStorage.setItem("columns_scanner1",  JSON.stringify(originals.current.cols1));
    localStorage.setItem("columns_scanner2",  JSON.stringify(originals.current.cols2));
    localStorage.setItem("columns_watchlist", JSON.stringify(originals.current.colsWatchlist));
    localStorage.setItem("columns_level2",    JSON.stringify(originals.current.colsLevel2));
    localStorage.setItem("columns_timesales", JSON.stringify(originals.current.colsTimeSales));
    originals.current.fonts.forEach(({ id, title, header, content }) => {
      document.documentElement.style.setProperty(`--fs-title-${id}`,   `${title}px`);
      document.documentElement.style.setProperty(`--fs-header-${id}`,  `${header}px`);
      document.documentElement.style.setProperty(`--fs-content-${id}`, `${content}px`);
      localStorage.setItem(getFontKey(id, "title"),   String(title));
      localStorage.setItem(getFontKey(id, "header"),  String(header));
      localStorage.setItem(getFontKey(id, "content"), String(content));
    });
    onClose();
  }

  function toggleCol(cols: string[], setCols: (c: string[]) => void, id: string) {
    setCols(cols.includes(id) ? cols.filter(c => c !== id) : [...cols, id]);
  }

  function ColSection({ title, columns, selected, setSelected }: {
    title: string; columns: { id: string; label: string }[]; selected: string[]; setSelected: (c: string[]) => void;
  }) {
    return (
      <div className="konfigurator-panel">
        <div className="konfigurator-panel-title">{title}</div>
        <div className="konfigurator-cols">
          <div className="konfigurator-col-fixed">
            <span className="col-fixed-label">☑ Symbol / Ticker</span>
            <span className="col-fixed-hint">(altid synlig)</span>
          </div>
          {columns.map(col => (
            <label key={col.id} className="konfigurator-col-item">
              <input type="checkbox" checked={selected.includes(col.id)} onChange={() => toggleCol(selected, setSelected, col.id)} />
              <span>{col.label}</span>
            </label>
          ))}
        </div>
      </div>
    );
  }

  function FontRow({ label, value, onChange, min, max }: { label: string; value: number; onChange: (v: number) => void; min: number; max: number }) {
    return (
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 5 }}>
        <span style={{ fontSize: 11, color: "var(--text-muted)", width: 130, flexShrink: 0 }}>{label}</span>
        <input type="range" min={min} max={max} step={1} value={value}
          onChange={e => onChange(parseInt(e.target.value))}
          style={{ flex: 1, accentColor: "var(--accent)" }} />
        <span style={{ fontSize: 11, color: "var(--text-primary)", width: 28, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{value}px</span>
      </div>
    );
  }

  return (
    <div className="konfigurator">
      <div className="konfigurator-header">
        <span className="konfigurator-title">⚙ Konfigurator</span>
        <div style={{ display: "flex", gap: "8px" }}>
          <button className="konfigurator-cancel" onClick={handleCancel}>✕ Annuller</button>
          <button className="konfigurator-close" onClick={onClose}>✓ Gem & Luk</button>
        </div>
      </div>
      <div className="konfigurator-body" style={{ overflowX: "auto" }}>

        {/* ── Fontstørrelser per vinduestype ── */}
        <div className="konfigurator-panel">
          <div className="konfigurator-panel-title">Fontstørrelser per vindue</div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))", gap: 12 }}>
            {FONT_WINDOW_TYPES.map(({ id, label }) => (
              <div key={id} style={{ background: "var(--bg-base)", borderRadius: 4, padding: "10px 14px", border: "1px solid var(--border-subtle)" }}>
                <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text-secondary)", marginBottom: 10, textTransform: "uppercase", letterSpacing: "0.5px" }}>
                  {label}
                </div>
                <FontRow label="Vindues-titel"        value={fonts[id]?.title   ?? FONT_DEFAULTS.title}   onChange={v => setFont(id, "title",   v)} min={10} max={24} />
                <FontRow label="Kolonne-overskrifter" value={fonts[id]?.header  ?? FONT_DEFAULTS.header}  onChange={v => setFont(id, "header",  v)} min={8}  max={18} />
                <FontRow label="Data-indhold"         value={fonts[id]?.content ?? FONT_DEFAULTS.content} onChange={v => setFont(id, "content", v)} min={9}  max={20} />
                {/* Live preview */}
                <div style={{ marginTop: 8, padding: "5px 8px", background: "var(--bg-elevated)", borderRadius: 3, display: "flex", gap: 14, alignItems: "baseline", flexWrap: "wrap" }}>
                  <span style={{ fontSize: fonts[id]?.title   ?? FONT_DEFAULTS.title,   color: "var(--bear)", fontWeight: 700, textTransform: "uppercase" }}>AAPL</span>
                  <span style={{ fontSize: fonts[id]?.header  ?? FONT_DEFAULTS.header,  color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.4px" }}>PRICE</span>
                  <span style={{ fontSize: fonts[id]?.content ?? FONT_DEFAULTS.content, color: "var(--bull)", fontWeight: 600 }}>$187.42 +4.2%</span>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="konfigurator-divider" />
        <ColSection title="Small Cap Scanner — Kolonner" columns={ALL_COLUMNS} selected={cols1} setSelected={setCols1} />
        <div className="konfigurator-divider" />
        <ColSection title="Top Gainers — Kolonner"       columns={ALL_COLUMNS} selected={cols2} setSelected={setCols2} />
        <div className="konfigurator-divider" />
        <ColSection title="Watchlist — Kolonner"         columns={WATCHLIST_COLUMNS} selected={colsWatchlist} setSelected={setColsWatchlist} />
        <div className="konfigurator-divider" />
        <ColSection title="Level 2 — Kolonner"           columns={LEVEL2_COLUMNS}    selected={colsLevel2}    setSelected={setColsLevel2} />
        <div className="konfigurator-divider" />
        <ColSection title="Time & Sales — Kolonner"      columns={TIMESALES_COLUMNS} selected={colsTimeSales} setSelected={setColsTimeSales} />
      </div>
    </div>
  );
}

// ── Level 2 Panel ─────────────────────────────────────────────
function Level2Panel({ ticker }: { ticker: string }) {
  const tickerName = useTickerName(ticker);
  const displayTicker = tickerName ? `${ticker} · ${tickerName}` : ticker;
  const [bids, setBids] = useState<Array<{ price: number; size: number; marketMaker: string }>>([]);
  const [asks, setAsks] = useState<Array<{ price: number; size: number; marketMaker: string }>>([]);
  const [status, setStatus] = useState<"connecting" | "ready" | "error" | "closed">("connecting");
  const [errorMsg, setErrorMsg] = useState<string>("");
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (!ticker) return;

    setBids([]);
    setAsks([]);
    setStatus("connecting");
    setErrorMsg("");

    let reconnectTimer: number | null = null;
    let isUnmounted = false;

    function connect() {
      if (isUnmounted) return;

      const ws = new WebSocket(`ws://127.0.0.1:8000/ws/level2/${ticker}`);
      wsRef.current = ws;

      ws.onmessage = (ev) => {
        try {
          const data = JSON.parse(ev.data);
          if (data.type === "ready") {
            setStatus("ready");
          } else if (data.type === "depth") {
            setBids(data.bids.filter((b: any) => b.price > 0));
            setAsks(data.asks.filter((a: any) => a.price > 0));
          } else if (data.type === "error") {
            setStatus("error");
            setErrorMsg(data.msg || "Ukendt fejl");
          }
        } catch (e) {
          // Ignorer corrupted
        }
      };

      ws.onerror = () => {
        // onclose følger efter
      };

      ws.onclose = () => {
        if (isUnmounted) return;
        setStatus("connecting");
        setErrorMsg("Genforbinder...");
        reconnectTimer = window.setTimeout(connect, 3000);
      };
    }

    connect();

    return () => {
      isUnmounted = true;
      if (reconnectTimer) window.clearTimeout(reconnectTimer);
      wsRef.current?.close();
      wsRef.current = null;
    };
  }, [displayTicker]);

  // ── Render ────────────────────────────────────────────
  if (status === "error") {
    return (
      <div className="level2-panel">
        <div className="level2-header">Level 2 — {displayTicker}</div>
        <div style={{ padding: 24, textAlign: "center", color: "var(--text-secondary)" }}>
          <div style={{ fontSize: 32, marginBottom: 12 }}>⚠️</div>
          <div style={{ fontSize: 13 }}>{errorMsg}</div>
        </div>
      </div>
    );
  }

  if (status === "connecting") {
    return (
      <div className="level2-panel">
        <div style={{ padding: 24, textAlign: "center", color: "var(--text-muted)" }}>
          {errorMsg || `Forbinder til ${displayTicker}...`}
        </div>
      </div>
    );
  }

  // Beregn metrikker
  const bestBid = bids.length > 0 ? bids[0].price : 0;
  const bestAsk = asks.length > 0 ? asks[0].price : 0;
  const spread  = bestAsk - bestBid;

  const totalBidSize = bids.reduce((s, b) => s + b.size, 0);
  const totalAskSize = asks.reduce((s, a) => s + a.size, 0);
  const totalSize    = totalBidSize + totalAskSize;
  const bidPct       = totalSize > 0 ? (totalBidSize / totalSize) * 100 : 50;
  const askPct       = 100 - bidPct;

  return (
    <div className="level2-panel">
      <div className="level2-header">
        Level 2 — {displayTicker}
        {bestBid > 0 && bestAsk > 0 && (
          <span style={{ float: "right", fontSize: 12, color: "var(--text-primary)", fontWeight: 600 }}>
            Spread: ${spread.toFixed(2)}
          </span>
        )}
      </div>

      {/* ── Bid/Ask balance-søjle ─────────────────────── */}
      {totalSize > 0 && (
        <div style={{
          padding: "8px 12px",
          background: "var(--bg-elevated)",
          borderBottom: "1px solid var(--border-subtle)",
        }}>
          <div style={{
            display: "flex",
            justifyContent: "space-between",
            fontSize: 11,
            color: "var(--text-secondary)",
            marginBottom: 4,
          }}>
            <span style={{ color: "var(--bull)" }}>
              {totalBidSize.toLocaleString("da-DK")} bids ({bidPct.toFixed(0)}%)
            </span>
            <span style={{ color: "var(--bear)" }}>
              ({askPct.toFixed(0)}%) {totalAskSize.toLocaleString("da-DK")} asks
            </span>
          </div>
          <div style={{
            display: "flex",
            height: 6,
            borderRadius: 3,
            overflow: "hidden",
            background: "var(--bg-base)",
          }}>
            <div style={{
              width: `${bidPct}%`,
              background: "var(--bull)",
              transition: "width 200ms ease-out",
            }} />
            <div style={{
              width: `${askPct}%`,
              background: "var(--bear)",
              transition: "width 200ms ease-out",
            }} />
          </div>
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, padding: 8 }}>
        {/* BIDS */}
        <div>
          <table className="level2-table">
            <thead>
              <tr>
                <th>MM</th>
                <th>Pris</th>
                <th>Stk</th>
              </tr>
            </thead>
            <tbody>
              {bids.length === 0 ? (
                <tr><td colSpan={3} style={{ textAlign: "center", color: "var(--text-muted)", fontStyle: "italic", padding: 8 }}>—</td></tr>
              ) : (
                bids.map((b, i) => (
                  <tr key={`bid-${i}`} style={{ background: i === 0 ? "rgba(74, 222, 128, 0.08)" : undefined }}>
                    <td style={{ color: "var(--text-secondary)" }}>{b.marketMaker}</td>
                    <td style={{ color: "var(--bull)" }}>${b.price.toFixed(2)}</td>
                    <td>{b.size.toLocaleString("da-DK")}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        {/* ASKS */}
        <div>
          <table className="level2-table">
            <thead>
              <tr>
                <th>MM</th>
                <th>Pris</th>
                <th>Stk</th>
              </tr>
            </thead>
            <tbody>
              {asks.length === 0 ? (
                <tr><td colSpan={3} style={{ textAlign: "center", color: "var(--text-muted)", fontStyle: "italic", padding: 8 }}>—</td></tr>
              ) : (
                asks.map((a, i) => (
                  <tr key={`ask-${i}`} style={{ background: i === 0 ? "rgba(248, 113, 113, 0.08)" : undefined }}>
                    <td style={{ color: "var(--text-secondary)" }}>{a.marketMaker}</td>
                    <td style={{ color: "var(--bear)" }}>${a.price.toFixed(2)}</td>
                    <td>{a.size.toLocaleString("da-DK")}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

// ── Time & Sales Panel ────────────────────────────────────────
function TimeSalesPanel({ ticker }: { ticker: string }) {
  const tickerName = useTickerName(ticker);
  const displayTicker = tickerName ? `${ticker} · ${tickerName}` : ticker;
  const [ticks, setTicks] = useState<Array<{
    time: string;
    timeMs: number;
    price: number;
    size: number;
    direction: "up" | "down" | "neutral";
    id: number;
  }>>([]);
  const [status, setStatus] = useState<"connecting" | "ready" | "error" | "closed">("connecting");
  const [errorMsg, setErrorMsg] = useState<string>("");
  const [now, setNow] = useState(Date.now());
  const wsRef = useRef<WebSocket | null>(null);
  const tickIdRef = useRef(0);

  useEffect(() => {
    if (!displayTicker) return;

    setTicks([]);
    setStatus("connecting");
    setErrorMsg("");

    let reconnectTimer: number | null = null;
    let isUnmounted = false;

    function connect() {
      if (isUnmounted) return;

      const ws = new WebSocket(`ws://127.0.0.1:8000/ws/timesales/${ticker}`);
      wsRef.current = ws;

      ws.onmessage = (ev) => {
        try {
          const data = JSON.parse(ev.data);
          if (data.type === "ready") {
            setStatus("ready");
          } else if (data.type === "tick") {
            const id = tickIdRef.current++;
            setTicks(prev => {
              const next = [{
                id,
                time: data.time?.slice(11, 19) ?? "—",
                timeMs: Date.now(),
                price: data.price,
                size: data.size,
                direction: data.direction,
              }, ...prev];
              return next.slice(0, 200);
            });
          } else if (data.type === "error") {
            setStatus("error");
            setErrorMsg(data.msg || "Ukendt fejl");
          }
        } catch (e) {
          // Ignorer corrupted messages
        }
      };

      ws.onerror = () => {
        // onclose følger efter — håndter reconnect der
      };

      ws.onclose = () => {
        if (isUnmounted) return;
        setStatus("connecting");  // Vis "Genforbinder..." i stedet for "Lukket"
        setErrorMsg("Genforbinder...");
        reconnectTimer = window.setTimeout(connect, 3000);
      };
    }

    connect();

    return () => {
      isUnmounted = true;
      if (reconnectTimer) window.clearTimeout(reconnectTimer);
      wsRef.current?.close();
      wsRef.current = null;
    };
  }, [displayTicker]);

  // Tick "now" hvert sekund så 60-sek vinduet ruller
  useEffect(() => {
    const interval = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(interval);
  }, []);

  // ── Beregn statistik for sidste 60 sekunder ──────────────
  const cutoff      = now - 60_000;
  const recentTicks = ticks.filter(t => t.timeMs >= cutoff);
  const upTicks     = recentTicks.filter(t => t.direction === "up");
  const downTicks   = recentTicks.filter(t => t.direction === "down");
  const neutralTicks = recentTicks.filter(t => t.direction === "neutral");

  const upPct      = recentTicks.length ? Math.round(upTicks.length / recentTicks.length * 100) : 0;
  const downPct    = recentTicks.length ? Math.round(downTicks.length / recentTicks.length * 100) : 0;
  const neutralPct = Math.max(0, 100 - upPct - downPct);

  const buyFlow  = upTicks.reduce((s, t) => s + t.price * t.size, 0);
  const sellFlow = downTicks.reduce((s, t) => s + t.price * t.size, 0);
  const fmtFlow  = (v: number) => v >= 1000
    ? `$${(v/1000).toFixed(1)}k`
    : `$${Math.round(v)}`;

  // ── Render ────────────────────────────────────────────
  if (status === "error") {
    return (
      <div className="timesales-panel">
        <div className="timesales-header">Time & Sales — {displayTicker}</div>
        <div style={{ padding: 24, textAlign: "center", color: "var(--text-secondary)" }}>
          <div style={{ fontSize: 32, marginBottom: 12 }}>⚠️</div>
          <div style={{ fontSize: 13 }}>{errorMsg}</div>
        </div>
      </div>
    );
  }

  if (status === "connecting") {
    return (
      <div className="timesales-panel">
        <div style={{ padding: 24, textAlign: "center", color: "var(--text-muted)" }}>
          {errorMsg || `Forbinder til ${displayTicker}...`}
        </div>
      </div>
    );
  }

  return (
    <div className="timesales-panel">
      
      {/* ── Statusbjælke (sidste 60 sek) ───────────────── */}
      <div style={{
        padding: "8px 12px",
        background: "var(--bg-elevated)",
        borderBottom: "1px solid var(--border-subtle)",
        fontSize: 11,
        color: "var(--text-secondary)",
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        flexWrap: "wrap",
        gap: 12
      }}>
        <span>
          <strong style={{ color: "var(--text-primary)" }}>{ticks.length}</strong> ticks · sidste 60s: <strong style={{ color: "var(--text-primary)" }}>{recentTicks.length}</strong>
        </span>
        <span>
          <span style={{ color: "var(--bull)" }}>▲ {upPct}%</span>
          {" / "}
          <span style={{ color: "var(--bear)" }}>▼ {downPct}%</span>
          {" / "}
          <span style={{ color: "var(--text-muted)" }}>─ {neutralPct}%</span>
        </span>
        <span>
          <span style={{ color: "var(--bull)" }}>+{fmtFlow(buyFlow)}</span>
          {" / "}
          <span style={{ color: "var(--bear)" }}>−{fmtFlow(sellFlow)}</span>
        </span>
      </div>

      <div className="timesales-scroll">
        <table className="timesales-table">
          <thead>
            <tr>
              <th>Tid</th>
              <th style={{ textAlign: "right" }}>Pris</th>
              <th style={{ textAlign: "right" }}>Stk</th>
            </tr>
          </thead>
          <tbody>
            {ticks.length === 0 ? (
              <tr>
                <td colSpan={3} style={{ textAlign: "center", color: "var(--text-muted)", padding: 16, fontStyle: "italic" }}>
                  Venter på ticks...
                </td>
              </tr>
            ) : (
              ticks.map(t => (
                <tr key={t.id} className={
                  t.direction === "up"   ? "row-up"   :
                  t.direction === "down" ? "row-down" : ""
                }>
                  <td>{t.time}</td>
                  <td style={{ textAlign: "right" }}>${t.price.toFixed(2)}</td>
                  <td style={{ textAlign: "right" }}>{t.size.toLocaleString("da-DK")}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
// ── Window Renderer ───────────────────────────────────────────
function renderWindowContent(id: WindowId, props: {
  stocks: any[]; selectedTicker: string; onSelectTicker: (t: string) => void;
  watchlist: string[]; onAddTicker: (t: string) => void; onRemoveTicker: (t: string) => void;
  news: any[]; portfolio: any; buyStock: any; sellStock: any; resetPortfolio: any; currentPrice: number;
  alerts: any[]; alertThreshold: number; onThresholdChange: (v: number) => void;
  onAddWindow: (id: WindowId) => void; onCloseWindow: (id: WindowId) => void;
  onRequestOrder: (action: "BUY" | "SELL", ticker: string, shares: number, price: number) => void;
}) {
  switch(id) {
    case "scanner1":    return <ScannerTable stocks={props.stocks} sortBy="momentum" selectedTicker={props.selectedTicker} onSelectTicker={props.onSelectTicker} scannerId="scanner1" />;
    case "scanner2":    return <ScannerTable stocks={props.stocks} sortBy="gainers"  selectedTicker={props.selectedTicker} onSelectTicker={props.onSelectTicker} scannerId="scanner2" />;
    case "watchlist":   return <WatchlistPanel stocks={props.stocks} selectedTicker={props.selectedTicker} onSelectTicker={props.onSelectTicker} watchlist={props.watchlist} onAddTicker={props.onAddTicker} onRemoveTicker={props.onRemoveTicker} onRequestOrder={props.onRequestOrder} />;
    case "newsroom":    return <NewsRoom news={props.news} selectedTicker={props.selectedTicker} onSelectTicker={props.onSelectTicker} watchlist={props.watchlist} />;
    case "papertrading":return <PaperTradingPanel portfolio={props.portfolio} selectedTicker={props.selectedTicker} currentPrice={props.currentPrice} onBuy={props.buyStock} onSell={props.sellStock} onReset={props.resetPortfolio} onSelectTicker={props.onSelectTicker} />;
    case "chart1min":   return <TradingViewWidget ticker={props.selectedTicker} timeframe="1 min" />;
    case "chart2min":   return <TradingViewWidget ticker={props.selectedTicker} timeframe="2 min" />;
    case "chart3min":   return <TradingViewWidget ticker={props.selectedTicker} timeframe="3 min" />;
    case "chart5min":   return <TradingViewWidget ticker={props.selectedTicker} timeframe="5 min" />;
    case "chart10min":  return <TradingViewWidget ticker={props.selectedTicker} timeframe="10 min" />;
    case "chart15min":  return <TradingViewWidget ticker={props.selectedTicker} timeframe="15 min" />;
    case "chart30min":  return <TradingViewWidget ticker={props.selectedTicker} timeframe="30 min" />;
    case "chart1time":  return <TradingViewWidget ticker={props.selectedTicker} timeframe="1 time" />;
    case "chart4time":  return <TradingViewWidget ticker={props.selectedTicker} timeframe="4 time" />;
    case "chartdaily":  return <TradingViewWidget ticker={props.selectedTicker} timeframe="Daily" />;
    case "chartweekly": return <TradingViewWidget ticker={props.selectedTicker} timeframe="Weekly" />;
    case "level2":      return <Level2Panel ticker={props.selectedTicker} />;
    case "timesales":   return <TimeSalesPanel ticker={props.selectedTicker} />;
    case "journal":     return <TradeJournal />;
    case "livealgo":    return <LiveAlgo />;
    case "marketoverview": return <MarketOverview />;
    case "account": return <AccountPanel onSelectTicker={props.onSelectTicker} />;
    case "orders":  return <OrdersWindow />;
    case "alerts":
      return <AlertsWindow
        alerts={props.alerts ?? []}
        selectedTicker={props.selectedTicker}
        onSelectTicker={props.onSelectTicker}
        watchlist={props.watchlist ?? []}
        alertThreshold={props.alertThreshold ?? 0.5}
        onThresholdChange={props.onThresholdChange}
      />;
    default:            return <div className="pt-empty">Ukendt vindue</div>;
  }
}

function getWindowTitle(id: WindowId, selectedTicker: string, stocks?: any[], tickerName?: string): string {
  const isHistorical = stocks && stocks.length > 0 && stocks[0]?.source === "historical";
  const scannerSuffix = isHistorical ? " ⚠ Hist." : "";
  const withName = tickerName ? `${selectedTicker} · ${tickerName}` : selectedTicker;

  const t: Partial<Record<WindowId, string>> = {
    scanner1:    `Small Cap Scanner${scannerSuffix}`,
    scanner2:    `Top Gainers${scannerSuffix}`,
    chart1min:   `${withName} — 1 min`,
    chart2min:   `${withName} — 2 min`,
    chart3min:   `${withName} — 3 min`,
    chart5min:   `${withName} — 5 min`,
    chart10min:  `${withName} — 10 min`,
    chart15min:  `${withName} — 15 min`,
    chart30min:  `${withName} — 30 min`,
    chart1time:  `${withName} — 1 time`,
    chart4time:  `${withName} — 4 time`,
    chartdaily:  `${withName} — Daily`,
    chartweekly: `${withName} — Weekly`,
    level2:      `Level 2 — ${withName}`,
    timesales:   `Time & Sales — ${withName}`,
  };
  return t[id] ?? WINDOW_LABELS[id];
}

function isChartWindow(id: WindowId): boolean { return id.startsWith("chart"); }

// ── Main App ──────────────────────────────────────────────────
function App() {
  const [layoutToast, setLayoutToast] = useState<string>("");
  const [activeView, setActiveView]         = useState<ActiveView>("scanners");
  const [selectedTicker, setSelectedTicker] = useState<string>(() => localStorage.getItem("selectedTicker") || "NVDA");
  const [alertThreshold, setAlertThreshold] = useState<number>(() => { const s = localStorage.getItem("alertThreshold"); return s ? parseFloat(s) : ALERT_THRESHOLD_DEFAULT; });
  const [watchlist, setWatchlist]           = useState<string[]>(() => { const s = localStorage.getItem("watchlist"); return s ? JSON.parse(s) : ["NVDA","TSLA","AAPL"]; });
  const [soundEnabled, setSoundEnabled]     = useState<boolean>(() => { const s = localStorage.getItem("soundEnabled"); return s !== null ? JSON.parse(s) : true; });
  const [layouts, setLayouts]               = useState<Layout[]>(() => loadLayouts(window.innerWidth, window.innerHeight));
  const [activeLayoutId, setActiveLayoutIdState] = useState<string>(() => getActiveLayoutId());

  useEffect(() => { localStorage.setItem("selectedTicker", selectedTicker); }, [selectedTicker]);
  useEffect(() => { localStorage.setItem("alertThreshold", alertThreshold.toString()); }, [alertThreshold]);
  useEffect(() => { localStorage.setItem("watchlist",      JSON.stringify(watchlist)); }, [watchlist]);
  useEffect(() => { localStorage.setItem("soundEnabled",   JSON.stringify(soundEnabled)); }, [soundEnabled]);

  // Indlæs gemte fontstørrelser ved opstart
  useEffect(() => { applyAllFonts(); }, []);

  const {
    stocksArray, alerts, news, portfolio, status,
    buyStock, sellStock, resetPortfolio,
    ibkrBuy, ibkrSell, lastOrderResult, clearLastOrderResult,
  } = useMarketData(alertThreshold, soundEnabled);
  const currentPrice = stocksArray.find(s => s.ticker === selectedTicker)?.price || 0;
  const activeLayout = layouts.find(l => l.id === activeLayoutId);

  // ── Bekræftelses-dialog state for manuelle IBKR-ordrer ──────
  const [orderConfirm, setOrderConfirm] = useState<{
    action: "BUY" | "SELL"; ticker: string; shares: number; price: number;
  } | null>(null); 
  const selectedTickerName = useTickerName(selectedTicker);
  
  function handleLoadLayout(id: string) { setActiveLayoutId(id); setActiveLayoutIdState(id); }

  function handleSaveLayout(name: string) {
    if (name.startsWith("__overwrite__")) {
      const id = name.replace("__overwrite__", "");
      const updated = layouts.map(l => l.id !== id ? l : { ...l, windows: activeLayout?.windows || l.windows });
      setLayouts(updated); saveLayouts(updated);
      const layoutName = layouts.find(l => l.id === id)?.name || "Layout";
      setLayoutToast(`✓ "${layoutName}" opdateret`);
      setTimeout(() => setLayoutToast(""), 2000);
      return;
    }
    const newLayout = saveCurrentAsLayout(name, activeLayout?.windows || [], window.innerWidth, window.innerHeight);
    setLayouts(loadLayouts(window.innerWidth, window.innerHeight));
    handleLoadLayout(newLayout.id);
    setLayoutToast(`✓ "${name}" gemt`);
    setTimeout(() => setLayoutToast(""), 2000);
  }

  function handleDeleteLayout(id: string) {
    const updated = deleteLayout(id, window.innerWidth, window.innerHeight);
    setLayouts(updated);
    if (activeLayoutId === id) handleLoadLayout(updated[0]?.id || "ibens-orb");
  }

  function autoArrange() {
    if (!activeLayout) return;
    const gap = 6, topH = 62;
    const w = window.innerWidth, h = window.innerHeight - topH;
    const open = activeLayout.windows.filter(win => !win.closed);
    if (open.length === 0) return;
    const cols = Math.ceil(Math.sqrt(open.length));
    const rows = Math.ceil(open.length / cols);
    const winW = Math.floor((w - gap * (cols + 1)) / cols);
    const winH = Math.floor((h - gap * (rows + 1)) / rows);
    const updated = layouts.map(l => {
      if (l.id !== activeLayoutId) return l;
      let idx = 0;
      return { ...l, windows: l.windows.map(win => {
        if (win.closed) return win;
        const col = idx % cols, row = Math.floor(idx / cols); idx++;
        return { ...win, x: gap + col*(winW+gap), y: gap + row*(winH+gap), width: winW, height: winH, minimized: false, maximized: false };
      })};
    });
    setLayouts(updated); saveLayouts(updated);
  }

  function handleAddWindow(id: WindowId) {
    const w = window.innerWidth - 200, h = window.innerHeight - 100;
    const existing = activeLayout?.windows.find(win => win.id === id);
    if (existing) { updateWindowState(id, { closed: false, minimized: false }); return; }
    const newWindow: WindowConfig = { id, x: Math.floor(w/4), y: Math.floor(h/4), width: Math.floor(w/2), height: Math.floor(h/2), minimized: false, maximized: false, closed: false };
    const updated = layouts.map(l => l.id !== activeLayoutId ? l : { ...l, windows: [...l.windows, newWindow] });
    setLayouts(updated); saveLayouts(updated);
  }

  function updateWindowState(id: WindowId, state: Partial<WindowConfig>) {
    const updated = layouts.map(l => l.id !== activeLayoutId ? l : { ...l, windows: l.windows.map(w => w.id === id ? { ...w, ...state } : w) });
    setLayouts(updated); saveLayouts(updated);
  }

  const windowProps = {
    stocks: stocksArray, selectedTicker, onSelectTicker: setSelectedTicker, watchlist,
    onAddTicker: (t: string) => setWatchlist(w => [...w, t]),
    onRemoveTicker: (t: string) => setWatchlist(w => w.filter(x => x !== t)),
    news, portfolio, buyStock, sellStock, resetPortfolio, currentPrice,
    alerts, alertThreshold, onThresholdChange: setAlertThreshold,
    onAddWindow:   handleAddWindow,
    onCloseWindow: (id: WindowId) => updateWindowState(id, { closed: true }),
    onRequestOrder: (action: "BUY" | "SELL", ticker: string, shares: number, price: number) => {
      setOrderConfirm({ action, ticker, shares, price });
    },
  };

  return (
    <div className="app">
      {/* Top bar — kun status, INGEN nyhedsbånd */}
      <div className="top-bar" style={{ justifyContent: "flex-end" }}>
        <div className="status-bar">
          <ConnectionStatus status={status} />
          <MarketStatus />
          <AlertCounter count={alerts.length} />
          <Clock />
        </div>
      </div>
      {layoutToast && (
        <div style={{
          position: "fixed",
          top: 70,
          left: "50%",
          transform: "translateX(-50%)",
          background: "var(--bg-elevated, #2a2a2a)",
          border: "1px solid var(--border-subtle, #444)",
          color: "var(--bull, #4ade80)",
          padding: "10px 24px",
          borderRadius: 6,
          fontSize: 14,
          fontWeight: 600,
          boxShadow: "0 4px 12px rgba(0,0,0,0.4)",
          zIndex: 10000,
          pointerEvents: "none",
        }}>
          {layoutToast}
        </div>
      )}
      <Menubar
        activeView={activeView} onViewChange={setActiveView}
        layouts={layouts} activeLayoutId={activeLayoutId}
        onLoadLayout={handleLoadLayout} onSaveLayout={handleSaveLayout} onDeleteLayout={handleDeleteLayout}
        onAutoArrange={autoArrange} soundEnabled={soundEnabled} onToggleSound={() => setSoundEnabled(s => !s)}
        onAddWindow={handleAddWindow}
        activeWindowIds={activeLayout?.windows.filter(w => !w.closed).map(w => w.id as WindowId) || []}
      />

      <div className="workspace">
        <div className="desktop-area">
          {activeView === "konfigurator" ? (
            <Konfigurator onClose={() => setActiveView("scanners")} />
          ) : (
            <>
              {activeLayout?.windows.filter(w => !w.closed).map(win => (
                <FloatingWindow
                  key={win.id} id={win.id}
                  title={getWindowTitle(win.id as WindowId, selectedTicker, stocksArray, selectedTickerName)}
                  defaultState={win}
                  onClose={() => updateWindowState(win.id as WindowId, { closed: true })}
                  tradingViewTicker={isChartWindow(win.id as WindowId) ? selectedTicker : undefined}
                  onStateChange={(state) => updateWindowState(win.id as WindowId, state)}
                  windowType={getWindowType(win.id as WindowId)}
                >
                  {renderWindowContent(win.id as WindowId, windowProps)}
                </FloatingWindow>
              ))}
            </>
          )}
        </div>
      </div>

      {/* ── Bekræftelses-dialog for manuelle IBKR-ordrer ── */}
      {orderConfirm && (
        <div style={{
          position: "fixed",
          top: 70,
          left: "50%",
          transform: "translateX(-50%)",
          background: "var(--bg-elevated)",
          border: `2px solid ${orderConfirm.action === "BUY" ? "var(--bull)" : "var(--bear)"}`,
          borderRadius: 6,
          padding: "14px 24px",
          fontSize: 14,
          fontWeight: 600,
          boxShadow: "0 4px 20px rgba(0,0,0,0.5)",
          zIndex: 10001,
          display: "flex",
          alignItems: "center",
          gap: 16,
        }}>
          <span style={{ color: "var(--text-primary)" }}>
            Bekræft <span style={{ color: orderConfirm.action === "BUY" ? "var(--bull)" : "var(--bear)" }}>
              {orderConfirm.action === "BUY" ? "KØB" : "SÆLG"}
            </span> {orderConfirm.shares} {orderConfirm.ticker} @ ${orderConfirm.price.toFixed(2)}
          </span>
          <button
            onClick={() => {
              if (orderConfirm.action === "BUY") ibkrBuy(orderConfirm.ticker, orderConfirm.shares);
              else                                ibkrSell(orderConfirm.ticker, orderConfirm.shares);
              setOrderConfirm(null);
            }}
            style={{
              background: orderConfirm.action === "BUY" ? "var(--bull)" : "var(--bear)",
              color: "#000",
              border: "none",
              padding: "6px 16px",
              borderRadius: 3,
              fontWeight: 700,
              fontSize: 13,
              cursor: "pointer",
            }}
          >
            Bekræft
          </button>
          <button
            onClick={() => setOrderConfirm(null)}
            style={{
              background: "var(--bg-input)",
              color: "var(--text-secondary)",
              border: "1px solid var(--border-default)",
              padding: "6px 16px",
              borderRadius: 3,
              fontSize: 13,
              cursor: "pointer",
            }}
          >
            Annuller
          </button>
        </div>
      )}

      {/* ── Resultat-toast efter ordre ── */}
      {lastOrderResult && (
        <OrderResultToast result={lastOrderResult} onClose={clearLastOrderResult} />
      )}
    </div>
  );
}

// ── Toast der viser resultatet af en IBKR-ordre — auto-dismiss efter 4 sek
function OrderResultToast({ result, onClose }: { result: any; onClose: () => void }) {
  useEffect(() => {
    const t = setTimeout(onClose, 4000);
    return () => clearTimeout(t);
  }, [result, onClose]);

  const success = result.success;
  const color = success ? "var(--bull)" : "var(--bear)";
  const text = success
    ? `✓ ${result.action} ${result.shares} ${result.ticker} — ${result.status}${result.filled ? ` (fyldt: ${result.filled} @ $${result.avg_fill?.toFixed(2)})` : ""}`
    : `✗ ${result.action} ${result.ticker} fejlede: ${result.error}`;

  return (
    <div style={{
      position: "fixed",
      bottom: 30,
      left: "50%",
      transform: "translateX(-50%)",
      background: "var(--bg-elevated)",
      border: `1px solid ${color}`,
      color: color,
      padding: "10px 20px",
      borderRadius: 4,
      fontSize: 13,
      fontWeight: 600,
      boxShadow: "0 4px 12px rgba(0,0,0,0.4)",
      zIndex: 10000,
      maxWidth: "80%",
    }}>
      {text}
    </div>
  );
}

export default App;
