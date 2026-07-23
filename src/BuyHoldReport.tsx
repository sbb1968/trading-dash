import { useState, useEffect, useCallback, CSSProperties } from "react";

// Buy-and-Hold-scores — en LISTE af tickers man selv tilføjer. Hver ticker scores via
// analyze-endpointet og vises som ÉN linje (samme layout som Buy-and-Hold Top-15) + pris,
// sorteret højeste SAMLET øverst, gemt i localStorage. Dobbeltklik på tickeren åbner den
// detaljerede score i sit eget vindue (BuyHoldDetail). Flere kan være åbne samtidig.
const API_JSON = "http://127.0.0.1:8000/buyhold/analyze_json";
const LS_KEY = "td_buyhold_scores";

interface ScoreRow {
  ticker: string; company: string | null; price: number | null; final: number; band: string;
  gate: number; quality: number; growth: number; valuation: number; trend: number; oe_yield: number | null;
}

const BAND_LABEL: Record<string, string> = {
  "STAERK KOEB-OG-HOLD-KANDIDAT": "Stærk køb-og-hold-kandidat",
  "EGNET (langsigtet)": "Egnet (langsigtet)",
  "NEUTRAL / AFVENT": "Neutral – afvent",
  "SVAG (langsigtet)": "Svag (langsigtet)",
  "FRARAADES (langsigtet)": "Frarådes (langsigtet)",
};
const bandLabel = (b: string) => BAND_LABEL[b] ?? b;
function bandColor(b: string): string {
  if (b.startsWith("STAERK")) return "var(--bull)";
  if (b.startsWith("EGNET")) return "var(--neutral)";
  if (b.startsWith("SVAG") || b.startsWith("FRARAADES")) return "var(--bear)";
  return "var(--text-muted)";
}
function num(v: number, digits = 1): string {
  if (isNaN(v)) return "—";
  return (v >= 0 ? "+" : "") + v.toFixed(digits);
}
function scoreColor(v: number): string {
  if (isNaN(v)) return "var(--text-muted)";
  if (v >= 15) return "var(--bull)";
  if (v <= -15) return "var(--bear)";
  return "var(--neutral)";
}

const thStyle: CSSProperties = { padding: "8px 10px", textAlign: "right", verticalAlign: "bottom", whiteSpace: "nowrap" };
const tdStyle: CSSProperties = { padding: "8px 10px", textAlign: "right", whiteSpace: "nowrap" };

function Th({ title, sub, tip, left = false }: { title: string; sub?: string; tip?: string; left?: boolean }) {
  const [pos, setPos] = useState<{ x: number; y: number } | null>(null);
  return (
    <th style={{ ...thStyle, textAlign: left ? "left" : "right" }}
      onMouseEnter={tip ? (e) => { const r = e.currentTarget.getBoundingClientRect(); setPos({ x: r.left, y: r.bottom }); } : undefined}
      onMouseLeave={tip ? () => setPos(null) : undefined}>
      <div style={{ fontSize: 12, fontWeight: 700, color: "var(--text-secondary)", display: "inline-block", cursor: tip ? "help" : "default", borderBottom: tip ? "1px dotted var(--text-muted)" : undefined }}>{title}</div>
      {sub && <div style={{ fontSize: 9, fontWeight: 400, color: "var(--text-muted)", marginTop: 1 }}>{sub}</div>}
      {tip && pos && (
        <div style={{
          position: "fixed", left: Math.min(pos.x, window.innerWidth - 280), top: pos.y + 4, zIndex: 2000,
          maxWidth: 260, background: "var(--bg-elevated)", border: "1px solid var(--border-strong)", borderRadius: 6,
          padding: "8px 10px", fontSize: 12, fontWeight: 400, color: "var(--text-primary)", lineHeight: 1.45,
          textAlign: "left", whiteSpace: "normal", boxShadow: "0 6px 18px rgba(0,0,0,0.45)", pointerEvents: "none",
        }}>{tip}</div>
      )}
    </th>
  );
}

function loadRows(): ScoreRow[] {
  try { const s = localStorage.getItem(LS_KEY); return s ? JSON.parse(s) : []; } catch { return []; }
}

export function BuyHoldReport({ onSelectTicker, onOpenDetail }:
  { onSelectTicker?: (t: string) => void; onOpenDetail?: (kind: string, ticker: string) => void }) {
  const [rows, setRows] = useState<ScoreRow[]>(loadRows);
  const [ticker, setTicker] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    try { localStorage.setItem(LS_KEY, JSON.stringify(rows)); } catch { /* ignore */ }
  }, [rows]);

  const sortRows = (rs: ScoreRow[]) => [...rs].sort((a, b) => b.final - a.final);

  const addTicker = useCallback(async () => {
    const t = ticker.trim().toUpperCase();
    if (!t) return;
    setBusy(true); setErr("");
    try {
      const resp = await fetch(API_JSON, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ticker: t }),
      });
      if (!resp.ok) {
        let detail = `HTTP ${resp.status}`;
        try { const e = await resp.json(); detail = e.detail || detail; } catch { /* ignore */ }
        setErr(`${t}: ${detail}`); return;
      }
      const d = await resp.json();
      const row: ScoreRow = {
        ticker: d.ticker, company: d.company ?? null, price: d.price ?? null,
        final: Number(d.final), band: d.final_band, gate: Number(d.gate),
        quality: Number(d.layers?.quality?.score), growth: Number(d.layers?.growth?.score),
        valuation: Number(d.layers?.valuation?.score), trend: Number(d.layers?.trend?.score),
        oe_yield: d.tiles?.oe_yield ?? null,
      };
      setRows(rs => sortRows([...rs.filter(r => r.ticker !== row.ticker), row]));
      setTicker("");
      if (onSelectTicker) onSelectTicker(row.ticker);
    } catch (e: any) {
      setErr(`Kunne ikke naa backenden (koerer den paa :8000?): ${e?.message || e}`);
    } finally {
      setBusy(false);
    }
  }, [ticker, onSelectTicker]);

  const remove = (t: string) => setRows(rs => rs.filter(r => r.ticker !== t));

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", background: "var(--bg-base)", color: "var(--text-primary)" }}>
      <div style={{ padding: "10px 12px", borderBottom: "1px solid var(--border-subtle)", flexShrink: 0 }}>
        <div style={{ fontSize: 15, fontWeight: 800, letterSpacing: "0.3px", marginBottom: 6 }}>Buy-and-Hold-scores</div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <input type="text" placeholder="Tilføj ticker (fx KO)" value={ticker}
            onChange={e => { setTicker(e.target.value.toUpperCase()); setErr(""); }}
            onKeyDown={e => { if (e.key === "Enter" && !busy) addTicker(); }} maxLength={10}
            style={{ flex: 1, background: "var(--bg-input)", color: "var(--text-primary)", border: "1px solid var(--border-strong)", borderRadius: 4, padding: "6px 10px", fontSize: 14, fontWeight: 700, letterSpacing: "0.5px" }} />
          <button onClick={addTicker} disabled={busy}
            style={{ background: busy ? "var(--bg-elevated)" : "var(--accent)", color: busy ? "var(--text-muted)" : "#fff", border: "none", borderRadius: 4, padding: "6px 16px", fontSize: 13, fontWeight: 700, cursor: busy ? "default" : "pointer", whiteSpace: "nowrap" }}>
            {busy ? "Scorer…" : "Tilføj"}
          </button>
        </div>
        {err && <div style={{ marginTop: 6, color: "var(--bear)", fontSize: 12.5 }}>⚠ {err}</div>}
        <div style={{ marginTop: 6, fontSize: 11.5, color: "var(--text-muted)" }}>
          Tilføj tickers — hver scores langsigtet og vises som én linje (sorteret højeste SAMLET øverst).
          <b> Dobbeltklik</b> en ticker for den detaljerede score i eget vindue.
        </div>
      </div>

      <div style={{ flex: 1, overflowY: "auto", padding: "4px 14px 14px" }}>
        {rows.length === 0 ? (
          <div style={{ color: "var(--text-muted)", fontSize: 13, padding: 12, lineHeight: 1.5 }}>
            Ingen tickers endnu. Skriv en ticker ovenfor og tryk <b>Tilføj</b>.
          </div>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr>
                <Th title="#" left tip="Rangering efter SAMLET score — 1 = højeste." />
                <Th title="Aktie" sub="ticker · firmanavn" left tip="Dobbeltklik for den detaljerede score i eget vindue." />
                <Th title="Pris" sub="sidste kurs" tip="Aktiens sidste kurs fra scoringen (USD)." />
                <Th title="SAMLET" sub="vægtet + gated" tip="Vægtet lag-score (Kvalitet 35% + Vækst 25% + Værdi 25% + Trend 15%) ganget med risiko-gaten (−100…+100)." />
                <Th title="Vurdering" sub="samlet bånd" left tip="Ord-bånd for SAMLET." />
                <Th title="Risiko-gate" sub="0–1" tip="Gate 0–1: konkurs (Altman Z), udvanding, FCF-fortegn, gæld, likviditet." />
                <Th title="Kvalitet" sub="35% vægt" tip="Lag 1 (35%): marginer, ROE/ROIC, gæld, kapitalafkast." />
                <Th title="Vækst" sub="25% vægt" tip="Lag 2 (25%): omsætnings-/EPS-/FCF-vækst og holdbarhed." />
                <Th title="Værdi" sub="25% vægt" tip="Lag 3 (25%): værdiansættelse — P/E, EV/EBITDA, P/FCF, OE-yield." />
                <Th title="Trend" sub="15% vægt" tip="Lag 4 (15%): langsigtet kurstrend." />
                <Th title="OE-yield" sub="owner earnings" tip="Owner-Earnings-yield i % — normaliseret ejerindtjening pr. markedsværdi." />
                <th style={{ ...thStyle, textAlign: "center" }}></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={r.ticker} style={{ borderTop: "1px solid var(--border-subtle)" }}>
                  <td style={{ ...tdStyle, textAlign: "left", color: "var(--text-muted)", fontWeight: 700 }}>{i + 1}</td>
                  <td style={{ ...tdStyle, textAlign: "left", maxWidth: 240, cursor: "pointer" }}
                    onDoubleClick={() => onOpenDetail?.("buyhold", r.ticker)}
                    onClick={() => onSelectTicker?.(r.ticker)}
                    title="Dobbeltklik for detaljeret score">
                    <div style={{ fontWeight: 800, fontSize: 14 }}>{r.ticker}</div>
                    {r.company && (
                      <div style={{ fontSize: 11, color: "var(--text-muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.company}</div>
                    )}
                  </td>
                  <td style={tdStyle}>{r.price != null ? `$${r.price.toFixed(2)}` : "—"}</td>
                  <td style={{ ...tdStyle, fontWeight: 800, fontSize: 15, color: scoreColor(r.final) }}>{num(r.final, 1)}</td>
                  <td style={{ ...tdStyle, textAlign: "left", color: bandColor(r.band), fontWeight: 600 }}>{bandLabel(r.band)}</td>
                  <td style={tdStyle}>{isNaN(r.gate) ? "—" : r.gate.toFixed(2)}</td>
                  <td style={{ ...tdStyle, color: scoreColor(r.quality), fontWeight: 600 }}>{num(r.quality)}</td>
                  <td style={{ ...tdStyle, color: scoreColor(r.growth), fontWeight: 600 }}>{num(r.growth)}</td>
                  <td style={{ ...tdStyle, color: scoreColor(r.valuation), fontWeight: 600 }}>{num(r.valuation)}</td>
                  <td style={{ ...tdStyle, color: scoreColor(r.trend), fontWeight: 600 }}>{num(r.trend)}</td>
                  <td style={{ ...tdStyle, color: "var(--text-secondary)" }}>{r.oe_yield == null || isNaN(r.oe_yield) ? "—" : `${r.oe_yield.toFixed(1)}%`}</td>
                  <td style={{ ...tdStyle, textAlign: "center" }}>
                    <span onClick={() => remove(r.ticker)} title="Fjern fra listen"
                      style={{ cursor: "pointer", color: "var(--text-muted)", fontWeight: 700, padding: "0 4px" }}>✕</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
