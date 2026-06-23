import { useState, useEffect, useCallback, CSSProperties } from "react";

const API = "http://127.0.0.1:8000/halt/list";

interface HaltRow {
  ticker: string;
  reason: string;            // "LULD" | "NYHED"
  direction: string;         // "up" | "down"
  halt_price: number | null;
  halt_ts_utc: string;
  resumed_ts_utc: string | null;
  status: string;            // "HALTET" | "GENAABNET"
}
interface HaltData {
  halted: HaltRow[];
  asof: string | null;
  running: boolean;
  universe_size: number;
}

function ageText(utc: string | null): string {
  if (!utc) return "";
  const then = new Date(utc).getTime();
  if (isNaN(then)) return "";
  const secs = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (secs < 60) return `${secs}s siden`;
  const mins = Math.floor(secs / 60);
  return `${mins} min siden`;
}

// Husets tids-konvention: dansk tid foerst, ET i parentes.
function fmtTime(utc: string | null): string {
  if (!utc) return "—";
  const d = new Date(utc);
  if (isNaN(d.getTime())) return "—";
  const opt: Intl.DateTimeFormatOptions = { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false };
  const dk = d.toLocaleTimeString("da-DK", { ...opt, timeZone: "Europe/Copenhagen" });
  const et = d.toLocaleTimeString("en-US", { ...opt, timeZone: "America/New_York" });
  return `${dk} (${et} ET)`;
}

function fmtPrice(v: number | null): string {
  return v == null ? "—" : `$${v.toFixed(2)}`;
}

// Aarsag-badge: LULD up groen / LULD down roed / NYHED rav.
function badge(r: HaltRow): { text: string; color: string } {
  if (r.reason === "NYHED") return { text: "NYHED", color: "var(--neutral)" };
  if (r.direction === "up") return { text: "LULD ↑", color: "var(--bull)" };
  return { text: "LULD ↓", color: "var(--bear)" };
}

// Raekke-tonering: haltet up groenlig, down roedlig, nyhed rav; genaabnet tonet ned.
function rowTint(r: HaltRow): CSSProperties {
  const resumed = r.status === "GENAABNET";
  let bg = "transparent";
  if (r.reason === "NYHED") bg = "rgba(245,158,11,0.12)";
  else if (r.direction === "up") bg = "rgba(34,197,94,0.12)";
  else bg = "rgba(239,68,68,0.12)";
  return { background: bg, opacity: resumed ? 0.5 : 1 };
}

const thStyle: CSSProperties = { padding: "8px 10px", textAlign: "left", whiteSpace: "nowrap", fontSize: 12, fontWeight: 700, color: "var(--text-secondary)" };
const tdStyle: CSSProperties = { padding: "8px 10px", textAlign: "left", whiteSpace: "nowrap" };

export function HaltScanner({ onSelectTicker }: { onSelectTicker?: (t: string) => void }) {
  const [data, setData]     = useState<HaltData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]   = useState("");

  const refresh = useCallback(async () => {
    try {
      const r = await fetch(API);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setData(await r.json());
      setError("");
    } catch (e: any) {
      setError(`Kunne ikke nå backenden (kører den på :8000?): ${e?.message || e}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 3500);   // poll hyppigt — halts er sekund-foelsomme
    return () => clearInterval(id);
  }, [refresh]);

  const rows = data?.halted ?? [];

  return (
    <div style={{ position: "relative", display: "flex", flexDirection: "column", height: "100%", background: "var(--bg-base)", color: "var(--text-primary)" }}>
      <div style={{ padding: "10px 14px", borderBottom: "1px solid var(--border-subtle)", flexShrink: 0 }}>
        <div style={{ fontSize: 15, fontWeight: 800, letterSpacing: "0.3px" }}>Halt-scanner</div>
        <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 3 }}>
          {data
            ? `${data.running ? "Overvåger" : "Inaktiv"} · ${data.universe_size} movers${data.asof ? ` · opdateret ${ageText(data.asof)}` : ""}`
            : "Forbinder…"}
        </div>
      </div>

      <div style={{ padding: "8px 14px", fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.5, borderBottom: "1px solid var(--border-subtle)" }}>
        Aktier der er <b>haltet lige nu</b> (eller netop genåbnet) i dagens movers-univers. En halt er selv et signal:
        <b> LULD</b> = voldsom bevægelse, <b>NYHED</b> = materiel nyhed. Klik en række for at sætte symbolet i de andre vinduer.
      </div>

      {error && (
        <div style={{ padding: "8px 14px", margin: "8px 14px", border: "1px solid var(--bear)", borderRadius: 4, color: "var(--bear)", fontSize: 12 }}>
          {error}
        </div>
      )}

      <div style={{ flex: 1, overflowY: "auto", padding: "4px 14px 14px" }}>
        {loading && !data ? (
          <div style={{ color: "var(--text-muted)", fontSize: 13, padding: 12 }}>Henter…</div>
        ) : rows.length === 0 ? (
          <div style={{ color: "var(--text-muted)", fontSize: 13, padding: 12, lineHeight: 1.5 }}>
            Ingen halts lige nu.{data && !data.running ? " (Scanneren er inaktiv — er TWS logget ind og markedet åbent?)" : ""}
          </div>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr>
                <th style={thStyle}>Ticker</th>
                <th style={thStyle}>Årsag</th>
                <th style={thStyle}>Halt-tid</th>
                <th style={{ ...thStyle, textAlign: "right" }}>Pris</th>
                <th style={thStyle}>Status</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const b = badge(r);
                return (
                  <tr key={r.ticker}
                    onClick={() => onSelectTicker && onSelectTicker(String(r.ticker).toUpperCase())}
                    style={{ borderTop: "1px solid var(--border-subtle)", cursor: onSelectTicker ? "pointer" : "default", ...rowTint(r) }}
                    title={onSelectTicker ? "Sæt symbol i de andre vinduer" : undefined}>
                    <td style={{ ...tdStyle, fontWeight: 800, fontSize: 14 }}>{r.ticker}</td>
                    <td style={tdStyle}>
                      <span style={{ fontSize: 11, fontWeight: 700, color: "var(--bg-base)", background: b.color, borderRadius: 3, padding: "2px 7px" }}>{b.text}</span>
                    </td>
                    <td style={{ ...tdStyle, color: "var(--text-secondary)" }}>{fmtTime(r.halt_ts_utc)}</td>
                    <td style={{ ...tdStyle, textAlign: "right" }}>{fmtPrice(r.halt_price)}</td>
                    <td style={{ ...tdStyle, fontWeight: 700, color: r.status === "HALTET" ? "var(--text-primary)" : "var(--text-muted)" }}>{r.status}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
