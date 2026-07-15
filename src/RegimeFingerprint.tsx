import { useState, useEffect } from "react";

// ── Typer (loest — matcher regime_fingerprint_output/fingerprint_*.json) ──
interface Smallcap {
  coverage?: { names: number; day_points: number; days: number };
  m1_gap_follow_through_rate?: number | null;
  m1_median_fade_depth_pct?: number | null;
  m2_intraday_autocorr_5min?: number | null;
  m3_median_5min_atr_pct?: number | null;
  m3_median_daily_range_pct?: number | null;
  m4_hod_bins_pct?: Record<string, number>;
  m4_hod_morning_dominated?: boolean;
  m5_breadth_pct_green?: number | null;
  m5_name_dispersion_pct?: number | null;
}
interface FutMetric {
  coverage_days?: number;
  m7_daily_autocorr?: number | null;
  m7_continuation_rate?: number | null;
  m8_overnight_sum_pct?: number | null;
  m8_intraday_sum_pct?: number | null;
  m8_overnight_intraday_ratio?: number | null;
  m9_rvol_short_10d?: number | null;
  m9_rvol_long_40d?: number | null;
  m9_term_ratio_short_long?: number | null;
}
interface SpreadMetric {
  coverage_days?: number;
  m10_spread_autocorr1?: number | null;
  m10_VR2?: number | null;
  m10_VR5?: number | null;
  m10_VR10?: number | null;
  m10_half_life_bars?: number | null;
}
interface WindowBlock {
  span?: [string, string];
  futures?: Record<string, FutMetric>;
  spread?: Record<string, SpreadMetric>;
  smallcap?: Smallcap;
  error?: string;
}
interface Fingerprint {
  run_date?: string;
  windows?: Record<string, WindowBlock>;
  notes?: string[];
  verdict?: string[];
}
interface LatestResp {
  status: "ok" | "none" | "error";
  generated_file?: string;
  generated_at?: string;
  fingerprint?: Fingerprint;
  summary_text?: string;
  error?: string;
}

// ── Smaa hjaelpere ────────────────────────────────────────────
const fmt = (v: number | null | undefined, d = 2): string =>
  (v === null || v === undefined || Number.isNaN(v)) ? "n/a"
    : (Math.abs(v) < 100 ? v.toFixed(d) : v.toFixed(0));

function Dot({ c }: { c: string }) {
  return <span style={{ display: "inline-block", width: 8, height: 8, borderRadius: "50%", background: c, marginRight: 6, flexShrink: 0 }} />;
}
function InfoBox({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ background: "var(--bg-base)", borderRadius: 4, padding: "6px 10px", marginTop: 8, fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.55, borderLeft: "2px solid var(--border-default)" }}>
      {children}
    </div>
  );
}
function Row({ label, value, dot }: { label: string; value: React.ReactNode; dot?: string }) {
  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "3px 0", borderBottom: "1px solid var(--border-subtle)" }}>
      <span style={{ fontSize: 13, color: "var(--text-secondary)", display: "flex", alignItems: "center" }}>{dot && <Dot c={dot} />}{label}</span>
      <span style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}>{value}</span>
    </div>
  );
}

// recent vs prior -> pil + farve (retningen er neutral-beskrivende; groen=op, roed=ned)
function Shift({ label, recent, prior, d = 2 }: { label: string; recent?: number | null; prior?: number | null; d?: number }) {
  const has = recent !== null && recent !== undefined && prior !== null && prior !== undefined;
  const up = has && (recent as number) > (prior as number);
  const arrow = !has ? "" : up ? " ▲" : (recent as number) < (prior as number) ? " ▼" : " =";
  const col = !has ? "var(--text-secondary)" : up ? "var(--bull)" : (recent as number) < (prior as number) ? "var(--bear)" : "var(--text-secondary)";
  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "3px 0", borderBottom: "1px solid var(--border-subtle)" }}>
      <span style={{ fontSize: 13, color: "var(--text-secondary)" }}>{label}</span>
      <span style={{ fontSize: 13, fontWeight: 600, color: col }}>
        {fmt(prior, d)} → {fmt(recent, d)}<span>{arrow}</span>
      </span>
    </div>
  );
}

const card = (extra?: React.CSSProperties): React.CSSProperties => ({
  background: "var(--bg-elevated)", border: "1px solid var(--border-subtle)", borderRadius: 6, padding: "10px 12px", marginBottom: 10, ...extra,
});
const H = ({ children }: { children: React.ReactNode }) =>
  <div style={{ fontSize: 13, fontWeight: 700, color: "var(--text-primary)", marginBottom: 6, textTransform: "uppercase", letterSpacing: 0.4 }}>{children}</div>;

// ══════════════════════════════════════════════════════════════
export function RegimeFingerprint() {
  const [resp, setResp] = useState<LatestResp | null>(null);
  const [loading, setLoading] = useState(true);
  const [copied, setCopied] = useState(false);

  async function load() {
    setLoading(true);
    try {
      const r = await fetch("http://127.0.0.1:8000/regime-fingerprint/latest");
      setResp(await r.json());
    } catch {
      setResp({ status: "error", error: "Kunne ikke naa backend" });
    }
    setLoading(false);
  }
  useEffect(() => { load(); }, []);

  function copyToClaude() {
    const txt = resp?.summary_text || "";
    const when = resp?.generated_at ? new Date(resp.generated_at).toLocaleString("da-DK") : "?";
    const full = `Regime-fingeraftryk genereret ${when} — indsat fra Trading Dash:\n\n${txt}`;
    navigator.clipboard.writeText(full).then(() => { setCopied(true); setTimeout(() => setCopied(false), 2000); });
  }

  const wrap: React.CSSProperties = { padding: 14, height: "100%", overflowY: "auto", background: "var(--bg-base)", color: "var(--text-primary)" };

  if (loading && !resp) return <div style={wrap}>Indlaeser regime-fingeraftryk…</div>;

  const ok = resp?.status === "ok" && !!resp.fingerprint;
  const fp = resp?.fingerprint;
  const rec = fp?.windows?.recent;
  const pri = fp?.windows?.prior;
  const hasDetail = !!(ok && rec && rec.futures);

  // regime-skift-kilder (garderet)
  const recSc = rec?.smallcap, priSc = pri?.smallcap;
  const recES = rec?.futures?.ES, priES = pri?.futures?.ES;

  return (
    <div style={wrap}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
        <div style={{ fontSize: 15, fontWeight: 700 }}>Regime-fingeraftryk</div>
        <div style={{ display: "flex", gap: 8 }}>
          <button onClick={load} style={btn("var(--bg-elevated)")}>Opdater</button>
          {ok && <button onClick={copyToClaude} style={btn("var(--accent, #2563eb)")}>{copied ? "Kopieret!" : "Kopier til Claude"}</button>}
        </div>
      </div>

      {!ok && (
        <div style={card()}>
          {resp?.status === "none"
            ? "Intet fingeraftryk endnu — koeres mandag morgen paa algoserveren."
            : `Kunne ikke hente fingeraftryk${resp?.error ? " (" + resp.error + ")" : ""}.`}
        </div>
      )}

      {ok && (
        <>
          <div style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 10 }}>
            Genereret: {resp?.generated_at ? new Date(resp.generated_at).toLocaleString("da-DK") : "?"} · {resp?.generated_file}
            <span style={{ marginLeft: 8, color: "var(--neutral, #f59e0b)" }}>Beskrivende — ingen handler, ingen edge-paastand.</span>
          </div>

          {/* Verdikt */}
          <div style={card({ borderLeft: "3px solid var(--bull)" })}>
            <H>Verdikt (recent)</H>
            {fp?.verdict && fp.verdict.length > 0
              ? fp.verdict.map((v, i) => <div key={i} style={{ fontSize: 13, fontWeight: 600, padding: "2px 0" }}>→ {v}</div>)
              : <div style={{ fontSize: 13, color: "var(--text-secondary)" }}>Ingen ny familie klart favoriseret (tving ikke en beslutning).</div>}
          </div>

          {/* Regime-skift */}
          {hasDetail && (
            <div style={card()}>
              <H>Regime-skift (prior → recent)</H>
              <Shift label="Gap follow-through-rate" recent={recSc?.m1_gap_follow_through_rate} prior={priSc?.m1_gap_follow_through_rate} />
              <Shift label="Intraday 5-min autokorr" recent={recSc?.m2_intraday_autocorr_5min} prior={priSc?.m2_intraday_autocorr_5min} d={3} />
              <Shift label="ES overnight/intraday-ratio" recent={recES?.m8_overnight_intraday_ratio} prior={priES?.m8_overnight_intraday_ratio} />
              <Shift label="Navne-dispersion %" recent={recSc?.m5_name_dispersion_pct} prior={priSc?.m5_name_dispersion_pct} />
              <InfoBox>▲ = steget siden prior, ▼ = faldet. Store skift = regimet har aendret sig.</InfoBox>
            </div>
          )}

          {/* Small-cap lag */}
          {recSc && recSc.coverage && (
            <div style={card()}>
              <H>Small-cap lag (recent, navne={recSc.coverage.names}, dage={recSc.coverage.days})</H>
              <Row label="Gap follow-through-rate" value={fmt(recSc.m1_gap_follow_through_rate)}
                   dot={(recSc.m1_gap_follow_through_rate ?? 0) > 0.55 ? "var(--bull)" : "var(--text-secondary)"} />
              <Row label="Intraday 5-min autokorr" value={fmt(recSc.m2_intraday_autocorr_5min, 3)} />
              <Row label="Median daglig range %" value={fmt(recSc.m3_median_daily_range_pct)} />
              <Row label="Breadth % groenne" value={fmt(recSc.m5_breadth_pct_green, 1)} />
              <Row label="Navne-dispersion %" value={fmt(recSc.m5_name_dispersion_pct)}
                   dot={(recSc.m5_name_dispersion_pct ?? 0) > 3 ? "var(--bull)" : "var(--text-secondary)"} />
              <Row label="HOD morgen-domineret" value={recSc.m4_hod_morning_dominated ? "ja" : "nej"} />
            </div>
          )}

          {/* Index + spread lag */}
          {hasDetail && (
            <div style={card()}>
              <H>Index / futures (recent)</H>
              {Object.entries(rec!.futures!).map(([idx, m]) => (
                <Row key={idx} label={`${idx}: trend-persistens / overnight-ratio`}
                     value={`${fmt(m.m7_daily_autocorr, 2)} / ${fmt(m.m8_overnight_intraday_ratio, 2)}`}
                     dot={(m.m8_overnight_intraday_ratio ?? 0) > 1.5 ? "var(--neutral, #f59e0b)" : "var(--text-secondary)"} />
              ))}
              <div style={{ height: 6 }} />
              <H>Spread (kerne for spor A) — VR5 / half-life</H>
              {rec!.spread && Object.entries(rec!.spread).map(([pr, s]) => (
                <Row key={pr} label={pr}
                     value={"m10_VR5" in s ? `VR5 ${fmt(s.m10_VR5)} · hl ${fmt(s.m10_half_life_bars)}` : `kun ${s.coverage_days ?? 0} dage`}
                     dot={(s.m10_VR5 ?? 2) < 0.9 ? "var(--bull)" : "var(--bear)"} />
              ))}
              <InfoBox>VR5 &lt; 0,9 (groen prik) = spread mean-reverting NU → spor A levedygtigt. VR5 &gt; 1 = trender.</InfoBox>
            </div>
          )}

          {/* Coverage / forbehold */}
          {fp?.notes && fp.notes.length > 0 && (
            <div style={card()}>
              <H>Coverage / forbehold</H>
              {fp.notes.map((n, i) => <div key={i} style={{ fontSize: 12, color: "var(--text-secondary)", padding: "1px 0" }}>• {n}</div>)}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function btn(bg: string): React.CSSProperties {
  return { background: bg, color: "var(--text-primary)", border: "1px solid var(--border-subtle)", borderRadius: 5, padding: "5px 12px", fontSize: 12, cursor: "pointer" };
}
