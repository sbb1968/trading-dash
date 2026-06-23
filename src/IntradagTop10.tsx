import { useState, useEffect, useCallback, CSSProperties } from "react";

const API_GET = "http://127.0.0.1:8000/intradag/top10";
const API_RUN = "http://127.0.0.1:8000/intradag/top10/run";

interface Row {
  rank: number;
  symbol: string;
  final: number | string;
  band: string;
  gate: number | string;
  price: number | string;
  gap_pct: number | string;
  rvol: number | string;
  float_shares: number | null;
  spread_pct: number | null;
}

interface Progress { done: number; total: number; }
interface TopData {
  generated_local: string | null;
  generated_utc: string | null;
  source: string | null;
  count: number;
  rows: Row[];
  running: boolean;
  started_utc: string | null;
  progress: Progress | null;
}

function ageText(utc: string | null): string {
  if (!utc) return "";
  const then = new Date(utc).getTime();
  if (isNaN(then)) return "";
  const mins = Math.max(0, Math.floor((Date.now() - then) / 60000));
  if (mins < 1) return "lige nu";
  if (mins < 60) return `${mins} min gammel`;
  const hours = Math.floor(mins / 60);
  return `${hours} t ${mins % 60} min gammel`;
}

function num(v: number | string, digits = 1): string {
  const n = typeof v === "number" ? v : parseFloat(v);
  if (isNaN(n)) return String(v ?? "—");
  return (n >= 0 ? "+" : "") + n.toFixed(digits);
}
function plain(v: number | string, digits = 1): string {
  const n = typeof v === "number" ? v : parseFloat(v);
  return isNaN(n) ? "—" : n.toFixed(digits);
}
function fmtShares(v: number | null): string {
  if (v == null) return "—";
  if (v >= 1e9) return `${(v / 1e9).toFixed(1)}B`;
  if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  return v.toLocaleString();
}
function fmtSpread(v: number | null): string {
  return v == null ? "—" : `${(v * 100).toFixed(2)}%`;
}

function scoreColor(v: number | string): string {
  const n = typeof v === "number" ? v : parseFloat(v);
  if (isNaN(n)) return "var(--text-muted)";
  if (n >= 20) return "var(--bull)";
  if (n <= -20) return "var(--bear)";
  return "var(--neutral)";
}

// Backend sender intradag-baand (technical_intraday._band) som ASCII; vis pyntet.
const BAND_LABEL: Record<string, string> = {
  "Staerk intradag-opstilling": "Stærk",
  "Medvind": "Medvind",
  "Neutral / blandet": "Neutral",
  "Svag": "Svag",
  "Fraraades (intradag)": "Frarådes",
  "ingen data": "Afventer",
};
const bandLabel = (b: string) => BAND_LABEL[b] ?? b;
function bandColor(b: string): string {
  if (b.startsWith("Staerk") || b === "Medvind") return "var(--bull)";
  if (b.startsWith("Svag") || b.startsWith("Fraraades")) return "var(--bear)";
  return "var(--neutral)";
}

const thStyle: CSSProperties = { padding: "8px 10px", textAlign: "right", verticalAlign: "bottom", whiteSpace: "nowrap" };
const tdStyle: CSSProperties = { padding: "8px 10px", textAlign: "right", whiteSpace: "nowrap" };

function Th({ title, sub, left = false }: { title: string; sub?: string; left?: boolean }) {
  return (
    <th style={{ ...thStyle, textAlign: left ? "left" : "right" }}>
      <div style={{ fontSize: 12, fontWeight: 700, color: "var(--text-secondary)" }}>{title}</div>
      {sub && <div style={{ fontSize: 9, fontWeight: 400, color: "var(--text-muted)", marginTop: 1 }}>{sub}</div>}
    </th>
  );
}

export function IntradagTop10({ onSelectTicker }: { onSelectTicker?: (t: string) => void }) {
  const [data, setData] = useState<TopData | null>(null);
  const [loading, setLoading] = useState(true);
  const [starting, setStarting] = useState(false);
  const [note, setNote] = useState("");

  const refresh = useCallback(async () => {
    try {
      const resp = await fetch(API_GET);
      if (resp.ok) setData(await resp.json());
    } catch {
      /* ingen forbindelse - behold hidtil */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    // poll hyppigere mens en scan koerer (fremdrift), ellers roligt
    const id = setInterval(refresh, 8000);
    return () => clearInterval(id);
  }, [refresh]);

  async function runScan() {
    setStarting(true);
    setNote("");
    try {
      const resp = await fetch(API_RUN, { method: "POST" });
      const r = await resp.json();
      if (r.already_running) setNote("En scan kører allerede.");
      else if (r.started) setNote("Scan startet — tager et par minutter; listen opdateres løbende.");
      else setNote(`Kunne ikke starte: ${r.error || "ukendt fejl"}`);
      await refresh();
    } catch (e: any) {
      setNote(`Kunne ikke nå backenden (kører den på :8000?): ${e?.message || e}`);
    } finally {
      setStarting(false);
    }
  }

  const running = data?.running ?? false;
  const disabled = starting || running;
  const prog = data?.progress;

  return (
    <div style={{ position: "relative", display: "flex", flexDirection: "column", height: "100%", background: "var(--bg-base)", color: "var(--text-primary)" }}>
      <div style={{ padding: "10px 14px", borderBottom: "1px solid var(--border-subtle)", display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexShrink: 0 }}>
        <div>
          <div style={{ fontSize: 15, fontWeight: 800, letterSpacing: "0.3px" }}>Day trading Top-10</div>
          <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 3 }}>
            {data?.generated_local
              ? `Genereret ${data.generated_local} · ${ageText(data.generated_utc)}`
              : "Ingen scan kørt endnu"}
          </div>
        </div>
        <button
          onClick={runScan}
          disabled={disabled}
          title="Scorer dagens momentum-kandidater gennem den gatede konfluens. Tager et par minutter."
          style={{
            background: disabled ? "var(--bg-elevated)" : "var(--accent)",
            color: disabled ? "var(--text-muted)" : "var(--bg-base)",
            border: "none", borderRadius: 4, padding: "8px 16px", fontSize: 13, fontWeight: 700,
            cursor: disabled ? "default" : "pointer", whiteSpace: "nowrap",
          }}
        >
          {running ? "Kører…" : starting ? "Starter…" : "Kør scan"}
        </button>
      </div>

      <div style={{ padding: "8px 14px", fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.5, borderBottom: "1px solid var(--border-subtle)" }}>
        Dagens momentum-kandidater (gap &gt; 5%, RVOL &gt; 2) scoret gennem den <b>gatede konfluens</b> (teknik
        × forsyning × handelbarhed) — rangeret efter <b>Samlet</b>. Klik en række for at sætte symbolet i de andre vinduer.
      </div>

      {running && (
        <div style={{ padding: "8px 14px", background: "var(--bg-elevated)", color: "var(--text-primary)", fontSize: 12, borderBottom: "1px solid var(--border-subtle)" }}>
          Scan kører{prog ? ` — scorer ${prog.done}/${prog.total}…` : "…"} Listen opdateres løbende.
        </div>
      )}
      {note && (
        <div style={{ padding: "6px 14px", fontSize: 12, color: "var(--text-muted)", borderBottom: "1px solid var(--border-subtle)" }}>{note}</div>
      )}

      <div style={{ flex: 1, overflowY: "auto", padding: "4px 14px 14px" }}>
        {loading && !data ? (
          <div style={{ color: "var(--text-muted)", fontSize: 13, padding: 12 }}>Henter…</div>
        ) : !data?.rows?.length ? (
          <div style={{ color: "var(--text-muted)", fontSize: 13, padding: 12, lineHeight: 1.5 }}>
            Ingen liste endnu. Tryk <b>Kør scan</b> for at score dagens momentum-kandidater. Tager et par minutter.
          </div>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr>
                <Th title="#" left />
                <Th title="Symbol" left />
                <Th title="Samlet" sub="gated konfluens" />
                <Th title="Vurdering" sub="bånd" left />
                <Th title="Gate" sub="handelbarhed" />
                <Th title="Gap %" sub="i dag" />
                <Th title="RVOL" sub="rel. volumen" />
                <Th title="Float" sub="frie aktier" />
                <Th title="Spænd" sub="bid/ask" />
              </tr>
            </thead>
            <tbody>
              {data.rows.map((r) => (
                <tr key={r.rank}
                  onClick={() => onSelectTicker && onSelectTicker(String(r.symbol).toUpperCase())}
                  style={{ borderTop: "1px solid var(--border-subtle)", cursor: onSelectTicker ? "pointer" : "default" }}
                  title={onSelectTicker ? "Sæt symbol i de andre vinduer" : undefined}>
                  <td style={{ ...tdStyle, textAlign: "left", color: "var(--text-muted)", fontWeight: 700 }}>{r.rank}</td>
                  <td style={{ ...tdStyle, textAlign: "left", fontWeight: 800, fontSize: 14 }}>{r.symbol}</td>
                  <td style={{ ...tdStyle, fontWeight: 800, fontSize: 15, color: scoreColor(r.final) }}>{num(r.final, 1)}</td>
                  <td style={{ ...tdStyle, textAlign: "left", color: bandColor(r.band), fontWeight: 600 }}>{bandLabel(r.band)}</td>
                  <td style={tdStyle}>{plain(r.gate, 3)}</td>
                  <td style={{ ...tdStyle, color: scoreColor(r.gap_pct) }}>{num(r.gap_pct)}</td>
                  <td style={tdStyle}>{plain(r.rvol, 1)}x</td>
                  <td style={tdStyle}>{fmtShares(r.float_shares)}</td>
                  <td style={tdStyle}>{fmtSpread(r.spread_pct)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
