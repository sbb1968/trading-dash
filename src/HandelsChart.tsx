import { useEffect, useState, useCallback } from "react";

// Handels-chart (Stage B): trade-liste for ALLE 4 algoer + server-genereret PNG pr. handel,
// genskabt med algoens egne bar-parametre (40 bars før entry · handlen · 40 bars efter exit),
// med præcis grøn entry-pil og rød exit-pil. PNG hentes inline via direkte URL (workstation
// dev-mode-auth); trade-listen via JSON. Kræver TWS forbundet (bar-genhentning er et IBKR-kald).
const API = "http://127.0.0.1:8000";

interface Peer {
  id: string; name: string; host: string | null; url: string | null;
  enabled: boolean; is_self: boolean; selectable: boolean;
}
interface Machine { id: string; name: string; ok: boolean; n_trades: number; err?: string | null; }

interface Trade {
  trade_id: string;
  symbol: string;
  source: string;
  side: string;
  entry_time_et?: string;
  exit_time_et?: string;
  entry_price?: number;
  exit_price?: number;
  pnl?: number;
  exit_reason?: string;
  current_stop?: number;
  current_target?: number;
  // Sat af fleet-endpointet: hvilken maskine handlen hoerer til.
  machine_id?: string;
  machine_name?: string;
  machine_url?: string | null;   // null = self (localhost); ellers ejer-maskinens URL
}

const ALGOS = [
  { source: "Konfluens 2",      label: "Konfluens 2",     color: "#2563eb" },
  { source: "Europa-reversion", label: "EUREVERSION",     color: "#059669" },
  { source: "BuyTheDip",        label: "BuyTheDip",       color: "#d97706" },
  { source: "Trend Join Long",  label: "Trend Join Long", color: "#7c3aed" },
];
const COLOR_OF: Record<string, string> = Object.fromEntries(ALGOS.map(a => [a.source, a.color]));

function isoDaysAgo(n: number): string {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d.toISOString().slice(0, 10);
}

function fmtTime(et?: string): string {
  // entry_time_et er ISO ("2026-06-29T15:40:14..."); vis dato + klokkeslæt kort
  if (!et) return "—";
  const [d, t] = et.split("T");
  return `${d ?? ""} ${(t ?? "").slice(0, 5)}`.trim();
}

export function HandelsChart({ onSelectTicker }: { onSelectTicker?: (t: string) => void }) {
  const [start, setStart] = useState(isoDaysAgo(30));
  const [end, setEnd] = useState(isoDaysAgo(0));
  const [active, setActive] = useState<Set<string>>(new Set(ALGOS.map(a => a.source)));
  const [trades, setTrades] = useState<Trade[]>([]);
  const [sel, setSel] = useState<Trade | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [before, setBefore] = useState(40);
  const [after, setAfter] = useState(40);
  const [imgLoading, setImgLoading] = useState(false);
  // Maskin-vaelger (samme som Dagens Log) — fleet fan-out til de valgte maskiner.
  const [peers, setPeers] = useState<Peer[]>([]);
  const [selPeers, setSelPeers] = useState<Set<string>>(new Set());
  const [peersErr, setPeersErr] = useState("");
  const [machines, setMachines] = useState<Machine[]>([]);

  // Hent maskin-listen ved aabning; forvaelg alle valgbare.
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const r = await fetch(`${API}/fleet/peers`);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const data = await r.json();
        const ps: Peer[] = data.peers || [];
        if (!alive) return;
        setPeers(ps);
        setSelPeers(new Set(ps.filter(p => p.selectable).map(p => p.id)));
      } catch (e: any) {
        if (alive) setPeersErr(`Kunne ikke hente maskin-listen (:8000): ${e?.message || e}`);
      }
    })();
    return () => { alive = false; };
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setErr("");
    try {
      const srcs = [...active].join(",");
      const peersParam = [...selPeers].join(",");
      const url = `${API}/handels-chart/trades_fleet?start=${start}&end=${end}` +
        (srcs ? `&sources=${encodeURIComponent(srcs)}` : "") +
        `&peers=${encodeURIComponent(peersParam)}`;
      const r = await fetch(url);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j = await r.json();
      setTrades(j.trades || []);
      setMachines(j.machines || []);
    } catch (e: any) {
      setErr(e?.message || "Kunne ikke hente handler");
      setTrades([]); setMachines([]);
    } finally {
      setLoading(false);
    }
  }, [start, end, active, selPeers]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => { if (sel) setImgLoading(true); }, [sel, before, after]);

  const toggle = (src: string) => {
    setActive(prev => {
      const next = new Set(prev);
      if (next.has(src)) next.delete(src); else next.add(src);
      return next;
    });
  };
  const togglePeer = (id: string) => {
    setSelPeers(prev => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  const input: React.CSSProperties = {
    background: "var(--bg-base)", color: "var(--text-primary)",
    border: "1px solid var(--border-default)", borderRadius: 5, padding: "4px 7px", fontSize: 12,
  };
  const pnlColor = (p?: number) => (typeof p !== "number" ? "var(--text-secondary)"
    : p >= 0 ? "var(--bull, #16a34a)" : "var(--bear, #dc2626)");

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column",
      background: "var(--bg-base)", color: "var(--text-primary)" }}>
      {/* Header + filtre */}
      <div style={{ padding: "12px 14px 8px", borderBottom: "1px solid var(--border-subtle)" }}>
        <div style={{ fontSize: 16, fontWeight: 800, color: "var(--accent)", marginBottom: 6 }}>
          📈 Handels-chart
        </div>
        <div style={{ display: "flex", gap: 12, alignItems: "flex-end", flexWrap: "wrap" }}>
          <label style={{ display: "flex", flexDirection: "column", gap: 3, fontSize: 11,
            color: "var(--text-secondary)" }}>
            Fra
            <input type="date" value={start} max={end} onChange={e => setStart(e.target.value)} style={input} />
          </label>
          <label style={{ display: "flex", flexDirection: "column", gap: 3, fontSize: 11,
            color: "var(--text-secondary)" }}>
            Til
            <input type="date" value={end} min={start} onChange={e => setEnd(e.target.value)} style={input} />
          </label>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {ALGOS.map(a => (
              <button key={a.source} onClick={() => toggle(a.source)}
                style={{ cursor: "pointer", padding: "5px 9px", borderRadius: 14, fontSize: 11.5,
                  fontWeight: 700, border: `1px solid ${a.color}`,
                  background: active.has(a.source) ? a.color : "transparent",
                  color: active.has(a.source) ? "#fff" : a.color }}>
                {a.label}
              </button>
            ))}
          </div>
        </div>

        {/* Maskin-vælger (samme som Dagens Log) */}
        <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap", marginTop: 8 }}>
          <span style={{ fontSize: 11, color: "var(--text-muted)", fontWeight: 600 }}>Maskiner:</span>
          {peersErr && <span style={{ fontSize: 11, color: "var(--bear)" }}>{peersErr}</span>}
          {peers.map(pe => (
            <label key={pe.id} title={pe.selectable ? undefined : "Ikke opsat endnu"}
              style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 11.5,
                cursor: pe.selectable ? "pointer" : "not-allowed",
                color: pe.selectable ? "var(--text-secondary)" : "var(--text-muted)",
                opacity: pe.selectable ? 1 : 0.5 }}>
              <input type="checkbox" disabled={!pe.selectable}
                checked={selPeers.has(pe.id)} onChange={() => togglePeer(pe.id)} />
              {pe.name}
              {pe.is_self && <span style={{ color: "var(--text-muted)" }}> (denne maskine)</span>}
              {!pe.selectable && <span style={{ color: "var(--text-muted)" }}> (ikke opsat)</span>}
            </label>
          ))}
        </div>

        {/* Status pr. maskine (kun hvis en fjern-maskine fejlede) */}
        {machines.some(m => !m.ok) && (
          <div style={{ fontSize: 11, color: "var(--bear)", marginTop: 5 }}>
            {machines.filter(m => !m.ok).map(m => `${m.name}: kunne ikke nås`).join(" · ")}
          </div>
        )}
      </div>

      {/* Indhold: trade-liste (venstre) + chart (højre) */}
      <div style={{ flex: 1, display: "flex", minHeight: 0 }}>
        {/* Trade-liste */}
        <div style={{ width: 460, borderRight: "1px solid var(--border-subtle)", overflow: "auto", flexShrink: 0 }}>
          {loading && <div style={{ padding: 16, color: "var(--text-secondary)", fontSize: 15 }}>Henter handler…</div>}
          {err && <div style={{ padding: 16, color: "var(--bear)", fontSize: 15 }}>⚠ {err} — er backenden startet?</div>}
          {!loading && !err && trades.length === 0 &&
            <div style={{ padding: 16, color: "var(--text-secondary)", fontSize: 15, lineHeight: 1.5 }}>
              Ingen lukkede handler i intervallet for de valgte algoer og maskiner.
            </div>}
          {trades.map(t => {
            const key = `${t.machine_id || ""}-${t.trade_id}`;
            const isSel = sel?.trade_id === t.trade_id && sel?.machine_id === t.machine_id;
            return (
              <div key={key} onClick={() => setSel(t)}
                style={{ padding: "11px 14px", cursor: "pointer", borderBottom: "1px solid var(--border-subtle)",
                  background: isSel ? "var(--bg-elevated)" : "transparent",
                  borderLeft: `4px solid ${isSel ? (COLOR_OF[t.source] || "var(--accent)") : "transparent"}` }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                  <span style={{ fontWeight: 800, fontSize: 18 }}>{t.symbol}</span>
                  <span style={{ fontWeight: 800, fontSize: 16.5, color: pnlColor(t.pnl) }}>
                    {typeof t.pnl === "number" ? `${t.pnl >= 0 ? "+" : ""}$${t.pnl.toFixed(2)}` : "—"}
                  </span>
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 14,
                  color: "var(--text-secondary)", marginTop: 4 }}>
                  <span style={{ color: COLOR_OF[t.source] || "var(--text-secondary)", fontWeight: 600 }}>{t.source}</span>
                  <span>{(t.side || "").toUpperCase()} · {t.exit_reason || "?"}</span>
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 13.5,
                  color: "var(--text-secondary)", marginTop: 4 }}>
                  <span>{fmtTime(t.entry_time_et)} → {fmtTime(t.exit_time_et)}</span>
                  {t.machine_name && <span style={{ color: "var(--text-muted)" }}>🖥 {t.machine_name}</span>}
                </div>
              </div>
            );
          })}
        </div>

        {/* Chart + metadata */}
        <div style={{ flex: 1, display: "flex", flexDirection: "column", padding: 12,
          minWidth: 0, minHeight: 0 }}>
          {!sel && (
            <div style={{ color: "var(--text-secondary)", fontSize: 15, lineHeight: 1.6, paddingTop: 40,
              textAlign: "center" }}>
              Vælg en handel i listen for at se chartet.<br />
              <span style={{ fontSize: 13 }}>
                Chartet genskabes fra IBKR med algoens egne bar-parametre — {before} bars før entry,
                selve handlen, {after} bars efter exit. Grøn pil = entry, rød pil = exit.
              </span>
            </div>
          )}
          {sel && (
            <>
              <div style={{ display: "flex", gap: 16, alignItems: "center", flexWrap: "wrap",
                marginBottom: 8 }}>
                <span style={{ fontSize: 20, fontWeight: 800, cursor: onSelectTicker ? "pointer" : "default" }}
                  onClick={() => onSelectTicker?.(sel.symbol)} title={onSelectTicker ? "Vælg ticker" : ""}>
                  {sel.symbol}
                </span>
                <span style={{ fontSize: 15, color: COLOR_OF[sel.source] || "var(--text-secondary)", fontWeight: 700 }}>
                  {sel.source}
                </span>
                <span style={{ fontSize: 15, color: pnlColor(sel.pnl), fontWeight: 800 }}>
                  P&amp;L {typeof sel.pnl === "number" ? `${sel.pnl >= 0 ? "+" : ""}$${sel.pnl.toFixed(2)}` : "—"}
                </span>
                <span style={{ fontSize: 13.5, color: "var(--text-secondary)" }}>
                  {(sel.side || "").toUpperCase()} · entry ${sel.entry_price?.toFixed(2) ?? "—"} → exit
                  ${sel.exit_price?.toFixed(2) ?? "—"} · {sel.exit_reason || "?"}
                  {typeof sel.current_stop === "number" && ` · stop $${sel.current_stop.toFixed(2)}`}
                  {typeof sel.current_target === "number" && ` · target $${sel.current_target.toFixed(2)}`}
                </span>
              </div>

              <div style={{ display: "flex", gap: 12, alignItems: "center", marginBottom: 8, fontSize: 13,
                color: "var(--text-secondary)" }}>
                <label style={{ display: "flex", gap: 5, alignItems: "center" }}>
                  Bars før
                  <input type="number" min={5} max={200} value={before}
                    onChange={e => setBefore(Math.max(5, Math.min(200, +e.target.value || 40)))}
                    style={{ ...input, width: 64 }} />
                </label>
                <label style={{ display: "flex", gap: 5, alignItems: "center" }}>
                  Bars efter
                  <input type="number" min={5} max={200} value={after}
                    onChange={e => setAfter(Math.max(5, Math.min(200, +e.target.value || 40)))}
                    style={{ ...input, width: 64 }} />
                </label>
                {imgLoading && <span>⏳ genhenter bars fra IBKR…</span>}
              </div>

              {/* Fyld højden, bevar konstant candle-bredde: korte charts centreres,
                  lange scroller vandret (width:fit-content + margin:auto undgår flex-
                  centrerings-clip så man kan scrolle fra venstre). */}
              <div style={{ flex: 1, minHeight: 0, overflow: "auto" }}>
                <div style={{ height: "100%", width: "fit-content", margin: "0 auto" }}>
                  <img
                    key={`${sel.machine_id || ""}-${sel.trade_id}-${before}-${after}`}
                    src={`${sel.machine_url || API}/handels-chart/trade/${sel.trade_id}.png?bars_before=${before}&bars_after=${after}`}
                    alt={`Chart for ${sel.symbol}`}
                    onLoad={() => setImgLoading(false)}
                    onError={() => setImgLoading(false)}
                    style={{ height: "100%", width: "auto", display: "block",
                      borderRadius: 6, border: "1px solid var(--border-subtle)",
                      opacity: imgLoading ? 0.4 : 1, transition: "opacity .2s" }}
                  />
                </div>
              </div>
              <div style={{ marginTop: 6, fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.4 }}>
                Chartet genskabes på {sel.machine_url ? `${sel.machine_name} (fjern-maskine)` : "denne maskine"} —
                hvis det ikke vises: tjek at DEN maskines TWS/Gateway er forbundet (bars genhentes live fra IBKR).
                Micro-caps ~6 mdr. tilbage i 1-min historik.
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
