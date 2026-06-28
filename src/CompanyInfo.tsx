import { useEffect, useState } from "react";
import { openUrl } from "@tauri-apps/plugin-opener";

// Firma-info (Google-knowledge-panel-stil) for den valgte ticker. Henter /company/<ticker>
// (Finnhub-profil + Wikipedia-beskrivelse + yfinance), dagligt cachet i backenden.
const API = "http://127.0.0.1:8000/company/";

interface Company {
  ticker: string; name: string; exchange: string; industry: string; sector: string;
  country: string; currency: string; ipo: string;
  market_cap_musd: number | null; shares_out_m: number | null;
  employees: number | null; ceo: string; logo: string; website: string;
  description: string; desc_source: string; wiki_url: string; ok: boolean;
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
  const [data, setData] = useState<Company | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [noLogo, setNoLogo] = useState(false);

  useEffect(() => {
    if (!ticker) { setData(null); return; }
    let cancelled = false;
    setLoading(true); setErr(""); setNoLogo(false);
    fetch(API + encodeURIComponent(ticker))
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
  }, [ticker]);

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

  const linkStyle: React.CSSProperties = { color: "var(--accent, #3b82f6)", cursor: "pointer" };

  return (
    <div style={{ height: "100%", overflow: "auto", padding: "16px 18px",
      background: "var(--bg-base)", color: "var(--text-primary)" }}>
      {!ticker && <div style={{ color: "var(--text-muted)" }}>Vælg en ticker for at se firma-info.</div>}
      {ticker && loading && !data && <div style={{ color: "var(--text-muted)" }}>Henter firma-info for {ticker}…</div>}
      {err && <div style={{ color: "var(--bear)" }}>⚠ {err}</div>}

      {data && (
        <>
          {/* Header */}
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

          {/* Beskrivelse */}
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

          {/* Fakta-tabel */}
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

          {/* Links */}
          <div style={{ marginTop: 12, display: "flex", gap: 16, fontSize: 12.5 }}>
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
