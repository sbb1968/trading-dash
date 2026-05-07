import { useState, useEffect, useCallback } from "react";
import { TradingViewWidget } from "./TradingViewWidget";
import { FloatingWindow } from "./FloatingWindow";
import { PaperTradingPanel } from "./PaperTrading";
import { useMarketData } from "./useMarketData";
import {
  WindowConfig, WindowId, WINDOW_LABELS,
  loadLayouts, saveLayouts, getActiveLayoutId,
} from "./layouts";

// ── Samme hjælpefunktioner som i App.tsx ──────────────────────
const WATCHLIST_COLUMNS = [
  { id: "price",  label: "Price"    },
  { id: "change", label: "Change %" },
  { id: "volume", label: "Volume"   },
  { id: "float",  label: "Float"    },
  { id: "relvol", label: "RelVol"   },
  { id: "gap",    label: "Gap %"    },
];

const DEFAULT_COLUMNS = ["price","change","volume","float","relvol","gap"];

function getWindowTitle(id: WindowId, ticker: string): string {
  const titles: Partial<Record<WindowId, string>> = {
    chart1min:   `${ticker} — 1 min`,
    chart2min:   `${ticker} — 2 min`,
    chart3min:   `${ticker} — 3 min`,
    chart5min:   `${ticker} — 5 min`,
    chart10min:  `${ticker} — 10 min`,
    chart15min:  `${ticker} — 15 min`,
    chart30min:  `${ticker} — 30 min`,
    chart1time:  `${ticker} — 1 time`,
    chart4time:  `${ticker} — 4 time`,
    chartdaily:  `${ticker} — Daily`,
    chartweekly: `${ticker} — Weekly`,
    level2:      `Level 2 — ${ticker}`,
    timesales:   `Time & Sales — ${ticker}`,
  };
  return titles[id] ?? WINDOW_LABELS[id];
}

function isChartWindow(id: WindowId): boolean {
  return id.startsWith("chart");
}

// ── Screen2 komponent ─────────────────────────────────────────
export default function Screen2() {
  const [selectedTicker, setSelectedTicker] = useState<string>(
    () => localStorage.getItem("selectedTicker") || "NVDA"
  );
  const [windows, setWindows] = useState<WindowConfig[]>(() => {
    const layoutId = getActiveLayoutId();
    const layouts  = loadLayouts(window.innerWidth, window.innerHeight);
    const active   = layouts.find(l => l.id === layoutId);
    return active?.screen2Windows ?? [];
  });
  const [addMenuOpen, setAddMenuOpen] = useState(false);

  const { stocksArray, portfolio, buyStock, sellStock, resetPortfolio } =
    useMarketData(0.5, false);

  const currentPrice = stocksArray.find(s => s.ticker === selectedTicker)?.price || 0;

  // Lyt på ticker-ændringer fra skærm 1 via localStorage
  useEffect(() => {
    function onStorage(e: StorageEvent) {
      if (e.key === "selectedTicker" && e.newValue) {
        setSelectedTicker(e.newValue);
      }
    }
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  // Gem vinduer til layoutet når de ændres
  const saveWindows = useCallback((newWindows: WindowConfig[]) => {
    const layoutId = getActiveLayoutId();
    const layouts  = loadLayouts(window.innerWidth, window.innerHeight);
    const updated  = layouts.map(l =>
      l.id === layoutId ? { ...l, screen2Windows: newWindows } : l
    );
    saveLayouts(updated);
  }, []);

  function updateWindowState(id: WindowId, state: Partial<WindowConfig>) {
    setWindows(prev => {
      const updated = prev.map(w => w.id === id ? { ...w, ...state } : w);
      saveWindows(updated);
      return updated;
    });
  }

  function handleAddWindow(id: WindowId) {
    setAddMenuOpen(false);
    const existing = windows.find(w => w.id === id);
    if (existing) {
      updateWindowState(id, { closed: false, minimized: false });
      return;
    }
    const w = window.innerWidth;
    const h = window.innerHeight - 40;
    const newWin: WindowConfig = {
      id,
      x: Math.floor(w / 4),
      y: Math.floor(h / 4),
      width:  Math.floor(w / 2),
      height: Math.floor(h / 2),
      minimized: false,
      maximized: false,
      closed:    false,
    };
    setWindows(prev => {
      const updated = [...prev, newWin];
      saveWindows(updated);
      return updated;
    });
  }

  // ── Auto-arrange ──────────────────────────────────────────
  function autoArrange() {
    const gap    = 6;
    const menuH  = 32;
    const w      = window.innerWidth;
    const h      = window.innerHeight - menuH;
    const open   = windows.filter(win => !win.closed);
    const count  = open.length;
    if (count === 0) return;

    const cols = Math.ceil(Math.sqrt(count));
    const rows = Math.ceil(count / cols);
    const winW = Math.floor((w - gap * (cols + 1)) / cols);
    const winH = Math.floor((h - gap * (rows + 1)) / rows);

    setWindows(prev => {
      let idx = 0;
      const updated = prev.map(win => {
        if (win.closed) return win;
        const col = idx % cols;
        const row = Math.floor(idx / cols);
        idx++;
        return {
          ...win,
          x:         gap + col * (winW + gap),
          y:         gap + row * (winH + gap),
          width:     winW,
          height:    winH,
          minimized: false,
          maximized: false,
        };
      });
      saveWindows(updated);
      return updated;
    });
  }

  function renderContent(id: WindowId) {
    switch (id) {
      case "scanner1":
      case "scanner2": {
        const sortBy = id === "scanner1" ? "momentum" : "gainers";
        const sorted = [...stocksArray].sort((a, b) =>
          sortBy === "momentum"
            ? Math.abs(b.rel_vol_5min) - Math.abs(a.rel_vol_5min)
            : b.change_percent - a.change_percent
        ).slice(0, 20);
        return (
          <div className="scanner-panel">
            <div className="scanner-scroll scanner-scroll-both">
              <table className="scanner-table">
                <thead><tr><th>Symbol</th>{DEFAULT_COLUMNS.map(c => <th key={c}>{WATCHLIST_COLUMNS.find(wc => wc.id === c)?.label ?? c}</th>)}</tr></thead>
                <tbody>
                  {sorted.map(s => (
                    <tr key={s.ticker}
                      className={[s.change_percent >= 0 ? "row-up" : "row-down", s.ticker === selectedTicker ? "row-selected" : ""].join(" ")}
                      onClick={() => setSelectedTicker(s.ticker)}
                    >
                      <td className="sym-cell">{s.ticker}</td>
                      <td>${s.price.toFixed(2)}</td>
                      <td className={s.change_percent >= 0 ? "positive" : "negative"}>{s.change_percent >= 0 ? "+" : ""}{s.change_percent.toFixed(2)}%</td>
                      <td>{(s.volume/1000000).toFixed(1)}M</td>
                      <td>{s.float}</td>
                      <td>{s.rel_vol_daily}x</td>
                      <td className={s.gap_percent >= 0 ? "positive" : "negative"}>{s.gap_percent >= 0 ? "+" : ""}{s.gap_percent.toFixed(1)}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        );
      }
      case "chart1min":   return <TradingViewWidget ticker={selectedTicker} timeframe="1 min" />;
      case "chart2min":   return <TradingViewWidget ticker={selectedTicker} timeframe="2 min" />;
      case "chart3min":   return <TradingViewWidget ticker={selectedTicker} timeframe="3 min" />;
      case "chart5min":   return <TradingViewWidget ticker={selectedTicker} timeframe="5 min" />;
      case "chart10min":  return <TradingViewWidget ticker={selectedTicker} timeframe="10 min" />;
      case "chart15min":  return <TradingViewWidget ticker={selectedTicker} timeframe="15 min" />;
      case "chart30min":  return <TradingViewWidget ticker={selectedTicker} timeframe="30 min" />;
      case "chart1time":  return <TradingViewWidget ticker={selectedTicker} timeframe="1 time" />;
      case "chart4time":  return <TradingViewWidget ticker={selectedTicker} timeframe="4 time" />;
      case "chartdaily":  return <TradingViewWidget ticker={selectedTicker} timeframe="Daily" />;
      case "chartweekly": return <TradingViewWidget ticker={selectedTicker} timeframe="Weekly" />;
      case "papertrading":
        return (
          <PaperTradingPanel
            portfolio={portfolio}
            selectedTicker={selectedTicker}
            currentPrice={currentPrice}
            onBuy={buyStock}
            onSell={sellStock}
            onReset={resetPortfolio}
            onSelectTicker={setSelectedTicker}
          />
        );
      default:
        return <div className="pt-empty">{WINDOW_LABELS[id]}</div>;
    }
  }

  const activeWindowIds = windows.filter(w => !w.closed).map(w => w.id as WindowId);

  return (
    <div className="app" style={{ position: "relative" }}>

      {/* Skærm 2 menubar */}
      <div className="menubar" style={{ position: "relative", zIndex: 500 }}>
        <span style={{ fontSize: 12, color: "var(--text-muted)", padding: "0 10px", fontWeight: 600, letterSpacing: "0.5px" }}>
          SKÆRM 2
        </span>

        <div style={{ width: "1px", height: "16px", background: "var(--border-default)", margin: "0 4px" }} />

        {/* + Vindue dropdown */}
        <div className="menu-item-wrapper" style={{ position: "relative" }}>
          <button
            className="menu-btn"
            onClick={() => setAddMenuOpen(o => !o)}
          >
            + Vindue <span className="menu-arrow">{addMenuOpen ? "▲" : "▼"}</span>
          </button>
          {addMenuOpen && (
            <div className="menu-dropdown" onClick={() => setAddMenuOpen(false)}>
              {(Object.keys(WINDOW_LABELS) as WindowId[]).map(id => {
                const isActive = activeWindowIds.includes(id);
                return (
                  <div
                    key={id}
                    className={`menu-dropdown-item ${isActive ? "menu-window-active" : ""}`}
                    onClick={() => !isActive && handleAddWindow(id)}
                  >
                    {isActive
                      ? <span className="menu-check">✓</span>
                      : <span className="menu-check-empty" />
                    }
                    {WINDOW_LABELS[id]}
                    {isActive && <span className="menu-layout-badge">åben</span>}
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Auto-arrange knap */}
        <button
          className="menu-btn"
          onClick={autoArrange}
          title="Arrangér alle vinduer automatisk"
        >
          ⊞ Auto-arrange
        </button>

        {/* Valgt ticker */}
        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 8, paddingRight: 10 }}>
          <span style={{ fontSize: 11, color: "var(--text-muted)" }}>Ticker:</span>
          <span style={{ fontSize: 13, fontWeight: 700, color: "var(--text-primary)" }}>{selectedTicker}</span>
        </div>
      </div>

      {/* Desktop */}
      <div className="desktop-area" style={{ position: "absolute", top: 32, left: 0, right: 0, bottom: 0 }}>
        {windows.filter(w => !w.closed).map(win => (
          <FloatingWindow
            key={win.id}
            id={`s2_${win.id}`}
            title={getWindowTitle(win.id as WindowId, selectedTicker)}
            defaultState={win}
            onClose={() => updateWindowState(win.id as WindowId, { closed: true })}
            tradingViewTicker={isChartWindow(win.id as WindowId) ? selectedTicker : undefined}
            onStateChange={(state) => updateWindowState(win.id as WindowId, state)}
          >
            {renderContent(win.id as WindowId)}
          </FloatingWindow>
        ))}

        {windows.filter(w => !w.closed).length === 0 && (
          <div style={{
            position: "absolute", top: "50%", left: "50%",
            transform: "translate(-50%, -50%)",
            textAlign: "center", color: "var(--text-muted)",
          }}>
            <div style={{ fontSize: 48, marginBottom: 16 }}>🖥</div>
            <div style={{ fontSize: 14, marginBottom: 8 }}>Skærm 2 er tom</div>
            <div style={{ fontSize: 12 }}>Brug <strong style={{ color: "var(--text-secondary)" }}>+ Vindue</strong> ovenfor til at tilføje paneler</div>
          </div>
        )}
      </div>
    </div>
  );
}
