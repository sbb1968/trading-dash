import { useState, useEffect } from "react";

// ── Typer (loest — matcher regime_fingerprint_output/fingerprint_*.json) ──
interface Smallcap {
  coverage?: { names: number; day_points: number; days: number };
  m1_gap_follow_through_rate?: number | null;
  m2_intraday_autocorr_5min?: number | null;
  m3_median_daily_range_pct?: number | null;
  m4_hod_morning_dominated?: boolean;
  m5_breadth_pct_green?: number | null;
  m5_name_dispersion_pct?: number | null;
}
interface FutMetric {
  m7_daily_autocorr?: number | null;
  m8_overnight_intraday_ratio?: number | null;
}
interface WindowBlock {
  span?: [string, string];
  futures?: Record<string, FutMetric>;
  smallcap?: Smallcap;
  error?: string;
}
interface Fingerprint {
  run_date?: string;
  windows?: Record<string, WindowBlock>;
  notes?: string[];
  regime_label?: string;
  prior_regime_label?: string;
}
type HistRow = Record<string, string>;
interface LatestResp {
  status: "ok" | "none" | "error";
  generated_file?: string;
  generated_at?: string;
  regime_label?: string;
  fingerprint?: Fingerprint;
  briefing_text?: string;
  summary_text?: string;
  history?: HistRow[];
  error?: string;
}

// ── Smaa hjaelpere ────────────────────────────────────────────
const num = (v: unknown): number | null => {
  const n = typeof v === "number" ? v : parseFloat(String(v));
  return Number.isFinite(n) ? n : null;
};
const fmt = (v: number | null | undefined, d = 2): string =>
  (v === null || v === undefined || Number.isNaN(v)) ? "n/a"
    : (Math.abs(v) < 100 ? v.toFixed(d) : v.toFixed(0));
const meanPersist = (b?: WindowBlock): number | null => {
  const xs = Object.values(b?.futures ?? {}).map(f => f.m7_daily_autocorr).filter((x): x is number => x !== null && x !== undefined);
  return xs.length ? xs.reduce((a, c) => a + c, 0) / xs.length : null;
};

// regime-etiket -> menneskelig visning (titel/beskrivelse/strategifamilie/farve)
function regimeInfo(label?: string): { title: string; desc: string; family: string; color: string } {
  const l = label ?? "";
  if (l.startsWith("Stock-picking")) return {
    title: "Stock-picking-marked",
    desc: "De rigtige navne løber, de forkerte gør ikke — men indekset som helhed har ingen pålidelig retning. Fordelen ligger i at VÆLGE mellem navne, ikke i at ride markedet.",
    family: "Relativ Styrke (tværsnitlig rangering)", color: "var(--bull)",
  };
  if (l.startsWith("Momentum")) return {
    title: "Momentum-marked",
    desc: "Det stærke fortsætter, morgenretningen holder, og bevægelsen er koncentreret tidligt på dagen.",
    family: "Konfluens 2 / Trend Join Long", color: "var(--neutral, #f59e0b)",
  };
  if (l.startsWith("Intraday mean")) return {
    title: "Mean-reversion-marked",
    desc: "Bevægelser overdriver og trækkes tilbage inden for dagen (choppy), og morgenretningen vender oftere end den holder.",
    family: "BuyTheDip (køber dykket)", color: "var(--accent, #2563eb)",
  };
  return {
    title: "Blandet billede",
    desc: "Ingen enkelt familie dominerer tydeligt lige nu.",
    family: "Ingen klar favorit — kør bredt eller afvent.", color: "var(--text-secondary)",
  };
}

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
    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "4px 0", borderBottom: "1px solid var(--border-subtle)", gap: 12 }}>
      <span style={{ fontSize: 13, color: "var(--text-secondary)", display: "flex", alignItems: "center", flexShrink: 0 }}>{dot && <Dot c={dot} />}{label}</span>
      <span style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)", textAlign: "right" }}>{value}</span>
    </div>
  );
}
// recent vs prior -> pil (neutral-beskrivende; groen=op, roed=ned — "op" er ikke i sig selv godt/skidt)
function Shift({ label, recent, prior, d = 2 }: { label: string; recent?: number | null; prior?: number | null; d?: number }) {
  const has = recent !== null && recent !== undefined && prior !== null && prior !== undefined;
  const up = has && (recent as number) > (prior as number);
  const dn = has && (recent as number) < (prior as number);
  const arrow = !has ? "" : up ? " ▲" : dn ? " ▼" : " =";
  const col = !has ? "var(--text-secondary)" : up ? "var(--bull)" : dn ? "var(--bear)" : "var(--text-secondary)";
  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "3px 0", borderBottom: "1px solid var(--border-subtle)" }}>
      <span style={{ fontSize: 13, color: "var(--text-secondary)" }}>{label}</span>
      <span style={{ fontSize: 13, fontWeight: 600, color: col }}>{fmt(prior, d)} → {fmt(recent, d)}<span>{arrow}</span></span>
    </div>
  );
}

const card = (extra?: React.CSSProperties): React.CSSProperties => ({
  background: "var(--bg-elevated)", border: "1px solid var(--border-subtle)", borderRadius: 6, padding: "10px 12px", marginBottom: 10, ...extra,
});
const H = ({ children }: { children: React.ReactNode }) =>
  <div style={{ fontSize: 13, fontWeight: 700, color: "var(--text-primary)", marginBottom: 6, textTransform: "uppercase", letterSpacing: 0.4 }}>{children}</div>;

const regimeShort = (l?: string): string =>
  !l ? "?" : l.startsWith("Stock-picking") ? "Stock-picking" : l.startsWith("Momentum") ? "Momentum"
    : l.startsWith("Intraday mean") ? "Mean-reversion" : "Blandet";
const regimeColor = (l?: string): string =>
  !l ? "var(--text-secondary)" : l.startsWith("Stock-picking") ? "var(--bull)" : l.startsWith("Momentum") ? "var(--neutral, #f59e0b)"
    : l.startsWith("Intraday mean") ? "var(--accent, #2563eb)" : "var(--text-secondary)";

// ══════════════════════════════════════════════════════════════
export function RegimeFingerprint() {
  const [resp, setResp] = useState<LatestResp | null>(null);
  const [loading, setLoading] = useState(true);
  const [copied, setCopied] = useState(false);
  const [showTech, setShowTech] = useState(false);

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
  if (loading && !resp) return <div style={wrap}>Indlæser regime-fingeraftryk…</div>;

  const ok = resp?.status === "ok" && !!resp.fingerprint;
  const fp = resp?.fingerprint;
  const rec = fp?.windows?.recent;
  const pri = fp?.windows?.prior;
  const recSc = rec?.smallcap, priSc = pri?.smallcap;
  const recES = rec?.futures?.ES, priES = pri?.futures?.ES;
  const label = fp?.regime_label ?? resp?.regime_label;
  const priorLabel = fp?.prior_regime_label;
  const info = regimeInfo(label);
  const hist = resp?.history ?? [];

  // afledte menneske-vaerdier
  const disp = recSc?.m5_name_dispersion_pct ?? null;
  const dBand = disp === null ? "ukendt" : disp >= 3.8 ? "Stor" : disp >= 3.0 ? "Moderat" : "Lille";
  const mp = meanPersist(rec);
  const ft = recSc?.m1_gap_follow_through_rate ?? null;
  const ftTxt = ft === null ? "ukendt" : ft > 0.55 ? "Holder (følger igennem)" : ft < 0.45 ? "Vender (fader)" : "Blandet";
  const ac = recSc?.m2_intraday_autocorr_5min ?? null;
  const acTxt = ac === null ? "ukendt" : ac > 0.05 ? "Trendende" : ac < -0.05 ? "Choppy (mean-rev)" : "Neutral";

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
            ? "Intet fingeraftryk endnu — køres mandag morgen på algoserveren."
            : `Kunne ikke hente fingeraftryk${resp?.error ? " (" + resp.error + ")" : ""}.`}
        </div>
      )}

      {ok && (
        <>
          <div style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 10 }}>
            Senest opdateret: {resp?.generated_at ? new Date(resp.generated_at).toLocaleString("da-DK") : "?"}
            {rec?.span && <> · måler perioden {rec.span[0]} … {rec.span[1]}</>}
          </div>

          {/* HERO: Nuvaerende regime */}
          <div style={card({ borderLeft: `4px solid ${info.color}`, padding: "14px 16px" })}>
            <div style={{ fontSize: 11, color: "var(--text-secondary)", textTransform: "uppercase", letterSpacing: 0.5 }}>Markedet lige nu er et</div>
            <div style={{ fontSize: 22, fontWeight: 800, color: info.color, margin: "2px 0 6px" }}>{info.title}</div>
            <div style={{ fontSize: 13, color: "var(--text-secondary)", lineHeight: 1.55 }}>{info.desc}</div>
            <div style={{ marginTop: 10, fontSize: 13 }}>
              <span style={{ color: "var(--text-secondary)" }}>Passer bedst til: </span>
              <span style={{ fontWeight: 700, color: "var(--text-primary)" }}>{info.family}</span>
            </div>
          </div>

          {/* Hvad markedet goer lige nu */}
          <div style={card()}>
            <H>Hvad markedet gør lige nu</H>
            <Row label="Spredning mellem aktier" dot={(disp ?? 0) > 3 ? "var(--bull)" : "var(--text-secondary)"}
                 value={`${dBand}${disp !== null ? ` (${disp.toFixed(2)}%)` : ""}`} />
            <Row label="Retning i indekset"
                 value={mp === null ? "n/a" : `${mp < 0.05 ? "Ingen pålidelig trend" : "Trendende"} (${mp.toFixed(2)})`} />
            <Row label="Morgenretningen" dot={(ft ?? 0) > 0.55 ? "var(--bull)" : "var(--text-secondary)"}
                 value={ft === null ? "n/a" : `${ftTxt} — ${(ft * 100).toFixed(0)}% af dagene`} />
            <Row label="Hvornår på dagen"
                 value={recSc?.m4_hod_morning_dominated === undefined ? "n/a" : recSc.m4_hod_morning_dominated ? "Mest i de første 30–60 min" : "Spredt over dagen"} />
            <Row label="Inden for dagen" value={ac === null ? "n/a" : `${acTxt} (${ac.toFixed(3)})`} />
            <Row label="Bredde (grønne navne)" value={recSc?.m5_breadth_pct_green != null ? `${recSc.m5_breadth_pct_green.toFixed(0)}%` : "n/a"} />
          </div>

          {/* Skifter regimet? */}
          <div style={card()}>
            <H>Skifter regimet?</H>
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: 13, marginBottom: 6 }}>
              <span><span style={{ color: "var(--text-secondary)" }}>Sidste måned: </span><span style={{ fontWeight: 700, color: regimeColor(priorLabel) }}>{regimeShort(priorLabel)}</span></span>
              <span><span style={{ color: "var(--text-secondary)" }}>Nu: </span><span style={{ fontWeight: 700, color: regimeColor(label) }}>{regimeShort(label)}</span></span>
            </div>
            <div style={{ fontSize: 13, fontWeight: 700, color: priorLabel === label ? "var(--bull)" : "var(--neutral, #f59e0b)", marginBottom: 6 }}>
              {priorLabel === label ? "→ Stabilt — samme regime som sidste måned." : `→ Regimet har SKIFTET: fra ${regimeShort(priorLabel)} til ${regimeShort(label)}.`}
            </div>
            <Shift label="Spredning mellem aktier" recent={recSc?.m5_name_dispersion_pct} prior={priSc?.m5_name_dispersion_pct} />
            <Shift label="Morgen-follow-through" recent={recSc?.m1_gap_follow_through_rate} prior={priSc?.m1_gap_follow_through_rate} />
            <Shift label="Inden-for-dagen (chop)" recent={recSc?.m2_intraday_autocorr_5min} prior={priSc?.m2_intraday_autocorr_5min} d={3} />
            <Shift label="Index-trend (ES)" recent={recES?.m7_daily_autocorr} prior={priES?.m7_daily_autocorr} />
            <InfoBox>▲ steget · ▼ faldet siden sidste måned. Store skift = markedet har ændret karakter.</InfoBox>
          </div>

          {/* Historik — skift over tid */}
          <div style={card()}>
            <H>Historik (skift over tid)</H>
            {hist.length >= 2 ? (
              <>
                <div style={{ display: "grid", gridTemplateColumns: "88px 1fr 56px 56px", fontSize: 11, color: "var(--text-secondary)", padding: "2px 0", borderBottom: "1px solid var(--border-default)" }}>
                  <span>Dato</span><span>Regime</span><span style={{ textAlign: "right" }}>Spred.%</span><span style={{ textAlign: "right" }}>Follow</span>
                </div>
                {hist.slice(-8).map((r, i) => (
                  <div key={i} style={{ display: "grid", gridTemplateColumns: "88px 1fr 56px 56px", fontSize: 12, padding: "3px 0", borderBottom: "1px solid var(--border-subtle)" }}>
                    <span style={{ color: "var(--text-secondary)" }}>{r.run_date}</span>
                    <span style={{ fontWeight: 600, color: regimeColor(r.regime) }}>{regimeShort(r.regime)}</span>
                    <span style={{ textAlign: "right" }}>{fmt(num(r.dispersion_pct), 2)}</span>
                    <span style={{ textAlign: "right" }}>{fmt(num(r.follow_through), 2)}</span>
                  </div>
                ))}
                <InfoBox>Når "Regime"-kolonnen skifter værdi mellem rækker, har markedet skiftet karakter.</InfoBox>
              </>
            ) : (
              <div style={{ fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.55 }}>
                Kun én kørsel endnu. Skift over tid bliver synligt når fingeraftrykket har kørt et par uger
                (det kører automatisk hver mandag på algoserveren).
              </div>
            )}
          </div>

          {/* Data-friskhed / forbehold (teknisk, sammenklappet) */}
          {(fp?.notes?.length || resp?.summary_text) && (
            <div style={card()}>
              <div onClick={() => setShowTech(v => !v)} style={{ cursor: "pointer", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <H>Data-friskhed & tekniske detaljer</H>
                <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>{showTech ? "skjul ▲" : "vis ▼"}</span>
              </div>
              {showTech && (
                <>
                  {fp?.notes?.map((n, i) => <div key={i} style={{ fontSize: 12, color: "var(--text-secondary)", padding: "1px 0" }}>• {n}</div>)}
                  {resp?.summary_text && (
                    <pre style={{ marginTop: 8, fontSize: 11, color: "var(--text-secondary)", whiteSpace: "pre-wrap", lineHeight: 1.4, maxHeight: 320, overflowY: "auto", background: "var(--bg-base)", padding: 8, borderRadius: 4 }}>
                      {resp.summary_text}
                    </pre>
                  )}
                </>
              )}
            </div>
          )}

          <div style={{ fontSize: 11, color: "var(--text-secondary)", textAlign: "center", marginTop: 4, marginBottom: 8 }}>
            Beskrivende øjebliksbillede af markedsregimet · ingen handelsanbefaling
          </div>
        </>
      )}
    </div>
  );
}

function btn(bg: string): React.CSSProperties {
  return { background: bg, color: "var(--text-primary)", border: "1px solid var(--border-subtle)", borderRadius: 5, padding: "5px 12px", fontSize: 12, cursor: "pointer" };
}
