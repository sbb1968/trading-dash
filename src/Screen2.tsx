import { useState, useEffect, useCallback } from "react";
import { FloatingWindow, getNextZ } from "./FloatingWindow";
import { LiveLogProvider } from "./LiveLogContext";
import { useMarketData } from "./useMarketData";
import { renderWindowContent, getWindowTitle, isChartWindow } from "./App";
import { AddWindowMenu, LabelWithShortcut } from "./Menubar";
import {
  WindowConfig, WindowId, WINDOW_LABELS,
  loadWorkspace2, saveWorkspace2, clampWindows, WORKSPACE2_KEY,
} from "./layouts";

// ── Screen2 komponent ─────────────────────────────────────────
// Selvstaendigt Tauri-vindue (egen React-rod). Genbruger skaerm 1's fulde
// renderWindowContent + getWindowTitle (eksporteret fra App.tsx), saa ALLE
// vinduestyper virker praecis som paa skaerm 1 — ikke en forenklet delmaengde.
export default function Screen2() {
  const [selectedTicker, setSelectedTicker] = useState<string>(
    () => localStorage.getItem("selectedTicker") || "NVDA"
  );
  const [windows, setWindows] = useState<WindowConfig[]>(
    () => loadWorkspace2(window.innerWidth, window.innerHeight)
  );

  const { stocksArray, news, portfolio, buyStock, sellStock, resetPortfolio } =
    useMarketData();

  const [watchlist, setWatchlist] = useState<string[]>(() => {
    const s = localStorage.getItem("watchlist");
    return s ? JSON.parse(s) : ["NVDA", "TSLA", "AAPL"];
  });

  const currentPrice = stocksArray.find(s => s.ticker === selectedTicker)?.price || 0;

  // Lyt på ticker-ændringer fra skærm 1 via localStorage
  useEffect(() => {
    function onStorage(e: StorageEvent) {
      if (e.key === "selectedTicker" && e.newValue) {
        setSelectedTicker(e.newValue);
      }
      // Skærm 1 anvendte et layout -> opdater skærm 2 efter den nye opsaetning.
      if (e.key === WORKSPACE2_KEY && e.newValue) {
        try { setWindows(clampWindows(JSON.parse(e.newValue), window.innerWidth, window.innerHeight)); } catch { /* ignore */ }
      }
    }
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  // Hold valgt ticker i sync til skærm 1 (samme localStorage-nøgle)
  useEffect(() => { localStorage.setItem("selectedTicker", selectedTicker); }, [selectedTicker]);

  // ALT+A = Auto-arrange (ALT+T haandteres af den delte AddWindowMenu)
  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if (e.altKey && e.key.toUpperCase() === "A") { e.preventDefault(); autoArrange(); }
    }
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [windows]);

  // Gem skærm 2's levende opsaetning (egen workspace, parallelt med skærm 1).
  const saveWindows = useCallback((newWindows: WindowConfig[]) => {
    saveWorkspace2(newWindows);
  }, []);

  function updateWindowState(id: WindowId, state: Partial<WindowConfig>) {
    setWindows(prev => {
      const updated = prev.map(w => w.id === id ? { ...w, ...state } : w);
      saveWindows(updated);
      return updated;
    });
  }

  function handleAddWindow(id: WindowId) {
    const existing = windows.find(w => w.id === id);
    if (existing) {
      updateWindowState(id, { closed: false, minimized: false, zIndex: getNextZ() });
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
      zIndex:    getNextZ(),
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

  // Samme props-form som skærm 1 (App.tsx). Order-confirm-modalen findes kun
  // paa skærm 1 -> onRequestOrder er en no-op her (vinduet virker stadig).
  const windowProps = {
    stocks: stocksArray, selectedTicker, onSelectTicker: setSelectedTicker, watchlist,
    onAddTicker:    (t: string) => setWatchlist(w => w.includes(t) ? w : [...w, t]),
    onRemoveTicker: (t: string) => setWatchlist(w => w.filter(x => x !== t)),
    news, portfolio, buyStock, sellStock, resetPortfolio, currentPrice,
    onAddWindow:    handleAddWindow,
    onCloseWindow:  (id: WindowId) => updateWindowState(id, { closed: true }),
    onRequestOrder: () => {},
  };

  const activeWindowIds = windows.filter(w => !w.closed).map(w => w.id as WindowId);

  return (
    <div className="app" style={{ position: "relative" }}>

      {/* Skærm 2 menubar */}
      <div className="menubar" style={{ position: "relative", zIndex: 500 }}>
        <span style={{ fontSize: 12, color: "var(--text-muted)", padding: "0 10px", fontWeight: 600, letterSpacing: "0.5px" }}>
          SKÆRM 2
        </span>

        <div style={{ width: "1px", height: "16px", background: "var(--border-default)", margin: "0 4px" }} />

        {/* Tilføj vindue — DELT komponent (præcis som skærm 1: ChartSubmenu + grupper) */}
        <AddWindowMenu onAddWindow={handleAddWindow} activeWindowIds={activeWindowIds} />

        {/* Auto-arrange knap */}
        <button
          className="menu-btn"
          onClick={autoArrange}
          title="Arrangér alle vinduer automatisk (ALT+A)"
        >
          ⊞ <LabelWithShortcut text="Auto-arrange" shortcut="A" />
        </button>

        {/* Dokumentation + Hjælp — direkte synlige knapper (nemt for Iben) */}
        <button className="menu-btn" onClick={() => handleAddWindow("docs")} title="Åbn dokumentation">
          📄 {WINDOW_LABELS["docs"]}
        </button>
        <button className="menu-btn" onClick={() => handleAddWindow("assistent")} title="Åbn hjælp fra Claude">
          💬 {WINDOW_LABELS["assistent"]}
        </button>

        {/* Valgt ticker */}
        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 8, paddingRight: 10 }}>
          <span style={{ fontSize: 11, color: "var(--text-muted)" }}>Ticker:</span>
          <span style={{ fontSize: 13, fontWeight: 700, color: "var(--text-primary)" }}>{selectedTicker}</span>
        </div>
      </div>

      {/* Desktop */}
      <LiveLogProvider>
        <div className="desktop-area" style={{ position: "absolute", top: 32, left: 0, right: 0, bottom: 0 }}>
          {windows.filter(w => !w.closed).map(win => (
            <FloatingWindow
              key={win.id}
              id={`s2_${win.id}`}
              title={getWindowTitle(win.id as WindowId, selectedTicker, stocksArray)}
              defaultState={win}
              onClose={() => updateWindowState(win.id as WindowId, { closed: true })}
              tradingViewTicker={isChartWindow(win.id as WindowId) ? selectedTicker : undefined}
              onStateChange={(state) => updateWindowState(win.id as WindowId, state)}
            >
              {renderWindowContent(win.id as WindowId, windowProps)}
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
      </LiveLogProvider>
    </div>
  );
}
