import { useState, type CSSProperties } from "react";
import ReactMarkdown from "react-markdown";

const API = "http://127.0.0.1:8000";

interface Meta { from: string; to: string; n_trades: number; }

const today = () => new Date().toISOString().slice(0, 10);

// Proportional, laesbar rendering af rapporten (IKKE monospace). Hver markdown-
// node faar app-tema-styling via components-prop'en.
const md: Record<string, (p: any) => JSX.Element> = {
  h1: ({ children }) => <h1 style={{ fontSize: 20, fontWeight: 800, margin: "4px 0 2px", color: "var(--text-primary)" }}>{children}</h1>,
  h2: ({ children }) => <h2 style={{ fontSize: 16, fontWeight: 700, margin: "18px 0 6px", paddingBottom: 4, borderBottom: "1px solid var(--border-default)", color: "var(--text-primary)" }}>{children}</h2>,
  h3: ({ children }) => <h3 style={{ fontSize: 14, fontWeight: 700, margin: "14px 0 4px", color: "var(--accent)" }}>{children}</h3>,
  p:  ({ children }) => <p style={{ margin: "4px 0", lineHeight: 1.5, color: "var(--text-secondary)" }}>{children}</p>,
  ul: ({ children }) => <ul style={{ margin: "4px 0", paddingLeft: 22 }}>{children}</ul>,
  li: ({ children }) => <li style={{ margin: "2px 0", lineHeight: 1.5, color: "var(--text-secondary)" }}>{children}</li>,
  strong: ({ children }) => <strong style={{ color: "var(--text-primary)", fontWeight: 700 }}>{children}</strong>,
  em: ({ children }) => <em style={{ color: "var(--text-muted)", fontStyle: "italic" }}>{children}</em>,
};

const btn: CSSProperties = {
  background: "var(--accent)", color: "var(--bg-base)", border: "none",
  borderRadius: 6, padding: "7px 14px", fontSize: 13, fontWeight: 700, cursor: "pointer",
};
const inp: CSSProperties = {
  background: "var(--bg-elevated)", color: "var(--text-primary)",
  border: "1px solid var(--border-default)", borderRadius: 4, padding: "5px 8px", fontSize: 13,
};

export function DagensLogWindow() {
  const [from, setFrom]       = useState(today);
  const [to, setTo]           = useState(today);
  const [markdown, setMarkdown] = useState("");
  const [meta, setMeta]       = useState<Meta | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError]     = useState("");
  const [copied, setCopied]   = useState(false);

  async function run() {
    setLoading(true); setError(""); setCopied(false);
    try {
      const r = await fetch(`${API}/dagenslog/report?from=${from}&to=${to}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      setMarkdown(data.markdown || "");
      setMeta({ from: data.from, to: data.to, n_trades: data.n_trades });
    } catch (e: any) {
      setError(`Kunne ikke hente dagens log fra backenden (:8000): ${e?.message || e}`);
      setMarkdown(""); setMeta(null);
    } finally {
      setLoading(false);
    }
  }

  async function copyToClaude() {
    try {
      await navigator.clipboard.writeText(markdown);
    } catch {
      // Fallback hvis clipboard-API'et er blokeret i WebView'en
      const ta = document.createElement("textarea");
      ta.value = markdown;
      ta.style.position = "fixed"; ta.style.opacity = "0";
      document.body.appendChild(ta); ta.focus(); ta.select();
      try { document.execCommand("copy"); } catch { /* ignore */ }
      document.body.removeChild(ta);
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column",
                  background: "var(--bg-base)", color: "var(--text-primary)" }}>

      {/* Instruktion til Iben */}
      <div style={{ padding: "10px 12px", fontSize: 12.5, lineHeight: 1.5,
                    color: "var(--text-secondary)", background: "var(--bg-surface)",
                    borderBottom: "1px solid var(--border-default)" }}>
        Vælg evt. et dato-interval (default er i dag) og tryk <strong style={{ color: "var(--text-primary)" }}>Kør dagens log</strong>.
        Tryk så <strong style={{ color: "var(--text-primary)" }}>Kopier til Claude</strong>, åbn en ny Claude-samtale,
        indsæt, og bed Claude om at <strong style={{ color: "var(--text-primary)" }}>fortolke dagens handler og komme med forslag</strong>.
      </div>

      {/* Kontrol-række */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap",
                    padding: "10px 12px", borderBottom: "1px solid var(--border-subtle)" }}>
        <label style={{ fontSize: 12, color: "var(--text-muted)" }}>Fra:</label>
        <input type="date" value={from} onChange={e => setFrom(e.target.value)} style={inp} />
        <label style={{ fontSize: 12, color: "var(--text-muted)" }}>Til:</label>
        <input type="date" value={to} onChange={e => setTo(e.target.value)} style={inp} />
        <button onClick={run} disabled={loading} style={{ ...btn, opacity: loading ? 0.6 : 1 }}>
          {loading ? "Henter…" : "Kør dagens log"}
        </button>

        {meta && !loading && (
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginLeft: "auto" }}>
            <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
              {meta.from}{meta.to !== meta.from ? ` → ${meta.to}` : ""} · {meta.n_trades} handler
            </span>
            <button onClick={copyToClaude} style={btn}>
              {copied ? "Kopieret!" : "Kopier til Claude"}
            </button>
          </div>
        )}
      </div>

      {/* Rapport */}
      <div style={{ flex: 1, overflow: "auto", padding: "12px 16px" }}>
        {loading && <div style={{ color: "var(--text-muted)", fontSize: 13 }}>Henter dagens log…</div>}
        {error && (
          <div style={{ padding: "10px 12px", border: "1px solid var(--bear)",
                        borderRadius: 4, color: "var(--bear)", fontSize: 13 }}>
            {error}
          </div>
        )}
        {!loading && !error && !markdown && (
          <div style={{ color: "var(--text-muted)", fontSize: 13 }}>
            Vælg datoer og tryk <strong>Kør dagens log</strong> for at hente rapporten.
          </div>
        )}
        {!loading && markdown && (
          <ReactMarkdown components={md as any}>{markdown}</ReactMarkdown>
        )}
      </div>
    </div>
  );
}
