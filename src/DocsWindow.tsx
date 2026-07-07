import { useState, useEffect } from "react";

interface DocItem { name: string; title: string; category?: string; group?: string; }
interface DocGroup { category: string; items: DocItem[]; }
interface SuperGroup { group: string; cats: DocGroup[]; }

const API = "http://127.0.0.1:8000";

// Backenden leverer docs forsorteret paa (gruppe, kategori, NN). Vi samler dem i to
// niveauer: super-gruppe ("Trading-former" / "Strategier") -> kategori -> dokumenter.
function groupDocs(docs: DocItem[]): SuperGroup[] {
  const supers: SuperGroup[] = [];
  for (const d of docs) {
    const gName = d.group || "Øvrige";
    const cName = d.category || "Øvrige";
    let sg = supers.find(s => s.group === gName);
    if (!sg) { sg = { group: gName, cats: [] }; supers.push(sg); }
    let c = sg.cats.find(x => x.category === cName);
    if (!c) { c = { category: cName, items: [] }; sg.cats.push(c); }
    c.items.push(d);
  }
  return supers;
}

export function DocsWindow() {
  const [docs, setDocs]       = useState<DocItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState("");

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const r = await fetch(`${API}/docs/list`);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const data = await r.json();
        if (alive) setDocs(data.docs || []);
      } catch (e: any) {
        if (alive) setError(`Kunne ikke hente listen fra backenden (:8000): ${e?.message || e}`);
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; };
  }, []);

  async function openDoc(name: string) {
    const url = `${API}/docs/file/${encodeURIComponent(name)}`;
    try {
      const { openUrl } = await import("@tauri-apps/plugin-opener");
      await openUrl(url);
    } catch {
      window.open(url, "_blank");
    }
  }

  return (
    <div style={{ height: "100%", overflow: "auto", background: "var(--bg-base)",
                  color: "var(--text-primary)", padding: 12 }}>
      <div style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: 10 }}>
        Klik et dokument for at vise det. Nye PDF'er i backend/docs/ vises automatisk.
      </div>

      {loading && <div style={{ color: "var(--text-muted)", fontSize: 13 }}>Henter dokumenter...</div>}

      {error && (
        <div style={{ padding: "10px 12px", border: "1px solid var(--bear)",
                      borderRadius: 4, color: "var(--bear)", fontSize: 13 }}>
          {error}
        </div>
      )}

      {!loading && !error && docs.length === 0 && (
        <div style={{ color: "var(--text-muted)", fontSize: 13 }}>Ingen dokumenter i backend/docs/.</div>
      )}

      {groupDocs(docs).map(sg => (
        <div key={sg.group} style={{ marginBottom: 22 }}>
          <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: 0.6, textTransform: "uppercase",
                        color: "var(--text-muted)", borderBottom: "1px solid var(--border-default)",
                        paddingBottom: 5, marginBottom: 12 }}>
            {sg.group}
          </div>
          {sg.cats.map(g => (
            <div key={g.category} style={{ marginBottom: 14 }}>
              <div style={{ fontSize: 14, fontWeight: 700, color: "var(--accent)", marginBottom: 8 }}>
                {g.category}
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {g.items.map(d => (
                  <button
                    key={d.name}
                    onClick={() => openDoc(d.name)}
                    style={{ display: "flex", alignItems: "center", gap: 10, textAlign: "left",
                             background: "var(--bg-elevated)", color: "var(--text-primary)",
                             border: "1px solid var(--border-default)", borderRadius: 6,
                             padding: "10px 12px", fontSize: 13, cursor: "pointer" }}
                  >
                    <span style={{ fontSize: 10, fontWeight: 700, color: "#fff",
                                   background: "var(--accent)", borderRadius: 3, padding: "2px 5px" }}>PDF</span>
                    <span style={{ flex: 1 }}>{d.title}</span>
                    <span style={{ fontSize: 11, color: "var(--text-muted)" }}>Vis</span>
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}
