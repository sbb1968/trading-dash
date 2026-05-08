import { useState, useEffect, useRef } from "react";
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
import { AlgoDemo } from "./AlgoDemo";
import { LiveAlgo } from "./LiveAlgo";
import { AlertsPanel } from "./Alertspanel";
import { AlgoHub } from "./AlgoHub"; 
import { MarketOverview } from "./MarketOverview";


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
  { id: "algodemo",  label: "Algotrading Demo" },
  { id: "livealgo",  label: "Live Algo" },
  { id: "alerts",    label: "Pris/Nyheds Alerts" },
  { id: "algohub", label: "Algo Hub" },
  { id: "marketoverview", label: "Markedsoverblik" },
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

  return (
    <div className="scanner-panel">
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
                <td className="sym-cell">{stock.ticker}</td>
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
function WatchlistPanel({ stocks, selectedTicker, onSelectTicker, watchlist, onAddTicker, onRemoveTicker }: {
  stocks: any[]; selectedTicker: string; onSelectTicker: (ticker: string) => void;
  watchlist: string[]; onAddTicker: (ticker: string) => void; onRemoveTicker: (ticker: string) => void;
}) {
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

  const watchedStocks = stocks.filter(s => watchlist.includes(s.ticker));

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
        <input className="watchlist-input" type="text" placeholder="Tilføj ticker" value={input}
          onChange={e => { setInput(e.target.value.toUpperCase()); setError(""); }}
          onKeyDown={e => e.key === "Enter" && handleAdd()} maxLength={6} />
        <button className="watchlist-btn" onClick={handleAdd}>+ Tilføj</button>
      </div>
      {error && <div className="watchlist-error">{error}</div>}
      <div className="watchlist-scroll">
        <table className="scanner-table">
          <thead>
            <tr>
              <th>Symbol</th>
              {visibleCols.map(colId => { const col = WATCHLIST_COLUMNS.find(c => c.id === colId); return <th key={colId}>{col?.label}</th>; })}
              <th></th>
            </tr>
          </thead>
          <tbody>
            {watchedStocks.length === 0 && <tr><td colSpan={visibleCols.length + 2} className="watchlist-empty">Ingen aktier endnu</td></tr>}
            {watchedStocks.map(stock => (
              <tr key={stock.ticker}
                className={[stock.change_percent >= 0 ? "row-up" : "row-down", stock.ticker === selectedTicker ? "row-selected" : ""].join(" ")}
                onClick={() => onSelectTicker(stock.ticker)}
              >
                <td className="sym-cell">{stock.ticker}</td>
                {visibleCols.map(colId => renderCell(stock, colId))}
                <td><button className="watchlist-remove" onClick={e => { e.stopPropagation(); onRemoveTicker(stock.ticker); }}>✕</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
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

  return (
    <div className="newsroom-container">
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
                      <tr key={item.id} className={[`news-row-${item.sentiment}`, item.isNew ? "news-row-new" : ""].join(" ")} onClick={() => onSelectTicker(item.ticker)}>
                        <td className="news-time" style={{ width: "60px" }}>{item.time}</td>
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
                <tr key={item.id}
                  className={[`news-row-${item.sentiment}`, item.ticker === selectedTicker ? "row-selected" : "", item.isNew ? "news-row-new" : ""].join(" ")}
                  onClick={() => onSelectTicker(item.ticker)}
                >
                  <td className="news-time">{item.time}</td>
                  <td className="news-headline">{item.headline}</td>
                  <td className="news-ticker-cell"><span className={`news-sentiment-dot sentiment-${item.sentiment}`}>●</span>{item.ticker}</td>
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
  return (
    <div className="level2-panel">
      <div className="level2-header">Level 2 — {ticker}</div>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", height: "100%", gap: 12, padding: 24 }}>
        <div style={{ fontSize: 32 }}>📊</div>
        <div style={{ fontSize: 14, fontWeight: 700, color: "var(--text-primary)", textAlign: "center" }}>
          Level 2 kræver IBKR Level 2 abonnement
        </div>
        <div style={{ fontSize: 12, color: "var(--text-secondary)", textAlign: "center", lineHeight: 1.6 }}>
          Level 2 data viser ordrebogen med bud og udbud fra alle market makers i realtid.
          Dette kræver et aktivt Level 2 / NASDAQ TotalView abonnement i IBKR.
        </div>
        <div style={{ fontSize: 11, color: "var(--text-muted)", textAlign: "center", marginTop: 8, padding: "8px 12px", background: "var(--bg-elevated)", borderRadius: 6, border: "1px solid var(--border-subtle)" }}>
          Aktivér via IBKR: Account Management → Market Data Subscriptions
        </div>
      </div>
    </div>
  );
}

// ── Time & Sales Panel ────────────────────────────────────────
function TimeSalesPanel({ ticker }: { ticker: string }) {
  const [ticks, setTicks] = useState<Array<{
    time: string;
    price: number;
    size: number;
    direction: "up" | "down" | "neutral";
    id: number;
  }>>([]);
  const [status, setStatus] = useState<"connecting" | "ready" | "error" | "closed">("connecting");
  const [errorMsg, setErrorMsg] = useState<string>("");
  const wsRef = useRef<WebSocket | null>(null);
  const tickIdRef = useRef(0);

  useEffect(() => {
    if (!ticker) return;

    setTicks([]);
    setStatus("connecting");
    setErrorMsg("");

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
              price: data.price,
              size: data.size,
              direction: data.direction,
            }, ...prev];
            // Behold kun de seneste 200 ticks for performance
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
      setStatus("error");
      setErrorMsg("Forbindelsesfejl — er backend kørende?");
    };

    ws.onclose = () => {
      setStatus(prev => prev === "error" ? prev : "closed");
    };

    return () => {
      ws.close();
      wsRef.current = null;
    };
  }, [ticker]);

  // ── Render ────────────────────────────────────────────
  if (status === "error") {
    return (
      <div className="timesales-panel">
        <div className="timesales-header">Time & Sales — {ticker}</div>
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
        <div className="timesales-header">Time & Sales — {ticker}</div>
        <div style={{ padding: 24, textAlign: "center", color: "var(--text-muted)" }}>
          Forbinder til {ticker}...
        </div>
      </div>
    );
  }

  return (
    <div className="timesales-panel">
      <div className="timesales-header">
        Time & Sales — {ticker}
        <span style={{ float: "right", fontSize: 11, color: "var(--text-muted)", fontWeight: 400 }}>
          {ticks.length} ticks
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
}) {
  switch(id) {
    case "scanner1":    return <ScannerTable stocks={props.stocks} sortBy="momentum" selectedTicker={props.selectedTicker} onSelectTicker={props.onSelectTicker} scannerId="scanner1" />;
    case "scanner2":    return <ScannerTable stocks={props.stocks} sortBy="gainers"  selectedTicker={props.selectedTicker} onSelectTicker={props.onSelectTicker} scannerId="scanner2" />;
    case "watchlist":   return <WatchlistPanel stocks={props.stocks} selectedTicker={props.selectedTicker} onSelectTicker={props.onSelectTicker} watchlist={props.watchlist} onAddTicker={props.onAddTicker} onRemoveTicker={props.onRemoveTicker} />;
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
    case "algodemo":    return <AlgoDemo />;
    case "livealgo":    return <LiveAlgo />;
    case "algohub":
      return (
        <AlgoHub
          onOpen={(id) => props.onAddWindow(id as WindowId)}
          onClose={() => props.onCloseWindow("algohub" as WindowId)}
        />
      );
    case "marketoverview": return <MarketOverview />;
    default:            return <div className="pt-empty">Ukendt vindue</div>;
  }
}

function getWindowTitle(id: WindowId, selectedTicker: string, stocks?: any[]): string {
  const isHistorical = stocks && stocks.length > 0 && stocks[0]?.source === "historical";
  const scannerSuffix = isHistorical ? " ⚠ Hist." : "";

  const t: Partial<Record<WindowId, string>> = {
    scanner1:    `Small Cap Scanner${scannerSuffix}`,
    scanner2:    `Top Gainers${scannerSuffix}`,
    chart1min:   `${selectedTicker} — 1 min`,
    chart2min:   `${selectedTicker} — 2 min`,
    chart3min:   `${selectedTicker} — 3 min`,
    chart5min:   `${selectedTicker} — 5 min`,
    chart10min:  `${selectedTicker} — 10 min`,
    chart15min:  `${selectedTicker} — 15 min`,
    chart30min:  `${selectedTicker} — 30 min`,
    chart1time:  `${selectedTicker} — 1 time`,
    chart4time:  `${selectedTicker} — 4 time`,
    chartdaily:  `${selectedTicker} — Daily`,
    chartweekly: `${selectedTicker} — Weekly`,
    level2:      `Level 2 — ${selectedTicker}`,
    timesales:   `Time & Sales — ${selectedTicker}`,
  };
  return t[id] ?? WINDOW_LABELS[id];
}

function isChartWindow(id: WindowId): boolean { return id.startsWith("chart"); }

// ── Main App ──────────────────────────────────────────────────
function App() {
  const [navWidth, setNavWidth] = useState<number>(() => {
    const saved = localStorage.getItem("nav_width");
    return saved ? parseInt(saved) : 280;
  });
  useEffect(() => {
    localStorage.setItem("nav_width", String(navWidth));
  }, [navWidth]);
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

  const { stocksArray, alerts, news, portfolio, status, buyStock, sellStock, resetPortfolio } = useMarketData(alertThreshold, soundEnabled);
  const currentPrice = stocksArray.find(s => s.ticker === selectedTicker)?.price || 0;
  const activeLayout = layouts.find(l => l.id === activeLayoutId);

  function handleLoadLayout(id: string) { setActiveLayoutId(id); setActiveLayoutIdState(id); }

  function handleSaveLayout(name: string) {
    if (name.startsWith("__overwrite__")) {
      const id = name.replace("__overwrite__", "");
      const updated = layouts.map(l => l.id !== id ? l : { ...l, windows: activeLayout?.windows || l.windows });
      setLayouts(updated); saveLayouts(updated); return;
    }
    const newLayout = saveCurrentAsLayout(name, activeLayout?.windows || [], window.innerWidth, window.innerHeight);
    setLayouts(loadLayouts(window.innerWidth, window.innerHeight));
    handleLoadLayout(newLayout.id);
  }

  function handleDeleteLayout(id: string) {
    const updated = deleteLayout(id, window.innerWidth, window.innerHeight);
    setLayouts(updated);
    if (activeLayoutId === id) handleLoadLayout(updated[0]?.id || "ibens-orb");
  }

  function autoArrange() {
    if (!activeLayout) return;
    const gap = 6, navW = navWidth, topH = 62;
    const w = window.innerWidth - navW, h = window.innerHeight - topH;
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
    onAddWindow:   handleAddWindow,
    onCloseWindow: (id: WindowId) => updateWindowState(id, { closed: true }),
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

      <Menubar
        activeView={activeView} onViewChange={setActiveView}
        layouts={layouts} activeLayoutId={activeLayoutId}
        onLoadLayout={handleLoadLayout} onSaveLayout={handleSaveLayout} onDeleteLayout={handleDeleteLayout}
        onAutoArrange={autoArrange} soundEnabled={soundEnabled} onToggleSound={() => setSoundEnabled(s => !s)}
        onAddWindow={handleAddWindow}
        activeWindowIds={activeLayout?.windows.filter(w => !w.closed).map(w => w.id as WindowId) || []}
      />

      <div className="workspace">
        <nav className="left-nav" style={{ width: navWidth }}>
          <AlertsPanel
            alerts={alerts}
            news={news}
            selectedTicker={selectedTicker}
            onSelectTicker={setSelectedTicker}
            watchlist={watchlist}
            alertThreshold={alertThreshold}
            onThresholdChange={setAlertThreshold}
          />
          <div
            className="left-nav-resizer"
            onMouseDown={e => {
              e.preventDefault();
              const startX = e.clientX;
              const startW = navWidth;
              const onMove = (ev: MouseEvent) => {
                const newW = Math.max(160, Math.min(600, startW + ev.clientX - startX));
                setNavWidth(newW);
              };
              const onUp = () => {
                window.removeEventListener("mousemove", onMove);
                window.removeEventListener("mouseup", onUp);
                };
              window.addEventListener("mousemove", onMove);
              window.addEventListener("mouseup", onUp);
            }}
          />
        </nav>
        <div className="desktop-area">
          {activeView === "konfigurator" ? (
            <Konfigurator onClose={() => setActiveView("scanners")} />
          ) : (
            <>
              {activeLayout?.windows.filter(w => !w.closed).map(win => (
                <FloatingWindow
                  key={win.id} id={win.id}
                  title={getWindowTitle(win.id as WindowId, selectedTicker, stocksArray)}
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
    </div>
  );
}

export default App;
