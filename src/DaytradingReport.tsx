import { useState, useEffect, useCallback, CSSProperties } from "react";

// Day trading-scores — en LISTE af symboler man selv tilføjer. Hvert symbol scores via
// analyze-endpointet og vises som ÉN linje (samme layout som Day trading Top-15) + pris,
// sorteret højeste Samlet øverst, gemt i localStorage. Dobbeltklik på symbolet åbner den
// detaljerede score i sit eget vindue (DaytradingDetail). Flere kan være åbne samtidig.
const API_JSON = "http://127.0.0.1:8000/daytrading/analyze_json";
const LS_KEY = "td_daytrading_scores";

interface ScoreRow {
  symbol: string; price: number | null; final: number | null; band: string;
  gate: number; float_shares: number | null; spread_pct: number | null;
}

const BAND_LABEL: Record<string, string> = {
  "Staerk day trading-opstilling": "Stærk", "Medvind": "Medvind",
  "Neutral / blandet": "Neutral", "Svag": "Svag",
  "Fraraades (day trading)": "Frarådes", "ingen data": "Afventer",
};
const bandLabel = (b: string) => BAND_LABEL[b] ?? b;
function bandColor(b: string): string {
  if (b.startsWith("Staerk") || b === "Medvind") return "var(--bull)";
  if (b === "Svag" || b.startsWith("Fraraades")) return "var(--bear)";
  return "var(--text-muted)";
}
function num(v: number | null, digits = 1): string {
  if (v == null || isNaN(v)) return "—";
  return (v >= 0 ? "+" : "") + v.toFixed(digits);
}
function scoreColor(v: number | null): string {
  if (v == null || isNaN(v)) return "var(--text-muted)";
  if (v >= 15) return "var(--bull)";
  if (v <= -15) return "var(--bear)";
  return "var(--neutral)";
}
function fmtShares(v: number | null): string {
  if (v == null) return "—";
  if (v >= 1e9) return `${(v / 1e9).toFixed(1)}B`;
  if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  return v.toLocaleString();
}
const fmtSpread = (v: number | null) => (v == null ? "—" : `${(v * 100).toFixed(2)}%`);

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

export function DaytradingReport({ onSelectTicker, onOpenDetail }:
  { onSelectTicker?: (t: string) => void; onOpenDetail?: (kind: string, ticker: string) => void }) {
  const [rows, setRows] = useState<ScoreRow[]>(loadRows);
  const [symbol, setSymbol] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    try { localStorage.setItem(LS_KEY, JSON.stringify(rows)); } catch { /* ignore */ }
  }, [rows]);

  const sortRows = (rs: ScoreRow[]) => [...rs].sort((a, b) => (b.final ?? -Infinity) - (a.final ?? -Infinity));

  const addSymbol = useCallback(async () => {
    const s = symbol.trim().toUpperCase();
    if (!s) return;
    setBusy(true); setErr("");
    try {
      const resp = await fetch(API_JSON, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ symbol: s, timeframe: "5 mins" }),
      });
      if (!resp.ok) {
        let detail = `HTTP ${resp.status}`;
        try { const e = await resp.json(); detail = e.detail || detail; } catch { /* ignore */ }
        setErr(`${s}: ${detail}`); return;
      }
      const d = await resp.json();
      if (d.error) { setErr(`${s}: ${d.error}`); return; }
      const row: ScoreRow = {
        symbol: d.symbol, price: d.price ?? null, final: d.final ?? null, band: d.final_band,
        gate: Number(d.gate), float_shares: d.info?.float_shares ?? null, spread_pct: d.info?.spread_pct ?? null,
      };
      setRows(rs => sortRows([...rs.filter(r => r.symbol !== row.symbol), row]));
      setSymbol("");
      if (onSelectTicker) onSelectTicker(row.symbol);
    } catch (e: any) {
      setErr(`Kunne ikke naa backenden (kører den på :8000?): ${e?.message || e}`);
    } finally {
      setBusy(false);
    }
  }, [symbol, onSelectTicker]);

  const remove = (s: string) => setRows(rs => rs.filter(r => r.symbol !== s));

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", background: "var(--bg-base)", color: "var(--text-primary)" }}>
      <div style={{ padding: "10px 12px", borderBottom: "1px solid var(--border-subtle)", flexShrink: 0 }}>
        <div style={{ fontSize: 15, fontWeight: 800, letterSpacing: "0.3px", marginBottom: 6 }}>Day trading-scores</div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <input type="text" placeholder="Tilføj symbol (fx AMAT)" value={symbol}
            onChange={e => { setSymbol(e.target.value.toUpperCase()); setErr(""); }}
            onKeyDown={e => { if (e.key === "Enter" && !busy) addSymbol(); }} maxLength={10}
            style={{ flex: 1, background: "var(--bg-input)", color: "var(--text-primary)", border: "1px solid var(--border-strong)", borderRadius: 4, padding: "6px 10px", fontSize: 14, fontWeight: 700, letterSpacing: "0.5px" }} />
          <button onClick={addSymbol} disabled={busy}
            style={{ background: busy ? "var(--bg-elevated)" : "var(--accent)", color: busy ? "var(--text-muted)" : "#fff", border: "none", borderRadius: 4, padding: "6px 16px", fontSize: 13, fontWeight: 700, cursor: busy ? "default" : "pointer", whiteSpace: "nowrap" }}>
            {busy ? "Scorer…" : "Tilføj"}
          </button>
        </div>
        {err && <div style={{ marginTop: 6, color: "var(--bear)", fontSize: 12.5 }}>⚠ {err}</div>}
        <div style={{ marginTop: 6, fontSize: 11.5, color: "var(--text-muted)" }}>
          Tilføj symboler — hvert scores (5-min konfluens) og vises som én linje (sorteret højeste Samlet øverst).
          <b> Dobbeltklik</b> et symbol for den detaljerede score i eget vindue.
        </div>
      </div>

      <div style={{ flex: 1, overflowY: "auto", padding: "4px 14px 14px" }}>
        {rows.length === 0 ? (
          <div style={{ color: "var(--text-muted)", fontSize: 13, padding: 12, lineHeight: 1.5 }}>
            Ingen symboler endnu. Skriv et symbol ovenfor og tryk <b>Tilføj</b>.
          </div>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr>
                <Th title="#" left tip="Rangering efter Samlet score — 1 = højeste." />
                <Th title="Symbol" left tip="Dobbeltklik for den detaljerede score i eget vindue." />
                <Th title="Pris" sub="sidste kurs" tip="Aktiens sidste kurs fra scoringen (USD)." />
                <Th title="Samlet" sub="gated konfluens" tip="Den samlede day trading-konfluens (−100…+100): teknisk + forsyning + katalysator ganget med handelbarheds-gaten." />
                <Th title="Vurdering" sub="bånd" left tip="Ord-bånd for Samlet." />
                <Th title="Gate" sub="handelbarhed" tip="Handelbarhed 0–1: likviditet, dollar-volumen, pris, bid/ask-spænd, halt." />
                <Th title="Float" sub="frie aktier" tip="Antal frit omsættelige aktier." />
                <Th title="Spænd" sub="bid/ask" tip="Bid/ask-spænd i procent." />
                <th style={{ ...thStyle, textAlign: "center" }}></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={r.symbol} style={{ borderTop: "1px solid var(--border-subtle)" }}>
                  <td style={{ ...tdStyle, textAlign: "left", color: "var(--text-muted)", fontWeight: 700 }}>{i + 1}</td>
                  <td style={{ ...tdStyle, textAlign: "left", fontWeight: 800, fontSize: 14, cursor: "pointer" }}
                    onDoubleClick={() => onOpenDetail?.("daytrading", r.symbol)}
                    onClick={() => onSelectTicker?.(r.symbol)}
                    title="Dobbeltklik for detaljeret score">{r.symbol}</td>
                  <td style={tdStyle}>{r.price != null ? `$${r.price.toFixed(2)}` : "—"}</td>
                  <td style={{ ...tdStyle, fontWeight: 800, fontSize: 15, color: scoreColor(r.final) }}>{num(r.final, 1)}</td>
                  <td style={{ ...tdStyle, textAlign: "left", color: bandColor(r.band), fontWeight: 600 }}>{bandLabel(r.band)}</td>
                  <td style={tdStyle}>{isNaN(r.gate) ? "—" : r.gate.toFixed(3)}</td>
                  <td style={tdStyle}>{fmtShares(r.float_shares)}</td>
                  <td style={tdStyle}>{fmtSpread(r.spread_pct)}</td>
                  <td style={{ ...tdStyle, textAlign: "center" }}>
                    <span onClick={() => remove(r.symbol)} title="Fjern fra listen"
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
