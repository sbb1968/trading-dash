import { useState, useEffect } from "react";
import ReactMarkdown from "react-markdown";

interface Props {
  strategy: string;            // fx "confluence2"
  version: "iben" | "teknisk"; // hvilken doc-version
  onClose: () => void;
}

export function StrategyDocsModal({ strategy, version, onClose }: Props) {
  const [content, setContent] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState("");

  useEffect(() => {
    setLoading(true);
    setError("");
    fetch(`http://127.0.0.1:8000/strategies/${strategy}/docs/${version}`)
      .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then(data => {
        setContent(data.content || "");
        setLoading(false);
      })
      .catch(e => {
        setError(`Kunne ikke hente dokumentation: ${e.message || e}`);
        setLoading(false);
      });
  }, [strategy, version]);

  // ESC for at lukke
  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [onClose]);

  const title = version === "iben"
    ? "📖 Strategi — almindelig forklaring"
    : "🔬 Strategi — teknisk reference";

  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0, 0, 0, 0.7)",
        zIndex: 9999,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 40,
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          background: "var(--bg-base)",
          border: "1px solid var(--border-default)",
          borderRadius: 8,
          width: "min(900px, 90vw)",
          maxHeight: "90vh",
          display: "flex",
          flexDirection: "column",
          boxShadow: "0 20px 60px rgba(0,0,0,0.5)",
        }}
      >
        {/* Header */}
        <div style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "14px 20px",
          borderBottom: "1px solid var(--border-subtle)",
          flexShrink: 0,
        }}>
          <div style={{
            fontSize: 16,
            fontWeight: 700,
            color: "var(--accent)",
          }}>
            {title}
          </div>
          <button
            onClick={onClose}
            style={{
              background: "transparent",
              border: "1px solid var(--border-default)",
              color: "var(--text-secondary)",
              borderRadius: 4,
              padding: "4px 10px",
              cursor: "pointer",
              fontSize: 13,
            }}
            title="Luk (ESC)"
          >
            ✕ Luk
          </button>
        </div>

        {/* Body */}
        <div style={{
          flex: 1,
          overflow: "auto",
          padding: "20px 28px",
          fontSize: 14,
          lineHeight: 1.6,
          color: "var(--text-primary)",
        }}>
          {loading && (
            <div style={{
              textAlign: "center",
              padding: 40,
              color: "var(--text-muted)",
              fontStyle: "italic",
            }}>
              Indlæser dokumentation...
            </div>
          )}

          {error && (
            <div style={{
              padding: "12px 16px",
              background: "rgba(248, 113, 113, 0.1)",
              border: "1px solid var(--bear)",
              borderRadius: 4,
              color: "var(--bear)",
            }}>
              ⚠ {error}
            </div>
          )}

          {content && (
            <div className="markdown-body">
              <ReactMarkdown>{content}</ReactMarkdown>
            </div>
          )}
        </div>
      </div>

      {/* Markdown styling — scoped via className */}
      <style>{`
        .markdown-body h1 {
          font-size: 22px;
          color: var(--accent);
          margin: 8px 0 14px;
          border-bottom: 2px solid var(--border-subtle);
          padding-bottom: 8px;
        }
        .markdown-body h2 {
          font-size: 18px;
          color: var(--text-primary);
          margin: 24px 0 10px;
          border-bottom: 1px solid var(--border-subtle);
          padding-bottom: 6px;
        }
        .markdown-body h3 {
          font-size: 15px;
          color: var(--text-primary);
          margin: 16px 0 8px;
          font-weight: 700;
        }
        .markdown-body p {
          margin: 8px 0;
        }
        .markdown-body strong {
          color: var(--accent);
          font-weight: 700;
        }
        .markdown-body code {
          background: var(--bg-elevated);
          color: var(--bull);
          padding: 2px 6px;
          border-radius: 3px;
          font-size: 12px;
          font-family: 'Courier New', monospace;
        }
        .markdown-body pre {
          background: var(--bg-elevated);
          border: 1px solid var(--border-subtle);
          border-radius: 4px;
          padding: 10px 14px;
          overflow-x: auto;
          margin: 10px 0;
        }
        .markdown-body pre code {
          background: transparent;
          color: var(--text-primary);
          padding: 0;
        }
        .markdown-body ul, .markdown-body ol {
          margin: 8px 0;
          padding-left: 24px;
        }
        .markdown-body li {
          margin: 4px 0;
        }
        .markdown-body table {
          border-collapse: collapse;
          width: 100%;
          margin: 12px 0;
          font-size: 13px;
        }
        .markdown-body th, .markdown-body td {
          border: 1px solid var(--border-subtle);
          padding: 6px 10px;
          text-align: left;
        }
        .markdown-body th {
          background: var(--bg-elevated);
          color: var(--accent);
          font-weight: 700;
        }
        .markdown-body hr {
          border: none;
          border-top: 1px solid var(--border-subtle);
          margin: 20px 0;
        }
        .markdown-body blockquote {
          border-left: 3px solid var(--accent);
          padding-left: 12px;
          color: var(--text-secondary);
          margin: 10px 0;
        }
      `}</style>
    </div>
  );
}
