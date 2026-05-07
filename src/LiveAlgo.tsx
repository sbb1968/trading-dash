import { useState, useEffect, useRef } from "react";

// ── Typer ─────────────────────────────────────────────────────
type AlgoStatus =
  | "idle" | "started" | "scanning" | "universe_ready"
  | "loading_orb" | "orb_ready" | "trading" | "done"
  | "stopped" | "error";

interface StatusMsg {
  type:       "algo_status";
  status:     AlgoStatus;
  message:    string;
  total_pnl:  number;
  positions:  number;
  trades:     number;
  time:       string;
}

interface TradeMsg {
  type:         "algo_trade";
  action:       "buy" | "sell";
  ticker:       string;
  price?:       number;
  shares?:      number;
  entry_price?: number;
  exit_price?:  number;
  pnl?:         number;
  pnl_pct?:     number;
  reason?:      string;
  entry_time?:  string;
  exit_time?:   string;
  time?:        string;
}

type AlgoMsg = StatusMsg | TradeMsg;

interface Trade {
  ticker:      string;
  entry_price: number;
  exit_price:  number;
  shares:      number;
  pnl:         number;
  pnl_pct:     number;
  reason:      string;
  entry_time:  string;
  exit_time:   string;
}

interface Position {
  ticker:      string;
  entry_price: number;
  shares:      number;
  entry_time:  string;
}

function usd(v: number) {
  return v.toLocaleString("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2 });
}

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

export function LiveAlgo() {
  const [status, setStatus]       = useState<AlgoStatus>("idle");
  const [message, setMessage]     = useState("Algoritmen er ikke startet");
  const [totalPnl, setTotalPnl]   = useState(0);
  const [positions, setPositions] = useState<Position[]>([]);
  const [trades, setTrades]       = useState<Trade[]>([]);
  const [log, setLog]             = useState<string[]>([]);
  const [connected, setConnected] = useState(false);
  const [universe, setUniverse]   = useState<string[]>([]);
  const wsRef  = useRef<WebSocket | null>(null);
  const logRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function connect() {
      const ws = new WebSocket("ws://127.0.0.1:8000/ws/algo");
      wsRef.current = ws;
      ws.onopen    = () => { setConnected(true);  addLog("✅ Forbundet til backend"); };
      ws.onmessage = (e) => { try { handleMessage(JSON.parse(e.data)); } catch {} };
      ws.onclose   = () => { setConnected(false); addLog("⚠ Backend ikke forbundet — genforbinder hvert 3. sek..."); setTimeout(connect, 3000); };
      ws.onerror   = () => setConnected(false);
    }
    connect();
    return () => wsRef.current?.close();
  }, []);

  function handleMessage(msg: AlgoMsg) {
    if (msg.type === "algo_status") {
      setStatus(msg.status);
      setMessage(msg.message);
      setTotalPnl(msg.total_pnl);
      addLog(`[${msg.time}] ${msg.message}`);
      if (msg.status === "universe_ready" && msg.message.includes(":")) {
        const part = msg.message.split(":")[1]?.trim();
        if (part) setUniverse(part.split(", ").filter(Boolean));
      }
    } else if (msg.type === "algo_trade") {
      if (msg.action === "buy") {
        setPositions(prev => [...prev, { ticker: msg.ticker, entry_price: msg.price!, shares: msg.shares!, entry_time: msg.time! }]);
        addLog(`📈 KØB  ${msg.shares} ${msg.ticker} @ ${usd(msg.price!)}`);
      } else {
        setPositions(prev => prev.filter(p => p.ticker !== msg.ticker));
        setTrades(prev => [...prev, {
          ticker: msg.ticker, entry_price: msg.entry_price!, exit_price: msg.exit_price!,
          shares: msg.shares!, pnl: msg.pnl!, pnl_pct: msg.pnl_pct!,
          reason: msg.reason!, entry_time: msg.entry_time!, exit_time: msg.exit_time!,
        }]);
        const emoji = (msg.pnl || 0) >= 0 ? "✅" : "❌";
        addLog(`📉 SÆLG ${msg.shares} ${msg.ticker} @ ${usd(msg.exit_price!)}  ${emoji} ${usd(msg.pnl!)} (${msg.reason})`);
      }
    }
  }

  function addLog(line: string) { setLog(prev => [...prev.slice(-99), line]); }

  useEffect(() => { if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight; }, [log]);

  function sendCommand(cmd: string) {
    console.log("[LiveAlgo] sendCommand:", cmd, "readyState:", wsRef.current?.readyState);
    if (wsRef.current?.readyState === WebSocket.OPEN)
      wsRef.current.send(JSON.stringify({ command: cmd }));
  }

  const wins     = trades.filter(t => t.pnl > 0);
  const losses   = trades.filter(t => t.pnl < 0);
  const isActive = ["started","scanning","universe_ready","loading_orb","orb_ready","trading"].includes(status);

  const fs  = "var(--fs-content-livealgo, 14px)";
  const fsh = "var(--fs-header-livealgo,  13px)";
  const fst = "var(--fs-title-livealgo,   18px)";

  const card = (extra?: React.CSSProperties): React.CSSProperties => ({
    background: "var(--bg-elevated)", border: "1px solid var(--border-subtle)",
    borderRadius: 8, padding: "14px 16px", ...extra,
  });

  const sectionTitle = (emoji: string, text: string) => (
    <div style={{ fontSize: fsh, fontWeight: 700, color: "var(--text-primary)", textTransform: "uppercase", letterSpacing: "0.5px", marginBottom: 10 }}>
      {emoji} {text}
    </div>
  );

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden", padding: "16px 18px", gap: 14, fontSize: fs }}>

      {/* ── Header ── */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div style={{ fontSize: fst, fontWeight: 800, color: "var(--accent)" }}>
          🤖 Live Algo Trading
        </div>
        <div style={{ fontSize: fsh, color: "var(--text-secondary)" }}>
          Stop -2% · Target +4% · 09:45–10:30 ET
        </div>
      </div>

      {/* ── Forbindelsesstatus — stor og tydelig ── */}
      <div style={{
        ...card(),
        display: "flex", alignItems: "center", gap: 14,
        background: connected ? "rgba(0,200,100,0.08)" : "rgba(255,60,60,0.08)",
        border: `1px solid ${connected ? "var(--bull)" : "var(--bear)"}`,
      }}>
        <div style={{
          width: 16, height: 16, borderRadius: "50%", flexShrink: 0,
          background: connected ? "var(--bull)" : "var(--bear)",
          boxShadow: connected ? "0 0 8px rgba(0,200,100,0.6)" : "0 0 8px rgba(255,60,60,0.6)",
        }} />
        <div>
          <div style={{ fontSize: 16, fontWeight: 800, color: connected ? "var(--bull)" : "var(--bear)" }}>
            {connected ? "Backend forbundet" : "Backend ikke forbundet"}
          </div>
          <div style={{ fontSize: fsh, color: "var(--text-secondary)", marginTop: 2 }}>
            {connected
              ? "Klar — klik Start Algoritme for at begynde"
              : "Start backend: uvicorn main:app --reload"}
          </div>
        </div>
      </div>

      {/* ── Algo-status ── */}
      <div style={card()}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6 }}>
          {isActive && (
            <div style={{ width: 10, height: 10, borderRadius: "50%", background: "var(--bull)", animation: "pulse 1.5s infinite", flexShrink: 0 }} />
          )}
          <span style={{ fontSize: 16, fontWeight: 800, color: STATUS_COLOR[status] }}>
            {STATUS_LABEL[status]}
          </span>
        </div>
        <div style={{ fontSize: fs, color: "var(--text-secondary)", lineHeight: 1.5 }}>{message}</div>
      </div>

      {/* ── START / STOP KNAP — vises altid ── */}
      {!isActive ? (
        <button
          onClick={() => { if (connected) sendCommand("start"); }}
          style={{
            background:    connected
              ? "linear-gradient(135deg, #00cc66, #00aa44)"
              : "var(--bg-elevated)",
            border:        connected ? "none" : "2px solid var(--border-default)",
            borderRadius:  10,
            padding:       "20px 0",
            width:         "100%",
            fontSize:      22,
            fontWeight:    900,
            color:         connected ? "#000" : "var(--text-secondary)",
            cursor:        connected ? "pointer" : "not-allowed",
            letterSpacing: "0.5px",
            boxShadow:     connected ? "0 4px 28px rgba(0,200,100,0.4)" : "none",
            transition:    "transform 0.1s, box-shadow 0.1s",
            opacity:       connected ? 1 : 0.5,
          }}
          onMouseEnter={e => {
            if (connected) {
              e.currentTarget.style.transform = "scale(1.02)";
              e.currentTarget.style.boxShadow = "0 6px 36px rgba(0,200,100,0.55)";
            }
          }}
          onMouseLeave={e => {
            e.currentTarget.style.transform = "scale(1)";
            e.currentTarget.style.boxShadow = connected ? "0 4px 28px rgba(0,200,100,0.4)" : "none";
          }}
        >
          ▶  Start Algoritme
        </button>
      ) : (
        <button
          onClick={() => sendCommand("stop")}
          style={{
            background: "var(--bear)", border: "none", borderRadius: 10,
            padding: "16px 0", width: "100%", fontSize: 18,
            fontWeight: 800, color: "#fff", cursor: "pointer",
          }}
        >
          ■  Stop Algoritme
        </button>
      )}

      {/* ── P&L + Stats ── */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 10 }}>
        {[
          { label: "Dagens P&L",  value: usd(totalPnl),                      color: totalPnl >= 0 ? "var(--bull)" : "var(--bear)", big: true  },
          { label: "Handler",     value: `${trades.length}`,                  color: "var(--text-primary)", big: false },
          { label: "Win / Loss",  value: `${wins.length} / ${losses.length}`, color: "var(--text-primary)", big: false },
          { label: "Positioner",  value: `${positions.length} / 3`,           color: positions.length > 0 ? "var(--accent)" : "var(--text-secondary)", big: false },
        ].map((item, i) => (
          <div key={i} style={{ ...card(), textAlign: "center" }}>
            <div style={{ fontSize: fsh, color: "var(--text-secondary)", textTransform: "uppercase", letterSpacing: "0.5px", marginBottom: 6 }}>{item.label}</div>
            <div style={{ fontSize: item.big ? 22 : 17, fontWeight: 800, color: item.color }}>{item.value}</div>
          </div>
        ))}
      </div>

      {/* ── Åbne positioner ── */}
      {positions.length > 0 && (
        <div style={card()}>
          {sectionTitle("📊", "Åbne positioner")}
          {positions.map((p, i) => (
            <div key={i} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "6px 0", borderBottom: i < positions.length-1 ? "1px solid var(--border-subtle)" : "none" }}>
              <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
                <span style={{ fontWeight: 800, fontSize: 16, color: "var(--accent)", fontFamily: "monospace" }}>{p.ticker}</span>
                <span style={{ fontSize: fs, color: "var(--text-primary)" }}>{p.shares} stk @ {usd(p.entry_price)}</span>
              </div>
              <span style={{ fontSize: fsh, color: "var(--text-secondary)" }}>ind {p.entry_time}</span>
            </div>
          ))}
        </div>
      )}

      {/* ── Equity kurve ── */}
      <div style={card()}>
        {sectionTitle("📈", "P&L kurve")}
        <EquityCurve trades={trades} />
      </div>

      {/* ── Handler-tabel ── */}
      {trades.length > 0 && (
        <div style={card()}>
          {sectionTitle("📋", "Handler i dag")}
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: fs }}>
            <thead>
              <tr style={{ borderBottom: "1px solid var(--border-default)" }}>
                {["Aktie","Tid","Entry","Exit","Stk.","P&L","Årsag"].map(h => (
                  <th key={h} style={{ padding: "5px 8px", textAlign: h === "P&L" ? "right" : "left", color: "var(--text-primary)", fontWeight: 700, fontSize: fsh, textTransform: "uppercase" }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {[...trades].reverse().map((t, i) => (
                <tr key={i} style={{ borderBottom: "1px solid var(--border-subtle)" }}>
                  <td style={{ padding: "6px 8px", fontWeight: 800, fontSize: 15, color: t.pnl >= 0 ? "var(--bull)" : "var(--bear)", fontFamily: "monospace" }}>{t.ticker}</td>
                  <td style={{ padding: "6px 8px", color: "var(--text-secondary)", fontSize: fsh }}>{t.entry_time}→{t.exit_time}</td>
                  <td style={{ padding: "6px 8px", color: "var(--text-primary)" }}>{usd(t.entry_price)}</td>
                  <td style={{ padding: "6px 8px", color: "var(--text-primary)" }}>{usd(t.exit_price)}</td>
                  <td style={{ padding: "6px 8px", color: "var(--text-primary)" }}>{t.shares}</td>
                  <td style={{ padding: "6px 8px", textAlign: "right", fontWeight: 800, color: t.pnl >= 0 ? "var(--bull)" : "var(--bear)" }}>
                    {t.pnl >= 0 ? "+" : ""}{usd(t.pnl)}
                  </td>
                  <td style={{ padding: "6px 8px" }}>
                    <span style={{
                      fontSize: fsh, padding: "3px 8px", borderRadius: 4,
                      background: t.reason === "take profit" ? "rgba(0,200,100,0.15)" : t.reason === "stop loss" ? "rgba(255,60,60,0.15)" : "rgba(100,100,200,0.15)",
                      color: t.reason === "take profit" ? "var(--bull)" : t.reason === "stop loss" ? "var(--bear)" : "var(--accent)",
                    }}>
                      {t.reason === "take profit" ? "🎯 TP" : t.reason === "stop loss" ? "🛑 SL" : "⏱ Tid"}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* ── Universe ── */}
      {universe.length > 0 && (
        <div style={card()}>
          {sectionTitle("🔍", "Dagens universe")}
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {universe.map(ticker => (
              <span key={ticker} style={{ background: "var(--bg-base)", border: "1px solid var(--border-default)", borderRadius: 4, padding: "4px 10px", fontSize: fs, fontWeight: 700, fontFamily: "monospace", color: "var(--text-primary)" }}>
                {ticker}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* ── Log ── */}
      <div style={{ ...card(), flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>
        {sectionTitle("📡", "Live log")}
        <div ref={logRef} style={{
          background: "var(--bg-base)", borderRadius: 4,
          padding: "10px 12px", minHeight: 200, flex: 1, overflowY: "auto",
          fontFamily: "monospace", fontSize: fsh, color: "var(--text-secondary)", lineHeight: 1.9,
        }}>
          {log.length === 0 && <span style={{ color: "var(--text-secondary)" }}>Afventer forbindelse til backend...</span>}
          {log.map((line, i) => (
            <div key={i} style={{
              color: line.includes("✅") || line.includes("📈") ? "var(--bull)"
                   : line.includes("❌") || line.includes("⚠") ? "var(--bear)"
                   : line.includes("📉") ? "var(--accent)"
                   : "var(--text-secondary)",
            }}>{line}</div>
          ))}
        </div>
      </div>

    </div>
  );
}
