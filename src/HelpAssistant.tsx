import { useState, useRef, useEffect } from "react";

// Hjaelpe-assistent: spoerg Claude om hvordan Trading Dash bruges.
// Backend-endpoint (samme maskine som frontenden -> localhost).
const API_HELP = "http://127.0.0.1:8000/help/ask";

interface ChatMsg {
  role: "user" | "assistant";
  content: string;
}

export function HelpAssistant() {
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, loading]);

  async function send() {
    const q = input.trim();
    if (!q || loading) return;
    const history = messages.slice(-10);
    const next: ChatMsg[] = [...messages, { role: "user", content: q }];
    setMessages(next);
    setInput("");
    setLoading(true);
    try {
      const resp = await fetch(API_HELP, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: q, history }),
      });
      let answer = "";
      if (resp.ok) {
        const data = await resp.json();
        answer = data.answer || "(tomt svar)";
      } else {
        answer = `Kunne ikke få svar (HTTP ${resp.status}). Sig til Søren hvis det bliver ved.`;
      }
      setMessages([...next, { role: "assistant", content: answer }]);
    } catch (e: any) {
      setMessages([
        ...next,
        {
          role: "assistant",
          content: `Kunne ikke nå hjælpe-assistenten (kører backenden på :8000?). Sig til Søren. (${e?.message || e})`,
        },
      ]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", background: "var(--bg-base)", color: "var(--text-primary)" }}>
      <div style={{ padding: "8px 12px", borderBottom: "1px solid var(--border-subtle)", fontSize: 13, fontWeight: 700, flexShrink: 0 }}>
        Hjælp – spørg om Trading Dash
      </div>

      <div ref={scrollRef} style={{ flex: 1, overflowY: "auto", padding: 12, display: "flex", flexDirection: "column", gap: 10 }}>
        {messages.length === 0 && (
          <div style={{ color: "var(--text-muted)", fontSize: 13, lineHeight: 1.5 }}>
            Stil et spørgsmål om hvordan Trading Dash bruges – fx "Hvordan åbner jeg Swing trading-rapporten?" eller "Hvorfor bytter den rundt på vinduerne?".
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} style={{ alignSelf: m.role === "user" ? "flex-end" : "flex-start", maxWidth: "85%" }}>
            <div
              style={{
                background: m.role === "user" ? "var(--accent)" : "var(--bg-elevated)",
                color: m.role === "user" ? "var(--bg-base)" : "var(--text-primary)",
                border: m.role === "user" ? "none" : "1px solid var(--border-subtle)",
                borderRadius: 8,
                padding: "8px 12px",
                fontSize: 13,
                lineHeight: 1.5,
                whiteSpace: "pre-wrap",
              }}
            >
              {m.content}
            </div>
          </div>
        ))}
        {loading && (
          <div style={{ alignSelf: "flex-start", color: "var(--text-muted)", fontSize: 13, padding: "8px 12px" }}>
            Tænker…
          </div>
        )}
      </div>

      <div style={{ padding: "10px 12px", borderTop: "1px solid var(--border-subtle)", display: "flex", gap: 8, flexShrink: 0 }}>
        <input
          type="text"
          placeholder="Skriv et spørgsmål…"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
          style={{ flex: 1, background: "var(--bg-input)", color: "var(--text-primary)", border: "1px solid var(--border-strong)", borderRadius: 4, padding: "8px 10px", fontSize: 13 }}
        />
        <button
          onClick={send}
          disabled={loading || !input.trim()}
          style={{
            background: loading || !input.trim() ? "var(--bg-elevated)" : "var(--accent)",
            color: loading || !input.trim() ? "var(--text-muted)" : "var(--bg-base)",
            border: "none",
            borderRadius: 4,
            padding: "8px 16px",
            fontSize: 13,
            fontWeight: 700,
            cursor: loading || !input.trim() ? "default" : "pointer",
          }}
        >
          Send
        </button>
      </div>
    </div>
  );
}
