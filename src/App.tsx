import { useState, useEffect, useRef } from "react";
import { openUrl } from "@tauri-apps/plugin-opener";
import "./App.css";
import { useMarketData } from "./useMarketData";
import type { StockData, IbkrOrderResult } from "./useMarketData";
import { TradingViewWidget } from "./TradingViewWidget";
import { FloatingWindow, getNextZ } from "./FloatingWindow";
import { Menubar } from "./Menubar";
import {
  Layout, WindowConfig, WindowId, WINDOW_LABELS,
  loadLayouts, saveLayouts, getActiveLayoutId, setActiveLayoutId,
  saveCurrentAsLayout, deleteLayout,
  loadWorkspace, saveWorkspace, loadWorkspace2, saveWorkspace2, clampWindows, migrateLayoutsOnce,
  WATCHLIST_COLUMNS, LEVEL2_COLUMNS, TIMESALES_COLUMNS,
  DEFAULT_WATCHLIST_COLUMNS, DEFAULT_LEVEL2_COLUMNS, DEFAULT_TIMESALES_COLUMNS,
} from "./layouts";
import { LiveAlgo } from "./LiveAlgo";
import { LiveLogProvider } from "./LiveLogContext";
import { MarketOverview } from "./MarketOverview";
import { AccountPanel } from "./AccountPanel";
import { OrdersWindow } from "./OrdersWindow";
import { SwingReport } from "./SwingReport";
import { BuyHoldReport } from "./BuyHoldReport";
import { IntradagReport } from "./IntradagReport";
import { IntradagTop10 } from "./IntradagTop10";
import { SectorNiche } from "./SectorNiche";
import { StrategyReport } from "./StrategyReport";
import { CompanyInfo } from "./CompanyInfo";
import { HandelsChart } from "./HandelsChart";
import { SwingDetail } from "./SwingDetail";
import { IntradagDetail } from "./IntradagDetail";
import { BuyHoldDetail } from "./BuyHoldDetail";
import { DocsWindow } from "./DocsWindow";
import { DagensLogWindow } from "./DagensLogWindow";
import { HaltScanner } from "./HaltScanner";
import { HelpAssistant } from "./HelpAssistant";
import { SwingTop10 } from "./SwingTop10";
import { BuyHoldTop10 } from "./BuyHoldTop10";
import { useTickerName } from "./useTickerName";


// ── Konstanter ────────────────────────────────────────────────
type ActiveView = "scanners" | "watchlist" | "charting" | "konfigurator";

// ── Font-størrelse system ─────────────────────────────────────
const FONT_WINDOW_TYPES = [
  { id: "menubar",   label: "Menubar" },
  { id: "scanner",   label: "Scannere" },
  { id: "watchlist", label: "Watchlist" },
  { id: "chart",     label: "Charts" },
  { id: "level2",    label: "Level 2" },
  { id: "timesales", label: "Time & Sales" },
  { id: "paper",     label: "Paper Trading" },
  { id: "livealgo",  label: "Live Algo" },
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
  if (id === "watchlist")    return "watchlist";
  if (id.startsWith("chart")) return "chart";
  if (id === "level2")       return "level2";
  if (id === "timesales")    return "timesales";
  if (id === "marketoverview") return "marketoverview";
  if (id === "account") return "account";
  return "scanner";
}

// Workspace "dirty" = afviger fra det layout den er baseret paa (saa intet layout
// vises som aktivt). Sammenligner geometri + aaben/lukket (IKKE z-orden/fokus).
function sameArrangement(a: WindowConfig[], b: WindowConfig[]): boolean {
  const sig = (ws: WindowConfig[]) => JSON.stringify(
    ws.map(w => ({ id: w.id, x: w.x, y: w.y, width: w.width, height: w.height,
                   minimized: w.minimized, maximized: w.maximized, closed: w.closed }))
      .sort((p, q) => p.id.localeCompare(q.id))
  );
  return sig(a) === sig(b);
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
// Markedsstatus fra ET-tid (delt af MarketStatus-badgen + Watchlist).
// open = RTH 9:30–16:00 ET (= 15:30–22:00 dansk tid); ellers pre/after/closed.
function getMarketStatus(): "open" | "pre" | "after" | "closed" {
  const now = new Date(), day = now.getDay();
  const et  = new Date(now.toLocaleString("en-US", { timeZone: "America/New_York" }));
  const mins = et.getHours() * 60 + et.getMinutes();
  if (day === 0 || day === 6)     return "closed";
  if (mins >= 240 && mins < 570)  return "pre";
  if (mins >= 570 && mins < 960)  return "open";
  if (mins >= 960 && mins < 1200) return "after";
  return "closed";
}

function MarketStatus() {
  const [status, setStatus] = useState<"open" | "pre" | "after" | "closed">("closed");
  useEffect(() => {
    const update = () => setStatus(getMarketStatus());
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

// ── Halt-alarm (syntetiseret, ingen asset-fil) ────────────────
// Kraftig, insisterende tredobbelt bip via Web Audio — spilles når en ticker Iben
// HOLDER pludselig halter. Ny AudioContext pr. kald (lukkes bagefter) så den ikke
// samler ressourcer; resume() dækker Chromiums autoplay-gate (Iben har klikket i UI'et).
function playHaltAlarm() {
  try {
    const Ctx = window.AudioContext || (window as any).webkitAudioContext;
    if (!Ctx) return;
    const ctx = new Ctx();
    ctx.resume?.();
    const t0 = ctx.currentTime;
    [0, 0.24, 0.48].forEach((dt, i) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "square";
      osc.frequency.value = i === 1 ? 660 : 880;   // høj-lav-høj = alarm-agtigt
      const t = t0 + dt;
      gain.gain.setValueAtTime(0.0001, t);
      gain.gain.exponentialRampToValueAtTime(0.3, t + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.2);
      osc.connect(gain); gain.connect(ctx.destination);
      osc.start(t); osc.stop(t + 0.22);
    });
    setTimeout(() => { try { ctx.close(); } catch { /* ignore */ } }, 1000);
  } catch { /* lyd er best-effort */ }
}

// ── Watchlist Panel ───────────────────────────────────────────
interface WatchMeta { addPrice?: number; bought?: { avgPrice: number; qty: number }; }

function WatchlistPanel({ stocks, selectedTicker, onSelectTicker, watchlist, onAddTicker, onRemoveTicker, onRequestOrder, orderResult }: {
  stocks: any[]; selectedTicker: string; onSelectTicker: (ticker: string) => void;
  watchlist: string[]; onAddTicker: (ticker: string) => void; onRemoveTicker: (ticker: string) => void;
  onRequestOrder: (action: "BUY" | "SELL", ticker: string, shares: number, price: number) => void;
  orderResult?: IbkrOrderResult | null;
}) {
  // Mængde pr. ticker — default 100 (Ross Cameron standard)
  const [orderShares, setOrderShares] = useState<Record<string, string>>({});
  const [input, setInput] = useState("");
  const [error, setError] = useState("");
  const [selectedNum, setSelectedNum] = useState<number | null>(null);   // ALT+tal-valgt række
  const [simHalt, setSimHalt] = useState<Set<string>>(() => new Set());  // ALT+H: simuleret halt (selv-test)

  // Side-kort pr. ticker: frossen "Pris" ved tilføj + (næste trin) køb-tilstand fra
  // Ibens konkrete ordre-fills (avgPrice/qty). Gemmes så det overlever genstart.
  const [meta, setMeta] = useState<Record<string, WatchMeta>>(() => {
    try { return JSON.parse(localStorage.getItem("watchlist_meta") || "{}"); } catch { return {}; }
  });
  useEffect(() => { localStorage.setItem("watchlist_meta", JSON.stringify(meta)); }, [meta]);

  // Markedsstatus (RTH) — styrer om KØB/SÆLG er muligt + farve pr. ticker.
  const [mkt, setMkt] = useState<"open" | "pre" | "after" | "closed">(getMarketStatus);
  useEffect(() => { const id = setInterval(() => setMkt(getMarketStatus()), 10000); return () => clearInterval(id); }, []);
  // Handelbar = RTH (open) ELLER pre-market. After-hours + lukket = IKKE handelbar.
  const canTrade = mkt === "open" || mkt === "pre";
  // Ticker-farve: grøn=åbent · gul=pre-market · rød=after-hours/lukket.
  const tickerColor = mkt === "open" ? "var(--bull)" : mkt === "pre" ? "var(--neutral)" : "var(--bear)";

  // Route Ibens ordre-resultater (fills) ind i den rette rækkes køb-tilstand:
  // KØB akkumulerer beholdning + vægtet gennemsnits-købspris; SÆLG reducerer (lukker
  // rækkens position når beholdning når 0). Gemmes i meta (localStorage) -> overlever
  // genstart; Aktuel pris + urealiseret P/L beregnes derefter live fra feedet.
  const processedRef = useRef<IbkrOrderResult | null>(null);
  useEffect(() => {
    const r = orderResult;
    if (!r || r === processedRef.current) return;
    processedRef.current = r;
    if (!r.success) return;
    const t = (r.ticker || "").toUpperCase();
    const filled = Number(r.filled) || 0;
    const avg = Number(r.avg_fill) || 0;
    if (!t || filled <= 0) return;
    let closed = false;
    setMeta(prev => {
      const cur = prev[t]?.bought;
      if (r.action === "BUY") {
        const oldQty = cur?.qty ?? 0, oldAvg = cur?.avgPrice ?? 0;
        const newQty = oldQty + filled;
        const newAvg = newQty > 0 ? (oldQty * oldAvg + filled * avg) / newQty : avg;
        return { ...prev, [t]: { ...prev[t], bought: { avgPrice: newAvg, qty: newQty } } };
      }
      // SÆLG: reducér beholdning
      const remaining = (cur?.qty ?? 0) - filled;
      if (remaining > 0) return { ...prev, [t]: { ...prev[t], bought: { avgPrice: cur?.avgPrice ?? avg, qty: remaining } } };
      // Position LUKKET -> nulstil hele rækken som en frisk tilføjelse (klar til ny handel).
      closed = true;
      return { ...prev, [t]: {} };
    });
    if (closed) {
      setOrderShares(prev => { const n = { ...prev }; delete n[t]; return n; });   // Stk -> default (100)
      captureAddPrice(t);   // genfrys "Pris" som ved en ny tilføjelse
    }
  }, [orderResult]);

  // Tastaturgenveje (hurtig handel): ALT+tal vælger rækken; K køber / S sælger den
  // valgte række med dens Stk-mængde. Stabil listener; logikken holdes i en ref så den
  // altid ser den nyeste liste + valg (opdateres lige før render nedenfor).
  const shortcutRef = useRef<(e: KeyboardEvent) => void>(() => {});
  useEffect(() => {
    const h = (e: KeyboardEvent) => shortcutRef.current(e);
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);

  // ── Halt-alarm ────────────────────────────────────────────
  // Overvåg om en ticker med ÅBEN position pludselig halter -> kraftig lyd + visuel
  // markering. Kant-udløst (kun ved selve overgangen til halt) + gentag hver 20. sek.
  // så længe en holdt position stadig er halted (i tilfælde af at Iben er trådt væk).
  const haltedHeldRef = useRef<Set<string>>(new Set());   // holdt-og-halted sidste tjek
  const haltedCurRef  = useRef<Set<string>>(new Set());   // holdt-og-halted lige nu (til interval)
  useEffect(() => {
    const cur = new Set<string>();
    for (const t of watchlist) {
      const live = stocks.find(s => s.ticker === t);
      const isHalted = Boolean((live as any)?.halted) || simHalt.has(t);
      const isHeld   = Boolean(meta[t]?.bought) || simHalt.has(t);   // ALT+H simulerer også "holdt"
      if (isHalted && isHeld) cur.add(t);
    }
    let fresh = false;
    cur.forEach(t => { if (!haltedHeldRef.current.has(t)) fresh = true; });
    haltedHeldRef.current = cur;
    haltedCurRef.current  = cur;
    if (fresh) playHaltAlarm();   // ny halt på en position Iben holder (eller ALT+H-test)
  }, [stocks, meta, watchlist, simHalt]);
  useEffect(() => {
    const id = setInterval(() => { if (haltedCurRef.current.size > 0) playHaltAlarm(); }, 20000);
    return () => clearInterval(id);
  }, []);

  function getShares(ticker: string): string { return orderShares[ticker] ?? "100"; }
  function setShares(ticker: string, value: string) {
    setOrderShares(prev => ({ ...prev, [ticker]: value.replace(/\D/g, "").slice(0, 6) }));
  }

  function handleOrder(action: "BUY" | "SELL", stock: any) {
    if (!canTrade) {
      const lbl = mkt === "after" ? "after-hours — handel er slået fra" : "markedet er lukket";
      alert(`${stock.ticker} kan ikke handles nu (${lbl}).\n\n` +
        `Handel er muligt i pre-market (fra kl. 10:00 dansk tid) og regulær åbningstid (15:30–22:00). ` +
        `After-hours er ikke tilladt.`);
      return;
    }
    const shares = parseInt(getShares(stock.ticker), 10);
    if (!shares || shares <= 0) { alert(`Angiv en gyldig mængde for ${stock.ticker}`); return; }
    if (!stock.price || stock.price <= 0) {
      alert(`Ingen live pris for ${stock.ticker} — kan ikke sende ordre.\n\n` +
        `• Markedet er lukket · • Tickeren findes ikke · • IBKR-feedet er ikke aktivt for aktien`);
      return;
    }
    onRequestOrder(action, stock.ticker, shares, stock.price);
  }

  async function openCompanySite(ticker: string) {
    const fallback = `https://www.google.com/search?q=${encodeURIComponent(ticker + " stock company website")}`;
    try {
      const r = await fetch(`http://127.0.0.1:8000/company/website?ticker=${encodeURIComponent(ticker)}`);
      const d = await r.json();
      openUrl(d.website || fallback);
    } catch { openUrl(fallback); }
  }

  // Frys "Pris" for en ticker: hent sidste kurs fra backend; fallback til live-feed.
  // Bruges både ved tilføj OG når en position lukkes (rækken nulstilles som en ny).
  async function captureAddPrice(t: string) {
    let addPrice: number | undefined;
    try {
      const r = await fetch(`http://127.0.0.1:8000/quote/${encodeURIComponent(t)}`);
      const d = await r.json();
      if (typeof d.price === "number" && d.price > 0) addPrice = d.price;
    } catch { /* ignore */ }
    if (addPrice == null) {
      const live = stocks.find(s => s.ticker === t);
      if (live && live.price > 0) addPrice = live.price;
    }
    if (addPrice != null) setMeta(prev => ({ ...prev, [t]: { ...prev[t], addPrice } }));
  }

  function handleAdd() {
    const t = input.trim().toUpperCase();
    if (!t) return;
    if (watchlist.includes(t)) { setError(`${t} er allerede på listen`); return; }
    onAddTicker(t); setInput(""); setError("");
    captureAddPrice(t);   // frys "Pris" ved tilføj
  }

  function removeRow(ticker: string) {
    const pos = meta[ticker]?.bought;
    const confirmOn = localStorage.getItem("confirm_delete_open") !== "false";   // default: bekræft
    if (pos && confirmOn &&
        !window.confirm(`${ticker} har en ÅBEN position (${pos.qty} stk). Fjern linjen alligevel?\n\n` +
          `Positionen LUKKES ikke — den følger i Ordre-vinduet.`)) {
      return;
    }
    onRemoveTicker(ticker);
    setMeta(prev => { const n = { ...prev }; delete n[ticker]; return n; });
  }

  const watchedStocks = watchlist.map(ticker => {
    const live = stocks.find(s => s.ticker === ticker);
    return live ?? { ticker, price: 0 };
  });
  // Tickers Iben HOLDER som lige nu er halted — til det tydelige top-banner.
  const haltedHeldTickers = watchedStocks.filter(s => {
    const t = s.ticker;
    return (Boolean((s as any).halted) || simHalt.has(t)) && (Boolean(meta[t]?.bought) || simHalt.has(t));
  }).map(s => s.ticker);

  const R = { textAlign: "right", whiteSpace: "nowrap" } as const;
  const usd = (v: number) => `$${v.toFixed(2)}`;

  // Genveje: ALT+tal vælg række · K køb · S sælg (den valgte række, med dens Stk).
  shortcutRef.current = (e: KeyboardEvent) => {
    const tgt = e.target as HTMLElement | null;
    const inField = !!tgt && (tgt.tagName === "INPUT" || tgt.tagName === "TEXTAREA" || tgt.isContentEditable);
    // ALT+H: slå simuleret halt til/fra på den valgte række (selv-test af halt-alarmen).
    if (e.altKey && (e.key === "h" || e.key === "H")) {
      if (selectedNum == null) return;
      const st = watchedStocks[selectedNum - 1];
      if (!st) return;
      e.preventDefault();
      setSimHalt(prev => {
        const n = new Set(prev);
        if (n.has(st.ticker)) n.delete(st.ticker); else n.add(st.ticker);
        return n;
      });
      return;
    }
    if (e.altKey && e.key >= "1" && e.key <= "9") {
      const idx = parseInt(e.key, 10) - 1;
      if (idx < watchedStocks.length) {
        e.preventDefault();
        (document.activeElement as HTMLElement | null)?.blur?.();
        setSelectedNum(idx + 1);
      }
      return;
    }
    if (!inField && !e.altKey && !e.ctrlKey && !e.metaKey && (e.key === "k" || e.key === "K" || e.key === "s" || e.key === "S")) {
      if (selectedNum == null) return;
      const stock = watchedStocks[selectedNum - 1];
      if (!stock) return;
      e.preventDefault();
      handleOrder(e.key.toLowerCase() === "k" ? "BUY" : "SELL", stock);
      setSelectedNum(null);
    }
  };

  return (
    <div className="watchlist-container">
      <div className="watchlist-add">
        <input className="watchlist-input" type="text" placeholder="Tilføj ticker (tryk Enter)" value={input}
          onChange={e => { setInput(e.target.value.toUpperCase()); setError(""); }}
          onKeyDown={e => e.key === "Enter" && handleAdd()} maxLength={6} />
      </div>
      {error && <div className="watchlist-error">{error}</div>}
      <div style={{ padding: "2px 8px 4px", fontSize: 10.5, color: "var(--text-muted)" }}>
        Genveje: <b>ALT+tal</b> vælg række · <b>K</b> køb · <b>S</b> sælg (handler den valgtes Stk-mængde) · <b>ALT+H</b> test halt-alarm
      </div>
      {haltedHeldTickers.length > 0 && (
        <div className="watchlist-halt-banner">
          ⛔ HALT — din åbne position er STOPPET på børsen: <b>{haltedHeldTickers.join(", ")}</b>
        </div>
      )}
      <div className="watchlist-scroll">
        <table className="scanner-table">
          <thead>
            <tr>
              <th style={{ textAlign: "center", width: 28 }}>#</th>
              <th style={{ textAlign: "left" }}>Ticker</th>
              <th style={R}>Pris</th>
              <th style={{ textAlign: "center", width: 76 }}>Stk</th>
              <th style={{ textAlign: "center", width: 130 }}>Handel</th>
              <th style={R}>Købspris</th>
              <th style={R}>Aktuel pris</th>
              <th style={R}>Beholdning</th>
              <th style={R}>Ur. P/L</th>
              <th style={R}>Ur. P/L %</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {watchedStocks.length === 0 && <tr><td colSpan={11} className="watchlist-empty">Ingen aktier endnu</td></tr>}
            {watchedStocks.map((stock, i) => {
              const m = meta[stock.ticker] || {};
              const b = m.bought;   // udfyldes i næste trin (ordre-sporing / fills)
              const live = stock.price > 0 ? stock.price : null;
              const aktuel = b ? live : null;   // Aktuel pris: kun efter køb
              const uplAmt = (b && aktuel != null) ? (aktuel - b.avgPrice) * b.qty : null;
              const uplPct = (b && aktuel != null && b.avgPrice > 0) ? (aktuel - b.avgPrice) / b.avgPrice * 100 : null;
              const plCls = (v: number | null) => v == null ? "" : v >= 0 ? "positive" : "negative";
              const halted = !!(stock as any).halted || simHalt.has(stock.ticker);
              const isHaltedHeld = halted && (!!b || simHalt.has(stock.ticker));   // holdt position + halt = fuld alarm
              return (
                <tr key={stock.ticker}
                  className={`${stock.ticker === selectedTicker ? "row-selected" : ""}${isHaltedHeld ? " watchlist-halted" : ""}`}
                  style={!isHaltedHeld && selectedNum === i + 1 ? { outline: "1px solid var(--accent)", background: "var(--accent-bg)" } : undefined}
                  onClick={() => onSelectTicker(stock.ticker)}>
                  <td style={{ textAlign: "center", color: selectedNum === i + 1 ? "var(--accent)" : "var(--text-muted)", fontWeight: 700 }}>{i + 1}</td>
                  <td className="sym-cell">
                    <span onClick={e => { e.stopPropagation(); openCompanySite(stock.ticker); }}
                      title={`${stock.ticker} — ${mkt === "open" ? "marked ÅBENT (kan handles)" : mkt === "pre" ? "PRE-MARKET (kan handles)" : mkt === "after" ? "AFTER-HOURS (handel ikke tilladt)" : "marked LUKKET (afvent åbning)"} · klik for hjemmeside`}
                      style={{ cursor: "pointer", textDecoration: "underline dotted", color: tickerColor, fontWeight: 700 }}>{stock.ticker}</span>
                    {halted && (
                      <span title={isHaltedHeld
                          ? "HALT: handlen i din åbne position er midlertidigt STOPPET på børsen"
                          : "Handlen i denne aktie er midlertidigt STOPPET (halt) på børsen"}
                        style={{ marginLeft: 6, fontSize: 9.5, fontWeight: 800, letterSpacing: 0.3, color: "#fff",
                                 background: isHaltedHeld ? "var(--bear)" : "var(--neutral)", borderRadius: 3, padding: "1px 5px", whiteSpace: "nowrap" }}>
                        ⛔ HALT
                      </span>
                    )}
                  </td>
                  <td style={R}>{m.addPrice != null ? usd(m.addPrice) : (live != null ? usd(live) : "—")}</td>
                  <td onClick={e => e.stopPropagation()} style={{ textAlign: "center" }}>
                    <input type="text" inputMode="numeric" value={getShares(stock.ticker)}
                      onChange={e => setShares(stock.ticker, e.target.value)}
                      onClick={e => e.stopPropagation()}
                      style={{ width: 60, background: "var(--bg-input)", border: "1px solid var(--border-default)", borderRadius: 3, color: "var(--text-primary)", fontSize: 12, padding: "3px 7px", textAlign: "right", fontFamily: "inherit", outline: "none", boxSizing: "border-box" }} />
                  </td>
                  <td onClick={e => e.stopPropagation()} style={{ textAlign: "center" }}>
                    <button onClick={e => { e.stopPropagation(); handleOrder("BUY", stock); }}
                      title={`Køb ${getShares(stock.ticker)} ${stock.ticker} @ market`}
                      style={{ background: "var(--bull-muted)", border: "1px solid var(--bull)", color: "var(--bull)", borderRadius: 3, fontSize: 11, fontWeight: 700, padding: "3px 10px", marginRight: 4, cursor: "pointer" }}>KØB</button>
                    <button onClick={e => { e.stopPropagation(); handleOrder("SELL", stock); }}
                      title={`Sælg ${getShares(stock.ticker)} ${stock.ticker} @ market`}
                      style={{ background: "var(--bear-muted)", border: "1px solid var(--bear)", color: "var(--bear)", borderRadius: 3, fontSize: 11, fontWeight: 700, padding: "3px 10px", cursor: "pointer" }}>SÆLG</button>
                  </td>
                  <td style={R}>{b ? usd(b.avgPrice) : "—"}</td>
                  <td style={R}>{aktuel != null ? usd(aktuel) : "—"}</td>
                  <td style={R}>{b ? b.qty : "—"}</td>
                  <td style={R} className={plCls(uplAmt)}>{uplAmt != null ? `${uplAmt >= 0 ? "+" : ""}$${uplAmt.toFixed(2)}` : "—"}</td>
                  <td style={R} className={plCls(uplPct)}>{uplPct != null ? `${uplPct >= 0 ? "+" : ""}${uplPct.toFixed(2)}%` : "—"}</td>
                  <td><button className="watchlist-remove" onClick={e => { e.stopPropagation(); removeRow(stock.ticker); }}>✕</button></td>
                </tr>
              );
            })}
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

// ── Konfigurator ──────────────────────────────────────────────
function Konfigurator({ onClose }: { onClose: () => void }) {
  const originals = useRef({
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

  useEffect(() => { localStorage.setItem("columns_watchlist", JSON.stringify(colsWatchlist)); }, [colsWatchlist]);
  useEffect(() => { localStorage.setItem("columns_level2",    JSON.stringify(colsLevel2)); }, [colsLevel2]);
  useEffect(() => { localStorage.setItem("columns_timesales", JSON.stringify(colsTimeSales)); }, [colsTimeSales]);

  // Handels-indstilling: spring ordre-bekræftelse over (KØB/SÆLG handler direkte).
  const [skipConfirm, setSkipConfirm] = useState<boolean>(() => localStorage.getItem("skip_order_confirm") === "true");
  useEffect(() => { localStorage.setItem("skip_order_confirm", skipConfirm ? "true" : "false"); }, [skipConfirm]);
  // Bekræft før en watchlist-linje med åben position fjernes (default: til).
  const [confirmDelete, setConfirmDelete] = useState<boolean>(() => localStorage.getItem("confirm_delete_open") !== "false");
  useEffect(() => { localStorage.setItem("confirm_delete_open", confirmDelete ? "true" : "false"); }, [confirmDelete]);

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
        <div className="konfigurator-panel">
          <div className="konfigurator-panel-title">Watchlist &amp; handel</div>
          <label className="konfigurator-col-item" style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}>
            <input type="checkbox" checked={skipConfirm} onChange={e => setSkipConfirm(e.target.checked)} />
            <span>Spring ordre-bekræftelse over — KØB/SÆLG handler <b>direkte</b> uden pop-up</span>
          </label>
          <label className="konfigurator-col-item" style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer", marginTop: 6 }}>
            <input type="checkbox" checked={confirmDelete} onChange={e => setConfirmDelete(e.target.checked)} />
            <span>Bekræft før en linje med <b>åben position</b> fjernes (krydset)</span>
          </label>
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 8, lineHeight: 1.5 }}>
            Watchlist-kolonnerne er nu en fast handels-opsætning (Ticker · Pris · Stk · KØB/SÆLG ·
            Købspris · Aktuel pris · Beholdning · Ur. P/L) og konfigureres ikke længere her.
          </div>
        </div>
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
export function renderWindowContent(id: WindowId, props: {
  stocks: any[]; selectedTicker: string; onSelectTicker: (t: string) => void;
  watchlist: string[]; onAddTicker: (t: string) => void; onRemoveTicker: (t: string) => void;
  currentPrice: number;
  onAddWindow: (id: WindowId) => void; onCloseWindow: (id: WindowId) => void;
  onRequestOrder: (action: "BUY" | "SELL", ticker: string, shares: number, price: number) => void;
  onOpenDetail: (kind: string, ticker: string) => void;
  orderResult?: IbkrOrderResult | null;
}) {
  switch(id) {
    case "watchlist":   return <WatchlistPanel stocks={props.stocks} selectedTicker={props.selectedTicker} onSelectTicker={props.onSelectTicker} watchlist={props.watchlist} onAddTicker={props.onAddTicker} onRemoveTicker={props.onRemoveTicker} onRequestOrder={props.onRequestOrder} orderResult={props.orderResult} />;
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
    case "livealgo":    return <LiveAlgo />;
    case "marketoverview": return <MarketOverview />;
    case "account": return <AccountPanel onSelectTicker={props.onSelectTicker} />;
    case "orders":  return <OrdersWindow />;
    case "swing":   return <SwingReport onSelectTicker={props.onSelectTicker} onOpenDetail={props.onOpenDetail} />;
    case "buyhold": return <BuyHoldReport onSelectTicker={props.onSelectTicker} onOpenDetail={props.onOpenDetail} />;
    case "intradagreport": return <IntradagReport onSelectTicker={props.onSelectTicker} onOpenDetail={props.onOpenDetail} />;
    case "intradagtop10": return <IntradagTop10 onSelectTicker={props.onSelectTicker} onOpenDetail={props.onOpenDetail} />;
    case "sektorniche": return <SectorNiche onSelectTicker={props.onSelectTicker} />;
    case "strategirapport": return <StrategyReport />;
    case "firmainfo": return <CompanyInfo ticker={props.selectedTicker} />;
    case "handelschart": return <HandelsChart onSelectTicker={props.onSelectTicker} />;
    case "docs":    return <DocsWindow />;
    case "dagenslog": return <DagensLogWindow />;
    case "haltscanner": return <HaltScanner onSelectTicker={props.onSelectTicker} />;
    case "assistent": return <HelpAssistant />;
    case "swingtop10": return <SwingTop10 onSelectTicker={props.onSelectTicker} onOpenDetail={props.onOpenDetail} />;
    case "buyholdtop10": return <BuyHoldTop10 onSelectTicker={props.onSelectTicker} onOpenDetail={props.onOpenDetail} />;
    default:            return <div className="pt-empty">Ukendt vindue</div>;
  }
}

export function getWindowTitle(id: WindowId, selectedTicker: string, stocks?: any[], tickerName?: string): string {
  const withName = tickerName ? `${selectedTicker} · ${tickerName}` : selectedTicker;

  const t: Partial<Record<WindowId, string>> = {
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
    firmainfo:   `Firma-info — ${selectedTicker}`,
  };
  return t[id] ?? WINDOW_LABELS[id];
}

export function isChartWindow(id: WindowId): boolean { return id.startsWith("chart"); }

// ── Main App ──────────────────────────────────────────────────
function App() {
  const [layoutToast, setLayoutToast] = useState<string>("");
  const [activeView, setActiveView]         = useState<ActiveView>("scanners");
  const [selectedTicker, setSelectedTicker] = useState<string>(() => localStorage.getItem("selectedTicker") || "NVDA");
  const [watchlist, setWatchlist]           = useState<string[]>(() => { const s = localStorage.getItem("watchlist"); return s ? JSON.parse(s) : ["NVDA","TSLA","AAPL"]; });
  const [layouts, setLayouts]               = useState<Layout[]>(() => {
    migrateLayoutsOnce(window.innerWidth, window.innerHeight);  // engangs-oprydning af gamle forurenede defaults
    return loadLayouts(window.innerWidth, window.innerHeight);
  });
  const [activeLayoutId, setActiveLayoutIdState] = useState<string>(() => getActiveLayoutId());
  // Den levende vindues-opsaetning (= sidste session). Genskabes ved opstart;
  // foerste gang -> Ibens ORB. Live-aendringer roerer KUN workspace, ikke layouts.
  const [workspace, setWorkspace]           = useState<WindowConfig[]>(() => loadWorkspace(window.innerWidth, window.innerHeight));

  // Detalje-vinduer (detaljeret score pr. ticker) — et selvstændigt, session-only lag
  // uden for WindowId/workspace-systemet, så FLERE kan være åbne samtidig for
  // forskellige tickers. Åbnes ved dobbeltklik på en ticker i scores/Top-15-listerne.
  const [detailWins, setDetailWins] = useState<{
    key: string; kind: string; ticker: string;
    st: { x: number; y: number; width: number; height: number; minimized: boolean; maximized: boolean; closed: boolean; zIndex: number };
  }[]>([]);

  useEffect(() => { localStorage.setItem("selectedTicker", selectedTicker); }, [selectedTicker]);
  useEffect(() => { localStorage.setItem("watchlist",      JSON.stringify(watchlist)); }, [watchlist]);
  // Gem den levende opsaetning loebende -> ingen "gem ved luk" noedvendig.
  useEffect(() => { saveWorkspace(workspace); }, [workspace]);

  // Indlæs gemte fontstørrelser ved opstart
  useEffect(() => { applyAllFonts(); }, []);

  const {
    stocksArray, status,
    ibkrBuy, ibkrSell, lastOrderResult, clearLastOrderResult, subscribeTickers,
  } = useMarketData();

  // Trin 3: abonnér på watchlist-tickers' live-kurs når listen ændres / ved (gen)forbindelse.
  useEffect(() => {
    if (status === "connected" && watchlist.length) subscribeTickers(watchlist);
  }, [watchlist, status, subscribeTickers]);
  const currentPrice = stocksArray.find(s => s.ticker === selectedTicker)?.price || 0;
  // Vis kun et layout som aktivt (✓) hvis workspace matcher det layout den er baseret
  // paa. Er opsaetningen aendret (ugemt), er der INTET aktivt layout.
  const baseLayout = layouts.find(l => l.id === activeLayoutId);
  const layoutDirty = !baseLayout || !sameArrangement(workspace, baseLayout.windows);

  // ── Bekræftelses-dialog state for manuelle IBKR-ordrer ──────
  const [orderConfirm, setOrderConfirm] = useState<{
    action: "BUY" | "SELL"; ticker: string; shares: number; price: number;
  } | null>(null); 
  const selectedTickerName = useTickerName(selectedTicker);
  
  // Anvend et navngivet layout = kopiér dets vinduer ind i workspace (rene kopier,
  // klampet ind paa skaermen). De navngivne layouts (skabeloner) roeres ikke.
  function handleLoadLayout(id: string) {
    const layout = layouts.find(l => l.id === id);
    if (layout) {
      setWorkspace(clampWindows(layout.windows.map(w => ({ ...w })), window.innerWidth, window.innerHeight));
      // Anvend ogsaa skærm 2's del — saveWorkspace2 fyrer et storage-event som
      // skærm 2-vinduet lytter paa og opdaterer sig efter.
      saveWorkspace2((layout.screen2Windows ?? []).map(w => ({ ...w })));
    }
    setActiveLayoutId(id); setActiveLayoutIdState(id);
  }

  function handleSaveLayout(name: string) {
    if (name.startsWith("__overwrite__")) {
      const id = name.replace("__overwrite__", "");
      // Snapshot baade skærm 1 (workspace) og skærm 2 (workspace2) ind i layoutet.
      const s2 = loadWorkspace2(window.innerWidth, window.innerHeight);
      const updated = layouts.map(l => l.id !== id ? l : { ...l, windows: workspace.map(w => ({ ...w })), screen2Windows: s2 });
      setLayouts(updated); saveLayouts(updated);
      const layoutName = layouts.find(l => l.id === id)?.name || "Layout";
      setLayoutToast(`✓ "${layoutName}" opdateret`);
      setTimeout(() => setLayoutToast(""), 2000);
      return;
    }
    const newLayout = saveCurrentAsLayout(name, workspace.map(w => ({ ...w })), window.innerWidth, window.innerHeight);
    setLayouts(loadLayouts(window.innerWidth, window.innerHeight));
    setActiveLayoutId(newLayout.id); setActiveLayoutIdState(newLayout.id);
    setLayoutToast(`✓ "${name}" gemt`);
    setTimeout(() => setLayoutToast(""), 2000);
  }

  function handleDeleteLayout(id: string) {
    const updated = deleteLayout(id, window.innerWidth, window.innerHeight);
    setLayouts(updated);
    // Roer IKKE workspace ved sletning — flyt kun "aktiv"-markeringen hvis det
    // slettede layout var markeret. Brugerens aabne vinduer forbliver praecis som de er.
    if (activeLayoutId === id) { setActiveLayoutId("ibens-orb"); setActiveLayoutIdState("ibens-orb"); }
  }

  function autoArrange() {
    const gap = 6, topH = 62;
    const w = window.innerWidth, h = window.innerHeight - topH;
    const open = workspace.filter(win => !win.closed);
    if (open.length === 0) return;
    const cols = Math.ceil(Math.sqrt(open.length));
    const rows = Math.ceil(open.length / cols);
    const winW = Math.floor((w - gap * (cols + 1)) / cols);
    const winH = Math.floor((h - gap * (rows + 1)) / rows);
    let idx = 0;
    setWorkspace(workspace.map(win => {
      if (win.closed) return win;
      const col = idx % cols, row = Math.floor(idx / cols); idx++;
      return { ...win, x: gap + col*(winW+gap), y: gap + row*(winH+gap), width: winW, height: winH, minimized: false, maximized: false };
    }));
  }

  function handleAddWindow(id: WindowId) {
    const w = window.innerWidth - 200, h = window.innerHeight - 100;
    const existing = workspace.find(win => win.id === id);
    if (existing) { updateWindowState(id, { closed: false, minimized: false, zIndex: getNextZ() }); return; }
    const newWindow: WindowConfig = { id, x: Math.floor(w/4), y: Math.floor(h/4), width: Math.floor(w/2), height: Math.floor(h/2), minimized: false, maximized: false, closed: false, zIndex: getNextZ() };
    setWorkspace(ws => [...ws, newWindow]);
  }

  function updateWindowState(id: WindowId, state: Partial<WindowConfig>) {
    setWorkspace(ws => ws.map(w => w.id === id ? { ...w, ...state } : w));
  }

  // Åbn (eller fokusér) et detaljeret score-vindue for en ticker. Flere tickers =
  // flere vinduer; samme ticker igen fokuserer det eksisterende.
  function openDetail(kind: string, ticker: string) {
    const t = (ticker || "").trim().toUpperCase();
    if (!t) return;
    const key = `detail:${kind}:${t}`;
    setDetailWins(ws => {
      if (ws.some(w => w.key === key)) {
        return ws.map(w => w.key === key ? { ...w, st: { ...w.st, closed: false, minimized: false, zIndex: getNextZ() } } : w);
      }
      const W = window.innerWidth, H = window.innerHeight;
      const off = (ws.length % 8) * 30;
      return [...ws, { key, kind, ticker: t, st: {
        x: Math.max(20, Math.floor(W / 2) - 380) + off, y: 80 + off,
        width: Math.min(780, W - 120), height: Math.min(760, H - 140),
        minimized: false, maximized: false, closed: false, zIndex: getNextZ(),
      } }];
    });
  }

  const windowProps = {
    stocks: stocksArray, selectedTicker, onSelectTicker: setSelectedTicker, watchlist,
    onAddTicker: (t: string) => setWatchlist(w => [...w, t]),
    onRemoveTicker: (t: string) => setWatchlist(w => w.filter(x => x !== t)),
    currentPrice,
    onAddWindow:   handleAddWindow,
    onCloseWindow: (id: WindowId) => updateWindowState(id, { closed: true }),
    onOpenDetail: openDetail,
    orderResult: lastOrderResult,
    onRequestOrder: (action: "BUY" | "SELL", ticker: string, shares: number, price: number) => {
      // Konfigurator-indstilling: spring bekræftelses-pop-up over og handl direkte.
      if (localStorage.getItem("skip_order_confirm") === "true") {
        if (action === "BUY") ibkrBuy(ticker, shares); else ibkrSell(ticker, shares);
      } else {
        setOrderConfirm({ action, ticker, shares, price });
      }
    },
  };

  return (
    <LiveLogProvider>
    <div className="app">
      {/* Top bar — kun status, INGEN nyhedsbånd */}
      <div className="top-bar" style={{ justifyContent: "flex-end" }}>
        <div className="status-bar">
          <ConnectionStatus status={status} />
          <MarketStatus />
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
        layouts={layouts} activeLayoutId={activeLayoutId} layoutDirty={layoutDirty}
        onLoadLayout={handleLoadLayout} onSaveLayout={handleSaveLayout} onDeleteLayout={handleDeleteLayout}
        onAutoArrange={autoArrange}
        onAddWindow={handleAddWindow}
        activeWindowIds={workspace.filter(w => !w.closed).map(w => w.id as WindowId)}
      />

      <div className="workspace">
        <div className="desktop-area">
          {activeView === "konfigurator" ? (
            <Konfigurator onClose={() => setActiveView("scanners")} />
          ) : (
            <>
              {workspace.filter(w => !w.closed).map(win => (
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

              {/* Detalje-vinduer (detaljeret score pr. ticker; flere kan være åbne samtidig) */}
              {detailWins.filter(w => !w.st.closed).map(w => (
                <FloatingWindow
                  key={w.key} id={w.key}
                  title={`${w.kind === "swing" ? "Swing" : w.kind === "intradag" ? "Day trading" : "Buy-and-Hold"}-score: ${w.ticker}`}
                  defaultState={w.st}
                  onClose={() => setDetailWins(ws => ws.filter(x => x.key !== w.key))}
                  onStateChange={(s) => setDetailWins(ws => ws.map(x => x.key === w.key ? { ...x, st: { ...x.st, ...s } } : x))}
                  windowType="swingdetail"
                >
                  {w.kind === "swing" && <SwingDetail ticker={w.ticker} />}
                  {w.kind === "intradag" && <IntradagDetail ticker={w.ticker} />}
                  {w.kind === "buyhold" && <BuyHoldDetail ticker={w.ticker} />}
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
    </LiveLogProvider>
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
