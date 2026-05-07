import { useState } from "react";

interface AlgoHubProps {
  onOpen:  (windowId: string) => void;
  onClose: () => void;
}

interface HubItem {
  id:          string;
  emoji:       string;
  title:       string;
  description: string;
  status:      "ready" | "beta" | "soon";
  color:       string;
}

const HUB_ITEMS: HubItem[] = [
  {
    id:          "strategymanager",
    emoji:       "⚙",
    title:       "Strategy Manager",
    description: "Start og stop strategier. Overvåg risiko, daglig P&L og eksponering på tværs af alle kørende algoritmer.",
    status:      "ready",
    color:       "var(--bull)",
  },
  {
    id:          "livealgo",
    emoji:       "🤖",
    title:       "Live Algo",
    description: "Momentum ORB breakout — din eksisterende algoritme. Kører 09:45–10:30 ET på US small caps.",
    status:      "ready",
    color:       "var(--accent)",
  },
  {
    id:          "algoperformance",
    emoji:       "📊",
    title:       "Performance",
    description: "Historisk P&L per strategi over tid. Win rate, profit factor, drawdown og equity kurve.",
    status:      "beta",
    color:       "var(--accent)",
  },
  {
    id:          "algojournal",
    emoji:       "📋",
    title:       "Algo Journal",
    description: "Automatisk log af alle algoritmens handler. Filtrer på strategi, dato og resultat.",
    status:      "soon",
    color:       "var(--text-secondary)",
  },
  {
    id:          "algodemo",
    emoji:       "🎓",
    title:       "Demo",
    description: "Pædagogisk demonstration af momentum breakout strategien med animeret backtest og forklaring.",
    status:      "ready",
    color:       "var(--text-secondary)",
  },
];

const STATUS_LABEL: Record<string, string> = {
  ready: "Klar",
  beta:  "Beta",
  soon:  "Kommer",
};

const STATUS_COLOR: Record<string, string> = {
  ready: "var(--bull)",
  beta:  "var(--accent)",
  soon:  "var(--text-secondary)",
};

export function AlgoHub({ onOpen, onClose }: AlgoHubProps) {
  const [hoveredId, setHoveredId] = useState<string | null>(null);

  const fst = "var(--fs-title-algohub,   18px)";
  const fsh = "var(--fs-header-algohub,  13px)";
  const fsc = "var(--fs-content-algohub, 14px)";

  return (
    <div style={{
      display:       "flex",
      flexDirection: "column",
      height:        "100%",
      overflow:      "hidden",
      padding:       "20px 22px",
      gap:           16,
    }}>

      {/* ── Header ── */}
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between" }}>
        <div>
          <div style={{ fontSize: fst, fontWeight: 900, color: "var(--accent)", marginBottom: 4 }}>
            🧠 Algo Hub
          </div>
          <div style={{ fontSize: fsh, color: "var(--text-secondary)", lineHeight: 1.5 }}>
            Central adgang til alle algoritmiske trading-værktøjer.
            Kør flere strategier parallelt med kombineret risikostyring.
          </div>
        </div>
        <button
          onClick={onClose}
          style={{
            background:   "transparent",
            border:       "1px solid var(--border-subtle)",
            borderRadius: 6,
            padding:      "6px 14px",
            color:        "var(--text-secondary)",
            cursor:       "pointer",
            fontSize:     fsh,
            flexShrink:   0,
            marginLeft:   16,
          }}
        >
          ✕ Luk
        </button>
      </div>

      {/* ── Risiko-oversigt ── */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 8 }}>
        {[
          { label: "Daily Loss Limit", value: "$300",       color: "var(--bear)"         },
          { label: "Max Eksponering",  value: "$20.000",    color: "var(--text-primary)"  },
          { label: "VIX Filter",       value: "< 15 stop",  color: "var(--accent)"        },
        ].map(item => (
          <div key={item.label} style={{
            background:   "var(--bg-elevated)",
            border:       "1px solid var(--border-subtle)",
            borderRadius: 6,
            padding:      "8px 12px",
            textAlign:    "center",
          }}>
            <div style={{ fontSize: 11, color: "var(--text-secondary)", textTransform: "uppercase", letterSpacing: "0.4px", marginBottom: 4 }}>
              {item.label}
            </div>
            <div style={{ fontSize: fsh, fontWeight: 700, color: item.color }}>
              {item.value}
            </div>
          </div>
        ))}
      </div>

      {/* ── Divider ── */}
      <div style={{ borderTop: "1px solid var(--border-subtle)" }} />

      {/* ── Hub items ── */}
      <div style={{ display: "flex", flexDirection: "column", gap: 8, flex: 1, overflowY: "auto" }}>
        {HUB_ITEMS.map(item => {
          const isHovered  = hoveredId === item.id;
          const isDisabled = item.status === "soon";

          return (
            <div
              key={item.id}
              onClick={() => !isDisabled && onOpen(item.id)}
              onMouseEnter={() => setHoveredId(item.id)}
              onMouseLeave={() => setHoveredId(null)}
              style={{
                display:      "flex",
                alignItems:   "center",
                gap:          14,
                padding:      "14px 16px",
                background:   isHovered && !isDisabled ? "var(--bg-elevated)" : "var(--bg-surface)",
                border:       `1px solid ${isHovered && !isDisabled ? item.color : "var(--border-subtle)"}`,
                borderRadius: 8,
                cursor:       isDisabled ? "default" : "pointer",
                opacity:      isDisabled ? 0.5 : 1,
                transition:   "all 0.15s",
              }}
            >
              <div style={{ fontSize: 28, flexShrink: 0, width: 40, textAlign: "center", filter: isDisabled ? "grayscale(1)" : "none" }}>
                {item.emoji}
              </div>

              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
                  <span style={{ fontSize: fsc, fontWeight: 800, color: isDisabled ? "var(--text-secondary)" : "var(--text-primary)" }}>
                    {item.title}
                  </span>
                  <span style={{
                    fontSize:      10,
                    fontWeight:    700,
                    color:         STATUS_COLOR[item.status],
                    background:    "var(--bg-base)",
                    border:        `1px solid ${STATUS_COLOR[item.status]}`,
                    borderRadius:  10,
                    padding:       "1px 7px",
                    textTransform: "uppercase",
                    letterSpacing: "0.4px",
                  }}>
                    {STATUS_LABEL[item.status]}
                  </span>
                </div>
                <div style={{ fontSize: fsh, color: "var(--text-secondary)", lineHeight: 1.4 }}>
                  {item.description}
                </div>
              </div>

              {!isDisabled && (
                <div style={{ fontSize: 18, color: isHovered ? item.color : "var(--border-default)", flexShrink: 0, transition: "color 0.15s" }}>
                  →
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* ── Footer ── */}
      <div style={{ fontSize: 11, color: "var(--text-secondary)", textAlign: "center", paddingTop: 8, borderTop: "1px solid var(--border-subtle)", lineHeight: 1.5 }}>
        Paper trading aktiv · Konto DUNXXXXXXX · Port 7497
      </div>

    </div>
  );
}
