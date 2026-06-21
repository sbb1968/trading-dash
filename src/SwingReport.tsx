import { useState } from "react";
import { openUrl } from "@tauri-apps/plugin-opener";
import { SwingChart } from "./SwingChart";

const API_JSON = "http://127.0.0.1:8000/swing/analyze_json";
// PDF: den paene rapport aabnes i EKSTERN browser (print sker der -> app-vinduet
// rammes ikke, saa man ikke ved et uheld lukker Trading Dash via dets X).
const API_HTML = "http://127.0.0.1:8000/swing/report.html";

// ---- Typer (matcher backendens analyze_json) -------------------------------
interface Factor { name: string; raw: number | null; signal: number; weight: number; weighted: number; }
interface Group { name: string; score: number; factors: Factor[]; }
interface Layer { score: number; band: string; weight: number; groups: Group[]; excluded: { name: string; why: string }[]; }
interface Driver { layer: string; name: string; contribution: number; signal: number; }
interface ChartLevel { price: number; kind: string; touches: number; size: string; }
interface ChartCandle { name: string; when: string; }
interface ChartData {
  current_price: number; structure: string;
  nearest_support: ChartLevel | null; nearest_resistance: ChartLevel | null;
  levels: ChartLevel[]; candles: ChartCandle[];
  swing_high: { t: string; price: number } | null;
  swing_low: { t: string; price: number } | null;
  bars: { t: string; o: number; h: number; l: number; c: number }[];
  pattern_needs_human: boolean;
}
interface SwingData {
  ticker: string; company: string | null; price: number | null; final: number; final_band: string;
  combined: number; gate: number; gate_straf: number;
  layers: { technical: Layer; fundamental: Layer; catalyst: Layer };
  drivers: { positive: Driver[]; negative: Driver[] };
  chart: ChartData | null;
  info: {
    eps_growth: number | null; dollar_vol: number | null;
    float_shares: number | null; float_pct: number | null;
    gap_pct: number | null; spread_pct: number | null;
    days_to_earnings: number | null; manual: number | null;
  };
}

// ---- Smaa hjaelpere ---------------------------------------------------------
// Backend sender ASCII-translittererede baand-navne; vis dem pyntet paa dansk.
const BAND_LABEL: Record<string, string> = {
  "STAERK SWING-KANDIDAT": "Stærk swing-kandidat",
  "EGNET MED FORBEHOLD": "Egnet med forbehold",
  "NEUTRAL-AFVENT": "Neutral – afvent",
  "SVAG": "Svag",
  "FRARAADES": "Frarådes",
  "Staerk": "Stærk", "Medvind": "Medvind", "Neutral": "Neutral",
  "Fraraades": "Frarådes",
};
const bandLabel = (b: string) => BAND_LABEL[b] ?? b;

function scoreColor(v: number): string {
  if (v >= 15) return "var(--bull)";
  if (v <= -15) return "var(--bear)";
  return "var(--neutral)";
}
function finalBandColor(b: string): string {
  if (b.startsWith("STAERK")) return "var(--bull)";
  if (b.startsWith("EGNET")) return "var(--neutral)";
  if (b.startsWith("SVAG") || b.startsWith("FRARAADES")) return "var(--bear)";
  return "var(--text-muted)";
}
const signed = (v: number, d = 0) => `${v > 0 ? "+" : ""}${v.toFixed(d)}`;
const fmtPct = (v: number | null, d = 1) => (v == null ? "—" : `${v.toFixed(d)}%`);
const fmtUSD = (v: number | null) =>
  v == null ? "—" : v >= 1e9 ? `$${(v / 1e9).toFixed(1)}B`
    : v >= 1e6 ? `$${(v / 1e6).toFixed(1)}M` : `$${Math.round(v).toLocaleString()}`;
const fmtShares = (v: number | null) =>
  v == null ? "—" : v >= 1e9 ? `${(v / 1e9).toFixed(1)}B`
    : v >= 1e6 ? `${(v / 1e6).toFixed(1)}M` : v.toLocaleString();

// ---- Genbrugskomponenter ----------------------------------------------------
function Badge({ text, color }: { text: string; color: string }) {
  return (
    <span style={{
      fontSize: 12, fontWeight: 700, color, border: `1px solid ${color}`,
      borderRadius: 4, padding: "1px 6px", whiteSpace: "nowrap",
      background: "var(--bg-overlay)",
    }}>{text}</span>
  );
}

// Signeret bjaelke, centreret i 0: gron til hoejre (positiv), rod til venstre.
function ScoreBar({ value }: { value: number }) {
  const pct = Math.min(Math.abs(value), 100);
  const pos = value >= 0;
  return (
    <div style={{
      position: "relative", height: 6, background: "var(--bg-overlay)",
      borderRadius: 3, overflow: "hidden",
    }}>
      <div style={{ position: "absolute", left: "50%", top: 0, bottom: 0, width: 1, background: "var(--border-strong)" }} />
      <div style={{
        position: "absolute", top: 0, bottom: 0,
        left: pos ? "50%" : `${50 - pct / 2}%`,
        width: `${pct / 2}%`, background: scoreColor(value),
      }} />
    </div>
  );
}

function LayerCard({ title, layer }: { title: string; layer: Layer }) {
  return (
    <div style={{
      flex: 1, minWidth: 0, background: "var(--bg-surface)",
      border: "1px solid var(--border-subtle)", borderRadius: 8, padding: 12,
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <span style={{ fontSize: 12, fontWeight: 700, color: "var(--text-primary)" }}>{title}</span>
        <span style={{ fontSize: 12, color: "var(--text-muted)" }}>{Math.round(layer.weight * 100)}%</span>
      </div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginTop: 4 }}>
        <span style={{ fontSize: 22, fontWeight: 800, color: scoreColor(layer.score) }}>{signed(layer.score)}</span>
        <Badge text={bandLabel(layer.band)} color={scoreColor(layer.score)} />
      </div>
      <div style={{ marginTop: 10, display: "flex", flexDirection: "column", gap: 8 }}>
        {layer.groups.map((g, i) => (
          <div key={i}>
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: 13, color: "var(--text-secondary)", marginBottom: 3 }}>
              <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{g.name}</span>
              <span style={{ color: scoreColor(g.score), fontWeight: 700, flexShrink: 0, marginLeft: 6 }}>{signed(g.score)}</span>
            </div>
            <ScoreBar value={g.score} />
          </div>
        ))}
        {layer.groups.length === 0 && (
          <span style={{ fontSize: 13, color: "var(--text-muted)" }}>Ingen faktorer i dette lag.</span>
        )}
      </div>
    </div>
  );
}

function DriverList({ title, color, items }: { title: string; color: string; items: Driver[] }) {
  return (
    <div style={{ flex: 1, minWidth: 0 }}>
      <div style={{ fontSize: 13, fontWeight: 700, color, marginBottom: 4 }}>{title}</div>
      {items.length === 0 && <div style={{ fontSize: 13, color: "var(--text-muted)" }}>Ingen.</div>}
      {items.map((d, i) => (
        <div key={i} style={{ display: "flex", justifyContent: "space-between", fontSize: 13, padding: "2px 0" }}>
          <span style={{ color: "var(--text-secondary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            <span style={{ color: "var(--text-muted)", fontSize: 11, textTransform: "uppercase", marginRight: 5 }}>{d.layer.slice(0, 4)}</span>
            {d.name}
          </span>
          <span style={{ color, fontWeight: 700, flexShrink: 0, marginLeft: 6 }}>{signed(d.contribution, 1)}</span>
        </div>
      ))}
    </div>
  );
}

function Chip({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 1, padding: "5px 9px", background: "var(--bg-elevated)", borderRadius: 6, minWidth: 84 }}>
      <span style={{ fontSize: 11, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.3px" }}>{label}</span>
      <span style={{ fontSize: 12, fontWeight: 700, color: "var(--text-primary)" }}>{value}</span>
    </div>
  );
}


function Slider({ label, value, onChange }: { label: string; value: number; onChange: (v: number) => void; }) {
  const col = value > 0 ? "var(--bull)" : value < 0 ? "var(--bear)" : "var(--text-muted)";
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      <span style={{ width: 110, fontSize: 13, color: "var(--text-muted)" }}>{label}</span>
      <input type="range" min={-100} max={100} step={5} value={value}
        onChange={e => onChange(Number(e.target.value))}
        style={{ flex: 1, accentColor: "var(--accent)" }} />
      <span style={{ width: 44, textAlign: "right", fontSize: 12, fontWeight: 700, color: col }}>{value > 0 ? "+" : ""}{value}</span>
    </div>
  );
}

// ============================================================================
export function SwingReport({ onSelectTicker }: { onSelectTicker?: (t: string) => void }) {
  const [ticker, setTicker]         = useState("");
  const [useOverlay, setUseOverlay] = useState(false);
  const [sr, setSr]                 = useState(0);
  const [pattern, setPattern]       = useState(0);
  const [candle, setCandle]         = useState(0);
  const [data, setData]             = useState<SwingData | null>(null);
  const [loading, setLoading]       = useState(false);
  const [error, setError]           = useState("");

  async function analyze() {
    const t = ticker.trim().toUpperCase();
    if (!t) { setError("Indtast en ticker"); return; }
    setLoading(true); setError(""); setData(null);
    try {
      const body: any = { ticker: t };
      if (useOverlay) { body.sr = sr; body.pattern = pattern; body.candle = candle; }
      const resp = await fetch(API_JSON, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      if (!resp.ok) {
        let detail = `HTTP ${resp.status}`;
        try { const e = await resp.json(); detail = e.detail || detail; } catch { /* ignore */ }
        setError(detail); return;
      }
      const d: SwingData = await resp.json();
      setData(d);
      if (onSelectTicker && d.ticker) onSelectTicker(d.ticker);
    } catch (e: any) {
      setError(`Kunne ikke naa backenden (koerer den paa :8000?): ${e?.message || e}`);
    } finally {
      setLoading(false);
    }
  }

  function exportPdf() {
    if (!data) return;
    // Aaben den paene rapport i ekstern browser; brugeren gemmer som PDF derfra.
    const p = new URLSearchParams({ ticker: data.ticker });
    if (useOverlay) { p.set("sr", String(sr)); p.set("pattern", String(pattern)); p.set("candle", String(candle)); }
    openUrl(`${API_HTML}?${p.toString()}`);
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", background: "var(--bg-base)", color: "var(--text-primary)" }}>
      {/* Kontrol-bar */}
      <div style={{ padding: "10px 12px", borderBottom: "1px solid var(--border-subtle)", display: "flex", flexDirection: "column", gap: 8, flexShrink: 0 }}>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <input type="text" placeholder="Ticker (fx MRVL)" value={ticker}
            onChange={e => { setTicker(e.target.value.toUpperCase()); setError(""); }}
            onKeyDown={e => { if (e.key === "Enter" && !loading) analyze(); }} maxLength={6}
            style={{ flex: 1, background: "var(--bg-input)", color: "var(--text-primary)", border: "1px solid var(--border-strong)", borderRadius: 4, padding: "6px 10px", fontSize: 14, fontWeight: 700, letterSpacing: "0.5px" }} />
          <button onClick={analyze} disabled={loading}
            style={{ background: loading ? "var(--bg-elevated)" : "var(--accent)", color: loading ? "var(--text-muted)" : "var(--bg-base)", border: "none", borderRadius: 4, padding: "6px 16px", fontSize: 13, fontWeight: 700, cursor: loading ? "default" : "pointer" }}>
            {loading ? "Analyserer..." : "Analyser"}
          </button>
          <button onClick={exportPdf} disabled={!data || loading}
            title="Aabn den paene rapport i browseren og gem som PDF"
            style={{ background: "var(--bg-elevated)", color: (!data || loading) ? "var(--text-muted)" : "var(--text-primary)", border: "1px solid var(--border-strong)", borderRadius: 4, padding: "6px 12px", fontSize: 13, fontWeight: 700, cursor: (!data || loading) ? "default" : "pointer" }}>
            PDF
          </button>
        </div>

        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 13, color: "var(--text-muted)", cursor: "pointer" }}>
          <input type="checkbox" checked={useOverlay} onChange={e => setUseOverlay(e.target.checked)} />
          Inkluder manuelt chart-overlay (din egen chart-vurdering)
        </label>

        {useOverlay && (
          <div style={{ display: "flex", flexDirection: "column", gap: 6, padding: "6px 4px 2px" }}>
            <Slider label="Support/modstand" value={sr}      onChange={setSr} />
            <Slider label="Chart-moenster"    value={pattern} onChange={setPattern} />
            <Slider label="Candlestick"       value={candle}  onChange={setCandle} />
            <span style={{ fontSize: 12, color: "var(--text-muted)" }}>-100 bearish · 0 neutral · +100 bullish. Flettes ind i det tekniske lag.</span>
          </div>
        )}
      </div>

      {/* Rapport-omraade */}
      <div style={{ flex: 1, overflow: "auto", minHeight: 0 }}>
        {error && (
          <div style={{ margin: 12, padding: "10px 12px", border: "1px solid var(--bear)", borderRadius: 4, color: "var(--bear)", fontSize: 13 }}>{error}</div>
        )}
        {!error && loading && (
          <div style={{ padding: 24, color: "var(--text-muted)", fontSize: 14 }}>Henter daglige bars (IBKR), fundamentaler og float ... tager et par sekunder.</div>
        )}
        {!error && !loading && !data && (
          <div style={{ padding: 24, color: "var(--text-muted)", fontSize: 14 }}>Indtast en ticker og tryk Analyser. Rapporten scorer aktien for swing-egnethed paa tvaers af teknik, fundamental og katalysator.</div>
        )}
        {!error && !loading && data && (
          <div style={{ padding: 14, display: "flex", flexDirection: "column", gap: 12 }}>
            {/* Header: ticker/pris + samlet score + baand */}
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", background: "var(--bg-surface)", border: "1px solid var(--border-subtle)", borderRadius: 8, padding: "12px 16px" }}>
              <div>
                <div style={{ fontSize: 24, fontWeight: 800, letterSpacing: "0.5px", color: "var(--text-primary)" }}>{data.ticker}{data.company ? <span style={{ fontSize: 14, fontWeight: 600, color: "var(--text-muted)", letterSpacing: "normal" }}> · {data.company}</span> : null}</div>
                {data.price != null && <div style={{ fontSize: 13, color: "var(--text-muted)" }}>${data.price.toFixed(2)}</div>}
              </div>
              <div style={{ textAlign: "right" }}>
                <div style={{ fontSize: 34, fontWeight: 800, lineHeight: 1, color: finalBandColor(data.final_band) }}>{signed(data.final)}</div>
                <div style={{ marginTop: 6 }}><Badge text={bandLabel(data.final_band)} color={finalBandColor(data.final_band)} /></div>
              </div>
            </div>

            {/* Gate-linje */}
            <div style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 13, color: "var(--text-muted)", padding: "0 4px" }}>
              <span>Kombineret <b style={{ color: "var(--text-secondary)" }}>{signed(data.combined)}</b></span>
              <span>×</span>
              <span>Tradability-gate <b style={{ color: data.gate >= 0.95 ? "var(--bull)" : data.gate >= 0.6 ? "var(--neutral)" : "var(--bear)" }}>{data.gate.toFixed(2)}</b></span>
              <span>→</span>
              <span>Samlet <b style={{ color: finalBandColor(data.final_band) }}>{signed(data.final)}</b></span>
            </div>

            {/* Tre lag */}
            <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
              <LayerCard title="Teknisk" layer={data.layers.technical} />
              <LayerCard title="Fundamental" layer={data.layers.fundamental} />
              <LayerCard title="Katalysator" layer={data.layers.catalyst} />
            </div>

            {/* Drivers */}
            <div style={{ display: "flex", gap: 16, background: "var(--bg-surface)", border: "1px solid var(--border-subtle)", borderRadius: 8, padding: 12 }}>
              <DriverList title="Medvind" color="var(--bull)" items={data.drivers.positive} />
              <div style={{ width: 1, background: "var(--border-subtle)" }} />
              <DriverList title="Modvind" color="var(--bear)" items={data.drivers.negative} />
            </div>

            {/* Info-chips */}
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <Chip label="EPS-vækst" value={fmtPct(data.info.eps_growth)} />
              <Chip label="Dollar-vol" value={fmtUSD(data.info.dollar_vol)} />
              <Chip label="Float" value={data.info.float_shares == null ? "—" : `${fmtShares(data.info.float_shares)}${data.info.float_pct != null ? ` (${data.info.float_pct.toFixed(0)}%)` : ""}`} />
              <Chip label="Gap" value={fmtPct(data.info.gap_pct)} />
              <Chip label="Spread" value={fmtPct(data.info.spread_pct, 2)} />
              <Chip label="Dage til earnings" value={data.info.days_to_earnings == null ? "—" : `${data.info.days_to_earnings}d`} />
            </div>

            {/* Annoteret chart fra chart-sektionen */}
            {data.chart && <SwingChart chart={data.chart} />}
          </div>
        )}
      </div>
    </div>
  );
}
