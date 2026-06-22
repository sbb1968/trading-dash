import { useState, useEffect, useCallback, CSSProperties } from "react";

const API_GET = "http://127.0.0.1:8000/swing/top10";
const API_RUN = "http://127.0.0.1:8000/swing/top10/run";

interface Row {
  rank: number;
  ticker: string;
  company?: string | null;
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
  if (isNaN(n)) return String(v ?? "—");
  return (n >= 0 ? "+" : "") + n.toFixed(digits);
}

// Farve efter score (samme skala som swing-rapporten): grøn medvind / rød modvind.
function scoreColor(v: number | string): string {
  const n = typeof v === "number" ? v : parseFloat(v);
  if (isNaN(n)) return "var(--text-muted)";
  if (n >= 15) return "var(--bull)";
  if (n <= -15) return "var(--bear)";
  return "var(--neutral)";
}

// Backend sender ASCII-baand; vis dem pyntet på dansk (som swing-rapporten).
const BAND_LABEL: Record<string, string> = {
  "STAERK SWING-KANDIDAT": "Stærk swing-kandidat",
  "EGNET MED FORBEHOLD": "Egnet med forbehold",
  "NEUTRAL / AFVENT": "Neutral – afvent",
  "SVAG": "Svag",
  "FRARAADES": "Frarådes",
};
const bandLabel = (b: string) => BAND_LABEL[b] ?? b;
function bandColor(b: string): string {
  if (b.startsWith("STAERK")) return "var(--bull)";
  if (b.startsWith("EGNET")) return "var(--neutral)";
  if (b.startsWith("SVAG") || b.startsWith("FRARAADES")) return "var(--bear)";
  return "var(--text-muted)";
}

const thStyle: CSSProperties = { padding: "8px 10px", textAlign: "right", verticalAlign: "bottom", whiteSpace: "nowrap" };
const tdStyle: CSSProperties = { padding: "8px 10px", textAlign: "right", whiteSpace: "nowrap" };

// Kolonne-header med beskrivende titel + lille under-tekst (forklaring).
function Th({ title, sub, left = false }: { title: string; sub?: string; left?: boolean }) {
  return (
    <th style={{ ...thStyle, textAlign: left ? "left" : "right" }}>
      <div style={{ fontSize: 12, fontWeight: 700, color: "var(--text-secondary)" }}>{title}</div>
      {sub && <div style={{ fontSize: 9, fontWeight: 400, color: "var(--text-muted)", marginTop: 1 }}>{sub}</div>}
    </th>
  );
}

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
      <div style={{ padding: "10px 14px", borderBottom: "1px solid var(--border-subtle)", display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexShrink: 0 }}>
        <div>
          <div style={{ fontSize: 15, fontWeight: 800, letterSpacing: "0.3px" }}>Swing top-10</div>
          <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 3 }}>
            {data?.generated_local
              ? `Genereret ${data.generated_local} · kilde: ${data.source ?? "?"} · ${ageText(data.generated_utc)}`
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
            border: "none", borderRadius: 4, padding: "8px 16px", fontSize: 13, fontWeight: 700,
            cursor: disabled ? "default" : "pointer", whiteSpace: "nowrap",
          }}
        >
          {running ? "Kører…" : starting ? "Starter…" : "Kør ny (IBKR)"}
        </button>
      </div>

      {/* Forklarende legende (som swing-rapportens kontekst) */}
      <div style={{ padding: "8px 14px", fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.5, borderBottom: "1px solid var(--border-subtle)" }}>
        De 10 højest-scorende swing-kandidater fra hele det likvide US-univers. Alle scores er
        <b> −100…+100</b> (grøn = medvind, rød = modvind). <b>Samlet</b> = teknisk (55%) + fundamental (20%)
        + katalysator (25%), ganget med <b>handelbarheden</b> (0–1, likviditet/spænd). Listen er forfiltreret
        på 3-måneders relativ styrke før den fulde scoring.
      </div>

      {running && (
        <div style={{ padding: "8px 14px", background: "var(--bg-elevated)", color: "var(--text-primary)", fontSize: 12, borderBottom: "1px solid var(--border-subtle)" }}>
          Ny top-10 kører{data?.started_utc ? ` — startet ${new Date(data.started_utc).toLocaleTimeString()}` : ""}. Det tager et par timer; listen opdateres automatisk når den er færdig.
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
            Ingen top-10 at vise endnu. Tryk <b>Kør ny (IBKR)</b> for at lave en. Det tager et par timer og bør køres uden for handelstid.
          </div>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr>
                <Th title="#" left />
                <Th title="Aktie" sub="ticker · firmanavn" left />
                <Th title="Samlet" sub="vægtet + gated" />
                <Th title="Vurdering" sub="samlet bånd" left />
                <Th title="Handelbarhed" sub="likviditet 0–1" />
                <Th title="Teknisk" sub="55% vægt" />
                <Th title="Fundamental" sub="20% vægt" />
                <Th title="Katalysator" sub="25% vægt" />
                <Th title="Rel. styrke" sub="3 mdr vs S&P 500" />
              </tr>
            </thead>
            <tbody>
              {data.rows.map((r) => (
                <tr key={r.rank} style={{ borderTop: "1px solid var(--border-subtle)" }}>
                  <td style={{ ...tdStyle, textAlign: "left", color: "var(--text-muted)", fontWeight: 700 }}>{r.rank}</td>
                  <td style={{ ...tdStyle, textAlign: "left", maxWidth: 240 }}>
                    <div style={{ fontWeight: 800, fontSize: 14 }}>{r.ticker}</div>
                    {r.company && (
                      <div style={{ fontSize: 11, color: "var(--text-muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {r.company}
                      </div>
                    )}
                  </td>
                  <td style={{ ...tdStyle, fontWeight: 800, fontSize: 15, color: scoreColor(r.final) }}>{num(r.final, 1)}</td>
                  <td style={{ ...tdStyle, textAlign: "left", color: bandColor(r.band), fontWeight: 600 }}>{bandLabel(r.band)}</td>
                  <td style={tdStyle}>{num(r.gate, 2)}</td>
                  <td style={{ ...tdStyle, color: scoreColor(r.tech), fontWeight: 600 }}>{num(r.tech)}</td>
                  <td style={{ ...tdStyle, color: scoreColor(r.fund), fontWeight: 600 }}>{num(r.fund)}</td>
                  <td style={{ ...tdStyle, color: scoreColor(r.cat), fontWeight: 600 }}>{num(r.cat)}</td>
                  <td style={{ ...tdStyle, color: scoreColor(r.rs_3m), fontWeight: 600 }}>{num(r.rs_3m)}</td>
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
