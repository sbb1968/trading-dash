import { useState } from "react";
import { openUrl } from "@tauri-apps/plugin-opener";

const API_JSON = "http://127.0.0.1:8000/buyhold/analyze_json";
const API_HTML = "http://127.0.0.1:8000/buyhold/report.html";

// ---- Typer (matcher buyhold_report.report_to_json) -------------------------
interface Factor { name: string; raw: string | number | null; signal: number; weight: number; weighted: number; }
interface Group { name: string; score: number; factors: Factor[]; }
interface Layer { score: number; band: string; weight: number; groups: Group[]; excluded: { name: string; why: string }[]; }
interface Driver { layer: string; name: string; contribution: number; signal: number; }
interface GateSignal { signal: string; factor: number; raw: string; }
interface OwnerEarnings {
  value: number; norm_years: number | null; method: string | null;
  maint_capex: number | null; ocf_latest: number | null; fcf_latest: number | null;
}
interface Tiles {
  market_cap: number | null; sector: string | null; pe: number | null;
  oe_yield: number | null; dividend_yield: number | null; payout: number | null;
  roic: number | null; altman_z: number | null; fcf_yield: number | null;
}
interface BuyHoldData {
  ticker: string; company: string | null; price: number | null; final: number; final_band: string;
  combined: number; gate: number; gate_straf: number;
  is_financial: boolean; fundamental_na: boolean;
  layers: { quality: Layer; growth: Layer; valuation: Layer; trend: Layer };
  drivers: { positive: Driver[]; negative: Driver[] };
  gate_breakdown: GateSignal[];
  owner_earnings: OwnerEarnings | null;
  tiles: Tiles;
}

// ---- Hjaelpere --------------------------------------------------------------
const BAND_LABEL: Record<string, string> = {
  "STAERK KOEB-OG-HOLD-KANDIDAT": "Stærk køb-og-hold-kandidat",
  "EGNET (langsigtet)": "Egnet (langsigtet)",
  "NEUTRAL / AFVENT": "Neutral – afvent",
  "SVAG (langsigtet)": "Svag (langsigtet)",
  "FRARAADES (langsigtet)": "Frarådes (langsigtet)",
  "Staerk": "Stærk", "Medvind": "Medvind", "Neutral": "Neutral", "Svag": "Svag", "Fraraades": "Frarådes",
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
const fmtPct = (v: number | null, d = 1, scale = 1) => (v == null ? "—" : `${(v * scale).toFixed(d)}%`);
const fmtUSD = (v: number | null) =>
  v == null ? "—" : Math.abs(v) >= 1e9 ? `$${(v / 1e9).toFixed(1)}B`
    : Math.abs(v) >= 1e6 ? `$${(v / 1e6).toFixed(1)}M` : `$${Math.round(v).toLocaleString()}`;

function Badge({ text, color }: { text: string; color: string }) {
  return (
    <span style={{
      fontSize: 12, fontWeight: 700, color, border: `1px solid ${color}`,
      borderRadius: 4, padding: "1px 6px", whiteSpace: "nowrap", background: "var(--bg-overlay)",
    }}>{text}</span>
  );
}

function ScoreBar({ value }: { value: number }) {
  const pct = Math.min(Math.abs(value), 100);
  const pos = value >= 0;
  return (
    <div style={{ position: "relative", height: 6, background: "var(--bg-overlay)", borderRadius: 3, overflow: "hidden" }}>
      <div style={{ position: "absolute", left: "50%", top: 0, bottom: 0, width: 1, background: "var(--border-strong)" }} />
      <div style={{ position: "absolute", top: 0, bottom: 0, left: pos ? "50%" : `${50 - pct / 2}%`, width: `${pct / 2}%`, background: scoreColor(value) }} />
    </div>
  );
}

function LayerCard({ title, layer }: { title: string; layer: Layer }) {
  return (
    <div style={{ flex: 1, minWidth: 0, background: "var(--bg-surface)", border: "1px solid var(--border-subtle)", borderRadius: 8, padding: 12 }}>
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
        {layer.groups.length === 0 && <span style={{ fontSize: 13, color: "var(--text-muted)" }}>Ingen faktorer (ekskluderet).</span>}
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
          <span style={{ color: "var(--text-secondary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{d.name}</span>
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

function gateFactorColor(f: number): string {
  if (f >= 0.7) return "var(--bull)";
  if (f >= 0.3) return "var(--neutral)";
  return "var(--bear)";
}

function FlagStripe({ text }: { text: string }) {
  return (
    <div style={{ background: "var(--bg-overlay)", border: "1px solid var(--neutral)", borderRadius: 6, padding: "8px 12px", fontSize: 13, color: "var(--neutral)" }}>
      ⚠ {text}
    </div>
  );
}

// ============================================================================
export function BuyHoldReport() {
  const [ticker, setTicker] = useState("");
  const [data, setData] = useState<BuyHoldData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function analyze() {
    const t = ticker.trim().toUpperCase();
    if (!t) { setError("Indtast en ticker"); return; }
    setLoading(true); setError(""); setData(null);
    try {
      const resp = await fetch(API_JSON, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ticker: t }),
      });
      if (!resp.ok) {
        let detail = `HTTP ${resp.status}`;
        try { const e = await resp.json(); detail = e.detail || detail; } catch { /* ignore */ }
        setError(detail); return;
      }
      setData(await resp.json());
    } catch (e: any) {
      setError(`Kunne ikke naa backenden (koerer den paa :8000?): ${e?.message || e}`);
    } finally {
      setLoading(false);
    }
  }

  function exportPdf(detail = false) {
    if (!data) return;
    const p = new URLSearchParams({ ticker: data.ticker });
    if (detail) p.set("detail", "1");
    openUrl(`${API_HTML}?${p.toString()}`);
  }

  const oe = data?.owner_earnings;
  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", background: "var(--bg-base)", color: "var(--text-primary)" }}>
      <div style={{ padding: "10px 12px", borderBottom: "1px solid var(--border-subtle)", display: "flex", gap: 8, alignItems: "center", flexShrink: 0 }}>
        <input type="text" placeholder="Ticker (fx KO)" value={ticker}
          onChange={e => { setTicker(e.target.value.toUpperCase()); setError(""); }}
          onKeyDown={e => { if (e.key === "Enter" && !loading) analyze(); }} maxLength={6}
          style={{ flex: 1, background: "var(--bg-input)", color: "var(--text-primary)", border: "1px solid var(--border-strong)", borderRadius: 4, padding: "6px 10px", fontSize: 14, fontWeight: 700, letterSpacing: "0.5px" }} />
        <button onClick={analyze} disabled={loading}
          style={{ background: loading ? "var(--bg-elevated)" : "var(--accent)", color: loading ? "var(--text-muted)" : "var(--bg-base)", border: "none", borderRadius: 4, padding: "6px 16px", fontSize: 13, fontWeight: 700, cursor: loading ? "default" : "pointer" }}>
          {loading ? "Analyserer..." : "Analyser"}
        </button>
        <button onClick={() => exportPdf(false)} disabled={!data || loading}
          title="Aabn den paene rapport i browseren og gem som PDF"
          style={{ background: "var(--bg-elevated)", color: (!data || loading) ? "var(--text-muted)" : "var(--text-primary)", border: "1px solid var(--border-strong)", borderRadius: 4, padding: "6px 12px", fontSize: 13, fontWeight: 700, cursor: (!data || loading) ? "default" : "pointer" }}>
          PDF
        </button>
        <button onClick={() => exportPdf(true)} disabled={!data || loading}
          title="Detaljeret PDF — alle faktor-tal pr. lag"
          style={{ background: "var(--bg-elevated)", color: (!data || loading) ? "var(--text-muted)" : "var(--text-primary)", border: "1px solid var(--border-strong)", borderRadius: 4, padding: "6px 12px", fontSize: 13, fontWeight: 700, cursor: (!data || loading) ? "default" : "pointer" }}>
          Detaljeret PDF
        </button>
      </div>

      <div style={{ flex: 1, overflow: "auto", minHeight: 0 }}>
        {error && <div style={{ margin: 12, padding: "10px 12px", border: "1px solid var(--bear)", borderRadius: 4, color: "var(--bear)", fontSize: 13 }}>{error}</div>}
        {!error && loading && <div style={{ padding: 24, color: "var(--text-muted)", fontSize: 14 }}>Henter fundamentaler (FMP) + uge-bars (IBKR) ... tager et par sekunder.</div>}
        {!error && !loading && !data && <div style={{ padding: 24, color: "var(--text-muted)", fontSize: 14 }}>Indtast en ticker og tryk Analyser. LANGSIGTET koeb-og-hold-vurdering: kvalitet, vaekst, vaerdiansaettelse, trend — gated paa strukturel risiko.</div>}
        {!error && !loading && data && (
          <div style={{ padding: 14, display: "flex", flexDirection: "column", gap: 12 }}>
            {/* Header */}
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", background: "var(--bg-surface)", border: "1px solid var(--border-subtle)", borderRadius: 8, padding: "12px 16px" }}>
              <div>
                <div style={{ fontSize: 24, fontWeight: 800, letterSpacing: "0.5px" }}>{data.ticker}{data.company ? <span style={{ fontSize: 14, fontWeight: 600, color: "var(--text-muted)", letterSpacing: "normal" }}> · {data.company}</span> : null}</div>
                <div style={{ fontSize: 13, color: "var(--text-muted)" }}>{data.price != null ? `$${data.price.toFixed(2)} · ` : ""}LANGSIGTET vurdering (min. 1 år)</div>
              </div>
              <div style={{ textAlign: "right" }}>
                <div style={{ fontSize: 34, fontWeight: 800, lineHeight: 1, color: finalBandColor(data.final_band) }}>{signed(data.final)}</div>
                <div style={{ marginTop: 6 }}><Badge text={bandLabel(data.final_band)} color={finalBandColor(data.final_band)} /></div>
              </div>
            </div>

            {data.fundamental_na && <FlagStripe text="Fundamentaldata utilgængelig for denne ticker — kun trend-laget vurderet; scoren er IKKE en fuld vurdering." />}
            {data.is_financial && <FlagStripe text="Begrænset model — finansiel sektor (capex/FCF/Owner-Earnings/gæld-faktorer udeladt)." />}

            {/* Score-linje */}
            <div style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 13, color: "var(--text-muted)", padding: "0 4px", flexWrap: "wrap" }}>
              <span>Kombineret <b style={{ color: "var(--text-secondary)" }}>{signed(data.combined)}</b></span><span>×</span>
              <span>Risiko-gate <b style={{ color: gateFactorColor(data.gate) }}>{data.gate.toFixed(2)}</b></span><span>→</span>
              <span>Samlet <b style={{ color: finalBandColor(data.final_band) }}>{signed(data.final)}</b></span>
            </div>

            {/* Fire lag */}
            <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
              <LayerCard title="Kvalitet" layer={data.layers.quality} />
              <LayerCard title="Vækst & holdbarhed" layer={data.layers.growth} />
              <LayerCard title="Værdiansættelse" layer={data.layers.valuation} />
              <LayerCard title="Langsigtet trend" layer={data.layers.trend} />
            </div>

            {/* Gate-blok m. nedbrydning */}
            <div style={{ background: "var(--bg-surface)", border: "1px solid var(--border-subtle)", borderRadius: 8, padding: 12 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span style={{ fontSize: 13, fontWeight: 700 }}>Risiko-gate</span>
                <span style={{ fontSize: 26, fontWeight: 800, color: gateFactorColor(data.gate) }}>{data.gate.toFixed(2)}</span>
              </div>
              <div style={{ fontSize: 11, color: "var(--text-muted)" }}>Strukturelle fatale fejl (konkurs/udvanding/FCF-distress) trækker gaten mod 0.</div>
              {data.gate < 1.0 && (
                <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 3 }}>
                  {data.gate_breakdown.map((gb, i) => (
                    <div key={i} style={{ display: "flex", justifyContent: "space-between", fontSize: 12 }}>
                      <span style={{ color: "var(--text-secondary)" }}>{gb.signal}</span>
                      <span style={{ display: "flex", gap: 10 }}>
                        <span style={{ color: "var(--text-muted)" }}>{gb.raw}</span>
                        <span style={{ color: gateFactorColor(gb.factor), fontWeight: 700, width: 36, textAlign: "right" }}>{gb.factor.toFixed(2)}</span>
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Owner Earnings-panel */}
            {oe && (
              <div style={{ background: "var(--bg-surface)", border: "1px solid var(--border-subtle)", borderRadius: 8, padding: 12 }}>
                <span style={{ fontSize: 13, fontWeight: 700 }}>Owner Earnings</span>
                <span style={{ fontSize: 11, color: "var(--text-muted)" }}> ({oe.norm_years}-års norm., est., {oe.method})</span>
                <div style={{ fontSize: 18, fontWeight: 800, marginTop: 2 }}>{fmtUSD(oe.value)}</div>
                <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>vedligeholds-capex {fmtUSD(oe.maint_capex)} · seneste år: OCF {fmtUSD(oe.ocf_latest)} / FCF {fmtUSD(oe.fcf_latest)}</div>
              </div>
            )}

            {/* Drivers */}
            <div style={{ display: "flex", gap: 16, background: "var(--bg-surface)", border: "1px solid var(--border-subtle)", borderRadius: 8, padding: 12 }}>
              <DriverList title="Medvind" color="var(--bull)" items={data.drivers.positive} />
              <div style={{ width: 1, background: "var(--border-subtle)" }} />
              <DriverList title="Modvind" color="var(--bear)" items={data.drivers.negative} />
            </div>

            {/* Tiles */}
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <Chip label="Markedsværdi" value={fmtUSD(data.tiles.market_cap)} />
              <Chip label="Sektor" value={data.tiles.sector ?? "—"} />
              <Chip label="P/E" value={data.tiles.pe == null ? "—" : data.tiles.pe.toFixed(1)} />
              <Chip label="OE-yield" value={fmtPct(data.tiles.oe_yield)} />
              <Chip label="FCF-yield" value={fmtPct(data.tiles.fcf_yield, 1, 100)} />
              <Chip label="Udbytte" value={data.tiles.dividend_yield == null ? "—" : `${(data.tiles.dividend_yield * 100).toFixed(1)}%${data.tiles.payout != null ? ` (${(data.tiles.payout * 100).toFixed(0)}%)` : ""}`} />
              <Chip label="ROIC" value={fmtPct(data.tiles.roic)} />
              <Chip label="Altman Z" value={data.tiles.altman_z == null ? "—" : data.tiles.altman_z.toFixed(2)} />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
