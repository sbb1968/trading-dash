import { useEffect, useState } from "react";
import { openUrl } from "@tauri-apps/plugin-opener";

// Firma-info (Google-knowledge-panel-stil). Følger den globale valgte ticker, men har
// også et lokalt inputfelt så man kan slå en hvilken som helst ticker op direkte.
// Henter /company/<ticker> (Finnhub-profil + Wikipedia + yfinance), dagligt cachet.
const API = "http://127.0.0.1:8000/company/";

interface Stats {
  price: number | null; pe: number | null; fwd_pe: number | null; pb: number | null;
  eps: number | null; div_yield: number | null; beta: number | null;
  profit_margin: number | null; roe: number | null; rev_growth: number | null;
  hi52: number | null; lo52: number | null;
}
interface Fin { year: number | string; revenue: number | null; net_income: number | null; }
interface Company {
  ticker: string; name: string; exchange: string; industry: string; sector: string;
  country: string; currency: string; ipo: string;
  market_cap_musd: number | null; shares_out_m: number | null;
  employees: number | null; ceo: string; logo: string; website: string;
  description: string; desc_source: string; wiki_url: string; ok: boolean;
  stats?: Stats; financials?: Fin[];
}

const n2 = (v: number | null | undefined) => (v == null || !isFinite(v)) ? "—" : v.toFixed(2);
const usd2 = (v: number | null | undefined) => (v == null || !isFinite(v)) ? "—" : `$${v.toFixed(2)}`;
const pctFrac = (v: number | null | undefined) => (v == null || !isFinite(v)) ? "—" : `${(v * 100).toFixed(1)}%`;
const pctRaw = (v: number | null | undefined) => (v == null || !isFinite(v)) ? "—" : `${v.toFixed(2)}%`;
function fmtBig(v: number | null | undefined): string {
  if (v == null || !isFinite(v)) return "—";
  const a = Math.abs(v), s = v < 0 ? "-" : "";
  if (a >= 1e9) return `${s}$${(a / 1e9).toFixed(2)} B`;
  if (a >= 1e6) return `${s}$${(a / 1e6).toFixed(0)} M`;
  return `${s}$${a.toFixed(0)}`;
}

function fmtCap(musd: number | null): string {
  if (musd == null || !isFinite(musd)) return "—";
  const usd = musd * 1e6;
  if (usd >= 1e12) return `$${(usd / 1e12).toFixed(2)} T`;
  if (usd >= 1e9)  return `$${(usd / 1e9).toFixed(1)} B`;
  return `$${(usd / 1e6).toFixed(0)} M`;
}
function fmtShares(m: number | null): string {
  if (m == null || !isFinite(m)) return "—";
  return m >= 1000 ? `${(m / 1000).toFixed(2)} mia.` : `${m.toFixed(0)} mio.`;
}
function fmtNum(n: number | null): string {
  return n == null ? "—" : n.toLocaleString("da-DK");
}

export function CompanyInfo({ ticker }: { ticker: string }) {
  const [query, setQuery] = useState(ticker || "");
  const [active, setActive] = useState(ticker || "");
  const [data, setData] = useState<Company | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [noLogo, setNoLogo] = useState(false);

  // Synk med den globale valgte ticker (charts/Level 2 osv.)
  useEffect(() => { setQuery(ticker || ""); setActive(ticker || ""); }, [ticker]);

  // Hent når den aktive ticker skifter
  useEffect(() => {
    if (!active) { setData(null); return; }
    let cancelled = false;
    setLoading(true); setErr(""); setNoLogo(false);
    fetch(API + encodeURIComponent(active))
      .then(r => { if (!r.ok) throw new Error(String(r.status)); return r.json(); })
      .then((j: Company) => { if (!cancelled) setData(j); })
      .catch((e: any) => {
        if (cancelled) return;
        const code = e?.message;
        if (code === "404")
          setErr("Backenden er forbundet, men kører gammel kode uden /company-endpointet — "
            + "genstart backenden (git pull + restart) for at få Firma-info.");
        else
          setErr(`Kunne ikke hente firma-info (${code || "netværksfejl"}).`);
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [active]);

  const submit = () => {
    const t = query.trim().toUpperCase();
    if (t) { setQuery(t); setActive(t); }
  };

  const facts: [string, string][] = data ? [
    ["Branche", data.industry || "—"],
    ["Sektor", data.sector || "—"],
    ["Land", data.country || "—"],
    ["Børs", data.exchange || "—"],
    ["IPO", data.ipo || "—"],
    ["Market cap", fmtCap(data.market_cap_musd)],
    ["Aktier udestående", fmtShares(data.shares_out_m)],
    ["Medarbejdere", fmtNum(data.employees)],
    ["CEO", data.ceo || "—"],
  ] : [];

  const s = data?.stats;
  const keyStats: [string, string][] = s ? [
    ["Kurs", usd2(s.price)],
    ["P/E", n2(s.pe)],
    ["Forward P/E", n2(s.fwd_pe)],
    ["P/B", n2(s.pb)],
    ["EPS", usd2(s.eps)],
    ["Udbytte", pctRaw(s.div_yield)],
    ["Beta", n2(s.beta)],
    ["Profitmargin", pctFrac(s.profit_margin)],
    ["ROE", pctFrac(s.roe)],
    ["Omsætningsvækst", pctFrac(s.rev_growth)],
    ["52-ugers interval", (s.lo52 != null && s.hi52 != null)
      ? `$${s.lo52.toFixed(0)} – $${s.hi52.toFixed(0)}` : "—"],
  ] : [];

  const linkStyle: React.CSSProperties = { color: "var(--accent, #3b82f6)", cursor: "pointer" };

  return (
    <div style={{ height: "100%", overflow: "auto", padding: "14px 18px",
      background: "var(--bg-base)", color: "var(--text-primary)" }}>
      {/* Ticker-input */}
      <div style={{ display: "flex", gap: 8, marginBottom: 14 }}>
        <input
          value={query}
          onChange={e => setQuery(e.target.value)}
          onKeyDown={e => { if (e.key === "Enter") submit(); }}
          placeholder="Ticker (fx NOW)"
          style={{ flex: 1, background: "var(--bg-base)", color: "var(--text-primary)",
            border: "1px solid var(--border-default)", borderRadius: 5, padding: "6px 10px",
            fontSize: 13, textTransform: "uppercase" }} />
        <button onClick={submit} disabled={loading}
          style={{ cursor: "pointer", padding: "6px 16px", borderRadius: 5,
            border: "1px solid var(--border-default)", background: "var(--accent, #3b82f6)",
            color: "#fff", fontWeight: 700, fontSize: 13 }}>
          {loading ? "…" : "Vis"}
        </button>
      </div>

      {!active && <div style={{ color: "var(--text-muted)" }}>Indtast en ticker og tryk Enter.</div>}
      {active && loading && !data && <div style={{ color: "var(--text-muted)" }}>Henter firma-info for {active}…</div>}
      {err && <div style={{ color: "var(--bear)" }}>⚠ {err}</div>}

      {data && (
        <>
          <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 10 }}>
            {data.logo && !noLogo && (
              <img src={data.logo} alt="" onError={() => setNoLogo(true)}
                style={{ width: 44, height: 44, objectFit: "contain", borderRadius: 6,
                  background: "#fff", padding: 3, flexShrink: 0 }} />
            )}
            <div style={{ minWidth: 0 }}>
              <div style={{ fontSize: 17, fontWeight: 800 }}>{data.name}</div>
              <div style={{ fontSize: 12, color: "var(--text-secondary)" }}>
                {data.ticker}{data.exchange ? ` · ${data.exchange}` : ""}
              </div>
            </div>
          </div>

          {data.description ? (
            <div style={{ fontSize: 12.5, lineHeight: 1.55, color: "var(--text-primary)" }}>
              {data.description}
              {data.desc_source && (
                <span style={{ color: "var(--text-muted)", fontSize: 11 }}>
                  {" "}— {data.desc_source}
                  {data.wiki_url && <> · <span style={linkStyle} onClick={() => openUrl(data.wiki_url)}>Wikipedia</span></>}
                </span>
              )}
            </div>
          ) : (
            <div style={{ color: "var(--text-muted)", fontSize: 12 }}>
              Ingen beskrivelse fundet for {data.ticker}.
            </div>
          )}

          <div style={{ marginTop: 14, border: "1px solid var(--border-subtle)", borderRadius: 8,
            overflow: "hidden" }}>
            {facts.map(([k, v], i) => (
              <div key={k} style={{ display: "flex", fontSize: 12.5,
                background: i % 2 ? "var(--bg-base)" : "var(--bg-elevated)",
                borderBottom: i < facts.length - 1 ? "1px solid var(--border-subtle)" : "none" }}>
                <div style={{ width: 150, padding: "7px 10px", color: "var(--text-secondary)" }}>{k}</div>
                <div style={{ flex: 1, padding: "7px 10px", fontWeight: 600 }}>{v}</div>
              </div>
            ))}
          </div>

          {/* Nøgletal */}
          {keyStats.length > 0 && (
            <div style={{ marginTop: 16 }}>
              <div style={{ fontSize: 12, fontWeight: 700, color: "var(--text-secondary)",
                textTransform: "uppercase", letterSpacing: "0.5px", marginBottom: 7 }}>Nøgletal</div>
              <div style={{ display: "grid", gap: 8,
                gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))" }}>
                {keyStats.map(([k, v]) => (
                  <div key={k} style={{ background: "var(--bg-elevated)",
                    border: "1px solid var(--border-subtle)", borderRadius: 6, padding: "6px 10px" }}>
                    <div style={{ fontSize: 10.5, color: "var(--text-muted)" }}>{k}</div>
                    <div style={{ fontSize: 14, fontWeight: 700 }}>{v}</div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Omsætning & overskud */}
          {data.financials && data.financials.length > 0 && (
            <div style={{ marginTop: 16 }}>
              <div style={{ fontSize: 12, fontWeight: 700, color: "var(--text-secondary)",
                textTransform: "uppercase", letterSpacing: "0.5px", marginBottom: 7 }}>
                Omsætning &amp; overskud (seneste {data.financials.length} år)
              </div>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12.5 }}>
                <thead>
                  <tr style={{ color: "var(--text-secondary)" }}>
                    <th style={{ textAlign: "left", padding: "5px 8px" }} />
                    {data.financials.map(f => (
                      <th key={f.year} style={{ textAlign: "right", padding: "5px 8px", fontWeight: 700 }}>
                        {f.year}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  <tr style={{ borderTop: "1px solid var(--border-subtle)" }}>
                    <td style={{ padding: "6px 8px", color: "var(--text-secondary)" }}>Omsætning</td>
                    {data.financials.map(f => (
                      <td key={f.year} style={{ textAlign: "right", padding: "6px 8px", fontWeight: 600,
                        fontVariantNumeric: "tabular-nums" }}>{fmtBig(f.revenue)}</td>
                    ))}
                  </tr>
                  <tr style={{ borderTop: "1px solid var(--border-subtle)" }}>
                    <td style={{ padding: "6px 8px", color: "var(--text-secondary)" }}>Overskud</td>
                    {data.financials.map(f => (
                      <td key={f.year} style={{ textAlign: "right", padding: "6px 8px", fontWeight: 600,
                        fontVariantNumeric: "tabular-nums",
                        color: (f.net_income ?? 0) < 0 ? "var(--bear)" : "var(--bull)" }}>
                        {fmtBig(f.net_income)}</td>
                    ))}
                  </tr>
                </tbody>
              </table>
            </div>
          )}

          <div style={{ marginTop: 16, display: "flex", gap: 16, fontSize: 12.5 }}>
            {data.website && <span style={linkStyle} onClick={() => openUrl(data.website)}>🌐 Hjemmeside</span>}
            {data.wiki_url && <span style={linkStyle} onClick={() => openUrl(data.wiki_url)}>📖 Wikipedia</span>}
          </div>

          <div style={{ marginTop: 14, color: "var(--text-muted)", fontSize: 10.5 }}>
            Kilder: Finnhub (profil/logo), Wikipedia (beskrivelse), yfinance (medarbejdere/CEO).
            Cachet dagligt. {loading && "· opdaterer…"}
          </div>
        </>
      )}
    </div>
  );
}
