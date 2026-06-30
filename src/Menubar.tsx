import { useState, useEffect, useRef } from "react";
import { invoke } from "@tauri-apps/api/core";
import { Layout, WindowId, WINDOW_LABELS } from "./layouts";
import { openUrl } from "@tauri-apps/plugin-opener";

type ActiveView = "scanners" | "watchlist" | "charting" | "konfigurator";

interface Props {
  activeView: ActiveView;
  onViewChange: (view: ActiveView) => void;
  layouts: Layout[];
  activeLayoutId: string;
  layoutDirty: boolean;   // workspace afviger fra det aktive layout (ugemt) -> ingen ✓
  onLoadLayout: (id: string) => void;
  onSaveLayout: (name: string) => void;
  onDeleteLayout: (id: string) => void;
  onAutoArrange: () => void;
  onAddWindow: (id: WindowId) => void;
  activeWindowIds: WindowId[];
}

// ── Chart tidsrammer ──────────────────────────────────────────
const CHART_TIMEFRAMES: { id: WindowId; label: string }[] = [
  { id: "chart1min",   label: "1 min"   },
  { id: "chart2min",   label: "2 min"   },
  { id: "chart3min",   label: "3 min"   },
  { id: "chart5min",   label: "5 min"   },
  { id: "chart10min",  label: "10 min"  },
  { id: "chart15min",  label: "15 min"  },
  { id: "chart30min",  label: "30 min"  },
  { id: "chart1time",  label: "1 time"  },
  { id: "chart4time",  label: "4 timer" },
  { id: "chartdaily",  label: "Daily"   },
  { id: "chartweekly", label: "Weekly"  },
];

// ── Ikke-chart vinduer med shortcuts ─────────────────────────
interface WinEntry { id: WindowId; shortcut: string }
// Seks logiske grupper i arbejdsgang-raekkefoelge (Charts indsaettes som #2 af
// AddWindowMenu via ChartSubmenu). Genvejstaster bevaret for muskelhukommelse.
const NON_CHART_GROUPS: { label: string; icon: string; items: WinEntry[] }[] = [
  { label: "Scannere", icon: "🔍", items: [
    { id: "swingtop10",    shortcut: "S" },   // Swing Top-10
    { id: "intradagtop10", shortcut: "I" },   // Intradag Top-10
    { id: "haltscanner",   shortcut: "H" },   // Halt-scanner
  ]},
  { label: "Markedsdata", icon: "📊", items: [
    { id: "watchlist",      shortcut: "A" },
    { id: "level2",         shortcut: "L" },
    { id: "timesales",      shortcut: "M" },
    { id: "marketoverview", shortcut: "R" },
    { id: "sektorniche",    shortcut: "K" },   // Sektorer & nicher
    { id: "firmainfo",      shortcut: "F" },   // Firma-info for valgt ticker
  ]},
  { label: "Analyse", icon: "🎯", items: [
    { id: "swing",          shortcut: "W" },   // Swing-rapport
    { id: "buyhold",        shortcut: "H" },   // Buy-and-Hold-rapport
    { id: "buyholdtop10",   shortcut: "B" },   // Buy-and-Hold Top-10
    { id: "intradagreport", shortcut: "T" },   // Intradag-rapport
    { id: "strategirapport",shortcut: "G" },   // Strategi-sammenligningsrapport
    { id: "handelschart",   shortcut: "C" },   // Handels-chart (entry/exit pr. handel)
  ]},
  { label: "Konto & ordrer", icon: "💼", items: [
    { id: "account",        shortcut: "U" },
    { id: "orders",         shortcut: "D" },
  ]},
  { label: "Algo & log", icon: "⚙", items: [
    { id: "livealgo",       shortcut: "O" },
    { id: "dagenslog",      shortcut: "E" },
  ]},
];

// Flad liste af alle shortcut-entries til brug i keydown-listener
const ALL_WIN_SHORTCUTS: WinEntry[] = NON_CHART_GROUPS.flatMap(g => g.items);

// Studio åbnes på ALGOSERVEREN (iben-algo) — den er datasamleren: alle maskiner pusher
// deres journal dertil, så KUN dens Studio viser HELE flåden i maskine-dropdownen.
// 127.0.0.1 ville på en workstation kun vise den lokale maskine (intet replikeret arkiv).
const STUDIO_URL = "http://iben-algo:8000/studio";

// ── Temaer ────────────────────────────────────────────────────
const THEMES: { id: string; label: string; dot: string; group: string }[] = [
  { id: "original",   label: "Original",       dot: "#555555", group: "Original" },
  { id: "stealth",    label: "Stealth Dark",   dot: "#3b82f6", group: "Mørke" },
  { id: "bloomberg",  label: "Bloomberg Blue",  dot: "#0099ff", group: "Mørke" },
  { id: "amber",      label: "Amber Terminal",  dot: "#ffaa00", group: "Mørke" },
  { id: "midnight",   label: "Midnight Green",  dot: "#00dd66", group: "Mørke" },
  { id: "crimson",    label: "Crimson",         dot: "#ff2255", group: "Mørke" },
  { id: "matrix",     label: "Matrix",          dot: "#00ff41", group: "Mørke" },
  { id: "dracula",    label: "Dracula",         dot: "#bd93f9", group: "Mørke" },
  { id: "sunset",     label: "Sunset",          dot: "#ff8833", group: "Mørke" },
  { id: "rosegold",   label: "Rose Gold",       dot: "#e8829a", group: "Mørke" },
  { id: "solarized",  label: "Solarized",       dot: "#268bd2", group: "Mørke" },
  { id: "monochrome", label: "Monochrome",      dot: "#aaaaaa", group: "Mørke" },
  { id: "arctic",     label: "Arctic",          dot: "#1a6fdd", group: "Lyst"  },
];

function useTheme() {
  const [theme, setTheme] = useState<string>(() =>
    localStorage.getItem("td-theme") ?? "stealth"
  );
  useEffect(() => {
    if (theme === "original") {
      document.body.removeAttribute("data-theme");
    } else {
      document.body.setAttribute("data-theme", theme);
    }
    localStorage.setItem("td-theme", theme);
  }, [theme]);
  return { theme, setTheme };
}

// ── Hjælper: understreg shortcut-bogstav ─────────────────────
export function LabelWithShortcut({ text, shortcut }: { text: string; shortcut: string }) {
  const idx = text.toUpperCase().indexOf(shortcut.toUpperCase());
  if (idx === -1) return <>{text}</>;
  return (
    <>
      {text.slice(0, idx)}
      <u style={{ textDecorationStyle: "solid" }}>{text[idx]}</u>
      {text.slice(idx + 1)}
    </>
  );
}

// ── DropdownMenu med hover-luk, ALT-shortcut og item-shortcuts ─
function DropdownMenu({ label, children, isActive, altKey, itemShortcuts }: {
  label: string;
  children: React.ReactNode;
  isActive?: boolean;
  altKey?: string;
  // Map fra shortcut-bogstav til callback — aktiveres når dropdown er åben
  itemShortcuts?: Record<string, () => void>;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // Luk ved klik udenfor
  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  // Keyboard handler — ALT+bogstav åbner, bare bogstav aktiverer item
  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      // ALT + altKey åbner/lukker denne dropdown
      if (altKey && e.altKey && e.key.toUpperCase() === altKey.toUpperCase()) {
        e.preventDefault();
        setOpen(o => !o);
        return;
      }

      // Bare bogstav (ingen ALT) aktiverer item-shortcut når dropdown er åben
      if (open && !e.altKey && !e.ctrlKey && !e.metaKey && itemShortcuts) {
        const cb = itemShortcuts[e.key.toUpperCase()];
        if (cb) {
          e.preventDefault();
          cb();
          setOpen(false);
        }
      }
    }
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [altKey, open, itemShortcuts]);

  return (
    <div
      className="menu-item-wrapper"
      ref={ref}
      onMouseLeave={() => setOpen(false)}
    >
      <button
        className={`menu-btn ${open ? "menu-btn-open" : ""} ${isActive ? "menu-btn-active" : ""}`}
        onClick={() => setOpen(o => !o)}
      >
        {altKey ? <LabelWithShortcut text={label} shortcut={altKey} /> : label}
        {" "}<span className="menu-arrow">{open ? "▲" : "▼"}</span>
      </button>
      {open && (
        <div className="menu-dropdown">
          {children}
        </div>
      )}
    </div>
  );
}

// ── DropdownItem ──────────────────────────────────────────────
function DropdownItem({ label, shortcut, active, isOpen, onClick }: {
  label: string;
  shortcut?: string;
  active?: boolean;
  isOpen?: boolean;
  onClick: () => void;
}) {
  return (
    <div
      className={`menu-dropdown-item ${active ? "menu-dropdown-item-active" : ""} ${isOpen ? "menu-window-open" : ""}`}
      onClick={onClick}
      title={shortcut ? `Genvej: ${shortcut}` : undefined}
    >
      {isOpen
        ? <span className="menu-check-open">●</span>
        : <span className="menu-check-empty" />
      }
      <span style={{ flex: 1 }}>
        {shortcut ? <LabelWithShortcut text={label} shortcut={shortcut} /> : label}
      </span>
      {isOpen && <span className="menu-layout-badge menu-badge-open">åben</span>}
    </div>
  );
}

// ── Chart submenu ─────────────────────────────────────────────
function ChartSubmenu({ onAddWindow }: { onAddWindow: (id: WindowId) => void }) {
  const [selected, setSelected] = useState<WindowId>("chart5min");
  return (
    <div style={{ padding: "4px 10px 8px" }} onClick={e => e.stopPropagation()}>
      <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
        <select
          value={selected}
          onChange={e => setSelected(e.target.value as WindowId)}
          style={{
            flex: 1,
            background:   "var(--bg-elevated)",
            color:        "var(--text-primary)",
            border:       "1px solid var(--border-default)",
            borderRadius: 3,
            padding:      "3px 6px",
            fontSize:     "var(--fs-content-menubar, 12px)",
            cursor:       "pointer",
          }}
        >
          {CHART_TIMEFRAMES.map(tf => (
            <option key={tf.id} value={tf.id}>{tf.label}</option>
          ))}
        </select>
        <button
          className="menu-btn"
          style={{ padding: "3px 10px", fontSize: "var(--fs-content-menubar, 12px)" }}
          onClick={() => onAddWindow(selected)}
        >
          + Tilføj
        </button>
      </div>
    </div>
  );
}

// ── "Tilføj vindue"-menu (DELT mellem skaerm 1 og skaerm 2) ───
// Eksporteres saa Screen2 faar PRAECIS samme grupperede dropdown (ChartSubmenu
// med tidsramme-vaelger + Scannere/Markedsdata/Øvrige), ALT+T og item-genveje.
// Een gruppe i "Tilfoej vindue": skillelinje (undtagen first), ikon-titel, items.
function Group({ group, first, activeWindowIds, onAddWindow }: {
  group: { label: string; icon: string; items: WinEntry[] };
  first?: boolean; activeWindowIds: WindowId[]; onAddWindow: (id: WindowId) => void;
}) {
  return (
    <>
      {!first && <div className="menu-dropdown-divider" />}
      <div className="menu-dropdown-section-title">{group.icon} {group.label}</div>
      {group.items.map(({ id, shortcut }) => (
        <DropdownItem
          key={id}
          label={WINDOW_LABELS[id]}
          shortcut={shortcut}
          isOpen={activeWindowIds.includes(id)}
          onClick={() => onAddWindow(id)}
        />
      ))}
    </>
  );
}

export function AddWindowMenu({ onAddWindow, activeWindowIds }: {
  onAddWindow: (id: WindowId) => void;
  activeWindowIds: WindowId[];
}) {
  const itemShortcuts: Record<string, () => void> = {};
  ALL_WIN_SHORTCUTS.forEach(({ id, shortcut }) => {
    itemShortcuts[shortcut.toUpperCase()] = () => onAddWindow(id);
  });
  return (
    <DropdownMenu label="Tilføj vindue" altKey="T" itemShortcuts={itemShortcuts}>
      {/* 1. Scannere */}
      <Group group={NON_CHART_GROUPS[0]} first activeWindowIds={activeWindowIds} onAddWindow={onAddWindow} />
      {/* 2. Charts */}
      <div className="menu-dropdown-divider" />
      <div className="menu-dropdown-section-title">📈 Charts</div>
      <ChartSubmenu onAddWindow={onAddWindow} />
      {/* 3-6. Markedsdata, Analyse, Konto & ordrer, Algo & log */}
      {NON_CHART_GROUPS.slice(1).map((g) => (
        <Group key={g.label} group={g} activeWindowIds={activeWindowIds} onAddWindow={onAddWindow} />
      ))}
    </DropdownMenu>
  );
}

// ── Tema-sektion inde i Værktøjer ─────────────────────────────
function ThemeSection({ theme, setTheme }: { theme: string; setTheme: (t: string) => void }) {
  const [expanded, setExpanded] = useState(false);
  const current = THEMES.find(t => t.id === theme) ?? THEMES[0];
  const groups  = Array.from(new Set(THEMES.map(t => t.group)));

  return (
    <div onClick={e => e.stopPropagation()}>
      <div className="menu-dropdown-divider" />
      <div className="menu-dropdown-section-title">Tema</div>
      <div
        className="menu-dropdown-item"
        onClick={() => setExpanded(o => !o)}
        style={{ justifyContent: "space-between" }}
      >
        <span style={{ display: "flex", alignItems: "center", gap: 7 }}>
          <span className="theme-dot" style={{ background: current.dot }} />
          {current.label}
        </span>
        <span className="menu-arrow" style={{ marginLeft: 8 }}>{expanded ? "▲" : "▼"}</span>
      </div>
      {expanded && (
        <div style={{ paddingLeft: 8 }}>
          {groups.map(group => (
            <div key={group}>
              <div className="menu-dropdown-section-title" style={{ paddingLeft: 4 }}>{group}</div>
              {THEMES.filter(t => t.group === group).map(t => (
                <div
                  key={t.id}
                  className={`menu-dropdown-item menu-theme-item ${t.id === theme ? "menu-dropdown-item-active" : ""}`}
                  onClick={() => { setTheme(t.id); setExpanded(false); }}
                >
                  <span className="theme-dot" style={{ background: t.dot }} />
                  {t.label}
                  {t.id === theme && <span className="menu-check menu-theme-check">✓</span>}
                </div>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Menubar ───────────────────────────────────────────────────
export function Menubar({
  activeView, onViewChange,
  layouts, activeLayoutId, layoutDirty, onLoadLayout, onSaveLayout, onDeleteLayout,
  onAutoArrange,
  onAddWindow, activeWindowIds
}: Props) {

  const [showSaveDialog, setShowSaveDialog] = useState(false);
  const [newLayoutName, setNewLayoutName]   = useState("");
  const { theme, setTheme } = useTheme();

  function handleSave() {
    if (!newLayoutName.trim()) return;
    onSaveLayout(newLayoutName.trim());
    setNewLayoutName("");
    setShowSaveDialog(false);
  }

  async function openScreen2() {
    try { await invoke("open_screen2"); } catch (err) { console.error("Skærm 2 fejl:", err); }
  }

  // ALT+A global shortcut til auto-arrange
  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if (e.altKey && e.key.toUpperCase() === "A") {
        e.preventDefault();
        onAutoArrange();
      }
    }
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [onAutoArrange]);

  // Item-shortcuts til "Værktøjer" dropdown
  const vaerktoejShortcuts: Record<string, () => void> = {
    "K": () => onViewChange("konfigurator"),
    "2": () => openScreen2(),
  };

  return (
    <div className="menubar">

      {/* ── Tilføj vindue — ALT+T (delt komponent, ogsaa paa skaerm 2) ── */}
      <AddWindowMenu onAddWindow={onAddWindow} activeWindowIds={activeWindowIds} />

      {/* ── Værktøjer — ALT+V ── */}
      <DropdownMenu
        label="Værktøjer"
        altKey="V"
        isActive={activeView === "konfigurator"}
        itemShortcuts={vaerktoejShortcuts}
      >
        <DropdownItem
          label="⚙ Konfigurator"
          shortcut="K"
          active={activeView === "konfigurator"}
          onClick={() => onViewChange("konfigurator")}
        />

        <div className="menu-dropdown-divider" />
        <div className="menu-dropdown-section-title">Layout</div>

        {layouts.map(layout => (
          <div key={layout.id} className="menu-layout-row">
            <div
              className={`menu-dropdown-item menu-layout-name ${layout.id === activeLayoutId && !layoutDirty ? "menu-dropdown-item-active" : ""}`}
              onClick={() => onLoadLayout(layout.id)}
            >
              {layout.id === activeLayoutId && !layoutDirty ? <span className="menu-check">✓</span> : <span className="menu-check-empty" />}
              {layout.name}
            </div>
            {!layout.isDefault && (
              <button
                className="menu-layout-delete"
                onClick={e => { e.stopPropagation(); onDeleteLayout(layout.id); }}
                title="Slet layout"
              >✕</button>
            )}
          </div>
        ))}

        <div className="menu-dropdown-item" onClick={() => onSaveLayout("__overwrite__" + activeLayoutId)}>
          💾 Opdater aktuelt layout
        </div>
        {!showSaveDialog && (
          <div className="menu-dropdown-item" onClick={e => { e.stopPropagation(); setShowSaveDialog(true); }}>
            💾 Gem som nyt layout...
          </div>
        )}
        {showSaveDialog && (
          <div className="menu-save-dialog" onClick={e => e.stopPropagation()}>
            <input
              className="menu-save-input"
              type="text"
              placeholder="Navn på layout..."
              value={newLayoutName}
              onChange={e => setNewLayoutName(e.target.value)}
              onKeyDown={e => { if (e.key === "Enter") handleSave(); if (e.key === "Escape") setShowSaveDialog(false); }}
              autoFocus
            />
            <div className="menu-save-buttons">
              <button className="menu-save-confirm" onClick={handleSave}>Gem</button>
              <button className="menu-save-cancel" onClick={() => setShowSaveDialog(false)}>Annuller</button>
            </div>
          </div>
        )}

        <ThemeSection theme={theme} setTheme={setTheme} />

        <div className="menu-dropdown-divider" />
        <DropdownItem label="🖥 Åbn Skærm 2" shortcut="2" onClick={openScreen2} />

      </DropdownMenu>

      {/* ── Auto-arrange — ALT+A ── */}
      <button className="menu-btn" onClick={onAutoArrange} title="Auto-arrange (ALT+A)">
        ⊞ <LabelWithShortcut text="Auto-arrange" shortcut="A" />
      </button>

      {/* ── HOEJRE: platform & hjaelp (doere, ikke vinduer) ── */}
      <div className="menubar-right">
        <span className="menubar-sep" />
        <button className="menu-btn menu-btn-door menu-btn-door-primary"
                onClick={() => openUrl(STUDIO_URL)}
                title="Åbn Studio på algoserveren (hele flåden) i browser">
          🎛 Studio
        </button>
        <button className="menu-btn menu-btn-door"
                onClick={() => onAddWindow("docs")} title="Åbn dokumentation">
          📄 {WINDOW_LABELS["docs"]}
        </button>
        <button className="menu-btn menu-btn-door"
                onClick={() => onAddWindow("assistent")} title="Åbn hjælp fra Claude">
          💬 {WINDOW_LABELS["assistent"]}
        </button>
      </div>

    </div>
  );
}
