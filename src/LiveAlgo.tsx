import { useState, useEffect, useRef } from "react";
import { StrategyDocsModal } from "./StrategyDocsModal";
import {
  useLiveLog, usd,
  AlgoStatus, StrategyStatusEnum, StrategyInfo, RiskStatus, Trade,
} from "./LiveLogContext";

// ── Strategy display name → docs filename key mapping ─────────
// Når en ny strategi registreres i backend, tilføj dens mapping her
// så docs-knapperne henter de rigtige .md-filer.
const STRATEGY_DOCS_KEYS: Record<string, string> = {
  "Konfluens 2":  "confluence2",
};

const STATUS_LABEL: Record<AlgoStatus, string> = {
  idle:          "Inaktiv",
  started:       "Starter...",
  scanning:      "Scanner markedet",
  universe_ready:"Universe klar",
  loading_orb:   "Beregner ORB",
  orb_ready:     "Klar til handel",
  trading:       "Handler aktivt",
  done:          "Handelsdagen afsluttet",
  stopped:       "Stoppet",
  error:         "Fejl",
};

const STATUS_COLOR: Record<AlgoStatus, string> = {
  idle:          "var(--text-secondary)",
  started:       "var(--accent)",
  scanning:      "var(--accent)",
  universe_ready:"var(--accent)",
  loading_orb:   "var(--accent)",
  orb_ready:     "var(--bull)",
  trading:       "var(--bull)",
  done:          "var(--text-secondary)",
  stopped:       "var(--text-secondary)",
  error:         "var(--bear)",
};

const STRATEGY_STATUS_LABEL: Record<StrategyStatusEnum, string> = {
  idle:    "Inaktiv",
  running: "Kører",
  paused:  "Pauseret",
  stopped: "Stoppet",
  error:   "Fejl",
};

const STRATEGY_STATUS_COLOR: Record<StrategyStatusEnum, string> = {
  idle:    "var(--text-secondary)",
  running: "var(--bull)",
  paused:  "var(--neutral, #f59e0b)",
  stopped: "var(--text-muted)",
  error:   "var(--bear)",
};

// ── EquityCurve (uændret) ─────────────────────────────────────
function EquityCurve({ trades }: { trades: Trade[] }) {
  if (trades.length < 2) {
    return (
      <div style={{ color: "var(--text-secondary)", fontSize: 13, textAlign: "center", padding: "24px 0" }}>
        Afventer handler...
      </div>
    );
  }
  const W = 280, H = 70;
  const cumPnl: number[] = [];
  let running = 0;
  trades.forEach(t => { running += t.pnl; cumPnl.push(running); });
  const mn  = Math.min(...cumPnl, 0);
  const mx  = Math.max(...cumPnl, 1);
  const rng = mx - mn || 1;
  const pts = cumPnl.map((v, i) => {
    const x = (i / Math.max(cumPnl.length - 1, 1)) * W;
    const y = H - ((v - mn) / rng) * H;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  const zeroY   = H - ((0 - mn) / rng) * H;
  const fillPts = `0,${H} ${pts} ${W},${H}`;
  return (
    <svg width={W} height={H + 22} style={{ display: "block", margin: "0 auto" }}>
      <line x1={0} y1={zeroY} x2={W} y2={zeroY} stroke="var(--border-default)" strokeWidth={1} strokeDasharray="3,3" />
      <polygon points={fillPts} fill={running >= 0 ? "rgba(0,200,100,0.10)" : "rgba(255,60,60,0.10)"} />
      <polyline points={pts} fill="none" stroke={running >= 0 ? "var(--bull)" : "var(--bear)"} strokeWidth={2.5} />
      <circle cx={(cumPnl.length-1)/Math.max(cumPnl.length-1,1)*W} cy={H-((running-mn)/rng)*H} r={4} fill={running >= 0 ? "var(--bull)" : "var(--bear)"} />
      <text x={2}   y={H+16} fontSize={11} fill="var(--text-secondary)">{usd(mn)}</text>
      <text x={W-2} y={H+16} fontSize={11} textAnchor="end" fontWeight="bold" fill={running >= 0 ? "var(--bull)" : "var(--bear)"}>{usd(running)}</text>
    </svg>
  );
}

// ── Risikostatus-stribe ───────────────────────────────────────
function RiskBar({ risk }: { risk: RiskStatus | null }) {
  if (!risk) {
    return (
      <div style={{
        padding: "8px 12px",
        background: "var(--bg-elevated)",
        border: "1px solid var(--border-subtle)",
        borderRadius: 6,
        fontSize: 12,
        color: "var(--text-muted)",
        textAlign: "center",
        fontStyle: "italic",
      }}>
        Afventer risikostatus...
      </div>
    );
  }

  // Procentvis af daglig grænse (negativ P&L = brug af grænse)
  const lossPct = risk.total_pnl_today < 0
    ? Math.min(100, Math.abs(risk.total_pnl_today) / risk.daily_loss_limit * 100)
    : 0;

  let barColor = "var(--bull)";
  let icon = "✅";
  let label = "Risiko OK";
  if (risk.emergency_active) {
    barColor = "var(--bear)";
    icon = "🛑";
    label = "NØDSTOP AKTIVT";
  } else if (risk.daily_limit_hit) {
    barColor = "var(--bear)";
    icon = "⛔";
    label = "Daglig tab-grænse nået";
  } else if (lossPct > 75) {
    barColor = "var(--bear)";
    icon = "⚠";
    label = "Tæt på tab-grænse";
  } else if (lossPct > 50) {
    barColor = "var(--neutral, #f59e0b)";
    icon = "⚠";
    label = "Halvvejs på tab-grænse";
  }

  return (
    <div style={{
      padding: "8px 12px",
      background: "var(--bg-elevated)",
      border: `1px solid ${barColor === "var(--bull)" ? "var(--border-subtle)" : barColor}`,
      borderRadius: 6,
      display: "flex",
      alignItems: "center",
      gap: 12,
      fontSize: 12,
    }}>
      <span style={{ fontWeight: 700, color: barColor }}>{icon} {label}</span>

      <span style={{ color: "var(--text-secondary)" }}>
        Daglig P&L:{" "}
        <strong style={{
          color: risk.total_pnl_today >= 0 ? "var(--bull)" : "var(--bear)",
          fontVariantNumeric: "tabular-nums",
        }}>
          {risk.total_pnl_today >= 0 ? "+" : ""}{usd(risk.total_pnl_today)}
        </strong>
        {" / "}
        <span style={{ color: "var(--text-muted)" }}>{usd(-risk.daily_loss_limit)} grænse</span>
      </span>

      {/* Progress bar — kun synlig ved tab */}
      {lossPct > 0 && (
        <div style={{
          flex: 1,
          height: 6,
          background: "var(--bg-input)",
          borderRadius: 3,
          overflow: "hidden",
          minWidth: 80,
        }}>
          <div style={{
            width: `${lossPct}%`,
            height: "100%",
            background: barColor,
            transition: "width 300ms ease",
          }} />
        </div>
      )}

      <span style={{ color: "var(--text-muted)", marginLeft: "auto", fontSize: 11 }}>
        Eksponering: {usd(risk.total_exposure)} / {usd(risk.max_total_exposure)}
      </span>
    </div>
  );
}

// ── Strategi-row ──────────────────────────────────────────────
function StrategyRow({
  strategy,
  isSelected,
  onSelect,
  onStart,
  onStop,
}: {
  strategy: StrategyInfo;
  isSelected: boolean;
  onSelect: () => void;
  onStart: (name: string) => void;
  onStop: (name: string) => void;
}) {
  const isRunning = strategy.status === "running";
  const statusColor = STRATEGY_STATUS_COLOR[strategy.status];

  return (
    <div
      onClick={onSelect}
      style={{
        display: "grid",
        gridTemplateColumns: "1fr 100px 110px 80px 90px",
        alignItems: "center",
        gap: 12,
        padding: "8px 12px",
        background: isSelected ? "var(--accent-bg)" : "var(--bg-elevated)",
        border: `1px solid ${isSelected ? "var(--accent)" : "var(--border-subtle)"}`,
        borderRadius: 6,
        cursor: "pointer",
        fontSize: 12,
        transition: "background 150ms ease",
      }}
    >
      <div>
        <div style={{ fontWeight: 700, color: "var(--text-primary)" }}>
          {strategy.name}
        </div>
        <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 2 }}>
          {strategy.description || strategy.asset_class}
        </div>
      </div>

      <div style={{ color: statusColor, fontWeight: 600 }}>
        {isRunning && (
          <span style={{
            display: "inline-block",
            width: 8,
            height: 8,
            borderRadius: "50%",
            background: statusColor,
            marginRight: 6,
            animation: "pulse 2s infinite",
          }} />
        )}
        {STRATEGY_STATUS_LABEL[strategy.status]}
      </div>

      <div style={{
        textAlign: "right",
        color: strategy.stats.pnl_today >= 0 ? "var(--bull)" : "var(--bear)",
        fontWeight: 600,
        fontVariantNumeric: "tabular-nums",
      }}>
        {strategy.stats.pnl_today >= 0 ? "+" : ""}{usd(strategy.stats.pnl_today)}
      </div>

      <div style={{ textAlign: "right", color: "var(--text-secondary)" }}>
        {strategy.stats.open_positions} pos
      </div>

      <div style={{ textAlign: "center" }} onClick={e => e.stopPropagation()}>
        {isRunning ? (
          <button
            onClick={() => onStop(strategy.name)}
            style={{
              background: "var(--bear-muted)",
              border: "1px solid var(--bear)",
              color: "var(--bear)",
              borderRadius: 3,
              fontSize: 10,
              fontWeight: 700,
              padding: "3px 10px",
              cursor: "pointer",
            }}
          >⏹ Stop</button>
        ) : (
          <button
            onClick={() => onStart(strategy.name)}
            style={{
              background: "var(--bull-muted)",
              border: "1px solid var(--bull)",
              color: "var(--bull)",
              borderRadius: 3,
              fontSize: 10,
              fontWeight: 700,
              padding: "3px 10px",
              cursor: "pointer",
            }}
          >▶ Start</button>
        )}
      </div>
    </div>
  );
}

// ── Hoved-komponent ───────────────────────────────────────────
export function LiveAlgo() {
  // Live-state læses fra provideren (overlever vinduets remount/reload)
  const {
    log, connected, status, message, totalPnl, positions, trades,
    universe, strategies, risk, startStrategy, stopStrategy, clearLog,
  } = useLiveLog();

  // View-state (lokal — nulstilling er harmløs)
  const [selectedStrategy, setSelectedStrategy] = useState<string | null>(null);

  // Docs modal — viser markdown-dokumentation i popup
  const [docsModal, setDocsModal] = useState<{strategy: string; version: "iben" | "teknisk"} | null>(null);

  const logRef = useRef<HTMLDivElement>(null);

  // Auto-vælg første strategi hvis ingen valgt (flyttet fra handleManagerStatus)
  useEffect(() => {
    if (!selectedStrategy && strategies.length > 0) setSelectedStrategy(strategies[0].name);
  }, [strategies, selectedStrategy]);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [log]);

  const wins   = trades.filter(t => t.pnl > 0);
  const losses = trades.filter(t => t.pnl < 0);

  const fs  = "var(--fs-content-livealgo, 14px)";
  const fsh = "var(--fs-header-livealgo,  13px)";
  const fst = "var(--fs-title-livealgo,   18px)";

  const card = (extra?: React.CSSProperties): React.CSSProperties => ({
    background: "var(--bg-elevated)",
    border: "1px solid var(--border-subtle)",
    borderRadius: 8,
    padding: "14px 16px",
    ...extra,
  });

  const docButtonStyle = (): React.CSSProperties => ({
    background: "var(--bg-elevated)",
    border: "1px solid var(--accent)",
    color: "var(--accent)",
    borderRadius: 4,
    padding: "4px 10px",
    fontSize: 11,
    fontWeight: 600,
    cursor: "pointer",
    transition: "all 150ms ease",
  });

  const sectionTitle = (emoji: string, text: string) => (
    <div style={{
      fontSize: fsh,
      fontWeight: 700,
      color: "var(--text-primary)",
      textTransform: "uppercase",
      letterSpacing: "0.5px",
      marginBottom: 10
    }}>
      {emoji} {text}
    </div>
  );

  return (
    <div style={{
      display: "flex",
      flexDirection: "column",
      height: "100%",
      overflow: "hidden",
      padding: "16px 18px",
      gap: 12,
      fontSize: fs
    }}>

      {/* ── Header ── */}
      <div style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        flexShrink: 0,
      }}>
        <div style={{ fontSize: fst, fontWeight: 800, color: "var(--accent)" }}>
          🤖 Live Algo Trading
        </div>
        <div style={{
          display: "flex", alignItems: "center", gap: 8,
          fontSize: 11, color: "var(--text-secondary)",
        }}>
          <span style={{
            width: 10, height: 10, borderRadius: "50%",
            background: connected ? "var(--bull)" : "var(--bear)",
          }} />
          {connected ? "Backend forbundet" : "Backend ikke forbundet"}
        </div>
      </div>

      {/* ── Risiko-stribe ── */}
      <div style={{ flexShrink: 0 }}>
        <RiskBar risk={risk} />
      </div>

      {/* ── Strategi-liste ── */}
      <div style={{ flexShrink: 0 }}>
        <div style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: 10,
        }}>
          <div style={{
            fontSize: fsh,
            fontWeight: 700,
            color: "var(--text-primary)",
            textTransform: "uppercase",
            letterSpacing: "0.5px",
          }}>
            📋 Strategier ({strategies.length})
          </div>
          {selectedStrategy && (() => {
            // Map display name → docs filename key
            const docsKey = STRATEGY_DOCS_KEYS[selectedStrategy] || "confluence2";
            return (
              <div style={{ display: "flex", gap: 6 }}>
                <button
                  onClick={() => setDocsModal({ strategy: docsKey, version: "iben" })}
                  style={docButtonStyle()}
                  title="Almindelig forklaring af strategien"
                >
                  📖 Forklaring
                </button>
                <button
                  onClick={() => setDocsModal({ strategy: docsKey, version: "teknisk" })}
                  style={docButtonStyle()}
                  title="Teknisk reference for strategien"
                >
                  🔬 Teknisk
                </button>
              </div>
            );
          })()}
        </div>
        {strategies.length === 0 ? (
          <div style={{
            padding: 16,
            textAlign: "center",
            color: "var(--text-muted)",
            fontStyle: "italic",
            background: "var(--bg-elevated)",
            border: "1px solid var(--border-subtle)",
            borderRadius: 6,
          }}>
            Ingen registrerede strategier
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {strategies.map(s => (
              <StrategyRow
                key={s.name}
                strategy={s}
                isSelected={s.name === selectedStrategy}
                onSelect={() => setSelectedStrategy(s.name)}
                onStart={startStrategy}
                onStop={stopStrategy}
              />
            ))}
          </div>
        )}
      </div>

      {/* ── Detalje-sektion for valgt strategi ── */}
      <div style={{
        flex: 1,
        minHeight: 0,
        display: "grid",
        gridTemplateColumns: "1fr 1fr",
        gap: 12,
        overflow: "hidden",
      }}>

        {/* Venstre kolonne: positioner + equity */}
        <div style={{
          display: "flex",
          flexDirection: "column",
          gap: 12,
          overflow: "hidden",
        }}>
          {/* Åbne positioner */}
          <div style={card({ flexShrink: 0 })}>
            {sectionTitle("📍", `Åbne positioner (${positions.length})`)}
            {positions.length === 0 ? (
              <div style={{ color: "var(--text-muted)", fontSize: 12, fontStyle: "italic", textAlign: "center", padding: 8 }}>
                Ingen åbne positioner
              </div>
            ) : (
              <table className="scanner-table" style={{ width: "100%", fontSize: 12 }}>
                <thead>
                  <tr>
                    <th style={{ textAlign: "left" }}>Ticker</th>
                    <th style={{ textAlign: "right" }}>Antal</th>
                    <th style={{ textAlign: "right" }}>Entry</th>
                    <th style={{ textAlign: "right" }}>Tid</th>
                  </tr>
                </thead>
                <tbody>
                  {positions.map(p => (
                    <tr key={p.ticker}>
                      <td><strong>{p.ticker}</strong></td>
                      <td style={{ textAlign: "right" }}>{p.shares}</td>
                      <td style={{ textAlign: "right" }}>{usd(p.entry_price)}</td>
                      <td style={{ textAlign: "right", color: "var(--text-muted)" }}>{p.entry_time}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          {/* Equity-kurve */}
          <div style={card({ flexShrink: 0 })}>
            {sectionTitle("📈", `P&L kurve (${trades.length} handler)`)}
            <EquityCurve trades={trades} />
            {trades.length > 0 && (
              <div style={{
                display: "flex",
                justifyContent: "space-around",
                marginTop: 10,
                fontSize: 11,
                color: "var(--text-secondary)",
              }}>
                <span>✅ {wins.length} vund</span>
                <span>❌ {losses.length} tabt</span>
                <span>Win rate: {trades.length > 0 ? (wins.length/trades.length*100).toFixed(0) : 0}%</span>
              </div>
            )}
          </div>
        </div>

        {/* Højre kolonne: log */}
        <div style={card({ display: "flex", flexDirection: "column", overflow: "hidden" })}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            {sectionTitle("📝", "Live log")}
            <button
              onClick={clearLog}
              disabled={log.length === 0}
              title="Tøm live-loggen (påvirker ikke journalen eller handler)"
              style={{
                background: "var(--bg-elevated)",
                border: "1px solid var(--border-subtle)",
                color: "var(--text-secondary)",
                borderRadius: 3,
                fontSize: 10,
                fontWeight: 600,
                padding: "3px 10px",
                cursor: log.length === 0 ? "default" : "pointer",
                opacity: log.length === 0 ? 0.5 : 1,
                flexShrink: 0,
              }}
            >🗑 Ryd log</button>
          </div>
          <div
            ref={logRef}
            style={{
              flex: 1,
              overflow: "auto",
              fontFamily: "monospace",
              fontSize: 11,
              lineHeight: 1.6,
              background: "var(--bg-base)",
              border: "1px solid var(--border-subtle)",
              borderRadius: 4,
              padding: "8px 10px",
            }}
          >
            {log.length === 0 ? (
              <div style={{ color: "var(--text-muted)", fontStyle: "italic" }}>
                Afventer aktivitet...
              </div>
            ) : (
              log.map((line, i) => (
                <div key={i} style={{
                  color: line.includes("KØB")  ? "var(--bull)"  :
                         line.includes("SÆLG") ? "var(--bear)"  :
                         line.includes("⚠")    ? "var(--neutral, #f59e0b)" :
                         line.includes("✅")   ? "var(--bull)"  :
                                                 "var(--text-secondary)",
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                }}>{line}</div>
              ))
            )}
          </div>
        </div>
      </div>

      {/* Status-besked nederst */}
      <div style={{
        flexShrink: 0,
        padding: "6px 12px",
        background: "var(--bg-elevated)",
        border: "1px solid var(--border-subtle)",
        borderRadius: 4,
        fontSize: 11,
        color: STATUS_COLOR[status],
        fontWeight: 600,
      }}>
        Status: {STATUS_LABEL[status]} — {message}
      </div>

      {/* Docs modal */}
      {docsModal && (
        <StrategyDocsModal
          strategy={docsModal.strategy}
          version={docsModal.version}
          onClose={() => setDocsModal(null)}
        />
      )}
    </div>
  );
}
