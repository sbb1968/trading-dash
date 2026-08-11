import { useState, useEffect, useRef } from "react";
import { openUrl } from "@tauri-apps/plugin-opener";
import "./App.css";
import { useMarketData } from "./useMarketData";
import type { StockData, IbkrOrderResult } from "./useMarketData";
import { TradingViewWidget } from "./TradingViewWidget";
import { FloatingWindow, getNextZ } from "./FloatingWindow";
import { Menubar } from "./Menubar";
import { isFutureSymbol } from "./futures";
import {
  Layout, WindowConfig, WindowId, WINDOW_LABELS,
  loadLayouts, saveLayouts, getActiveLayoutId, setActiveLayoutId,
  saveCurrentAsLayout, deleteLayout,
  loadWorkspace, saveWorkspace, clampWindows, migrateLayoutsOnce,
  WATCHLIST_COLUMNS, LEVEL2_COLUMNS, TIMESALES_COLUMNS,
  DEFAULT_WATCHLIST_COLUMNS, DEFAULT_LEVEL2_COLUMNS, DEFAULT_TIMESALES_COLUMNS,
} from "./layouts";
import { LiveAlgo } from "./LiveAlgo";
import { LiveLogProvider } from "./LiveLogContext";
import { MarketOverview } from "./MarketOverview";
import { RegimeFingerprint } from "./RegimeFingerprint";
import { AccountPanel } from "./AccountPanel";
import { OrdersWindow } from "./OrdersWindow";
import { SwingReport } from "./SwingReport";
import { BuyHoldReport } from "./BuyHoldReport";
import { DaytradingReport } from "./DaytradingReport";
import { DaytradingTop15 } from "./DaytradingTop15";
import { SectorNiche } from "./SectorNiche";
import { StrategyReport } from "./StrategyReport";
import { CompanyInfo } from "./CompanyInfo";
import { HandelsChart } from "./HandelsChart";
import { SwingDetail } from "./SwingDetail";
import { DaytradingDetail } from "./DaytradingDetail";
import { BuyHoldDetail } from "./BuyHoldDetail";
import { DocsWindow } from "./DocsWindow";
import { DagensLogWindow } from "./DagensLogWindow";
import { HaltScanner } from "./HaltScanner";
import { HelpAssistant } from "./HelpAssistant";
import { SwingTop15 } from "./SwingTop15";
import { BuyHoldTop15 } from "./BuyHoldTop15";
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
  { id: "regimefingerprint", label: "Regime-fingeraftryk" },
  { id: "account",   label: "Portfolio" },
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
  if (id === "regimefingerprint") return "regimefingerprint";
  if (id === "account") return "account";
  return "scanner";
}

// Workspace "dirty" = afviger fra det layout den er baseret paa (saa intet layout
// vises som aktivt). Sammenligner geometri + aaben/lukket (IKKE z-orden/fokus).
export function sameArrangement(a: WindowConfig[], b: WindowConfig[]): boolean {
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

// ── Futures-session (CME) ─────────────────────────────────────
// MES/M2K foelger IKKE aktiemarkedets aabningstid. CME's equity-index-futures
// handler soendag 18:00 ET til fredag 17:00 ET, med en daglig vedligeholdelses-
// pause 17:00-18:00 ET. Uden dette blev MES spaerret af aktie-gaten det meste
// af det doegn hvor den faktisk kan handles.
//
// Symbol-listen og TradingView-symbolerne bor i src/futures.ts — se den fil for
// hvorfor det er en spejling af backend/futures_katalog.py og ikke et fetch.
// Genekporteres her, saa eksisterende importoerer af isFutureSymbol fra App
// stadig virker.
export { isFutureSymbol } from "./futures";

// open = handelbar · break = daglig vedligeholdelsespause · closed = weekend
function getFuturesStatus(): "open" | "break" | "closed" {
  const now = new Date(), day = now.getDay();
  const et = new Date(now.toLocaleString("en-US", { timeZone: "America/New_York" }));
  const mins = et.getHours() * 60 + et.getMinutes();
  const CLOSE = 17 * 60, OPEN = 18 * 60;      // 17:00 / 18:00 ET
  if (day === 6) return "closed";                       // loerdag
  if (day === 0) return mins >= OPEN ? "open" : "closed";  // soendag: aabner 18:00
  if (day === 5) return mins < CLOSE ? "open" : "closed";  // fredag: lukker 17:00
  return (mins >= CLOSE && mins < OPEN) ? "break" : "open";  // man-tors
}

// Handelsstatus PR. INSTRUMENT — futures og aktier har hver deres kalender.
function tradeStatus(ticker: string, mkt: "open" | "pre" | "after" | "closed") {
  if (isFutureSymbol(ticker)) {
    const f = getFuturesStatus();
    return {
      canTrade: f === "open",
      color: f === "open" ? "var(--bull)" : "var(--bear)",
      title: f === "open" ? "FUTURES — handles naesten doegnet rundt (kan handles)"
           : f === "break" ? "FUTURES — daglig pause 23:00-00:00 dansk tid"
           : "FUTURES — weekend (aabner soendag 24:00 dansk tid)",
      blockMsg: f === "break"
        ? "CME holder daglig vedligeholdelsespause (23:00-00:00 dansk tid)."
        : "CME er lukket i weekenden. Handlen aabner soendag kl. 24:00 dansk tid.",
    };
  }
  return {
    canTrade: mkt === "open" || mkt === "pre",
    color: mkt === "open" ? "var(--bull)" : mkt === "pre" ? "var(--neutral)" : "var(--bear)",
    title: mkt === "open" ? "marked ÅBENT (kan handles)"
         : mkt === "pre" ? "PRE-MARKET (kan handles)"
         : mkt === "after" ? "AFTER-HOURS (handel ikke tilladt)"
         : "marked LUKKET (afvent åbning)",
    blockMsg: (mkt === "after" ? "after-hours — handel er slået fra" : "markedet er lukket")
      + ".\n\nHandel er muligt i pre-market (fra kl. 10:00 dansk tid) og regulær "
      + "åbningstid (15:30–22:00). After-hours er ikke tilladt.",
  };
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

// ── Kolonnevalg fra Konfiguratoren ──────────────────────────────
// localStorage's "storage"-event fyrer KUN i andre faner, ikke i den der skrev.
// Uden en egen begivenhed ville et hak i Konfiguratoren derfor foerst slaa
// igennem naar man genstartede appen — og det ville ligne at knappen ikke virker.
export const KOLONNER_AENDRET = "td-kolonner-aendret";

function useKolonner(noegle: string, standard: string[]): string[] {
  const laes = () => {
    try {
      const v = JSON.parse(localStorage.getItem(noegle) || "null");
      return Array.isArray(v) ? v : standard;
    } catch { return standard; }
  };
  const [cols, setCols] = useState<string[]>(laes);
  useEffect(() => {
    const opdater = () => setCols(laes());
    window.addEventListener(KOLONNER_AENDRET, opdater);
    window.addEventListener("storage", opdater);
    return () => {
      window.removeEventListener(KOLONNER_AENDRET, opdater);
      window.removeEventListener("storage", opdater);
    };
  }, [noegle]);
  return cols;
}

function WatchlistPanel({ stocks, selectedTicker, onSelectTicker, watchlist, onAddTicker, onRemoveTicker, onRequestOrder, orderResult, cols }: {
  stocks: any[]; selectedTicker: string; onSelectTicker: (ticker: string) => void;
  watchlist: string[]; onAddTicker: (ticker: string) => void; onRemoveTicker: (ticker: string) => void;
  onRequestOrder: (action: "BUY" | "SELL", ticker: string, shares: number, price: number) => void;
  orderResult?: IbkrOrderResult | null;
  cols?: string[];
}) {
  // Kolonnevalget hentes her frem for at blive traadt gennem hele vinduestraeet.
  // Er der intet gemt, vises ALT — et vindue der mangler sin konfiguration skal
  // vise for meget, ikke for lidt.
  const valgte = useKolonner("columns_watchlist", DEFAULT_WATCHLIST_COLUMNS);
  const vist = (id: string) => (cols ?? valgte).includes(id);

  // ⚠ HANDELSGENVEJENE FOELGER "HANDEL"-KOLONNEN. Er knapperne skjult, er K og S
  // ogsaa slaaet fra. Ellers kunne et enkelt tastetryk laegge en markedsordre paa
  // en maengde man hverken kan se eller aendre, fordi Stk-feltet er vaek — en
  // usynlig aftraekker er vaerre end ingen genvej.
  const handelAktiv = vist("handel");
  // Mængde pr. ticker — default 100 (Ross Cameron standard)
  const [orderShares, setOrderShares] = useState<Record<string, string>>({});
  const [input, setInput] = useState("");
  const [error, setError] = useState("");
  const [selectedNum, setSelectedNum] = useState<number | null>(1);      // markeret række (linje 1 fra start)
  const [simHalt, setSimHalt] = useState<Set<string>>(() => new Set());  // ALT+H: simuleret halt (selv-test)

  // Side-kort pr. ticker: frossen "Pris" ved tilføj + (næste trin) køb-tilstand fra
  // Ibens konkrete ordre-fills (avgPrice/qty). Gemmes så det overlever genstart.
  const [meta, setMeta] = useState<Record<string, WatchMeta>>(() => {
    try { return JSON.parse(localStorage.getItem("watchlist_meta") || "{}"); } catch { return {}; }
  });
  useEffect(() => { localStorage.setItem("watchlist_meta", JSON.stringify(meta)); }, [meta]);

  // Hold den markerede række gyldig: tom liste -> ingen; ellers klem ned i området
  // (linje 1 er default). Så er der altid netop én markeret linje når der er rækker.
  useEffect(() => {
    setSelectedNum(prev => {
      if (watchlist.length === 0) return null;
      if (prev == null) return 1;
      return Math.min(prev, watchlist.length);
    });
  }, [watchlist.length]);

  // Markedsstatus (RTH) — styrer om KØB/SÆLG er muligt + farve pr. ticker.
  const [mkt, setMkt] = useState<"open" | "pre" | "after" | "closed">(getMarketStatus);
  useEffect(() => { const id = setInterval(() => setMkt(getMarketStatus()), 10000); return () => clearInterval(id); }, []);
  // Handelbarhed afgoeres PR. INSTRUMENT (tradeStatus): aktier foelger RTH +
  // pre-market, futures foelger CME's egen kalender. Tidligere gjaldt aktie-
  // reglen for hele listen, saa MES blev spaerret naesten hele det doegn den
  // faktisk kan handles.
  // `mkt` opdateres hvert 10. sekund og driver gen-render, saa futures-status
  // (som laeses direkte i tradeStatus) ogsaa opdateres loebende.

  // Route Ibens ordre-resultater (fills) ind i den rette rækkes køb-tilstand:
  // KØB akkumulerer beholdning + vægtet gennemsnits-købspris; SÆLG reducerer (lukker
  // rækkens position når beholdning når 0). Gemmes i meta (localStorage) -> overlever
  // genstart; Aktuel pris + urealiseret P/L beregnes derefter live fra feedet.
  // Kvitteringen bliver staaende i 20 sekunder. Kort nok til ikke at forveksles
  // med naeste handel, langt nok til at man kan naa at laese kontoen.
  const [sidsteOrdre, setSidsteOrdre] = useState<IbkrOrderResult | null>(null);
  useEffect(() => {
    if (!orderResult) return;
    setSidsteOrdre(orderResult);
    const t = window.setTimeout(() => setSidsteOrdre(null), 20_000);
    return () => window.clearTimeout(t);
  }, [orderResult]);

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
  const stkRefs = useRef<Record<number, HTMLInputElement | null>>({});   // Stk-input pr. række (til ALT+tal-fokus)
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

  // Standardantal. 100 giver mening for en aktie — men IKKE for en future:
  // 100 MES-kontrakter er ca. 3,8 mio. USD i nominel eksponering (7.650 x 5 x 100).
  // IBKR ville afvise det paa margin, men feltet skal ikke staa og friste med et
  // tal der er hundrede gange for stort. Futures starter derfor paa 1.
  function getShares(ticker: string): string {
    return orderShares[ticker] ?? (isFutureSymbol(ticker) ? "1" : "100");
  }
  function setShares(ticker: string, value: string) {
    setOrderShares(prev => ({ ...prev, [ticker]: value.replace(/\D/g, "").slice(0, 6) }));
  }

  async function handleOrder(action: "BUY" | "SELL", stock: any) {
    const ts = tradeStatus(stock.ticker, mkt);
    if (!ts.canTrade) {
      alert(`${stock.ticker} kan ikke handles nu.\n\n${ts.blockMsg}`);
      return;
    }
    const shares = parseInt(getShares(stock.ticker), 10);
    if (!shares || shares <= 0) { alert(`Angiv en gyldig mængde for ${stock.ticker}`); return; }

    // ⚠ Her stod `if (!stock.price) return` med beskeden "Ingen live pris".
    //
    // Den blokerede paa noget der ikke betyder noget for ordren: prisen sendes
    // ALDRIG til backenden — ibkrBuy(ticker, shares) laegger en markedsordre, og
    // prisen bruges kun til bekraeftelses-dialogen.
    //
    // Til gengaeld ramte den hver gang: i sekunderne efter opstart har live-feedet
    // ikke naaet at abonnere, saa stock.price er 0. Foerste tryk paa K gav derfor
    // en fejl der pegede paa lukket marked eller ukendt ticker — hvoraf intet
    // passede. Maalt paa Ibens workstation 5/8: foerste tryk afvist, andet virkede.
    //
    // Vi skal stadig have BEVIS for at tickeren er rigtig og prissat — men et
    // levende feed er ikke den eneste kilde til det beviser. Tre trin, faldende
    // friskhed, alle aegte IBKR-priser:
    let pris = (stock.price > 0) ? stock.price : 0;
    if (!pris) pris = meta[stock.ticker]?.addPrice ?? 0;      // frosset /quote-pris
    if (!pris) {
      try {                                                    // sidste udvej: spoerg nu
        const r = await fetch(`http://127.0.0.1:8000/quote/${encodeURIComponent(stock.ticker)}`);
        const d = await r.json();
        if (typeof d.price === "number" && d.price > 0) pris = d.price;
      } catch { /* backend nede — haandteres nedenfor */ }
    }
    if (!pris) {
      alert(`Kan ikke prissætte ${stock.ticker} — ordren er IKKE sendt.\n\n` +
        `Backenden svarer ikke, eller IBKR kender ikke tickeren.\n` +
        `Futures handles med det rene symbol (MES, M2K) — ikke kontraktkoden.`);
      return;
    }
    onRequestOrder(action, stock.ticker, shares, pris);
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
      const st = watchedStocks[(selectedNum ?? 1) - 1];
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
        setSelectedNum(idx + 1);
        const inp = stkRefs.current[idx];   // stil cursoren i Stk-feltet -> klar til at ændre mængde inden K/S
        if (inp) { inp.focus(); inp.select(); }
      }
      return;
    }
    if (handelAktiv && !inField && !e.altKey && !e.ctrlKey && !e.metaKey && (e.key === "k" || e.key === "K" || e.key === "s" || e.key === "S")) {
      const stock = watchedStocks[(selectedNum ?? 1) - 1];
      if (!stock) return;
      e.preventDefault();
      handleOrder(e.key.toLowerCase() === "k" ? "BUY" : "SELL", stock);
      // markeringen bliver stående (persistent valg)
    }
  };

  return (
    <div className="watchlist-container">
      <div className="watchlist-add">
        <input className="watchlist-input" type="text" placeholder="Tilføj ticker (tryk Enter)" value={input}
          onChange={e => { setInput(e.target.value.toUpperCase()); setError(""); }}
          onKeyDown={e => e.key === "Enter" && handleAdd()} maxLength={10} />
      </div>
      {error && <div className="watchlist-error">{error}</div>}
      {/* ⚠ HJAELPELINJEN MAA IKKE LOVE EN AFTRAEKKER DER ER SLAAET FRA.
          Maalt paa Ibens workstation 11-08: kolonnevalget stod tomt, saa
          `handel` var skjult og K/S dermed deaktiveret (se handelAktiv) — men
          linjen her reklamerede stadig for dem. Et tryk paa K gjorde ingenting,
          uden nogen forklaring nogen steder. En genvej der annonceres og ikke
          virker, er vaerre end en der ikke annonceres. */}
      <div style={{ padding: "2px 8px 4px", fontSize: 10.5, color: "var(--text-muted)" }}>
        Genveje: <b>ALT+tal</b> vælg række · {handelAktiv
          ? <><b>K</b> køb · <b>S</b> sælg (handler den valgtes Stk-mængde) · </>
          : <span style={{ color: "var(--negative, #e05252)" }}>
              ⚠ K/S er slået fra — kolonnen “Handel” er fravalgt (Værktøjer →
              Konfigurator) ·{" "}
            </span>}<b>ALT+H</b> test halt-alarm
      </div>
      {/* ⚠ ORDREKVITTERING MED KONTO. To backends laa engang og lyttede paa samme
          port, én med gammel kode — en ordre kunne da lande paa den forkerte konto
          uden at noget fejlede. Det er en driftsfaelde, ikke en kodefejl, og den
          kan opstaa paa enhver maskine med en glemt proces. Derfor staar kontoen
          her, ved siden af handlen, frem for kun i journalen. */}
      {sidsteOrdre && (
        <div className={sidsteOrdre.success ? "ordre-kvittering" : "ordre-kvittering ordre-fejl"}>
          {sidsteOrdre.success ? (
            <>
              {sidsteOrdre.action === "BUY" ? "KØBT" : "SOLGT"}{" "}
              <b>{sidsteOrdre.filled ?? sidsteOrdre.shares} {sidsteOrdre.ticker}</b>
              {sidsteOrdre.avg_fill ? ` @ ${sidsteOrdre.avg_fill}` : ""}
              {" · konto "}
              <b className="ordre-konto">{sidsteOrdre.konto || "UKENDT"}</b>
              {sidsteOrdre.forbindelse === "ordre"
                ? <span className="ordre-vej"> · lokal ordre-Gateway{sidsteOrdre.port ? ` :${sidsteOrdre.port}` : ""}</span>
                : <span className="ordre-vej ordre-vej-delt"> · ⚠ DELT forbindelse</span>}
            </>
          ) : (
            <>⚠ {sidsteOrdre.action === "BUY" ? "Køb" : "Salg"} af {sidsteOrdre.ticker} fejlede: {sidsteOrdre.error}</>
          )}
        </div>
      )}
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
              {vist("pris")       && <th style={R}>Pris</th>}
              {vist("stk")        && <th style={{ textAlign: "center", width: 76 }}>Stk</th>}
              {vist("handel")     && <th style={{ textAlign: "center", width: 130 }}>Handel</th>}
              {vist("koebspris")  && <th style={R}>Købspris</th>}
              {vist("aktuel")     && <th style={R}>Aktuel pris</th>}
              {vist("beholdning") && <th style={R}>Beholdning</th>}
              {vist("upl")        && <th style={R}>Ur. P/L</th>}
              {vist("uplpct")     && <th style={R}>Ur. P/L %</th>}
              <th></th>
            </tr>
          </thead>
          <tbody>
            {watchedStocks.length === 0 && <tr><td colSpan={3 + WATCHLIST_COLUMNS.filter(c => vist(c.id)).length} className="watchlist-empty">Ingen aktier endnu</td></tr>}
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
                  onClick={() => { onSelectTicker(stock.ticker); setSelectedNum(i + 1); }}>
                  <td style={{ textAlign: "center", color: selectedNum === i + 1 ? "var(--accent)" : "var(--text-muted)", fontWeight: 700 }}>{i + 1}</td>
                  <td className="sym-cell">
                    <span onClick={e => { e.stopPropagation(); openCompanySite(stock.ticker); }}
                      title={`${stock.ticker} — ${tradeStatus(stock.ticker, mkt).title} · klik for hjemmeside`}
                      style={{ cursor: "pointer", textDecoration: "underline dotted",
                               color: tradeStatus(stock.ticker, mkt).color, fontWeight: 700 }}>{stock.ticker}</span>
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
                  {vist("pris") && <td style={R}>{m.addPrice != null ? usd(m.addPrice) : (live != null ? usd(live) : "—")}</td>}
                  {vist("stk") && <td onClick={e => e.stopPropagation()} style={{ textAlign: "center" }}>
                    <input type="text" inputMode="numeric" value={getShares(stock.ticker)}
                      ref={el => { stkRefs.current[i] = el; }}
                      onChange={e => setShares(stock.ticker, e.target.value)}
                      onFocus={() => setSelectedNum(i + 1)}
                      onClick={e => { e.stopPropagation(); setSelectedNum(i + 1); }}
                      onKeyDown={e => {
                        // K/S virker mens cursoren står i Stk-feltet: tast mængde -> K/S handler straks
                        if (handelAktiv && !e.altKey && !e.ctrlKey && !e.metaKey && (e.key === "k" || e.key === "K" || e.key === "s" || e.key === "S")) {
                          e.preventDefault();
                          handleOrder(e.key.toLowerCase() === "k" ? "BUY" : "SELL", stock);
                          (e.target as HTMLInputElement).blur();
                        }
                      }}
                      style={{ width: 60, background: "var(--bg-input)", border: "1px solid var(--border-default)", borderRadius: 3, color: "var(--text-primary)", fontSize: 12, padding: "3px 7px", textAlign: "right", fontFamily: "inherit", outline: "none", boxSizing: "border-box" }} />
                  </td>}
                  {vist("handel") && <td onClick={e => e.stopPropagation()} style={{ textAlign: "center" }}>
                    <button onClick={e => { e.stopPropagation(); handleOrder("BUY", stock); }}
                      title={`Køb ${getShares(stock.ticker)} ${stock.ticker} @ market`}
                      style={{ background: "var(--bull-muted)", border: "1px solid var(--bull)", color: "var(--bull)", borderRadius: 3, fontSize: 11, fontWeight: 700, padding: "3px 10px", marginRight: 4, cursor: "pointer" }}>KØB</button>
                    <button onClick={e => { e.stopPropagation(); handleOrder("SELL", stock); }}
                      title={`Sælg ${getShares(stock.ticker)} ${stock.ticker} @ market`}
                      style={{ background: "var(--bear-muted)", border: "1px solid var(--bear)", color: "var(--bear)", borderRadius: 3, fontSize: 11, fontWeight: 700, padding: "3px 10px", cursor: "pointer" }}>SÆLG</button>
                  </td>}
                  {vist("koebspris")  && <td style={R}>{b ? usd(b.avgPrice) : "—"}</td>}
                  {vist("aktuel")     && <td style={R}>{aktuel != null ? usd(aktuel) : "—"}</td>}
                  {vist("beholdning") && <td style={R}>{b ? b.qty : "—"}</td>}
                  {vist("upl")    && <td style={R} className={plCls(uplAmt)}>{uplAmt != null ? `${uplAmt >= 0 ? "+" : ""}$${uplAmt.toFixed(2)}` : "—"}</td>}
                  {vist("uplpct") && <td style={R} className={plCls(uplPct)}>{uplPct != null ? `${uplPct >= 0 ? "+" : ""}${uplPct.toFixed(2)}%` : "—"}</td>}
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
export function Konfigurator({ onClose }: { onClose: () => void }) {
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

  useEffect(() => {
    localStorage.setItem("columns_watchlist", JSON.stringify(colsWatchlist));
    window.dispatchEvent(new Event(KOLONNER_AENDRET));   // slaa igennem med det samme
  }, [colsWatchlist]);
  useEffect(() => { localStorage.setItem("columns_level2",    JSON.stringify(colsLevel2)); }, [colsLevel2]);
  useEffect(() => { localStorage.setItem("columns_timesales", JSON.stringify(colsTimeSales)); }, [colsTimeSales]);

  // Handels-indstilling: spring ordre-bekræftelse over (KØB/SÆLG handler direkte).
  const [skipConfirm, setSkipConfirm] = useState<boolean>(() => localStorage.getItem("skip_order_confirm") === "true");
  useEffect(() => { localStorage.setItem("skip_order_confirm", skipConfirm ? "true" : "false"); }, [skipConfirm]);
  // Bekræft før en watchlist-linje med åben position fjernes (default: til).
  const [confirmDelete, setConfirmDelete] = useState<boolean>(() => localStorage.getItem("confirm_delete_open") !== "false");
  useEffect(() => { localStorage.setItem("confirm_delete_open", confirmDelete ? "true" : "false"); }, [confirmDelete]);

  // ── Risikostyring: konfigurerbare grænser pr. strategi (backend /risk-config) ──
  const RISK_API = "http://127.0.0.1:8000";
  const [risk, setRisk] = useState<any | null>(null);
  const [riskDirty, setRiskDirty] = useState(false);
  const [riskErr, setRiskErr] = useState("");
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const r = await fetch(`${RISK_API}/risk-config`);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const d = await r.json();
        if (alive) setRisk(d);
      } catch (e: any) {
        if (alive) setRiskErr(`Kunne ikke hente risiko-config: ${e?.message || e}`);
      }
    })();
    return () => { alive = false; };
  }, []);

  function setRiskField(strat: string, key: string, field: "pct" | "amount", value: string) {
    setRisk((prev: any) => {
      if (!prev) return prev;
      const next = { ...prev, strategies: { ...prev.strategies } };
      next.strategies[strat] = prev.strategies[strat].map((row: any) =>
        row.key === key ? { ...row, [field]: value === "" ? null : value } : row);
      return next;
    });
    setRiskDirty(true);
  }
  // Live effektiv-værdi mens man redigerer: procent (af NLV) vinder hvis udfyldt.
  function riskEffective(row: any): number {
    const nlv = Number(risk?.nlv) || 0;
    const p = row.pct === "" || row.pct == null ? null : Number(row.pct);
    const a = row.amount === "" || row.amount == null ? null : Number(row.amount);
    if (p != null && p > 0 && nlv > 0) return p / 100 * nlv;
    if (a != null) return a;
    return Number(row.effective) || 0;
  }

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
    window.dispatchEvent(new Event(KOLONNER_AENDRET));
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

  async function handleSave() {
    // Gem risiko-config på backenden (font/kolonner er allerede gemt løbende).
    if (riskDirty && risk?.strategies) {
      const body: any = {};
      for (const [strat, rows] of Object.entries(risk.strategies)) {
        body[strat] = {};
        for (const row of rows as any[]) body[strat][row.key] = { pct: row.pct, amount: row.amount };
      }
      try {
        const r = await fetch(`${RISK_API}/risk-config`, {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
        });
        if (!r.ok) { setRiskErr(`Kunne ikke gemme risiko-config: ${(await r.text()).slice(0, 140)}`); return; }
      } catch (e: any) { setRiskErr(`Kunne ikke gemme risiko-config: ${e?.message || e}`); return; }
    }
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
          <button className="konfigurator-close" onClick={handleSave}>✓ Gem & Luk</button>
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
        </div>

        <div className="konfigurator-divider" />
        <ColSection title="Watchlist — Kolonner" columns={WATCHLIST_COLUMNS}
                    selected={colsWatchlist} setSelected={setColsWatchlist} />
        {!colsWatchlist.includes("handel") && (
          <div style={{ fontSize: 11, color: "var(--neutral, #d9a441)", padding: "0 14px 10px",
                        lineHeight: 1.5 }}>
            ⚠ Uden <b>Handel</b> er KØB/SÆLG-knapperne væk — og <b>K/S-genvejene er slået fra
            med dem</b>. Ellers kunne et tastetryk lægge en markedsordre på en mængde du
            hverken kan se eller ændre.
          </div>
        )}

        {/* ── Risikostyring: grænser pr. strategi ── */}
        <div className="konfigurator-divider" />
        <div className="konfigurator-panel">
          <div className="konfigurator-panel-title">Risikostyring — grænser pr. strategi</div>
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 10, lineHeight: 1.5 }}>
            For hver strategi: udfyld enten en <b>procentsats</b> (af kontoens egenkapital — har
            første prioritet) eller et <b>beløb ($)</b>. Tom procent → beløbet bruges.{" "}
            {risk?.nlv ? <>Egenkapital nu: <b>${Number(risk.nlv).toLocaleString("da-DK")}</b>.</> : null}{" "}
            Det globale daglige max er fjernet — hver strategi har sin egen grænse.
          </div>
          {riskErr && <div style={{ color: "var(--bear)", fontSize: 12, marginBottom: 8 }}>{riskErr}</div>}
          {!risk ? (
            <div style={{ fontSize: 12, color: "var(--text-muted)" }}>Henter…</div>
          ) : (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(360px, 1fr))", gap: 12 }}>
              {Object.entries(risk.strategies).map(([strat, rows]) => (
                <div key={strat} style={{ background: "var(--bg-base)", borderRadius: 4, padding: "10px 14px", border: "1px solid var(--border-subtle)" }}>
                  <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text-secondary)", marginBottom: 10, textTransform: "uppercase", letterSpacing: "0.5px" }}>{strat}</div>
                  {(rows as any[]).map(row => (
                    <div key={row.key} style={{ marginBottom: 9 }}>
                      <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 3 }}>{row.label}</div>
                      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                        <div style={{ display: "flex", alignItems: "center", gap: 3 }}>
                          <input type="number" min="0" step="0.1" placeholder="%" value={row.pct ?? ""}
                            onChange={e => setRiskField(strat, row.key, "pct", e.target.value)}
                            style={{ width: 62, background: "var(--bg-elevated)", border: "1px solid var(--border-subtle)", borderRadius: 3, color: "var(--text-primary)", fontSize: 12, padding: "4px 6px", textAlign: "right", fontVariantNumeric: "tabular-nums" }} />
                          <span style={{ fontSize: 11, color: "var(--text-muted)" }}>%</span>
                        </div>
                        <div style={{ display: "flex", alignItems: "center", gap: 3 }}>
                          <span style={{ fontSize: 11, color: "var(--text-muted)" }}>$</span>
                          <input type="number" min="0" step="1" placeholder="beløb" value={row.amount ?? ""}
                            onChange={e => setRiskField(strat, row.key, "amount", e.target.value)}
                            style={{ width: 76, background: "var(--bg-elevated)", border: "1px solid var(--border-subtle)", borderRadius: 3, color: "var(--text-primary)", fontSize: 12, padding: "4px 6px", textAlign: "right", fontVariantNumeric: "tabular-nums" }} />
                        </div>
                        <span style={{ fontSize: 11, color: "var(--text-secondary)", marginLeft: "auto", fontVariantNumeric: "tabular-nums" }}>
                          = ${Number(riskEffective(row)).toLocaleString("da-DK", { maximumFractionDigits: 0 })} nu
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="konfigurator-divider" />
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
    case "regimefingerprint": return <RegimeFingerprint />;
    case "account": return <AccountPanel onSelectTicker={props.onSelectTicker} />;
    case "orders":  return <OrdersWindow />;
    case "swing":   return <SwingReport onSelectTicker={props.onSelectTicker} onOpenDetail={props.onOpenDetail} />;
    case "buyhold": return <BuyHoldReport onSelectTicker={props.onSelectTicker} onOpenDetail={props.onOpenDetail} />;
    case "daytradingreport": return <DaytradingReport onSelectTicker={props.onSelectTicker} onOpenDetail={props.onOpenDetail} />;
    case "daytradingtop15": return <DaytradingTop15 onSelectTicker={props.onSelectTicker} onOpenDetail={props.onOpenDetail} />;
    case "sektorniche": return <SectorNiche onSelectTicker={props.onSelectTicker} />;
    case "strategirapport": return <StrategyReport />;
    case "firmainfo": return <CompanyInfo ticker={props.selectedTicker} />;
    case "handelschart": return <HandelsChart onSelectTicker={props.onSelectTicker} />;
    case "docs":    return <DocsWindow />;
    case "dagenslog": return <DagensLogWindow />;
    case "haltscanner": return <HaltScanner onSelectTicker={props.onSelectTicker} />;
    case "assistent": return <HelpAssistant />;
    case "swingtop15": return <SwingTop15 onSelectTicker={props.onSelectTicker} onOpenDetail={props.onOpenDetail} />;
    case "buyholdtop15": return <BuyHoldTop15 onSelectTicker={props.onSelectTicker} onOpenDetail={props.onOpenDetail} />;
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
      // Skærm 1-layout: anvend KUN paa skærm 1. Skærm 2 har sin egen liste og roeres ikke.
      setWorkspace(clampWindows(layout.windows.map(w => ({ ...w })), window.innerWidth, window.innerHeight));
    }
    setActiveLayoutId(id); setActiveLayoutIdState(id);
  }

  function handleSaveLayout(name: string) {
    if (name.startsWith("__overwrite__")) {
      const id = name.replace("__overwrite__", "");
      // Snapshot KUN skærm 1 (workspace) ind i layoutet.
      const updated = layouts.map(l => l.id !== id ? l : { ...l, windows: workspace.map(w => ({ ...w })) });
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
    // slettede layout var markeret. Fald tilbage til foerste tilbagevaerende (kan
    // vaere tom hvis alt er slettet). Brugerens aabne vinduer forbliver som de er.
    if (activeLayoutId === id) {
      const next = updated[0]?.id ?? "";
      setActiveLayoutId(next); setActiveLayoutIdState(next);
    }
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
                  title={`${w.kind === "swing" ? "Swing" : w.kind === "daytrading" ? "Day trading" : "Buy-and-Hold"}-score: ${w.ticker}`}
                  defaultState={w.st}
                  onClose={() => setDetailWins(ws => ws.filter(x => x.key !== w.key))}
                  onStateChange={(s) => setDetailWins(ws => ws.map(x => x.key === w.key ? { ...x, st: { ...x.st, ...s } } : x))}
                  windowType="swingdetail"
                >
                  {w.kind === "swing" && <SwingDetail ticker={w.ticker} />}
                  {w.kind === "daytrading" && <DaytradingDetail ticker={w.ticker} />}
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
