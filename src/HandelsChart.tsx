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
  shares?: number;
  pnl_pct?: number;
  variant?: string;
  entry_reason?: string;
  // Sat af fleet-endpointet: hvilken maskine handlen hoerer til.
  machine_id?: string;
  machine_name?: string;
  machine_url?: string | null;   // null = self (localhost); ellers ejer-maskinens URL
}

const ALGOS = [
  { source: "Konfluens 2",      label: "Konfluens 2" },
  { source: "Europa-reversion", label: "EUREVERSION" },
  { source: "BuyTheDip",        label: "BuyTheDip" },
  { source: "Trend Join Long",  label: "Trend Join Long" },
  { source: "Relativ Styrke",   label: "Relativ Styrke" },
  { source: "US-reversion",     label: "USREVERSION" },
  // Manuelle handler fra watchlist. Listen her er BAADE pill-listen og det
  // `sources`-filter der sendes til backenden — stod "manual" ikke her, blev
  // manuelle handler filtreret fra, selvom backenden returnerede dem.
  { source: "manual",           label: "Manuel" },
];
// PRÆCIS Studios pill-farver (index.html .pill-konfl2/.pill-rev/.pill-bd/.pill-tjl):
// tekstfarve + kant + subtil fyld. Én sandhedskilde for strategi-farverne her.
const STRAT_PILL: Record<string, { fg: string; border: string; bg: string }> = {
  "Konfluens 2":      { fg: "#5eead4", border: "rgba(45,212,191,0.55)",  bg: "rgba(45,212,191,0.16)" },
  "Europa-reversion": { fg: "#fde047", border: "rgba(250,204,21,0.55)",  bg: "rgba(250,204,21,0.16)" },
  "BuyTheDip":        { fg: "#f9a8d4", border: "rgba(244,114,182,0.55)", bg: "rgba(244,114,182,0.16)" },
  "Trend Join Long":  { fg: "#93c5fd", border: "rgba(96,165,250,0.55)",  bg: "rgba(96,165,250,0.16)" },
  "Relativ Styrke":   { fg: "#c4b5fd", border: "rgba(167,139,250,0.55)", bg: "rgba(167,139,250,0.16)" },
  "US-reversion":     { fg: "#67e8f9", border: "rgba(34,211,238,0.55)",  bg: "rgba(34,211,238,0.16)" },
  // Orange — samme som Studios .pill-manuel, saa en manuel handel ser ens ud
  // begge steder.
  "manual":           { fg: "#fdba74", border: "rgba(251,146,60,0.55)",  bg: "rgba(251,146,60,0.16)" },
};
const _FALLBACK_PILL = { fg: "#8a94a6", border: "rgba(138,148,166,0.5)", bg: "rgba(138,148,166,0.15)" };
const pillOf = (s: string) => STRAT_PILL[s] || _FALLBACK_PILL;
const COLOR_OF: Record<string, string> = Object.fromEntries(
  Object.entries(STRAT_PILL).map(([k, v]) => [k, v.fg]));

// Vist tekst pr. kilde. Uden denne stod den manuelle pill "manual" med smaat —
// databasens vaerdi, ikke et navn til et menneske. ALGOS baerer allerede
// etiketterne, saa de genbruges her frem for at skrive dem op igen.
const LABEL_OF: Record<string, string> = Object.fromEntries(
  ALGOS.map(a => [a.source, a.label]));

// Strategi-pill — præcis samme udtryk/farve som Studios pills.
function SourcePill({ source }: { source: string }) {
  const p = pillOf(source);
  return (
    <span style={{ display: "inline-block", padding: "2px 9px", borderRadius: 11,
      fontSize: 11.5, fontWeight: 700, whiteSpace: "nowrap",
      color: p.fg, background: p.bg, border: `1px solid ${p.border}` }}>
      {LABEL_OF[source] || source}
    </span>
  );
}

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
  const [names, setNames] = useState<Record<string, string>>({});   // ticker -> firmanavn

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
      if (!r.ok) {
        if (r.status === 404)
          throw new Error("Backenden er forbundet, men kører gammel kode uden fleet-endpointet "
            + "— genstart workstation-backenden (git pull + restart).");
        throw new Error(`HTTP ${r.status} — er backenden startet?`);
      }
      const j = await r.json();
      setTrades(j.trades || []);
      setMachines(j.machines || []);
    } catch (e: any) {
      setErr(e?.message || "Kunne ikke hente handler (netværksfejl — er backenden startet?)");
      setTrades([]); setMachines([]);
    } finally {
      setLoading(false);
    }
  }, [start, end, active, selPeers]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => { if (sel) setImgLoading(true); }, [sel, before, after]);

  // Firmanavne for de viste tickers (fra /ticker/info, cachet i state; fejl -> "").
  useEffect(() => {
    const missing = [...new Set(trades.map(t => t.symbol).filter(Boolean))].filter(s => !(s in names));
    if (missing.length === 0) return;
    let alive = true;
    (async () => {
      const res = await Promise.allSettled(missing.map(async sym => {
        const r = await fetch(`${API}/ticker/info?ticker=${encodeURIComponent(sym)}`);
        if (!r.ok) throw new Error();
        return ((await r.json()).name || "") as string;
      }));
      if (!alive) return;
      setNames(prev => {
        const next = { ...prev };
        res.forEach((r, i) => { next[missing[i]] = r.status === "fulfilled" ? r.value : ""; });
        return next;
      });
    })();
    return () => { alive = false; };
  }, [trades]);   // eslint-disable-line react-hooks/exhaustive-deps

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

  // Priser vises med den praecision aktien faktisk har — $9.165 maa ikke blive $9.17
  // naar man sammenligner med et stop paa $9.1346.
  const fmtPx = (p?: number) =>
    typeof p !== "number" ? "—" : p < 20 ? p.toFixed(3) : p.toFixed(2);

  // Holdetid i minutter, udledt af tidsstemplerne. Returnerer null hvis den bliver
  // negativ: entry stemples med BARENS tid og exit med vaegurets, saa korte handler
  // kan se ud til at slutte foer de begyndte (20 tilfaelde i juli 2026). Hellere
  // ingen varighed end en forkert.
  const holdMin = (t: Trade): number | null => {
    if (!t.entry_time_et || !t.exit_time_et) return null;
    const a = Date.parse(t.entry_time_et), b = Date.parse(t.exit_time_et);
    if (isNaN(a) || isNaN(b)) return null;
    const m = Math.round((b - a) / 60000);
    return m < 0 ? null : m;
  };

  // Rækker lukket af reconcile er ikke strategiens beslutning: exit-pris og -tid
  // afspejler intet, og P&L er altid 0. De skal kunne ses paa afstand.
  const isPhantom = (t: Trade) => (t.exit_reason || "").startsWith("reconcile");

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
            {ALGOS.map(a => {
              const p = pillOf(a.source); const on = active.has(a.source);
              return (
                <button key={a.source} onClick={() => toggle(a.source)}
                  style={{ cursor: "pointer", padding: "5px 10px", borderRadius: 14, fontSize: 11.5,
                    fontWeight: 700, border: `1px solid ${p.border}`, color: p.fg,
                    background: on ? p.bg : "transparent", opacity: on ? 1 : 0.55 }}>
                  {a.label}
                </button>
              );
            })}
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
          {err && <div style={{ padding: 16, color: "var(--bear)", fontSize: 15, lineHeight: 1.5 }}>⚠ {err}</div>}
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
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8 }}>
                  <span style={{ display: "flex", alignItems: "baseline", gap: 7, minWidth: 0 }}>
                    <span style={{ fontWeight: 800, fontSize: 18 }}>{t.symbol}</span>
                    {names[t.symbol] && <span style={{ fontSize: 12, color: "var(--text-muted)",
                      overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{names[t.symbol]}</span>}
                  </span>
                  <span style={{ fontWeight: 800, fontSize: 16.5, color: pnlColor(t.pnl), flexShrink: 0 }}>
                    {typeof t.pnl === "number" ? `${t.pnl >= 0 ? "+" : ""}$${t.pnl.toFixed(2)}` : "—"}
                  </span>
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center",
                  fontSize: 14, color: "var(--text-secondary)", marginTop: 5 }}>
                  <SourcePill source={t.source} />
                  <span>
                    {(t.side || "").toUpperCase()} · {t.exit_reason || "?"}
                    {typeof t.pnl_pct === "number" &&
                      <span style={{ color: pnlColor(t.pnl), marginLeft: 6, fontWeight: 700 }}>
                        {t.pnl_pct >= 0 ? "+" : ""}{t.pnl_pct.toFixed(2)}%
                      </span>}
                  </span>
                </div>
                {/* Antal, ind-/udpris og stop — nok til at bedømme handlen uden at
                    aabne den. Stoppets afstand til entry er tit hele forklaringen
                    paa et hurtigt stop-ud. */}
                <div style={{ fontSize: 13.5, color: "var(--text-secondary)", marginTop: 4 }}>
                  {typeof t.shares === "number" && <span>{t.shares} stk · </span>}
                  <span>${fmtPx(t.entry_price)} → ${fmtPx(t.exit_price)}</span>
                  {typeof t.current_stop === "number" &&
                    <span style={{ color: "var(--text-muted)" }}> · stop ${fmtPx(t.current_stop)}
                      {typeof t.entry_price === "number" && t.entry_price > 0 &&
                        ` (${((t.current_stop / t.entry_price - 1) * 100).toFixed(2)}%)`}
                    </span>}
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 13.5,
                  color: "var(--text-secondary)", marginTop: 4 }}>
                  <span>
                    {fmtTime(t.entry_time_et)} → {fmtTime(t.exit_time_et)}
                    {holdMin(t) !== null &&
                      <span style={{ color: "var(--text-muted)" }}> · {holdMin(t)} min</span>}
                  </span>
                  {t.machine_name && <span style={{ color: "var(--text-muted)" }}>🖥 {t.machine_name}</span>}
                </div>
                {/* Strategiens EGEN begrundelse (K2: "score=2, bricks=VBGEK··").
                    Det er det felt man sammenligner handler paa. */}
                {t.entry_reason &&
                  <div style={{ fontSize: 12.5, color: "var(--text-muted)", marginTop: 3,
                    overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {t.entry_reason}
                  </div>}
                {isPhantom(t) &&
                  <div style={{ fontSize: 12, color: "#ff9d4d", marginTop: 4, fontWeight: 700 }}>
                    ⚠ Fantom — lukket af reconcile, ikke af strategien. Tæller ikke med.
                  </div>}
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
                <span style={{ display: "flex", alignItems: "baseline", gap: 8, minWidth: 0 }}>
                  <span style={{ fontSize: 20, fontWeight: 800, cursor: onSelectTicker ? "pointer" : "default" }}
                    onClick={() => onSelectTicker?.(sel.symbol)} title={onSelectTicker ? "Vælg ticker" : ""}>
                    {sel.symbol}
                  </span>
                  {names[sel.symbol] && <span style={{ fontSize: 14, color: "var(--text-muted)",
                    overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{names[sel.symbol]}</span>}
                </span>
                <SourcePill source={sel.source} />
                <span style={{ fontSize: 15, color: pnlColor(sel.pnl), fontWeight: 800 }}>
                  P&amp;L {typeof sel.pnl === "number" ? `${sel.pnl >= 0 ? "+" : ""}$${sel.pnl.toFixed(2)}` : "—"}
                  {typeof sel.pnl_pct === "number" &&
                    ` (${sel.pnl_pct >= 0 ? "+" : ""}${sel.pnl_pct.toFixed(2)}%)`}
                </span>
                <span style={{ fontSize: 13.5, color: "var(--text-secondary)" }}>
                  {(sel.side || "").toUpperCase()}
                  {typeof sel.shares === "number" && ` ${sel.shares} stk`}
                  {` · entry $${fmtPx(sel.entry_price)} → exit $${fmtPx(sel.exit_price)} · ${sel.exit_reason || "?"}`}
                  {typeof sel.current_stop === "number" && ` · stop $${fmtPx(sel.current_stop)}`}
                  {typeof sel.current_target === "number" && ` · target $${fmtPx(sel.current_target)}`}
                  {holdMin(sel) !== null && ` · ${holdMin(sel)} min`}
                </span>
              </div>

              {/* Strategiens egen begrundelse + variant — det der forklarer HVORFOR
                  handlen blev taget, og hvad den skal sammenlignes med. */}
              {(sel.entry_reason || sel.variant) &&
                <div style={{ fontSize: 13, color: "var(--text-muted)", marginBottom: 8,
                  lineHeight: 1.5 }}>
                  {sel.entry_reason && <div>↳ {sel.entry_reason}</div>}
                  {sel.variant && <div style={{ opacity: 0.85 }}>variant: {sel.variant}</div>}
                </div>}

              {isPhantom(sel) &&
                <div style={{ fontSize: 13, color: "#ff9d4d", marginBottom: 8, fontWeight: 600 }}>
                  ⚠ Fantom-række: lukket af reconcile, ikke af strategien. Exit-pris og
                  -tidspunkt afspejler ingen beslutning, og P&amp;L er altid $0. Udelad den
                  fra enhver analyse.
                </div>}

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
                Chartet gen-tegnes altid på <b>algoserveren</b> (den har alle maskiners handler + TWS),
                uanset hvilken maskine du sidder ved. Vises det ikke: tjek at algoserverens TWS/Gateway er
                forbundet (bars genhentes live fra IBKR). Micro-caps ~6 mdr. tilbage i 1-min historik.
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
