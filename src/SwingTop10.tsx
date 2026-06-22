import { useState, useEffect, useCallback, CSSProperties } from "react";

const API_GET = "http://127.0.0.1:8000/swing/top10";
const API_RUN = "http://127.0.0.1:8000/swing/top10/run";

interface Row {
  rank: number;
  ticker: string;
  final: number | string;
  band: string;
  combined: number | string;
  gate: number | string;
  tech: number | string;
  fund: number | string;
  cat: number | string;
  rs_3m: number | string;
}

interface TopData {
  generated_local: string | null;
  generated_utc: string | null;
  source: string | null;
  count: number;
  rows: Row[];
  running: boolean;
  started_utc: string | null;
}

function ageText(utc: string | null): string {
  if (!utc) return "";
  const then = new Date(utc).getTime();
  if (isNaN(then)) return "";
  const mins = Math.max(0, Math.floor((Date.now() - then) / 60000));
  if (mins < 60) return `${mins} min gammel`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours} t ${mins % 60} min gammel`;
  const days = Math.floor(hours / 24);
  return `${days} dag${days > 1 ? "e" : ""} gammel`;
}

function num(v: number | string, digits = 1): string {
  const n = typeof v === "number" ? v : parseFloat(v);
  if (isNaN(n)) return String(v ?? "");
  return (n >= 0 ? "+" : "") + n.toFixed(digits);
}

const thStyle: CSSProperties = { padding: "6px 8px", textAlign: "right", fontWeight: 600, whiteSpace: "nowrap" };
const tdStyle: CSSProperties = { padding: "6px 8px", textAlign: "right", whiteSpace: "nowrap" };

export function SwingTop10() {
  const [data, setData] = useState<TopData | null>(null);
  const [loading, setLoading] = useState(true);
  const [starting, setStarting] = useState(false);
  const [note, setNote] = useState("");
  const [showConfirm, setShowConfirm] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const resp = await fetch(API_GET);
      if (resp.ok) setData(await resp.json());
    } catch {
      /* ingen forbindelse - haandteres ved at data forbliver som det er */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 30000);
    return () => clearInterval(id);
  }, [refresh]);

  // Kaldes naar brugeren har BEKRAEFTET pop-up'en.
  async function confirmRun() {
    setShowConfirm(false);
    setStarting(true);
    setNote("");
    try {
      const resp = await fetch(API_RUN, { method: "POST" });
      const r = await resp.json();
      if (r.already_running) setNote("En kørsel er allerede i gang.");
      else if (r.started) setNote("Ny kørsel startet — den tager et par timer.");
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

  return (
    <div style={{ position: "relative", display: "flex", flexDirection: "column", height: "100%", background: "var(--bg-base)", color: "var(--text-primary)" }}>
      <div style={{ padding: "8px 12px", borderBottom: "1px solid var(--border-subtle)", display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexShrink: 0 }}>
        <div>
          <div style={{ fontSize: 13, fontWeight: 700 }}>Swing top-10</div>
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
            {data?.generated_local
              ? `Genereret ${data.generated_local} (${data.source ?? "?"}) · ${ageText(data.generated_utc)}`
              : "Ingen top-10 genereret endnu"}
          </div>
        </div>
        <button
          onClick={() => setShowConfirm(true)}
          disabled={disabled}
          title="Kører en frisk top-10 mod IBKR. Tager et par timer. Kør uden for handelstid."
          style={{
            background: disabled ? "var(--bg-elevated)" : "var(--accent)",
            color: disabled ? "var(--text-muted)" : "var(--bg-base)",
            border: "none",
            borderRadius: 4,
            padding: "8px 14px",
            fontSize: 13,
            fontWeight: 700,
            cursor: disabled ? "default" : "pointer",
            whiteSpace: "nowrap",
          }}
        >
          {running ? "Kører…" : starting ? "Starter…" : "Kør ny (IBKR)"}
        </button>
      </div>

      {running && (
        <div style={{ padding: "8px 12px", background: "var(--bg-elevated)", color: "var(--text-primary)", fontSize: 12, borderBottom: "1px solid var(--border-subtle)" }}>
          Ny top-10 kører{data?.started_utc ? ` — startet ${new Date(data.started_utc).toLocaleTimeString()}` : ""}. Det tager et par timer; listen opdateres automatisk når den er færdig.
        </div>
      )}
      {note && (
        <div style={{ padding: "6px 12px", fontSize: 12, color: "var(--text-muted)", borderBottom: "1px solid var(--border-subtle)" }}>{note}</div>
      )}

      <div style={{ flex: 1, overflowY: "auto", padding: "4px 12px 12px" }}>
        {loading && !data ? (
          <div style={{ color: "var(--text-muted)", fontSize: 13, padding: 12 }}>Henter…</div>
        ) : !data?.rows?.length ? (
          <div style={{ color: "var(--text-muted)", fontSize: 13, padding: 12, lineHeight: 1.5 }}>
            Ingen top-10 at vise endnu. Tryk <b>Kør ny (IBKR)</b> for at lave en. Det tager et par timer og bør køres uden for handelstid.
          </div>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
            <thead>
              <tr style={{ color: "var(--text-muted)" }}>
                <th style={thStyle}>#</th>
                <th style={{ ...thStyle, textAlign: "left" }}>Ticker</th>
                <th style={thStyle}>SAMLET</th>
                <th style={{ ...thStyle, textAlign: "left" }}>Band</th>
                <th style={thStyle}>Gate</th>
                <th style={thStyle}>Tek</th>
                <th style={thStyle}>Fund</th>
                <th style={thStyle}>Kat</th>
                <th style={thStyle}>rs_3m</th>
              </tr>
            </thead>
            <tbody>
              {data.rows.map((r) => (
                <tr key={r.rank} style={{ borderTop: "1px solid var(--border-subtle)" }}>
                  <td style={{ ...tdStyle, color: "var(--text-muted)" }}>{r.rank}</td>
                  <td style={{ ...tdStyle, textAlign: "left", fontWeight: 700 }}>{r.ticker}</td>
                  <td style={{ ...tdStyle, fontWeight: 700 }}>{num(r.final, 2)}</td>
                  <td style={{ ...tdStyle, textAlign: "left", color: "var(--text-muted)" }}>{r.band}</td>
                  <td style={tdStyle}>{num(r.gate, 2)}</td>
                  <td style={tdStyle}>{num(r.tech)}</td>
                  <td style={tdStyle}>{num(r.fund)}</td>
                  <td style={tdStyle}>{num(r.cat)}</td>
                  <td style={tdStyle}>{num(r.rs_3m)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {showConfirm && (
        <div style={{ position: "absolute", inset: 0, background: "rgba(0,0,0,0.55)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 20, padding: 16 }}>
          <div style={{ background: "var(--bg-elevated)", border: "1px solid var(--border-strong)", borderRadius: 8, padding: 16, maxWidth: 440, color: "var(--text-primary)" }}>
            <div style={{ fontSize: 13, lineHeight: 1.55, marginBottom: 16 }}>
              Denne kørsel tager et par timer. Den afvikles i baggrunden og du kan ikke følge den, start denne top-10 senere for at se resultatet. Vil du stadigt "køre nu"?
            </div>
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
              <button
                onClick={() => setShowConfirm(false)}
                style={{ background: "var(--bg-base)", color: "var(--text-primary)", border: "1px solid var(--border-strong)", borderRadius: 4, padding: "8px 14px", fontSize: 13, fontWeight: 700, cursor: "pointer" }}
              >
                Annullér
              </button>
              <button
                onClick={confirmRun}
                style={{ background: "var(--accent)", color: "var(--bg-base)", border: "none", borderRadius: 4, padding: "8px 14px", fontSize: 13, fontWeight: 700, cursor: "pointer" }}
              >
                Ja, kør nu
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
