import { useState, useEffect, useRef, type CSSProperties } from "react";
import ReactMarkdown from "react-markdown";

const API = "http://127.0.0.1:8000";

interface Peer {
  id: string; name: string; host: string | null; url: string | null;
  enabled: boolean; is_self: boolean; selectable: boolean;
}
interface Machine { id: string; name: string; ok: boolean; n_trades: number; }

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
  background: "var(--accent)", color: "#fff", border: "none",
  borderRadius: 6, padding: "7px 14px", fontSize: 13, fontWeight: 700, cursor: "pointer",
};
const inp: CSSProperties = {
  background: "var(--bg-elevated)", color: "var(--text-primary)",
  border: "1px solid var(--border-default)", borderRadius: 4, padding: "5px 8px", fontSize: 13,
};

export function DagensLogWindow() {
  const [peers, setPeers]       = useState<Peer[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [peersErr, setPeersErr] = useState("");

  // UNCONTROLLED dato-inputs (ref + defaultValue): app'en re-renderer ~hvert sekund
  // (live WS-ticks); et controlled value={from} ville kassere Ibens dato-valg midt i
  // dato-vaelgeren. Med ref ejer DOM'en vaerdien og re-renders roerer den ikke.
  const fromRef = useRef<HTMLInputElement>(null);
  const toRef   = useRef<HTMLInputElement>(null);
  const [markdown, setMarkdown] = useState("");
  const [machines, setMachines] = useState<Machine[] | null>(null);
  const [period, setPeriod]     = useState<{ from: string; to: string } | null>(null);
  const [loading, setLoading]   = useState(false);
  const [error, setError]       = useState("");
  const [copied, setCopied]     = useState(false);

  // Hent maskin-listen ved aabning; forvaelg alle valgbare (enabled + url).
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const r = await fetch(`${API}/fleet/peers`);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const data = await r.json();
        const ps: Peer[] = data.peers || [];
        if (!alive) return;
        setPeers(ps);
        setSelected(new Set(ps.filter(p => p.selectable).map(p => p.id)));
      } catch (e: any) {
        if (alive) setPeersErr(`Kunne ikke hente maskin-listen fra backenden (:8000): ${e?.message || e}`);
      }
    })();
    return () => { alive = false; };
  }, []);

  function toggle(id: string) {
    setSelected(prev => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  async function run() {
    setLoading(true); setError(""); setCopied(false);
    try {
      const from = fromRef.current?.value || today();
      const to   = toRef.current?.value || today();
      const peersParam = encodeURIComponent([...selected].join(","));
      const r = await fetch(`${API}/dagenslog/report_fleet?from=${from}&to=${to}&peers=${peersParam}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      setMarkdown(data.markdown || "");
      setMachines(data.machines || []);
      setPeriod({ from: data.from, to: data.to });
    } catch (e: any) {
      setError(`Kunne ikke hente dagens log fra backenden (:8000): ${e?.message || e}`);
      setMarkdown(""); setMachines(null); setPeriod(null);
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

  const statusLine = machines && machines.length
    ? machines.map(m => `${m.name} ${m.ok ? `OK (${m.n_trades})` : "kunne ikke naas"}`).join(" · ")
    : "";

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column",
                  background: "var(--bg-base)", color: "var(--text-primary)" }}>

      {/* Instruktion til Iben */}
      <div style={{ padding: "10px 12px", fontSize: 12.5, lineHeight: 1.5,
                    color: "var(--text-secondary)", background: "var(--bg-surface)",
                    borderBottom: "1px solid var(--border-default)" }}>
        Vælg hvilke maskiner der skal med (default er alle opsatte) og evt. et dato-interval (default i dag),
        og tryk <strong style={{ color: "var(--text-primary)" }}>Kør dagens log</strong>.
        Tryk så <strong style={{ color: "var(--text-primary)" }}>Kopier til Claude</strong>, åbn en ny Claude-samtale,
        indsæt, og bed Claude om at <strong style={{ color: "var(--text-primary)" }}>fortolke dagens handler og komme med forslag</strong>.
      </div>

      {/* Maskin-afkrydsning */}
      <div style={{ display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap",
                    padding: "8px 12px", borderBottom: "1px solid var(--border-subtle)" }}>
        <span style={{ fontSize: 12, color: "var(--text-muted)", fontWeight: 600 }}>Maskiner:</span>
        {peersErr && <span style={{ fontSize: 12, color: "var(--bear)" }}>{peersErr}</span>}
        {peers.map(pe => (
          <label key={pe.id}
            title={pe.selectable ? undefined : "Ikke opsat endnu"}
            style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 12.5,
                     cursor: pe.selectable ? "pointer" : "not-allowed",
                     color: pe.selectable ? "var(--text-secondary)" : "var(--text-muted)",
                     opacity: pe.selectable ? 1 : 0.5 }}>
            <input type="checkbox" disabled={!pe.selectable}
              checked={selected.has(pe.id)} onChange={() => toggle(pe.id)} />
            {pe.name}
            {pe.is_self && <span style={{ color: "var(--text-muted)" }}>(denne maskine)</span>}
            {!pe.selectable && <span style={{ color: "var(--text-muted)" }}>(ikke opsat)</span>}
          </label>
        ))}
      </div>

      {/* Kontrol-række */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap",
                    padding: "10px 12px", borderBottom: "1px solid var(--border-subtle)" }}>
        <label style={{ fontSize: 12, color: "var(--text-muted)" }}>Fra:</label>
        <input type="date" ref={fromRef} defaultValue={today()} style={inp} />
        <label style={{ fontSize: 12, color: "var(--text-muted)" }}>Til:</label>
        <input type="date" ref={toRef} defaultValue={today()} style={inp} />
        <button onClick={run} disabled={loading || selected.size === 0}
          style={{ ...btn, opacity: (loading || selected.size === 0) ? 0.6 : 1 }}>
          {loading ? "Henter…" : "Kør dagens log"}
        </button>

        {markdown && !loading && (
          <button onClick={copyToClaude} style={{ ...btn, marginLeft: "auto" }}>
            {copied ? "Kopieret!" : "Kopier til Claude"}
          </button>
        )}
      </div>

      {/* Status-linje pr. maskine */}
      {statusLine && !loading && (
        <div style={{ padding: "6px 12px", fontSize: 12, color: "var(--text-muted)",
                      borderBottom: "1px solid var(--border-subtle)" }}>
          {period && <>{period.from}{period.to !== period.from ? ` → ${period.to}` : ""} · </>}{statusLine}
        </div>
      )}

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
            Vælg maskiner + datoer og tryk <strong>Kør dagens log</strong> for at hente rapporten.
          </div>
        )}
        {!loading && markdown && (
          <ReactMarkdown components={md as any}>{markdown}</ReactMarkdown>
        )}
      </div>
    </div>
  );
}
