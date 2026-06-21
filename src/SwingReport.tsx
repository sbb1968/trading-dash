import { useState } from "react";

const API = "http://127.0.0.1:8000/swing/analyze";

function Slider({ label, value, onChange }: {
  label: string; value: number; onChange: (v: number) => void;
}) {
  const col = value > 0 ? "var(--bull)" : value < 0 ? "var(--bear)" : "var(--text-muted)";
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      <span style={{ width: 110, fontSize: 11, color: "var(--text-muted)" }}>{label}</span>
      <input
        type="range" min={-100} max={100} step={5} value={value}
        onChange={e => onChange(Number(e.target.value))}
        style={{ flex: 1, accentColor: "var(--accent)" }}
      />
      <span style={{ width: 44, textAlign: "right", fontSize: 12, fontWeight: 700, color: col }}>
        {value > 0 ? "+" : ""}{value}
      </span>
    </div>
  );
}

export function SwingReport({ onSelectTicker }: { onSelectTicker?: (t: string) => void }) {
  const [ticker, setTicker]       = useState("");
  const [useOverlay, setUseOverlay] = useState(false);
  const [sr, setSr]               = useState(0);
  const [pattern, setPattern]     = useState(0);
  const [candle, setCandle]       = useState(0);
  const [report, setReport]       = useState("");
  const [loading, setLoading]     = useState(false);
  const [error, setError]         = useState("");

  async function analyze() {
    const t = ticker.trim().toUpperCase();
    if (!t) { setError("Indtast en ticker"); return; }
    setLoading(true); setError(""); setReport("");
    try {
      const body: any = { ticker: t };
      if (useOverlay) { body.sr = sr; body.pattern = pattern; body.candle = candle; }
      const resp = await fetch(API, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!resp.ok) {
        let detail = `HTTP ${resp.status}`;
        try { const e = await resp.json(); detail = e.detail || detail; } catch { /* ignore */ }
        setError(detail);
        return;
      }
      const data = await resp.json();
      setReport(data.report || "");
      if (onSelectTicker && data.ticker) onSelectTicker(data.ticker);
    } catch (e: any) {
      setError(`Kunne ikke naa backenden (koerer den paa :8000?): ${e?.message || e}`);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%",
                  background: "var(--bg-base)", color: "var(--text-primary)" }}>
      {/* Kontrol-bar */}
      <div style={{ padding: "10px 12px", borderBottom: "1px solid var(--border-default)",
                    display: "flex", flexDirection: "column", gap: 8, flexShrink: 0 }}>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <input
            type="text" placeholder="Ticker (fx MRVL)" value={ticker}
            onChange={e => { setTicker(e.target.value.toUpperCase()); setError(""); }}
            onKeyDown={e => { if (e.key === "Enter" && !loading) analyze(); }}
            maxLength={6}
            style={{ flex: 1, background: "var(--bg-elevated)", color: "var(--text-primary)",
                     border: "1px solid var(--border-default)", borderRadius: 4,
                     padding: "6px 10px", fontSize: 14, fontWeight: 700, letterSpacing: "0.5px" }}
          />
          <button
            onClick={analyze} disabled={loading}
            style={{ background: loading ? "var(--bg-elevated)" : "var(--accent)",
                     color: loading ? "var(--text-muted)" : "var(--bg-base)",
                     border: "none", borderRadius: 4, padding: "6px 16px",
                     fontSize: 13, fontWeight: 700, cursor: loading ? "default" : "pointer" }}
          >
            {loading ? "Analyserer..." : "Analyser"}
          </button>
        </div>

        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11,
                        color: "var(--text-muted)", cursor: "pointer" }}>
          <input type="checkbox" checked={useOverlay}
                 onChange={e => setUseOverlay(e.target.checked)} />
          Inkluder manuelt chart-overlay (din egen chart-vurdering)
        </label>

        {useOverlay && (
          <div style={{ display: "flex", flexDirection: "column", gap: 6,
                        padding: "6px 4px 2px" }}>
            <Slider label="Support/modstand" value={sr}      onChange={setSr} />
            <Slider label="Chart-moenster"    value={pattern} onChange={setPattern} />
            <Slider label="Candlestick"       value={candle}  onChange={setCandle} />
            <span style={{ fontSize: 10, color: "var(--text-muted)" }}>
              -100 bearish · 0 neutral · +100 bullish. Flettes ind i det tekniske lag.
            </span>
          </div>
        )}
      </div>

      {/* Rapport-omraade */}
      <div style={{ flex: 1, overflow: "auto", minHeight: 0 }}>
        {error && (
          <div style={{ margin: 12, padding: "10px 12px", border: "1px solid var(--bear)",
                        borderRadius: 4, color: "var(--bear)", fontSize: 13 }}>
            {error}
          </div>
        )}
        {!error && loading && (
          <div style={{ padding: 24, color: "var(--text-muted)", fontSize: 13 }}>
            Henter daglige bars (IBKR), fundamentaler og float ... tager et par sekunder.
          </div>
        )}
        {!error && !loading && !report && (
          <div style={{ padding: 24, color: "var(--text-muted)", fontSize: 13 }}>
            Indtast en ticker og tryk Analyser. Rapporten scorer aktien for
            swing-egnethed paa tvaers af teknik, fundamental og katalysator.
          </div>
        )}
        {!error && !loading && report && (
          <pre style={{ margin: 0, padding: "12px 14px",
                        fontFamily: "'Consolas','Menlo','Monaco',monospace",
                        fontSize: 11.5, lineHeight: 1.4, whiteSpace: "pre",
                        color: "var(--text-primary)" }}>
            {report}
          </pre>
        )}
      </div>
    </div>
  );
}
