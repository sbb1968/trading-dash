import { useState } from "react";
import { openUrl } from "@tauri-apps/plugin-opener";

// Strategi-sammenligningsrapport (PDF) — bygges server-side fra FAKTISKE journal-handler
// for det valgte interval og åbnes eksternt (som de andre rapporter via openUrl).
const API = "http://127.0.0.1:8000/strategirapport/pdf";

function isoDaysAgo(n: number): string {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d.toISOString().slice(0, 10);
}

export function StrategyReport() {
  const [start, setStart] = useState(isoDaysAgo(120));
  const [end, setEnd] = useState(isoDaysAgo(0));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const openReport = async () => {
    setErr("");
    setBusy(true);
    try {
      await openUrl(`${API}?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`);
    } catch (e: any) {
      setErr(e?.message || "Kunne ikke åbne rapporten");
    } finally {
      // backenden bygger PDF'en (matplotlib/reportlab) i et par sekunder før browseren får den
      setTimeout(() => setBusy(false), 2000);
    }
  };

  const input: React.CSSProperties = {
    background: "var(--bg-base)", color: "var(--text-primary)",
    border: "1px solid var(--border-default)", borderRadius: 5,
    padding: "5px 8px", fontSize: 13,
  };

  return (
    <div style={{ height: "100%", overflow: "auto", padding: "16px 18px",
      background: "var(--bg-base)", color: "var(--text-primary)" }}>
      <div style={{ fontSize: 16, fontWeight: 800, color: "var(--accent)", marginBottom: 4 }}>
        📊 Strategirapport
      </div>
      <div style={{ color: "var(--text-secondary)", fontSize: 12, marginBottom: 16, lineHeight: 1.5 }}>
        Sammenligner de fire strategier (Konfluens 2, EUREVERSION, BuyTheDip, Trend Join Long)
        på <strong>faktiske, lukkede handler</strong> fra journalen for det valgte interval —
        equity-kurver, nøgletal, exit-årsager og P&amp;L pr. måned. Åbnes som PDF.
      </div>

      <div style={{ display: "flex", gap: 14, alignItems: "flex-end", flexWrap: "wrap" }}>
        <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 12,
          color: "var(--text-secondary)" }}>
          Fra
          <input type="date" value={start} max={end}
            onChange={e => setStart(e.target.value)} style={input} />
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 12,
          color: "var(--text-secondary)" }}>
          Til
          <input type="date" value={end} min={start}
            onChange={e => setEnd(e.target.value)} style={input} />
        </label>
        <button onClick={openReport} disabled={busy}
          style={{ cursor: busy ? "default" : "pointer", padding: "7px 14px", borderRadius: 6,
            border: "1px solid var(--border-default)",
            background: busy ? "var(--bg-elevated)" : "var(--accent, #3b82f6)",
            color: busy ? "var(--text-secondary)" : "#fff", fontWeight: 700, fontSize: 13 }}>
          {busy ? "Bygger PDF…" : "📄 Åbn rapport (PDF)"}
        </button>
      </div>

      {err && <div style={{ marginTop: 12, color: "var(--bear)" }}>⚠ {err} — er backenden startet?</div>}

      <div style={{ marginTop: 18, padding: "10px 12px", background: "var(--bg-elevated)",
        border: "1px solid var(--border-subtle)", borderRadius: 6, fontSize: 11.5,
        color: "var(--text-secondary)", lineHeight: 1.55 }}>
        <strong>Bemærk:</strong> rapporten bygges på den maskines journal du er forbundet til.
        Den fulde flåde (K2/Europa/BuyTheDip) ligger på algoserveren; Trend Join Long har
        ingen handler endnu (først efter den er kørt). Tidligt er datagrundlaget tyndt —
        nøgletallene bliver mere meningsfulde efterhånden som strategierne handler.
      </div>
    </div>
  );
}
