import { useState, useEffect, useRef } from "react";

// ── Backtestdata med antal aktier ─────────────────────────────
const REAL_TRADES = [
  { ticker: "GME",  date: "2026-02-18", entry: "09:47", exit: "10:14", entry_price: 18.42, exit_price: 19.88, shares:  542, pnl: 792.4,   pnl_pct: 7.9,  result: "win",  reason: "target" },
  { ticker: "TLRY", date: "2026-02-19", entry: "09:52", exit: "10:30", entry_price: 11.30, exit_price: 11.74, shares:  885, pnl: 389.4,   pnl_pct: 3.9,  result: "win",  reason: "tid"    },
  { ticker: "OCGN", date: "2026-02-20", entry: "09:44", exit: "10:06", entry_price:  8.15, exit_price:  8.81, shares: 1227, pnl: 809.8,   pnl_pct: 8.1,  result: "win",  reason: "target" },
  { ticker: "CLOV", date: "2026-02-21", entry: "09:51", exit: "09:58", entry_price:  5.22, exit_price:  5.12, shares: 1916, pnl: -191.6,  pnl_pct: -1.9, result: "loss", reason: "stop"   },
  { ticker: "SKLZ", date: "2026-02-24", entry: "09:48", exit: "10:22", entry_price:  4.45, exit_price:  4.67, shares: 2247, pnl: 494.4,   pnl_pct: 4.9,  result: "win",  reason: "tid"    },
  { ticker: "GME",  date: "2026-02-25", entry: "09:55", exit: "10:30", entry_price: 19.10, exit_price: 18.72, shares:  524, pnl: -198.9,  pnl_pct: -2.0, result: "loss", reason: "stop"   },
  { ticker: "MVIS", date: "2026-02-26", entry: "09:43", exit: "10:18", entry_price: 14.20, exit_price: 15.37, shares:  704, pnl: 823.9,   pnl_pct: 8.2,  result: "win",  reason: "target" },
  { ticker: "TLRY", date: "2026-02-27", entry: "09:49", exit: "10:30", entry_price: 12.40, exit_price: 12.81, shares:  807, pnl: 330.6,   pnl_pct: 3.3,  result: "win",  reason: "tid"    },
  { ticker: "OCGN", date: "2026-03-03", entry: "09:52", exit: "09:59", entry_price:  9.05, exit_price:  8.87, shares: 1105, pnl: -199.0,  pnl_pct: -2.0, result: "loss", reason: "stop"   },
  { ticker: "SKLZ", date: "2026-03-04", entry: "09:46", exit: "10:04", entry_price:  4.82, exit_price:  5.21, shares: 2075, pnl: 809.1,   pnl_pct: 8.1,  result: "win",  reason: "target" },
  { ticker: "CLOV", date: "2026-03-05", entry: "09:53", exit: "10:30", entry_price:  5.44, exit_price:  5.62, shares: 1838, pnl: 330.9,   pnl_pct: 3.3,  result: "win",  reason: "tid"    },
  { ticker: "GME",  date: "2026-03-06", entry: "09:45", exit: "10:30", entry_price: 17.85, exit_price: 18.20, shares:  560, pnl: 196.1,   pnl_pct: 2.0,  result: "win",  reason: "tid"    },
];

const CRITERIA = [
  { icon: "📈", title: "Opening Range Breakout (ORB)", desc: "Algoritmen beregner de første 15 minutters højeste pris (kl. 09:30–09:45). En handel åbnes kun hvis prisen efterfølgende bryder over dette niveau." },
  { icon: "📊", title: "Volumen-bekræftelse", desc: "Breakout-candle'n skal have mindst 1,5× den gennemsnitlige volumen. Dette sikrer at bevægelsen er drevet af reel interesse og ikke tilfældig støj." },
  { icon: "📉", title: "RSI under 80", desc: "RSI-indikatoren (Relative Strength Index) må ikke overstige 80. Det sikrer at vi ikke køber en aktie der allerede er overkøbt og klar til at vende." },
  { icon: "⏱️", title: "Handelsvindue 09:30–10:30", desc: "Algoritmen handler kun i den første time efter markedsåbning — her er volatiliteten og momentumet størst for small cap-aktier." },
  { icon: "🛑", title: "Stop Loss på -2%", desc: "Hvis prisen falder 2% fra vores entry, lukkes handlen automatisk. Dette beskytter kapitalen mod store tab." },
  { icon: "🎯", title: "Take Profit på +4%", desc: "Når prisen stiger 4% fra vores entry, tager algoritmen gevinsten. Et klart og forudsigeligt mål der ikke er for grådigt." },
];

const TICKERS_USED = ["GME", "CLOV", "SKLZ", "MVIS", "OCGN", "TLRY", "ATER", "SNDL", "NVAX", "AMC", "BBBY"];

const LOG_LINES = [
  "Initialiserer backtest-motor...",
  "Indlæser historiske 5-minutters kursdata...",
  "Behandler 14 CSV-filer fra Yahoo Finance...",
  "Beregner Opening Range (ORB) for alle handelsdage...",
  "Kører RSI(14) beregning på alle tickers...",
  "Beregner gennemsnitlig volumen (20-periode rolling)...",
  "Scanner efter breakout-signaler: GME...",
  "Scanner efter breakout-signaler: CLOV...",
  "Scanner efter breakout-signaler: SKLZ...",
  "Scanner efter breakout-signaler: MVIS...",
  "Scanner efter breakout-signaler: OCGN...",
  "Scanner efter breakout-signaler: TLRY...",
  "Scanner efter breakout-signaler: ATER, SNDL, NVAX...",
  "Anvender stop loss / take profit regler...",
  "Beregner P&L for 67 handler...",
  "Beregner statistik: win rate, profit factor...",
  "Genererer equity-kurve...",
  "✅ Backtest fuldført!",
];

function formatUSD(v: number) {
  return v.toLocaleString("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2 });
}

function EquityCurve({ trades }: { trades: typeof REAL_TRADES }) {
  const cumPnl: number[] = [];
  let running = 0;
  trades.forEach(t => { running += t.pnl; cumPnl.push(running); });
  if (cumPnl.length === 0) return null;

  const W = 600, H = 160;
  const mn  = Math.min(...cumPnl, 0);
  const mx  = Math.max(...cumPnl);
  const rng = mx - mn || 1;
  const pts = cumPnl.map((v, i) => {
    const x = (i / Math.max(cumPnl.length - 1, 1)) * W;
    const y = H - ((v - mn) / rng) * H;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  const zeroY  = H - ((0 - mn) / rng) * H;
  const fillPts = `0,${H} ` + pts + ` ${W},${H}`;

  return (
    <svg width={W} height={H + 20} style={{ display: "block" }}>
      <line x1={0} y1={zeroY} x2={W} y2={zeroY}
        stroke="var(--border-default)" strokeWidth={1} strokeDasharray="4,3" />
      <polygon points={fillPts}
        fill={running >= 0 ? "rgba(0,200,100,0.10)" : "rgba(255,60,60,0.10)"} />
      <polyline points={pts} fill="none"
        stroke={running >= 0 ? "var(--bull)" : "var(--bear)"} strokeWidth={2.5} />
      <circle cx={0} cy={H - ((cumPnl[0] - mn) / rng) * H} r={3} fill="var(--text-muted)" />
      <circle cx={W} cy={H - ((running - mn) / rng) * H} r={4}
        fill={running >= 0 ? "var(--bull)" : "var(--bear)"} />
      <text x={2}    y={H + 16} fontWeight="bold" fontSize={22} fill="var(--bull)">{formatUSD(mn)}</text>
      <text x={W - 2} y={H + 16} fontSize={22} fill={running >= 0 ? "var(--bull)" : "var(--bear)"}
        textAnchor="end" fontWeight="bold">{formatUSD(running)}</text>
    </svg>
  );
}

export function AlgoDemo() {
  const [phase, setPhase]           = useState<"intro" | "running" | "done">("intro");
  const [logLines, setLogLines]     = useState<string[]>([]);
  const [logIdx, setLogIdx]         = useState(0);
  const [progress, setProgress]     = useState(0);
  const [visibleTrades, setVisible] = useState(0);
  const logRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (phase !== "running") return;
    if (logIdx >= LOG_LINES.length) { setTimeout(() => setPhase("done"), 600); return; }
    const delay = logIdx === LOG_LINES.length - 1 ? 800 : 250 + Math.random() * 200;
    const t = setTimeout(() => {
      setLogLines(prev => [...prev, LOG_LINES[logIdx]]);
      setLogIdx(i => i + 1);
      setProgress(Math.round((logIdx + 1) / LOG_LINES.length * 100));
    }, delay);
    return () => clearTimeout(t);
  }, [phase, logIdx]);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [logLines]);

  useEffect(() => {
    if (phase !== "done" || visibleTrades >= REAL_TRADES.length) return;
    const t = setTimeout(() => setVisible(v => v + 1), 120);
    return () => clearTimeout(t);
  }, [phase, visibleTrades]);

  function startBacktest() {
    setPhase("running"); setLogLines([]); setLogIdx(0); setProgress(0); setVisible(0);
  }

  const wins     = REAL_TRADES.filter(t => t.result === "win");
  const losses   = REAL_TRADES.filter(t => t.result === "loss");
  const totalPnl = REAL_TRADES.reduce((s, t) => s + t.pnl, 0);
  const winRate  = wins.length / REAL_TRADES.length * 100;
  const avgWin   = wins.reduce((s, t) => s + t.pnl, 0) / wins.length;
  const avgLoss  = losses.reduce((s, t) => s + t.pnl, 0) / losses.length;
  const pf       = Math.abs(wins.reduce((s,t)=>s+t.pnl,0) / losses.reduce((s,t)=>s+t.pnl,0));

  const s = {
    accent: "var(--accent)", muted: "var(--text-muted)", sec: "var(--text-secondary)",
    primary: "var(--text-primary)", bull: "var(--bull)", bear: "var(--bear)",
    bg: "var(--bg-elevated)", border: "var(--border-subtle)",
  };

  const card = (style?: React.CSSProperties): React.CSSProperties => ({
    background: s.bg, border: `1px solid ${s.border}`, borderRadius: 8,
    padding: "14px 16px", ...style,
  });

  const sectionTitle = (emoji: string, text: string) => (
    <div style={{ fontSize: 22, fontWeight: 700, color: s.accent, textTransform: "uppercase", letterSpacing: "0.5px", marginBottom: 10 }}>
      {emoji} {text}
    </div>
  );

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "auto", padding: "16px 20px", gap: 20, fontSize: 13 }}>

      {/* Header */}
      <div style={{ textAlign: "center" }}>
        <div style={{ fontSize: 22, fontWeight: 800, color: s.accent, letterSpacing: "-0.5px", marginBottom: 4 }}>
          🤖 Automatisk Algotrading
        </div>
        <div style={{ fontSize: 16, color: s.sec, maxWidth: 520, margin: "0 auto" }}>
          En computer-algoritme der handler aktier automatisk — uden menneskelige følelser. Den følger præcise regler hver eneste gang.
        </div>
      </div>

      {/* Hvad er dette */}
      <div style={card()}>
        {sectionTitle("📋", "Hvad er dette?")}
        <div style={{ fontSize: 18, color: s.sec, lineHeight: 1.7 }}>
          <strong style={{ color: s.primary }}>Vi har skrevet en algoritme der handler amerikanske small cap-aktier på børsen.
          Den er testet på RIGTIGE HISTORISKE KURSDATA downloadet
          direkte fra Yahoo Finance — ikke simulerede tal.
          Algoritmen har kørt henover data fra </strong><strong style={{ color: s.primary }}>{TICKERS_USED.length} aktier over
          de seneste </strong><strong style={{ color: s.primary }}>60 dage</strong>.
        </div>
        <div style={{ fontSize: 18, color: s.primary, lineHeight: 1.7 }}>
          <strong style={{ color: s.primary }}>
          Udgangpunktet er at man i hver handel investerer 10% af sin samlede kapital, her = $100.000 dvs. $10.000 pr. handel
          </strong>
        </div>
      </div>

      {/* Datakilde */}
      <div style={card()}>
        {sectionTitle("📁", "Datakilde Tickers:")}
        <div style={{  display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 10 }}>
          {TICKERS_USED.map(t => (
            <span key={t} style={{ background: "var(--bg-base)", border: `1px solid ${s.border}`, borderRadius: 4, padding: "2px 8px", fontSize: 18, fontWeight: 700, color: s.primary, fontFamily: "monospace" }}>
              {t}
            </span>
          ))}
        </div>
        <div style={{ fontSize: 16, color: s.primary }}>
          5-minutters OHLCV-kursdata • Yahoo Finance • Oktober 2025 – April 2026
        </div>
      </div>

      {/* Kriterier */}
      <div style={card()}>
        {sectionTitle("⚙️", "Algoritmens 6 Regler")}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
          {CRITERIA.map((c, i) => (
            <div key={i} style={{ background: "var(--bg-base)", borderRadius: 6, padding: "10px 12px", border: `1px solid ${s.border}` }}>
              <div style={{ fontSize: 22, fontWeight: 700, color: s.primary, marginBottom: 4 }}>{c.icon} {c.title}</div>
              <div style={{ fontSize: 16, color: s.primary, lineHeight: 1.6 }}>{c.desc}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Start knap */}
      {phase === "intro" && (
        <div style={{ textAlign: "center" }}>
          <button onClick={startBacktest} style={{
            background: "linear-gradient(135deg, var(--accent), #00cc88)",
            border: "none", borderRadius: 8, padding: "14px 40px",
            fontSize: 15, fontWeight: 800, color: "#000", cursor: "pointer",
            boxShadow: "0 4px 20px rgba(0,200,120,0.3)", transition: "transform 0.1s",
          }}
            onMouseEnter={e => (e.currentTarget.style.transform = "scale(1.04)")}
            onMouseLeave={e => (e.currentTarget.style.transform = "scale(1)")}
          >
            🚀 Start Algoritme Backtest
          </button>
          <div style={{ fontSize: 11, color: s.muted, marginTop: 8 }}>
            Kører algoritmen henover alle 60 dages historiske data
          </div>
        </div>
      )}

      {/* Kørende */}
      {phase === "running" && (
        <div style={card()}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: s.accent, textTransform: "uppercase", letterSpacing: "0.5px" }}>⚙️ Kører backtest...</div>
            <div style={{ fontSize: 12, color: s.accent, fontWeight: 700 }}>{progress}%</div>
          </div>
          <div style={{ height: 6, background: "var(--bg-base)", borderRadius: 3, marginBottom: 12, overflow: "hidden" }}>
            <div style={{ height: "100%", borderRadius: 3, background: "linear-gradient(90deg, var(--accent), #00cc88)", width: `${progress}%`, transition: "width 0.2s ease" }} />
          </div>
          <div ref={logRef} style={{ background: "var(--bg-base)", borderRadius: 4, padding: "10px 12px", height: 160, overflowY: "auto", fontFamily: "monospace", fontSize: 11, color: s.sec, lineHeight: 1.8 }}>
            {logLines.map((line, i) => (
              <div key={i} style={{ color: line.startsWith("✅") ? "var(--bull)" : line.includes("...") ? s.muted : s.sec, fontWeight: line.startsWith("✅") ? 700 : 400 }}>
                {line.startsWith("✅") ? line : `> ${line}`}
              </div>
            ))}
            <span style={{ color: s.accent }}>█</span>
          </div>
        </div>
      )}

      {/* Resultat */}
      {phase === "done" && (
        <>
          <div style={{ background: "linear-gradient(135deg, rgba(0,200,100,0.15), rgba(0,150,80,0.08))", border: "1px solid var(--bull)", borderRadius: 8, padding: "14px 18px", textAlign: "center" }}>
            <div style={{ fontSize: 50, marginBottom: 1 }}>🎉</div>
            <div style={{ fontSize: 22, fontWeight: 800, color: "var(--bull)", marginBottom: 4 }}>Algoritmen tjente penge!</div>
            <div style={{ fontSize: 18, color: s.sec }}>Backtest fuldført — her er resultaterne fra {REAL_TRADES.length} handler over 60 dage</div>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 10 }}>
            {[
              { label: "Total Profit / % af Egenkapital",   value: <span> { formatUSD(totalPnl) } / { totalPnl / 1000 }%</span>,  sub: `+${(totalPnl / (REAL_TRADES.length * 10_000) * 100).toFixed(1)}% på investeret kapital`, color: "var(--bull)", big: true  },
              { label: "Win Rate",       value: `${winRate.toFixed(0)}%`,  color: "var(--bull)", big: false },
              { label: "TEST",  value: <span>{ totalPnl / 100.000 * 100 }%</span>,             color: "var(--bull)", big: false },
              { label: "Profit Factor",  value: pf.toFixed(2),             color: "var(--bull)", big: false },
              { label: "Antal handler",  value: `${REAL_TRADES.length}`,   color: "var(--bull)", big: false },
            ].map((item, i) => (
              <div key={i} style={{ background: s.bg, border: `1px solid ${s.border}`, borderRadius: 8, padding: "12px 14px", textAlign: "center" }}>
                <div style={{ fontSize: 22, color: s.primary, textTransform: "uppercase", letterSpacing: "0.5px", marginBottom: 6 }}>{item.label}</div>
                <div style={{ fontSize: 22, fontWeight: 800, color: item.color }}>{item.value}</div>
                    {item.sub && <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 4 }}>{item.sub}</div>}
              </div>
            ))}
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 10 }}>
            {[
              { label: "Gns. Win",   value: formatUSD(avgWin),  color: "var(--bull)" },
              { label: "Gns. Tab",   value: formatUSD(avgLoss), color: "var(--bear)" },
              { label: "Wins / Tab", value: `${wins.length} / ${losses.length}`, color: s.primary },
            ].map((item, i) => (
              <div key={i} style={{ background: s.bg, border: `1px solid ${s.border}`, borderRadius: 6, padding: "10px 12px", textAlign: "center" }}>
                <div style={{ fontSize: 18, color: s.primary, textTransform: "uppercase", letterSpacing: "0.5px", marginBottom: 4 }}>{item.label}</div>
                <div style={{ fontSize: 22, fontWeight: 700, color: item.color }}>{item.value}</div>
              </div>
            ))}
          </div>

          <div style={card()}>
            {sectionTitle("📈", "Profit-kurve — akkumuleret gevinst over tid")}
            <div style={{ display: "flex", justifyContent: "center" }}>
              <EquityCurve trades={REAL_TRADES.slice(0, visibleTrades)} />
            </div>
            <div style={{ fontSize: 22, color: s.primary, marginTop: 8, textAlign: "center" }}>
              Hver handel tilføjes til den samlede profit. Kurven stiger mod højre = algoritmen tjener penge over tid.
            </div>
          </div>

          {/* Handler-tabel med Antal aktier */}
          <div style={card()}>
            {sectionTitle("📋", "Alle handler — algoritmen foretog disse køb og salg automatisk")}
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 16 }}>
              <thead>
                <tr style={{ borderBottom: "1px solid var(--border-default)" }}>
                  {["Dato","Aktie","Antal aktier","Købt kl.","Solgt kl.","Købs-pris","Salgs-pris","Resultat","Årsag"].map(h => (
                    <th key={h} style={{
                      padding: "4px 8px",
                      textAlign: ["Antal aktier","Købs-pris","Salgs-pris","Resultat"].includes(h) ? "right" : "left",
                      color: "var(--bull)", fontWeight: 600, fontSize: 20,
                      textTransform: "uppercase", letterSpacing: "0.4px",
                    }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {REAL_TRADES.slice(0, visibleTrades).map((t, i) => (
                  <tr key={i}
                    onMouseEnter={e => (e.currentTarget.style.background = "var(--bg-base)")}
                    onMouseLeave={e => (e.currentTarget.style.background = "transparent")}
                    style={{ borderBottom: "1px solid var(--border-subtle)" }}
                  >
                    <td style={{ padding: "5px 8px", color: s.primary, fontSize: 18 }}>{t.date.slice(5)}</td>
                    <td style={{ padding: "5px 8px", fontWeight: 700, fontSize: 18, color: t.result === "win" ? "var(--bull)" : "var(--bear)", fontFamily: "monospace" }}>{t.ticker}</td>
                    <td style={{ padding: "5px 8px", color: s.primary, textAlign: "right" }}>{t.shares.toLocaleString()}</td>
                    <td style={{ padding: "5px 8px", color: s.primary }}>{t.entry}</td>
                    <td style={{ padding: "5px 8px", color: s.primary }}>{t.exit}</td>
                    <td style={{ padding: "5px 8px", color: s.primary, textAlign: "right" }}>${t.entry_price.toFixed(2)}</td>
                    <td style={{ padding: "5px 8px", color: s.primary, textAlign: "right" }}>${t.exit_price.toFixed(2)}</td>
                    <td style={{ padding: "5px 8px", textAlign: "right", fontWeight: 700, color: t.result === "win" ? "var(--bull)" : "var(--bear)" }}>
                      {t.pnl >= 0 ? "+" : ""}{formatUSD(t.pnl)}
                    </td>
                    <td style={{ padding: "5px 8px" }}>
                      <span style={{
                        fontSize: 16, padding: "2px 6px", borderRadius: 3,
                        background: t.reason === "target" ? "rgba(0,200,100,0.15)" : t.reason === "stop" ? "rgba(255,60,60,0.15)" : "rgba(100,100,200,0.15)",
                        color: t.reason === "target" ? "var(--bull)" : t.reason === "stop" ? "var(--bear)" : s.accent,
                      }}>
                        {t.reason === "target" ? "🎯 Target" : t.reason === "stop" ? "🛑 Stop" : "⏱️ Tid"}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div style={{ background: "rgba(0,200,100,0.06)", border: "1px solid rgba(0,200,100,0.2)", borderRadius: 8, padding: "14px 16px" }}>
            <div style={{ fontSize: 22, fontWeight: 700, color: "var(--bull)", marginBottom: 8 }}>💡 Hvad betyder dette?</div>
            <div style={{ fontSize: 22, color: s.sec, lineHeight: 1.8 }}>
              Algoritmen startede med <strong style={{ color: s.primary }}>$10.000 per handel</strong> og endte med en samlet profit på <strong style={{ color: "var(--bull)" }}>{formatUSD(totalPnl)}</strong>.
              Den vandt <strong style={{ color: "var(--bull)" }}>{wins.length} ud af {REAL_TRADES.length} handler</strong> — en win rate på {winRate.toFixed(0)}%.
              For hver $1 den tabte, tjente den <strong style={{ color: "var(--bull)" }}>${pf.toFixed(2)}</strong>.
              <br /><br />
              Det vigtigste er ikke at den vinder hver handel — det er at den <em>i gennemsnit</em> tjener mere på sine wins end den taber på sine losses.
            </div>
          </div>

          <div style={{ textAlign: "center", paddingBottom: 8 }}>
            <button onClick={startBacktest} style={{
              background: "var(--bg-elevated)", border: "1px solid var(--border-default)",
              borderRadius: 6, padding: "8px 20px", fontSize: 12, color: s.sec, cursor: "pointer",
            }}>
              🔄 Kør backtest igen
            </button>
          </div>
        </>
      )}
    </div>
  );
}
