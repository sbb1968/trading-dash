import { useState, useEffect, useCallback, CSSProperties } from "react";

const API_GET = "http://127.0.0.1:8000/buyhold/top15";
const API_RUN = "http://127.0.0.1:8000/buyhold/top15/run";

interface Row {
  rank: number;
  ticker: string;
  company?: string | null;
  price: number | string;
  final: number | string;
  band: string;
  gate: number | string;
  quality: number | string;
  growth: number | string;
  valuation: number | string;
  trend: number | string;
  oe_yield: number | string;
}

interface TopData {
  generated_local: string | null;
  generated_utc: string | null;
  source: string | null;
  universe_size: number;
  scored_cached: number;
  count: number;
  rows: Row[];
  running: boolean;
  started_utc: string | null;
  progress: { done: number | null; total: number | null } | null;
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

function plain(v: number | string, digits = 2): string {
  const n = typeof v === "number" ? v : parseFloat(v);
  if (isNaN(n)) return String(v ?? "—");
  return n.toFixed(digits);
}

// Farve efter score (samme skala som buy-and-hold-scoren): grøn medvind / rød modvind.
function scoreColor(v: number | string): string {
  const n = typeof v === "number" ? v : parseFloat(v);
  if (isNaN(n)) return "var(--text-muted)";
  if (n >= 15) return "var(--bull)";
  if (n <= -15) return "var(--bear)";
  return "var(--neutral)";
}

// Backend sender ASCII-baand; vis dem pyntet på dansk (som buy-and-hold-scoren).
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

const thStyle: CSSProperties = { padding: "8px 10px", textAlign: "right", verticalAlign: "bottom", whiteSpace: "nowrap" };
const tdStyle: CSSProperties = { padding: "8px 10px", textAlign: "right", whiteSpace: "nowrap" };

// Kolonne-header med beskrivende titel + lille under-tekst (forklaring).
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

export function BuyHoldTop15({ onSelectTicker, onOpenDetail }:
  { onSelectTicker?: (t: string) => void; onOpenDetail?: (kind: string, ticker: string) => void } = {}) {
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
    const id = setInterval(refresh, 15000);
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
      else if (r.started) setNote("Ny kørsel startet — scorer op til ~35 navne, færdig om nogle minutter.");
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
  const progText = prog && prog.total ? ` — ${prog.done ?? 0}/${prog.total} scoret` : "";

  return (
    <div style={{ position: "relative", display: "flex", flexDirection: "column", height: "100%", background: "var(--bg-base)", color: "var(--text-primary)" }}>
      <div style={{ padding: "10px 14px", borderBottom: "1px solid var(--border-subtle)", display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexShrink: 0 }}>
        <div>
          <div style={{ fontSize: 15, fontWeight: 800, letterSpacing: "0.3px" }}>Buy-and-Hold Top-15</div>
          <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 3 }}>
            {data?.generated_local
              ? `Genereret ${data.generated_local} · ${data.scored_cached ?? 0}/${data.universe_size ?? 0} scoret · ${ageText(data.generated_utc)}`
              : "Ingen top-15 genereret endnu"}
          </div>
        </div>
        <button
          onClick={() => setShowConfirm(true)}
          disabled={disabled}
          title="Henter FMP-regnskaber + IBKR-bars. Kvote-budgetteret (~35 navne/kørsel). Kør uden for handelstid."
          style={{
            background: disabled ? "var(--bg-elevated)" : "var(--accent)",
            color: disabled ? "var(--text-muted)" : "#fff",
            border: "none", borderRadius: 4, padding: "8px 16px", fontSize: 13, fontWeight: 700,
            cursor: disabled ? "default" : "pointer", whiteSpace: "nowrap",
          }}
        >
          {running ? "Kører…" : starting ? "Starter…" : "Kør ny"}
        </button>
      </div>

      {/* Forklarende legende (som buy-and-hold-scorens kontekst) */}
      <div style={{ padding: "8px 14px", fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.5, borderBottom: "1px solid var(--border-subtle)" }}>
        De 15 højest-scorende køb-og-hold-kandidater fra det kuraterede kvalitetsunivers. Alle scores er
        <b> −100…+100</b> (grøn = medvind, rød = modvind). <b>SAMLET</b> = Kvalitet (35%) + Vækst &amp; holdbarhed
        (25%) + Værdiansættelse (25%) + Langsigtet trend (15%), ganget med <b>risiko-gaten</b> (0–1;
        konkurs/udvanding/FCF/gæld). Listen fyldes inkrementelt (FMP-kvote) og genfriskes ugentligt. Klik en
        række for at åbne den fulde score.
      </div>

      {running && (
        <div style={{ padding: "8px 14px", background: "var(--bg-elevated)", color: "var(--text-primary)", fontSize: 12, borderBottom: "1px solid var(--border-subtle)" }}>
          Ny top-15 kører{progText}{data?.started_utc ? ` · startet ${new Date(data.started_utc).toLocaleTimeString()}` : ""}. Listen opdateres automatisk når den er færdig.
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
            Ingen top-15 at vise endnu. Tryk <b>Kør ny</b> for at lave en. Kørslen er kvote-budgetteret (~35 navne) og bør køres uden for handelstid; første fyldning tager nogle dage.
          </div>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr>
                <Th title="#" left tip="Rangering efter SAMLET score — 1 = bedste køb-og-hold-kandidat." />
                <Th title="Aktie" sub="ticker · firmanavn" left tip="Aktiens ticker + firmanavn. Dobbeltklik for den detaljerede score i eget vindue." />
                <Th title="Pris" sub="sidste kurs" tip="Aktiens sidste kurs fra scoringen (USD)." />
                <Th title="SAMLET" sub="vægtet + gated" tip="Vægtet lag-score (Kvalitet 35% + Vækst 25% + Værdi 25% + Trend 15%) ganget med risiko-gaten (−100…+100). Det samlede køb-og-hold-tal." />
                <Th title="Vurdering" sub="samlet bånd" left tip="Ord-bånd for SAMLET: Stærk køb-og-hold-kandidat · Egnet (langsigtet) · Neutral – afvent · Svag · Frarådes." />
                <Th title="Risiko-gate" sub="0–1" tip="Gate 0–1: konkurs (Altman Z), udvanding, FCF-fortegn, gæld (nettogæld/EBITDA), likviditet. Lav gate trækker SAMLET kraftigt ned." />
                <Th title="Kvalitet" sub="35% vægt" tip="Lag 1 (35%): marginer, ROE/ROIC, gæld, kapitalafkast." />
                <Th title="Vækst" sub="25% vægt" tip="Lag 2 (25%): omsætnings-/EPS-/FCF-vækst og holdbarhed." />
                <Th title="Værdi" sub="25% vægt" tip="Lag 3 (25%): værdiansættelse — P/E, EV/EBITDA, P/FCF, Owner-Earnings-yield." />
                <Th title="Trend" sub="15% vægt" tip="Lag 4 (15%): langsigtet kurstrend (10/30/40-uge MA, afstand til ATH, relativ styrke). Udeladt hvis IBKR var nede." />
                <Th title="OE-yield" sub="owner earnings" tip="Owner-Earnings-yield i % — normaliseret ejerindtjening pr. markedsværdi. Højere = billigere pr. investeret krone." />
              </tr>
            </thead>
            <tbody>
              {data.rows.map((r) => (
                <tr key={r.rank} style={{ borderTop: "1px solid var(--border-subtle)" }}>
                  <td style={{ ...tdStyle, textAlign: "left", color: "var(--text-muted)", fontWeight: 700 }}>{r.rank}</td>
                  <td style={{ ...tdStyle, textAlign: "left", maxWidth: 240, cursor: "pointer" }}
                    onDoubleClick={() => onOpenDetail?.("buyhold", r.ticker)}
                    onClick={() => onSelectTicker?.(r.ticker)}
                    title="Dobbeltklik for detaljeret score">
                    <div style={{ fontWeight: 800, fontSize: 14 }}>{r.ticker}</div>
                    {r.company && (
                      <div style={{ fontSize: 11, color: "var(--text-muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {r.company}
                      </div>
                    )}
                  </td>
                  <td style={tdStyle}>{r.price != null && r.price !== "" ? `$${(typeof r.price === "number" ? r.price : parseFloat(r.price)).toFixed(2)}` : "—"}</td>
                  <td style={{ ...tdStyle, fontWeight: 800, fontSize: 15, color: scoreColor(r.final) }}>{num(r.final, 1)}</td>
                  <td style={{ ...tdStyle, textAlign: "left", color: bandColor(r.band), fontWeight: 600 }}>{bandLabel(r.band)}</td>
                  <td style={tdStyle}>{plain(r.gate, 2)}</td>
                  <td style={{ ...tdStyle, color: scoreColor(r.quality), fontWeight: 600 }}>{num(r.quality)}</td>
                  <td style={{ ...tdStyle, color: scoreColor(r.growth), fontWeight: 600 }}>{num(r.growth)}</td>
                  <td style={{ ...tdStyle, color: scoreColor(r.valuation), fontWeight: 600 }}>{num(r.valuation)}</td>
                  <td style={{ ...tdStyle, color: scoreColor(r.trend), fontWeight: 600 }}>{num(r.trend)}</td>
                  <td style={{ ...tdStyle, color: "var(--text-secondary)" }}>{r.oe_yield === "" || r.oe_yield == null ? "—" : `${plain(r.oe_yield, 2)}%`}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {showConfirm && (
        <div style={{ position: "absolute", inset: 0, background: "rgba(0,0,0,0.55)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 20, padding: 16 }}>
          <div style={{ background: "var(--bg-elevated)", border: "1px solid var(--border-strong)", borderRadius: 8, padding: 16, maxWidth: 460, color: "var(--text-primary)" }}>
            <div style={{ fontSize: 13, lineHeight: 1.55, marginBottom: 16 }}>
              Denne kørsel henter FMP-regnskaber + IBKR-kursdata og er <b>kvote-budgetteret</b> (~35 navne pr. kørsel
              pga. FMP's daglige grænse på ~250 kald). Første fyldning af hele listen tager <b>nogle dage</b>; derefter
              genfriskes den ugentligt. Kør <b>uden for handelstid</b> (egen IBKR-klient, ellers Error 162 mod en kørende
              strategi). Vil du køre nu?
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
                style={{ background: "var(--accent)", color: "#fff", border: "none", borderRadius: 4, padding: "8px 14px", fontSize: 13, fontWeight: 700, cursor: "pointer" }}
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
