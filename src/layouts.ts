// ── Window ID typer ───────────────────────────────────────────
export type WindowId =
  | "scanner1" | "scanner2" | "watchlist" | "newsroom" | "papertrading"
  | "chart1min" | "chart2min" | "chart3min" | "chart5min" | "chart10min"
  | "chart15min" | "chart30min"
  | "chart1time" | "chart4time"
  | "chartdaily" | "chartweekly"
  | "level2" | "timesales" | "journal" | "livealgo"
  | "alerts"
  | "marketoverview"
  | "account"
  | "orders"
  | "swing";

export const WINDOW_LABELS: Record<WindowId, string> = {
  scanner1:     "Small Cap Scanner",
  scanner2:     "Top Gainers",
  watchlist:    "Watchlist",
  newsroom:     "News Room",
  papertrading: "Paper Trading",
  chart1min:    "Chart 1 min",
  chart2min:    "Chart 2 min",
  chart3min:    "Chart 3 min",
  chart5min:    "Chart 5 min",
  chart10min:   "Chart 10 min",
  chart15min:   "Chart 15 min",
  chart30min:   "Chart 30 min",
  chart1time:   "Chart 1 time",
  chart4time:   "Chart 4 time",
  chartdaily:   "Chart Daily",
  chartweekly:  "Chart Weekly",
  level2:       "Level 2",
  timesales:    "Time & Sales",
  journal:      "Trade Journal",
  livealgo:     "Live Algo",
  alerts:       "Nyheds Alerts",
  marketoverview:  "marketoverview",
  account:         "Konto",
  orders:          "Ordrer",
  swing:           "Swing-rapport",
};

// ── WindowConfig ──────────────────────────────────────────────
export interface WindowConfig {
  id:         string;
  x:          number;
  y:          number;
  width:      number;
  height:     number;
  minimized:  boolean;
  maximized:  boolean;
  closed:     boolean;
  poppedOut?: boolean;
  popX?:      number;
  popY?:      number;
  popWidth?:  number;
  popHeight?: number;
}

// ── Layout — inkl. skærm 2 vinduer ───────────────────────────
export interface Layout {
  id:              string;
  name:            string;
  windows:         WindowConfig[];   // skærm 1
  screen2Windows?: WindowConfig[];   // skærm 2 (valgfri)
  isDefault?:      boolean;
}

// ── Kolonner ──────────────────────────────────────────────────
export const WATCHLIST_COLUMNS = [
  { id: "price",  label: "Price"    },
  { id: "change", label: "Change %" },
  { id: "volume", label: "Volume"   },
  { id: "float",  label: "Float"    },
  { id: "relvol", label: "RelVol"   },
  { id: "gap",    label: "Gap %"    },
];

export const LEVEL2_COLUMNS = [
  { id: "mm",       label: "MM"     },
  { id: "bidprice", label: "Bid"    },
  { id: "bidsize",  label: "Bid Sz" },
  { id: "askprice", label: "Ask"    },
  { id: "asksize",  label: "Ask Sz" },
  { id: "orders",   label: "Orders" },
];

export const TIMESALES_COLUMNS = [
  { id: "time",      label: "Tid"   },
  { id: "price",     label: "Pris"  },
  { id: "size",      label: "Stk"   },
  { id: "direction", label: "Dir"   },
  { id: "value",     label: "Værdi" },
];

export const DEFAULT_WATCHLIST_COLUMNS = ["price","change","volume","float","relvol","gap"];
export const DEFAULT_LEVEL2_COLUMNS    = ["mm","bidprice","bidsize","askprice","asksize"];
export const DEFAULT_TIMESALES_COLUMNS = ["time","price","size","direction"];

// ── Standard layouts ──────────────────────────────────────────
function makeDefaultLayouts(W: number, H: number): Layout[] {
  const navW = 200;
  const topH = 62;
  const aw   = W - navW;
  const ah   = H - topH;

  return [
    {
      id: "ibens-orb", name: "Ibens ORB", isDefault: true,
      windows: [
        { id: "scanner1",  x: 0,                   y: 0,                   width: Math.floor(aw*0.22), height: ah,                  minimized:false, maximized:false, closed:false },
        { id: "chart1min", x: Math.floor(aw*0.22), y: 0,                   width: Math.floor(aw*0.52), height: Math.floor(ah*0.65), minimized:false, maximized:false, closed:false },
        { id: "newsroom",  x: Math.floor(aw*0.74), y: 0,                   width: Math.floor(aw*0.26), height: Math.floor(ah*0.5),  minimized:false, maximized:false, closed:false },
        { id: "level2",    x: Math.floor(aw*0.22), y: Math.floor(ah*0.65), width: Math.floor(aw*0.26), height: Math.floor(ah*0.35), minimized:false, maximized:false, closed:false },
        { id: "timesales", x: Math.floor(aw*0.48), y: Math.floor(ah*0.65), width: Math.floor(aw*0.26), height: Math.floor(ah*0.35), minimized:false, maximized:false, closed:false },
      ],
      screen2Windows: [],
    },
    {
      id: "ibens-daytrading", name: "Ibens daytrading", isDefault: true,
      windows: [
        { id: "scanner1",  x: 0,                   y: 0,                   width: Math.floor(aw*0.20), height: Math.floor(ah*0.5),  minimized:false, maximized:false, closed:false },
        { id: "scanner2",  x: 0,                   y: Math.floor(ah*0.5),  width: Math.floor(aw*0.20), height: Math.floor(ah*0.5),  minimized:false, maximized:false, closed:false },
        { id: "chart1min", x: Math.floor(aw*0.20), y: 0,                   width: Math.floor(aw*0.40), height: Math.floor(ah*0.6),  minimized:false, maximized:false, closed:false },
        { id: "chart5min", x: Math.floor(aw*0.60), y: 0,                   width: Math.floor(aw*0.40), height: Math.floor(ah*0.6),  minimized:false, maximized:false, closed:false },
        { id: "watchlist", x: Math.floor(aw*0.20), y: Math.floor(ah*0.6),  width: Math.floor(aw*0.40), height: Math.floor(ah*0.4),  minimized:false, maximized:false, closed:false },
        { id: "newsroom",  x: Math.floor(aw*0.60), y: Math.floor(ah*0.6),  width: Math.floor(aw*0.40), height: Math.floor(ah*0.4),  minimized:false, maximized:false, closed:false },
      ],
      screen2Windows: [],
    },
    {
      id: "level2-ts", name: "Level 2 & T&S", isDefault: true,
      windows: [
        { id: "chart1min", x: 0,                  y: 0, width: Math.floor(aw*0.6), height: ah, minimized:false, maximized:false, closed:false },
        { id: "level2",    x: Math.floor(aw*0.6), y: 0, width: Math.floor(aw*0.2), height: ah, minimized:false, maximized:false, closed:false },
        { id: "timesales", x: Math.floor(aw*0.8), y: 0, width: Math.floor(aw*0.2), height: ah, minimized:false, maximized:false, closed:false },
      ],
      screen2Windows: [],
    },
    {
      id: "scanner-mode", name: "Scanner mode", isDefault: true,
      windows: [
        { id: "scanner1",  x: 0,                  y: 0,                   width: Math.floor(aw*0.5), height: Math.floor(ah*0.6), minimized:false, maximized:false, closed:false },
        { id: "scanner2",  x: Math.floor(aw*0.5), y: 0,                   width: Math.floor(aw*0.5), height: Math.floor(ah*0.6), minimized:false, maximized:false, closed:false },
        { id: "newsroom",  x: 0,                  y: Math.floor(ah*0.6),  width: aw,                 height: Math.floor(ah*0.4), minimized:false, maximized:false, closed:false },
      ],
      screen2Windows: [],
    },
    {
      id: "paper-trading", name: "Paper Trading", isDefault: true,
      windows: [
        { id: "papertrading", x: 0,                   y: 0, width: Math.floor(aw*0.35), height: ah, minimized:false, maximized:false, closed:false },
        { id: "chart1min",    x: Math.floor(aw*0.35), y: 0, width: Math.floor(aw*0.65), height: ah, minimized:false, maximized:false, closed:false },
      ],
      screen2Windows: [],
    },
    {
      id: "algotrading", name: "Algotrading", isDefault: true,
      windows: [
        { id: "livealgo",  x: 0, y: 0, width: aw, height: ah, minimized:false, maximized:false, closed:false },
      ],
      screen2Windows: [],
    },
  ];
}

// ── Gem / load layouts ────────────────────────────────────────
const STORAGE_KEY = "td_layouts";

export function loadLayouts(W: number, H: number): Layout[] {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (!saved) return makeDefaultLayouts(W, H);
    const parsed: Layout[] = JSON.parse(saved);
    const defaults   = makeDefaultLayouts(W, H);
    const defaultIds = defaults.map(d => d.id);
    // Bevar screen2Windows fra gemte layouts
    const mergedDefaults = defaults.map(def => {
      const saved = parsed.find(p => p.id === def.id);
      if (!saved) return def;
      // Bevar gemte ændringer (vinduer + screen2) — men hvis arrayet er tomt
      // efter en buggy save, så fald tilbage til defaults
      return {
        ...def,
        windows: saved.windows && saved.windows.length > 0 ? saved.windows : def.windows,
        screen2Windows: saved.screen2Windows ?? [],
      };
    });
    const customs = parsed.filter(l => !defaultIds.includes(l.id));
    return [...mergedDefaults, ...customs];
  } catch {
    return makeDefaultLayouts(W, H);
  }
}

export function saveLayouts(layouts: Layout[]) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(layouts));
}

export function getActiveLayoutId(): string {
  return localStorage.getItem("td_active_layout") || "ibens-orb";
}

export function setActiveLayoutId(id: string) {
  localStorage.setItem("td_active_layout", id);
}

export function saveCurrentAsLayout(name: string, windows: WindowConfig[], W: number, H: number): Layout {
  const id = `custom_${Date.now()}`;
  const newLayout: Layout = { id, name, windows, screen2Windows: [], isDefault: false };
  const existing = loadLayouts(W, H);
  const updated  = [...existing, newLayout];
  localStorage.setItem(STORAGE_KEY, JSON.stringify(updated));
  return newLayout;
}

export function deleteLayout(id: string, W: number, H: number): Layout[] {
  const layouts = loadLayouts(W, H).filter(l => l.id !== id);
  localStorage.setItem(STORAGE_KEY, JSON.stringify(layouts));
  return layouts;
}
