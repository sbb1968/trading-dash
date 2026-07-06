import { useState, useEffect, useRef } from "react";

// ── Typer ─────────────────────────────────────────────────────
interface IndexData {
  price:           number;
  gap_pct:         number;
  gap_status:      string;
  intraday_pct:    number;
  intraday_status: string;
  vwap:            number;
  above_vwap:      boolean | null;
}

interface MarketConditions {
  checked_at:        string;
  score:             number;
  score_label:       string;
  skal_handle:       boolean;
  position_size_pct: number;
  vix: {
    value:  number;
    status: string;
  };
  spy: IndexData;
  iwm: IndexData;
  scanner: {
    top_gainers:          string[];
    stocks_gap_over_10:   number;
    stocks_relvol_over_5: number;
  };
}

// ── Score bar ─────────────────────────────────────────────────
function ScoreBar({ score }: { score: number }) {
  const color = score >= 60 ? "var(--bull)" : score >= 40 ? "var(--neutral, #f59e0b)" : "var(--bear)";
  return (
    <div style={{ marginTop: 4 }}>
      <div style={{ height: 8, borderRadius: 4, background: "var(--bg-base)", overflow: "hidden", border: "1px solid var(--border-subtle)" }}>
        <div style={{ width: `${score}%`, height: "100%", background: color, borderRadius: 4, transition: "width 0.5s ease" }} />
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", marginTop: 2 }}>
        <span style={{ fontSize: 16, color: "var(--bear)" }}>0 — Rolig</span>
        <span style={{ fontSize: 16, color: "var(--neutral, #f59e0b)" }}>40 — Moderat</span>
        <span style={{ fontSize: 16, color: "var(--bull)" }}>60+ — Aktiv</span>
      </div>
    </div>
  );
}

// ── Status dot ────────────────────────────────────────────────
function Dot({ status }: { status: "green" | "yellow" | "red" | "grey" }) {
  const c = { green: "var(--bull)", yellow: "var(--neutral, #f59e0b)", red: "var(--bear)", grey: "var(--text-secondary)" };
  return <span style={{ display: "inline-block", width: 8, height: 8, borderRadius: "50%", background: c[status], marginRight: 6, flexShrink: 0 }} />;
}

// ── Info-boks med beskrivelse ─────────────────────────────────
function InfoBox({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ background: "var(--bg-base)", borderRadius: 4, padding: "6px 10px", marginTop: 8, fontSize: 11, color: "var(--text-secondary)", lineHeight: 1.55, borderLeft: "2px solid var(--border-default)" }}>
      {children}
    </div>
  );
}

// ── Række med label, dot og værdi ─────────────────────────────
function Row({ label, value, dot, description }: {
  label:       string;
  value:       React.ReactNode;
  dot?:        "green" | "yellow" | "red" | "grey";
  description: string;
}) {
  return (
    <div style={{ padding: "6px 0", borderBottom: "1px solid var(--border-subtle)" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <span style={{ fontSize: 12, color: "var(--text-secondary)", display: "flex", alignItems: "center" }}>
          {dot && <Dot status={dot} />}
          {label}
        </span>
        <span style={{ fontSize: 13, color: "var(--text-primary)", fontWeight: 600 }}>
          {value}
        </span>
      </div>
      <div style={{ fontSize: 11, color: "var(--text-muted, var(--text-secondary))", marginTop: 2, paddingLeft: dot ? 14 : 0, lineHeight: 1.4 }}>
        {description}
      </div>
    </div>
  );
}

// ── Indeks-hjælpere (delt SPY/IWM) ────────────────────────────
type DotColor = "green" | "yellow" | "red" | "grey";

function gapDot(idx?: IndexData): DotColor {
  if (!idx || idx.price === 0) return "grey";
  if (idx.gap_pct > 0.5) return "green";
  if (idx.gap_pct < -1.5) return "red";
  return "yellow";
}
function vwapDot(idx?: IndexData): DotColor {
  if (!idx || idx.above_vwap == null) return "grey";
  return idx.above_vwap ? "green" : "red";
}
function hasIntraday(idx?: IndexData): boolean {
  return !!idx && !(idx.above_vwap == null && idx.intraday_pct === 0);
}
function IntradayValue({ idx }: { idx?: IndexData }) {
  if (!hasIntraday(idx)) return <>Ukendt</>;
  const v = idx!;
  const c = v.intraday_pct >= 0 ? "var(--bull)" : "var(--bear)";
  return (
    <span style={{ color: c }}>
      {v.intraday_pct >= 0 ? "+" : ""}{v.intraday_pct.toFixed(2)}% ({v.intraday_status})
      {v.vwap > 0 ? ` · VWAP $${v.vwap.toFixed(2)}` : ""}
    </span>
  );
}
function intradayDesc(name: string, idx?: IndexData): string {
  if (!hasIntraday(idx)) return "Ingen intradag-data lige nu (uden for handelstid).";
  const v = idx!;
  const dir = v.intraday_pct >= 0 ? "op" : "ned";
  const vwapTxt = v.above_vwap == null ? "" : v.above_vwap
    ? " og handler OVER VWAP — intradag-styrke." : " og handler UNDER VWAP — intradag-svaghed.";
  return `${name} er ${v.intraday_pct >= 0 ? "+" : ""}${v.intraday_pct.toFixed(2)}% ${dir} fra dagens open${vwapTxt}`;
}
function gapDesc(name: string, idx?: IndexData): string {
  if (!idx || idx.price === 0) return "Kunne ikke hentes.";
  const g = idx.gap_pct;
  if (g < -1.5) return `${name} åbner ${g.toFixed(2)}% lavere end i går — kraftigt negativt gap.`;
  if (g < -0.5) return `${name} åbner ${g.toFixed(2)}% lavere — moderat negativt gap.`;
  if (g < 0.3)  return `${name} åbner næsten uændret (${g.toFixed(2)}%) — neutralt.`;
  if (g < 1.0)  return `${name} åbner ${g.toFixed(2)}% højere — positivt.`;
  return `${name} åbner ${g.toFixed(2)}% højere — stærkt positivt gap.`;
}

// ── Hoved-komponent ───────────────────────────────────────────
export function MarketOverview() {
  const [conditions, setConditions] = useState<MarketConditions | null>(null);
  const [loading,    setLoading]    = useState(false);
  const [error,      setError]      = useState("");
  const [lastUpdate, setLastUpdate] = useState("");
  const wsRef = useRef<WebSocket | null>(null);

  const fst = "var(--fs-title-marketoverview,   16px)";
  const fsh = "var(--fs-header-marketoverview,  12px)";

  useEffect(() => {
    function connect() {
      const ws = new WebSocket("ws://127.0.0.1:8000/ws/algo");
      wsRef.current = ws;
      ws.onmessage = (e) => {
        try {
          const msg = JSON.parse(e.data);
          if (msg.type === "market_conditions") {
            setConditions(msg);
            setLastUpdate(new Date().toLocaleString("da-DK"));
            setLoading(false);
            setError("");
          }
        } catch {}
      };
      ws.onclose = () => setTimeout(connect, 3000);
    }
    connect();
    return () => wsRef.current?.close();
  }, []);

  async function fetchConditions() {
    setLoading(true);
    setError("");
    try {
      const resp = await fetch("http://127.0.0.1:8000/market-conditions");
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      setConditions(data);
      setLastUpdate(new Date().toLocaleString("da-DK"));
    } catch {
      setError("Kunne ikke hente data — er backend kørende?");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { fetchConditions(); }, []);

  const card = (extra?: React.CSSProperties): React.CSSProperties => ({
    background: "var(--bg-elevated)", border: "1px solid var(--border-subtle)", borderRadius: 6, padding: "10px 12px", ...extra,
  });

  const scoreColor = !conditions ? "var(--text-secondary)" :
    conditions.score_label === "aktiv"   ? "var(--bull)" :
    conditions.score_label === "moderat" ? "var(--neutral, #f59e0b)" : "var(--bear)";

  const scoreEmoji = !conditions ? "⚪" :
    conditions.score_label === "aktiv" ? "🟢" :
    conditions.score_label === "moderat" ? "🟡" : "🔴";

  // Backenden kan svare med et DELVIST conditions-objekt (mangler vix/spy/iwm/scanner —
  // fx staale/genstartende backend). Gardér de data-tunge sektioner saa vinduet ALDRIG
  // crasher hele app'en paa et ufuldstaendigt svar.
  const hasDetail = !!(conditions && conditions.vix && conditions.spy && conditions.iwm && conditions.scanner);

  const vixDot = (): "green" | "yellow" | "red" | "grey" => {
    if (!conditions || conditions.vix.value === 0) return "grey";
    if (conditions.vix.value < 15) return "red";
    if (conditions.vix.value < 25) return "green";
    if (conditions.vix.value < 40) return "yellow";
    return "red";
  };

  const spyDot = (): "green" | "yellow" | "red" | "grey" => {
    if (!conditions || conditions.spy.price === 0) return "grey";
    if (conditions.spy.gap_pct > 0.5) return "green";
    if (conditions.spy.gap_pct < -1.5) return "red";
    return "yellow";
  };

  // Dynamisk VIX-beskrivelse
  const vixDescription = () => {
    if (!conditions || conditions.vix.value === 0) return "Kunne ikke hentes.";
    const v = conditions.vix.value;
    if (v < 15) return `VIX ${v.toFixed(1)} er meget lavt — markedet er roligt og small caps bevæger sig lidt. Algoritmen handler ikke på dage som denne.`;
    if (v < 20) return `VIX ${v.toFixed(1)} er normalt. Moderate udsving. Handel er tilladt med fuld position size hvis øvrige betingelser er opfyldt.`;
    if (v < 30) return `VIX ${v.toFixed(1)} er forhøjet — markedet er uroligt og small caps bevæger sig mere. Gode betingelser for momentum-strategier.`;
    if (v < 40) return `VIX ${v.toFixed(1)} er højt — store intradag-udsving. Momentum-strategier kan fungere godt men risikoen er også højere.`;
    return `VIX ${v.toFixed(1)} er ekstremt højt — markedet er i panik. Position size reduceres automatisk til 50%.`;
  };

  // Dynamisk SPY gap-beskrivelse
  const spyGapDescription = () => {
    if (!conditions || conditions.spy.price === 0) return "Kunne ikke hentes.";
    const g = conditions.spy.gap_pct;
    if (g < -1.5) return `SPY åbner ${g.toFixed(2)}% lavere end gårsdagens luk. Kraftigt negativt gap — algoritmen handler ikke på sådanne dage da risikoen for falske breakouts er høj.`;
    if (g < -0.5) return `SPY åbner ${g.toFixed(2)}% lavere. Moderat negativt gap. Small caps kan stadig have momentum men vær selektiv.`;
    if (g < 0.3)  return `SPY åbner næsten uændret (${g.toFixed(2)}%). Neutralt marked — small cap bevægelser drives primært af individuelle nyheder frem for markedsstemning.`;
    if (g < 1.0)  return `SPY åbner ${g.toFixed(2)}% højere. Positivt marked — god stemning der typisk løfter small caps og skaber momentum.`;
    return `SPY åbner ${g.toFixed(2)}% højere. Stærkt positivt gap — markedsstemningen er meget god og der er typisk mange aktive small caps denne dag.`;
  };

  // Dynamisk gap-aktier beskrivelse
  const gapStocksDescription = () => {
    if (!conditions) return "";
    const n = conditions.scanner.stocks_gap_over_10;
    if (n === 0) return "Ingen aktier med gap over 10% fundet. Rolig dag uden catalyst-drevne bevægelser — få handler forventet.";
    if (n <= 2)  return `${n} aktie${n > 1 ? "r" : ""} med gap over 10%. Enkeltaktier med nyheder. Begrænset aktivitet på markedet som helhed.`;
    if (n <= 5)  return `${n} aktier med gap over 10%. Moderat aktivitet — der er candidates at handle men ikke en ekstraordinært aktiv dag.`;
    return `${n} aktier med gap over 10%. Mange aktier med store gap-op bevægelser — tegn på en aktiv dag med gode handelsmuligheder.`;
  };

  // Dynamisk volumen-beskrivelse
  const volDescription = () => {
    if (!conditions) return "";
    const n = conditions.scanner.stocks_relvol_over_5;
    if (n === 0) return "Ingen aktier med usædvanligt høj volumen. Markedet er tyndt — breakouts har tendens til at fejle på dage med lav volumen.";
    if (n <= 2)  return `${n} aktie${n > 1 ? "r" : ""} med høj volumen. Sporadisk aktivitet. Vær selektiv og vent på klare setups.`;
    if (n <= 5)  return `${n} aktier med høj volumen. Rimelig aktivitet — der er interesse fra traders og institutioner i disse aktier.`;
    return `${n} aktier med høj volumen. Mange aktier tiltrækker kapital — tegn på at det er en aktiv handelsdag med god likviditet.`;
  };

  // Dynamisk gainers-beskrivelse
  const gainersDescription = () => {
    if (!conditions) return "";
    const n = conditions.scanner.top_gainers.length;
    if (n === 0) return "Scanneren fandt ingen kandidater. Muligvis fejl i datafeed eller meget rolig dag.";
    if (n <= 5)  return `${n} top gainers fundet. Begrænset universe — algoritmen vil bruge fallback-aktier hvis scanneren konsekvent returnerer få resultater.`;
    return `${n} top gainers fundet. Godt universe af kandidater til dagens handel. Algoritmen screener disse for ORB-breakout setups.`;
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", overflowY: "auto", padding: "14px 16px", gap: 12 }}>

      {/* ── Header ── */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div style={{ fontSize: fst, fontWeight: 800, color: "var(--accent)" }}>
          📊 Markedsoverblik
        </div>
        <button
          onClick={fetchConditions}
          disabled={loading}
          style={{
            background: loading ? "var(--bg-base)" : "var(--bg-elevated)",
            border: "1px solid var(--border-default)", borderRadius: 4,
            padding: "4px 10px", fontSize: fsh,
            color: loading ? "var(--text-secondary)" : "var(--text-primary)",
            cursor: loading ? "not-allowed" : "pointer",
          }}
        >
          {loading ? "⟳ Henter..." : "↺ Opdater"}
        </button>
      </div>

      {/* ── Fejl ── */}
      {error && (
        <div style={{ ...card(), background: "rgba(255,60,60,0.08)", border: "1px solid var(--bear)", color: "var(--bear)", fontSize: fsh }}>
          ❌ {error}
        </div>
      )}

      {/* ── Loading ── */}
      {loading && !conditions && (
        <div style={{ ...card(), textAlign: "center", color: "var(--text-secondary)", fontSize: fsh, padding: "24px" }}>
          Analyserer markedsforhold...
        </div>
      )}

      {/* ── Data ── */}
      {conditions && (
        <>

          {/* ── Aktivitetsscore ── */}
          <div style={card()}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 6 }}>
              <span style={{ fontSize: fsh, color: "var(--text-secondary)", textTransform: "uppercase", letterSpacing: "0.5px" }}>
                Aktivitetsscore
              </span>
              <span style={{ fontSize: 20, fontWeight: 900, color: scoreColor }}>
                {scoreEmoji} {conditions.score}/100
              </span>
            </div>
            <div style={{ fontSize: 10, color: "var(--text-muted, var(--text-secondary))", marginBottom: 4 }}>
              ⟳ Beregnet af backend: <strong>{conditions.checked_at || "—"}</strong>
              {" "}— klik ↺ Opdater; rykker tiden, reberegnes scoren
            </div>
            <ScoreBar score={conditions.score} />
            <div style={{ marginTop: 10, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span style={{ fontSize: 13, fontWeight: 700, color: scoreColor, textTransform: "uppercase" }}>
                {conditions.score_label}
              </span>
              <span style={{ fontSize: fsh, color: "var(--text-secondary)" }}>
                Position size: <strong style={{ color: scoreColor }}>{Math.round(conditions.position_size_pct * 100)}%</strong>
              </span>
            </div>

            <InfoBox>
              Scoren beregnes ud fra VIX (30%), SPY gap (20%), antal gap-aktier (25%) og volumen-aktivitet (25%).
              {" "}<strong style={{ color: "var(--bear)" }}>Under 40</strong>: rolig dag — algoritmen handler ikke.
              {" "}<strong style={{ color: "var(--neutral, #f59e0b)" }}>40-60</strong>: moderat dag — halveret position size.
              {" "}<strong style={{ color: "var(--bull)" }}>Over 60</strong>: aktiv dag — fuld position size.
            </InfoBox>

            <div style={{ marginTop: 8, padding: "6px 10px", borderRadius: 4,
              background: conditions.skal_handle ? "rgba(0,200,100,0.08)" : "rgba(255,60,60,0.08)",
              border: `1px solid ${conditions.skal_handle ? "var(--bull)" : "var(--bear)"}`,
              color: conditions.skal_handle ? "var(--bull)" : "var(--bear)",
              fontSize: fsh, fontWeight: 700, textAlign: "center",
            }}>
              {conditions.skal_handle
                ? `✅ Handel tilladt — ${Math.round(conditions.position_size_pct * 100)}% position size`
                : "🛑 Ingen handel i dag — forholdene er ikke til stede"}
            </div>
          </div>

          {hasDetail ? (<>
          {/* ── Markedsindikatorer ── */}
          <div style={card()}>
            <div style={{ fontSize: fsh, fontWeight: 700, color: "var(--text-secondary)", textTransform: "uppercase", letterSpacing: "0.5px", marginBottom: 4 }}>
              Markedsindikatorer
            </div>

            <Row
              label="VIX (CBOE Volatilitetsindex)"
              value={conditions.vix.value > 0 ? `${conditions.vix.value.toFixed(1)} — ${conditions.vix.status}` : "Ukendt"}
              dot={vixDot()}
              description={vixDescription()}
            />

            <Row
              label="SPY (S&P 500 ETF) kurs"
              value={conditions.spy.price > 0 ? `$${conditions.spy.price.toFixed(2)}` : "Ukendt"}
              dot="grey"
              description="SPY er det vigtigste barometer for det generelle marked. Når SPY stiger har small caps typisk medvind — og omvendt."
            />

            <Row
              label="SPY gap fra gårsdagens luk"
              value={conditions.spy.price > 0
                ? `${conditions.spy.gap_pct >= 0 ? "+" : ""}${conditions.spy.gap_pct.toFixed(2)}% (${conditions.spy.gap_status})`
                : "Ukendt"}
              dot={spyDot()}
              description={spyGapDescription()}
            />

            <Row
              label="SPY intradag (vs open)"
              value={<IntradayValue idx={conditions.spy} />}
              dot={vwapDot(conditions.spy)}
              description={intradayDesc("SPY", conditions.spy)}
            />
          </div>

          {/* ── IWM — small-cap-regime ── */}
          <div style={card()}>
            <div style={{ fontSize: fsh, fontWeight: 700, color: "var(--text-secondary)", textTransform: "uppercase", letterSpacing: "0.5px", marginBottom: 4 }}>
              IWM — Small Cap-regime (Russell 2000)
            </div>

            <Row
              label="IWM (Russell 2000 ETF) kurs"
              value={conditions.iwm?.price > 0 ? `$${conditions.iwm.price.toFixed(2)}` : "Ukendt"}
              dot="grey"
              description="IWM er small cap-barometeret (Russell 2000). For Ibens stil ofte vigtigere end SPY — det er HER small cap-regimet aflæses."
            />

            <Row
              label="IWM gap fra gårsdagens luk"
              value={conditions.iwm?.price > 0
                ? `${conditions.iwm.gap_pct >= 0 ? "+" : ""}${conditions.iwm.gap_pct.toFixed(2)}% (${conditions.iwm.gap_status})`
                : "Ukendt"}
              dot={gapDot(conditions.iwm)}
              description={gapDesc("IWM", conditions.iwm)}
            />

            <Row
              label="IWM intradag (vs open)"
              value={<IntradayValue idx={conditions.iwm} />}
              dot={vwapDot(conditions.iwm)}
              description={intradayDesc("IWM", conditions.iwm)}
            />
          </div>

          {/* ── Small Cap Scanner ── */}
          <div style={card()}>
            <div style={{ fontSize: fsh, fontWeight: 700, color: "var(--text-secondary)", textTransform: "uppercase", letterSpacing: "0.5px", marginBottom: 4 }}>
              Small Cap Scanner
            </div>

            <Row
              label="Aktier med gap > 10%"
              value={`${conditions.scanner.stocks_gap_over_10} aktier`}
              dot={conditions.scanner.stocks_gap_over_10 >= 5 ? "green" : conditions.scanner.stocks_gap_over_10 >= 2 ? "yellow" : "red"}
              description={gapStocksDescription()}
            />

            <Row
              label="Aktier med høj volumen"
              value={`${conditions.scanner.stocks_relvol_over_5} aktier`}
              dot={conditions.scanner.stocks_relvol_over_5 >= 5 ? "green" : conditions.scanner.stocks_relvol_over_5 >= 2 ? "yellow" : "red"}
              description={volDescription()}
            />

            <Row
              label="Top gainers i universe"
              value={`${conditions.scanner.top_gainers.length} fundet`}
              dot={conditions.scanner.top_gainers.length >= 10 ? "green" : conditions.scanner.top_gainers.length >= 5 ? "yellow" : "red"}
              description={gainersDescription()}
            />

            {conditions.scanner.top_gainers.length > 0 && (
              <div style={{ marginTop: 8 }}>
                <div style={{ fontSize: 10, color: "var(--text-secondary)", marginBottom: 4, textTransform: "uppercase", letterSpacing: "0.4px" }}>
                  Dagens candidates
                </div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                  {conditions.scanner.top_gainers.slice(0, 15).map(ticker => (
                    <span key={ticker} style={{
                      background: "var(--bg-base)", border: "1px solid var(--border-default)",
                      borderRadius: 3, padding: "2px 7px", fontSize: 11,
                      fontWeight: 700, fontFamily: "monospace", color: "var(--text-primary)",
                    }}>
                      {ticker}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>
          </>) : (
            <div style={{ ...card(), textAlign: "center", color: "var(--text-secondary)", fontSize: fsh, padding: "18px", lineHeight: 1.5 }}>
              ⏳ Markedsdata er ufuldstændig (VIX/SPY/IWM/scanner mangler i backendens svar).
              Backenden svarer delvist — genstart backenden og klik <strong>↺ Opdater</strong>.
            </div>
          )}

          {/* ── Tidsstempel ── */}
          <div style={{ fontSize: 10, color: "var(--text-secondary)", textAlign: "center", lineHeight: 1.6 }}>
            Beregnet af backend: <strong style={{ color: "var(--text-primary)" }}>{conditions.checked_at || "—"}</strong>
            {" · "}Hentet i vinduet: {lastUpdate || "—"}
            <br />
            Reberegnes ved hvert kald — rykker "Beregnet"-tiden når du klikker ↺ Opdater, kører den.
            Ændres scoren ikke, er markedsinputtene uændrede (fx lukket marked).
          </div>

        </>
      )}

    </div>
  );
}
