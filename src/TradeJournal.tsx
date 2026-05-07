import { useState } from "react";
import { MOCK_JOURNAL_TRADES, JournalTrade, SetupType, Emotion } from "./mockJournalData";

// ── Hjælpere ──────────────────────────────────────────────────
function formatUSD(val: number) {
  return val.toLocaleString("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2 });
}
function formatPct(val: number) {
  return `${val >= 0 ? "+" : ""}${val.toFixed(2)}%`;
}

const SETUP_TYPES: SetupType[] = ["ORB","Momentum","News Play","VWAP Bounce","Bull Flag","Reversal","Anden"];
const EMOTIONS: Emotion[]      = ["Rolig","Nervøs","Overmodig","Disciplineret","FOMO"];

// ── Font-størrelse CSS-variabler — bruges direkte som strenge ─
const fs  = "var(--fs-content-journal, 12px)";
const fsh = "var(--fs-header-journal,  10px)";
const fst = "var(--fs-title-journal,   14px)";

// ── Storage ───────────────────────────────────────────────────
const STORAGE_KEY = "td_journal_trades";

function loadTrades(): JournalTrade[] {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) return JSON.parse(saved);
  } catch {}
  const data = [...MOCK_JOURNAL_TRADES];
  localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
  return data;
}

function saveTrades(trades: JournalTrade[]) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(trades));
}

// ── Statistik ─────────────────────────────────────────────────
function calcStats(trades: JournalTrade[]) {
  if (trades.length === 0) return null;
  const wins   = trades.filter(t => t.pnl > 0);
  const losses = trades.filter(t => t.pnl < 0);
  const totalPnl = trades.reduce((s, t) => s + t.pnl, 0);
  const avgWin   = wins.length   > 0 ? wins.reduce((s, t) => s + t.pnl, 0)   / wins.length   : 0;
  const avgLoss  = losses.length > 0 ? losses.reduce((s, t) => s + t.pnl, 0) / losses.length : 0;
  const winRate  = wins.length / trades.length * 100;
  const profitFactor = losses.length > 0
    ? Math.abs(wins.reduce((s, t) => s + t.pnl, 0) / losses.reduce((s, t) => s + t.pnl, 0))
    : Infinity;
  const avgDuration = trades.reduce((s, t) => s + t.duration_min, 0) / trades.length;
  const bestTrade   = trades.reduce((a, b) => a.pnl > b.pnl ? a : b);
  const worstTrade  = trades.reduce((a, b) => a.pnl < b.pnl ? a : b);
  const followedPct = trades.filter(t => t.followed_plan).length / trades.length * 100;
  const bySetup: Record<string, { pnl: number; count: number; wins: number }> = {};
  trades.forEach(t => {
    if (!bySetup[t.setup]) bySetup[t.setup] = { pnl: 0, count: 0, wins: 0 };
    bySetup[t.setup].pnl += t.pnl;
    bySetup[t.setup].count++;
    if (t.pnl > 0) bySetup[t.setup].wins++;
  });
  return { totalPnl, winRate, avgWin, avgLoss, profitFactor, avgDuration, bestTrade, worstTrade, followedPct, bySetup, numWins: wins.length, numLosses: losses.length };
}

// ── Stat-kort ─────────────────────────────────────────────────
function StatCard({ label, value, sub, positive }: { label: string; value: string; sub?: string; positive?: boolean }) {
  return (
    <div style={{ background: "var(--bg-elevated)", border: "1px solid var(--border-subtle)", borderRadius: 4, padding: "8px 12px", minWidth: 110 }}>
      <div style={{ fontSize: fsh, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.4px", marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: fst, fontWeight: 700, color: positive === undefined ? "var(--text-primary)" : positive ? "var(--bull)" : "var(--bear)" }}>{value}</div>
      {sub && <div style={{ fontSize: fsh, color: "var(--text-muted)", marginTop: 2 }}>{sub}</div>}
    </div>
  );
}

// ── Kolonne-stil hjælpere ─────────────────────────────────────
const colStyle = (align: "left"|"right" = "left"): React.CSSProperties => ({
  padding: "4px 8px", textAlign: align,
  color: "var(--text-muted)", fontSize: fsh,
  fontWeight: 600, textTransform: "uppercase",
  letterSpacing: "0.4px", borderBottom: "1px solid var(--border-default)",
  whiteSpace: "nowrap",
});

const cellStyle = (align: "left"|"right" = "left"): React.CSSProperties => ({
  padding: "5px 8px", textAlign: align,
  fontSize: fs, borderBottom: "1px solid var(--border-subtle)",
  whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
});

// ── Hoved-komponent ───────────────────────────────────────────
export function TradeJournal() {
  const [trades, setTrades]             = useState<JournalTrade[]>(() => loadTrades());
  const [selected, setSelected]         = useState<JournalTrade | null>(null);
  const [editNotes, setEditNotes]       = useState("");
  const [editSetup, setEditSetup]       = useState<SetupType>("ORB");
  const [editEmotion, setEditEmotion]   = useState<Emotion>("Rolig");
  const [editPlan, setEditPlan]         = useState(true);
  const [editTags, setEditTags]         = useState("");
  const [activeTab, setActiveTab]       = useState<"liste" | "statistik">("liste");
  const [filterSetup, setFilterSetup]   = useState<string>("Alle");
  const [filterResult, setFilterResult] = useState<string>("Alle");
  const [sortBy, setSortBy]             = useState<"date" | "pnl" | "pnl_pct">("date");

  const stats = calcStats(trades);

  function openTrade(t: JournalTrade) {
    setSelected(t);
    setEditNotes(t.notes);
    setEditSetup(t.setup);
    setEditEmotion(t.emotion);
    setEditPlan(t.followed_plan);
    setEditTags(t.tags.join(", "));
  }

  function saveChanges() {
    if (!selected) return;
    const updated = trades.map(t => t.id !== selected.id ? t : {
      ...t,
      notes: editNotes, setup: editSetup, emotion: editEmotion,
      followed_plan: editPlan,
      tags: editTags.split(",").map(s => s.trim()).filter(Boolean),
    });
    setTrades(updated);
    saveTrades(updated);
    setSelected(null);
  }

  const filtered = trades
    .filter(t => filterSetup  === "Alle" || t.setup === filterSetup)
    .filter(t => filterResult === "Alle" || (filterResult === "Win" ? t.pnl > 0 : filterResult === "Loss" ? t.pnl < 0 : t.pnl === 0))
    .sort((a, b) => {
      if (sortBy === "date")    return b.date.localeCompare(a.date) || b.entry_time.localeCompare(a.entry_time);
      if (sortBy === "pnl")     return b.pnl - a.pnl;
      if (sortBy === "pnl_pct") return b.pnl_pct - a.pnl_pct;
      return 0;
    });

  // Kumulativ P&L kurve
  const cumPnl: number[] = [];
  let running = 0;
  [...trades].sort((a, b) => a.date.localeCompare(b.date)).forEach(t => { running += t.pnl; cumPnl.push(running); });
  const chartH = 60, chartW = 300;
  const chartMax = Math.max(...cumPnl, 1);
  const chartMin = Math.min(...cumPnl, 0);
  const chartRange = chartMax - chartMin || 1;
  function toY(v: number) { return chartH - ((v - chartMin) / chartRange) * chartH; }
  const points = cumPnl.map((v, i) => `${(i / Math.max(cumPnl.length - 1, 1)) * chartW},${toY(v)}`).join(" ");

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden", fontSize: fs }}>

      {/* ── Tabs ── */}
      <div style={{ display: "flex", borderBottom: "1px solid var(--border-default)", padding: "0 8px", gap: 4, flexShrink: 0 }}>
        {(["liste","statistik"] as const).map(tab => (
          <button key={tab} onClick={() => setActiveTab(tab)} style={{
            background: "none", border: "none", cursor: "pointer",
            padding: "6px 12px", fontSize: fs,
            color: activeTab === tab ? "var(--accent)" : "var(--text-muted)",
            borderBottom: activeTab === tab ? "2px solid var(--accent)" : "2px solid transparent",
            fontWeight: activeTab === tab ? 700 : 400,
          }}>
            {tab === "liste" ? "📋 Handel-liste" : "📊 Statistik"}
          </button>
        ))}
      </div>

      {/* ── STATISTIK TAB ── */}
      {activeTab === "statistik" && stats && (
        <div style={{ flex: 1, overflow: "auto", padding: "10px 12px" }}>
          <div style={{ marginBottom: 12 }}>
            <div style={{ fontSize: fsh, color: "var(--text-muted)", marginBottom: 6, textTransform: "uppercase", letterSpacing: "0.4px" }}>Kumulativ P&L</div>
            <svg width={chartW} height={chartH + 4} style={{ display: "block" }}>
              <line x1={0} y1={toY(0)} x2={chartW} y2={toY(0)} stroke="var(--border-default)" strokeWidth={1} strokeDasharray="4,3" />
              <polyline points={points} fill="none" stroke={running >= 0 ? "var(--bull)" : "var(--bear)"} strokeWidth={2} />
              {cumPnl.length > 0 && (
                <circle cx={(cumPnl.length - 1) / Math.max(cumPnl.length - 1, 1) * chartW} cy={toY(running)} r={3} fill={running >= 0 ? "var(--bull)" : "var(--bear)"} />
              )}
            </svg>
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 12 }}>
            <StatCard label="Total P&L"       value={formatUSD(stats.totalPnl)}     positive={stats.totalPnl >= 0} />
            <StatCard label="Win Rate"        value={`${stats.winRate.toFixed(1)}%`} sub={`${stats.numWins}W / ${stats.numLosses}L`} positive={stats.winRate >= 50} />
            <StatCard label="Profit Factor"   value={stats.profitFactor === Infinity ? "∞" : stats.profitFactor.toFixed(2)} positive={stats.profitFactor >= 1} />
            <StatCard label="Gns. Win"        value={formatUSD(stats.avgWin)}   positive={true} />
            <StatCard label="Gns. Tab"        value={formatUSD(stats.avgLoss)}  positive={false} />
            <StatCard label="Gns. Varighed"   value={`${stats.avgDuration.toFixed(0)} min`} />
            <StatCard label="Plan fulgt"      value={`${stats.followedPct.toFixed(0)}%`} positive={stats.followedPct >= 70} />
            <StatCard label="Bedste trade"    value={formatUSD(stats.bestTrade.pnl)}  sub={stats.bestTrade.ticker}  positive={true} />
            <StatCard label="Dårligste trade" value={formatUSD(stats.worstTrade.pnl)} sub={stats.worstTrade.ticker} positive={false} />
          </div>
          <div style={{ fontSize: fsh, color: "var(--text-muted)", marginBottom: 6, textTransform: "uppercase", letterSpacing: "0.4px" }}>P&L per setup</div>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr>
                <th style={colStyle("left")}>Setup</th>
                <th style={colStyle("right")}>Trades</th>
                <th style={colStyle("right")}>Win%</th>
                <th style={colStyle("right")}>P&L</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(stats.bySetup).sort((a, b) => b[1].pnl - a[1].pnl).map(([setup, data]) => (
                <tr key={setup}>
                  <td style={{ ...cellStyle("left"),  color: "var(--text-primary)" }}>{setup}</td>
                  <td style={{ ...cellStyle("right"), color: "var(--text-secondary)" }}>{data.count}</td>
                  <td style={{ ...cellStyle("right"), color: data.wins/data.count >= 0.5 ? "var(--bull)" : "var(--bear)" }}>{(data.wins/data.count*100).toFixed(0)}%</td>
                  <td style={{ ...cellStyle("right"), color: data.pnl >= 0 ? "var(--bull)" : "var(--bear)", fontWeight: 700 }}>{data.pnl >= 0 ? "+" : ""}{formatUSD(data.pnl)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* ── LISTE TAB ── */}
      {activeTab === "liste" && (
        <>
          <div style={{ display: "flex", gap: 8, padding: "6px 8px", borderBottom: "1px solid var(--border-subtle)", flexShrink: 0, flexWrap: "wrap", alignItems: "center" }}>
            <select value={filterSetup} onChange={e => setFilterSetup(e.target.value)}
              style={{ background: "var(--bg-elevated)", color: "var(--text-primary)", border: "1px solid var(--border-default)", borderRadius: 3, padding: "2px 6px", fontSize: fsh }}>
              <option>Alle</option>
              {SETUP_TYPES.map(s => <option key={s}>{s}</option>)}
            </select>
            <select value={filterResult} onChange={e => setFilterResult(e.target.value)}
              style={{ background: "var(--bg-elevated)", color: "var(--text-primary)", border: "1px solid var(--border-default)", borderRadius: 3, padding: "2px 6px", fontSize: fsh }}>
              <option>Alle</option><option>Win</option><option>Loss</option>
            </select>
            <select value={sortBy} onChange={e => setSortBy(e.target.value as any)}
              style={{ background: "var(--bg-elevated)", color: "var(--text-primary)", border: "1px solid var(--border-default)", borderRadius: 3, padding: "2px 6px", fontSize: fsh }}>
              <option value="date">Nyeste først</option>
              <option value="pnl">Bedste P&L</option>
              <option value="pnl_pct">Bedste %</option>
            </select>
            <span style={{ fontSize: fsh, color: "var(--text-muted)", marginLeft: "auto" }}>{filtered.length} handler</span>
          </div>

          <div style={{ flex: 1, overflow: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", tableLayout: "fixed" }}>
              <colgroup>
                <col style={{ width: 44 }} />
                <col style={{ width: 100 }} />
                <col style={{ width: 54 }} />
                <col style={{ width: 48 }} />
                <col style={{ width: 68 }} />
                <col style={{ width: 68 }} />
                <col style={{ width: 52 }} />
                <col style={{ width: 78 }} />
                <col style={{ width: 60 }} />
                <col style={{ width: 42 }} />
                <col style={{ width: 90 }} />
              </colgroup>
              <thead style={{ position: "sticky", top: 0, background: "var(--bg-surface)", zIndex: 2 }}>
                <tr>
                  <th style={colStyle("left")}>Dato</th>
                  <th style={colStyle("left")}>Tid</th>
                  <th style={colStyle("left")}>Aktie</th>
                  <th style={colStyle("left")}>Side</th>
                  <th style={colStyle("right")}>Entry</th>
                  <th style={colStyle("right")}>Exit</th>
                  <th style={colStyle("right")}>Stk.</th>
                  <th style={colStyle("right")}>P&amp;L</th>
                  <th style={colStyle("right")}>P&amp;L %</th>
                  <th style={colStyle("right")}>Min</th>
                  <th style={colStyle("left")}>Setup</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map(trade => {
                  const pnlColor = trade.pnl > 0 ? "var(--bull)" : trade.pnl < 0 ? "var(--bear)" : "var(--text-primary)";
                  return (
                    <tr key={trade.id} onClick={() => openTrade(trade)}
                      style={{ cursor: "pointer" }}
                      onMouseEnter={e => (e.currentTarget.style.background = "var(--bg-elevated)")}
                      onMouseLeave={e => (e.currentTarget.style.background = "transparent")}
                    >
                      <td style={{ ...cellStyle("left"),  color: "var(--text-muted)" }}>{trade.date.slice(5)}</td>
                      <td style={{ ...cellStyle("left"),  color: "var(--text-muted)" }}>{trade.entry_time}–{trade.exit_time}</td>
                      <td style={{ ...cellStyle("left"),  color: pnlColor, fontWeight: 700 }}>{trade.ticker}</td>
                      <td style={{ ...cellStyle("left"),  color: trade.side === "Long" ? "var(--bull)" : "var(--bear)" }}>{trade.side}</td>
                      <td style={{ ...cellStyle("right"), color: "var(--text-primary)" }}>{formatUSD(trade.entry_price)}</td>
                      <td style={{ ...cellStyle("right"), color: "var(--text-primary)" }}>{formatUSD(trade.exit_price)}</td>
                      <td style={{ ...cellStyle("right"), color: "var(--text-secondary)" }}>{trade.shares.toLocaleString()}</td>
                      <td style={{ ...cellStyle("right"), color: pnlColor, fontWeight: 700 }}>{trade.pnl >= 0 ? "+" : ""}{formatUSD(trade.pnl)}</td>
                      <td style={{ ...cellStyle("right"), color: pnlColor }}>{formatPct(trade.pnl_pct)}</td>
                      <td style={{ ...cellStyle("right"), color: "var(--text-muted)" }}>{trade.duration_min}</td>
                      <td style={{ ...cellStyle("left"),  color: "var(--text-secondary)" }}>
                        {trade.setup}
                        {!trade.followed_plan && (
                          <span style={{ fontSize: 9, color: "var(--bear)", marginLeft: 5, background: "rgba(255,40,40,0.12)", padding: "1px 4px", borderRadius: 2 }}>⚠</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}

      {/* ── DETALJE MODAL ── */}
      {selected && (
        <div style={{ position: "absolute", inset: 0, background: "rgba(0,0,0,0.75)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 9999 }}
          onClick={e => { if (e.target === e.currentTarget) setSelected(null); }}
        >
          <div style={{ background: "var(--bg-surface)", border: "1px solid var(--border-default)", borderRadius: 6, width: "min(580px, 92vw)", maxHeight: "85vh", overflow: "auto", padding: 18 }}>

            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 14 }}>
              <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
                <span style={{ fontSize: fst, fontWeight: 700, color: selected.pnl >= 0 ? "var(--bull)" : "var(--bear)" }}>{selected.ticker}</span>
                <span style={{ fontSize: fsh, color: "var(--text-muted)" }}>{selected.date}</span>
                <span style={{ fontSize: fsh, color: "var(--text-muted)" }}>{selected.entry_time} → {selected.exit_time}</span>
              </div>
              <button onClick={() => setSelected(null)} style={{ background: "none", border: "none", color: "var(--text-muted)", cursor: "pointer", fontSize: 18 }}>✕</button>
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 8, marginBottom: 14 }}>
              {([
                ["Side",         selected.side],
                ["Antal aktier", selected.shares.toLocaleString()],
                ["Entry pris",   formatUSD(selected.entry_price)],
                ["Exit pris",    formatUSD(selected.exit_price)],
                ["P&L",          `${selected.pnl >= 0 ? "+" : ""}${formatUSD(selected.pnl)}`],
                ["P&L %",        formatPct(selected.pnl_pct)],
                ["Varighed",     `${selected.duration_min} min`],
                ["Tidsramme",    selected.timeframe],
                ["Scanner",      selected.scanner],
                ["Setup",        selected.setup],
                ["Emotion",      selected.emotion],
                ["Plan fulgt",   selected.followed_plan ? "✓ Ja" : "✗ Nej"],
              ] as [string, string][]).map(([label, value]) => (
                <div key={label} style={{ background: "var(--bg-base)", borderRadius: 3, padding: "6px 8px" }}>
                  <div style={{ fontSize: 9, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.4px", marginBottom: 2 }}>{label}</div>
                  <div style={{
                    fontSize: fs, fontWeight: 600,
                    color: (label === "P&L" || label === "P&L %") ? (selected.pnl >= 0 ? "var(--bull)" : "var(--bear)")
                         : label === "Side"        ? (selected.side === "Long" ? "var(--bull)" : "var(--bear)")
                         : label === "Plan fulgt"  ? (selected.followed_plan ? "var(--bull)" : "var(--bear)")
                         : "var(--text-primary)",
                  }}>{value}</div>
                </div>
              ))}
            </div>

            {selected.tags.length > 0 && (
              <div style={{ marginBottom: 12, display: "flex", gap: 6, flexWrap: "wrap" }}>
                {selected.tags.map(tag => (
                  <span key={tag} style={{ fontSize: 10, background: "var(--bg-elevated)", border: "1px solid var(--border-subtle)", borderRadius: 3, padding: "2px 6px", color: "var(--text-secondary)" }}>{tag}</span>
                ))}
              </div>
            )}

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginBottom: 10 }}>
              <div>
                <label style={{ fontSize: fsh, color: "var(--text-muted)", display: "block", marginBottom: 4 }}>Setup</label>
                <select value={editSetup} onChange={e => setEditSetup(e.target.value as SetupType)}
                  style={{ width: "100%", background: "var(--bg-elevated)", color: "var(--text-primary)", border: "1px solid var(--border-default)", borderRadius: 3, padding: "4px 6px", fontSize: fs }}>
                  {SETUP_TYPES.map(s => <option key={s}>{s}</option>)}
                </select>
              </div>
              <div>
                <label style={{ fontSize: fsh, color: "var(--text-muted)", display: "block", marginBottom: 4 }}>Emotionel tilstand</label>
                <select value={editEmotion} onChange={e => setEditEmotion(e.target.value as Emotion)}
                  style={{ width: "100%", background: "var(--bg-elevated)", color: "var(--text-primary)", border: "1px solid var(--border-default)", borderRadius: 3, padding: "4px 6px", fontSize: fs }}>
                  {EMOTIONS.map(s => <option key={s}>{s}</option>)}
                </select>
              </div>
            </div>

            <div style={{ marginBottom: 10 }}>
              <label style={{ fontSize: fsh, color: "var(--text-muted)", display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}>
                <input type="checkbox" checked={editPlan} onChange={e => setEditPlan(e.target.checked)} />
                Fulgte min handelsplan
              </label>
            </div>

            <div style={{ marginBottom: 10 }}>
              <label style={{ fontSize: fsh, color: "var(--text-muted)", display: "block", marginBottom: 4 }}>Tags (kommasepareret)</label>
              <input value={editTags} onChange={e => setEditTags(e.target.value)}
                style={{ width: "100%", background: "var(--bg-elevated)", color: "var(--text-primary)", border: "1px solid var(--border-default)", borderRadius: 3, padding: "4px 6px", fontSize: fs, boxSizing: "border-box" }}
                placeholder="fx: gap-and-go, catalyst, FOMO" />
            </div>

            <div style={{ marginBottom: 14 }}>
              <label style={{ fontSize: fsh, color: "var(--text-muted)", display: "block", marginBottom: 4 }}>Noter</label>
              <textarea value={editNotes} onChange={e => setEditNotes(e.target.value)} rows={4}
                style={{ width: "100%", background: "var(--bg-elevated)", color: "var(--text-primary)", border: "1px solid var(--border-default)", borderRadius: 3, padding: "6px 8px", fontSize: fs, resize: "vertical", boxSizing: "border-box", fontFamily: "inherit" }}
                placeholder="Hvad gik godt? Hvad ville du gøre anderledes?" />
            </div>

            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
              <button onClick={() => setSelected(null)}
                style={{ background: "var(--bg-elevated)", border: "1px solid var(--border-default)", color: "var(--text-secondary)", borderRadius: 3, padding: "6px 14px", cursor: "pointer", fontSize: fs }}>
                Annuller
              </button>
              <button onClick={saveChanges}
                style={{ background: "var(--accent)", border: "none", color: "var(--bg-base)", borderRadius: 3, padding: "6px 14px", cursor: "pointer", fontSize: fs, fontWeight: 700 }}>
                ✓ Gem ændringer
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
