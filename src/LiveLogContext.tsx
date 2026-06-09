import { createContext, useContext, useEffect, useRef, useState } from "react";

const LOG_KEY = "td_livealgo_log";   // localStorage-nøgle
const LOG_MAX = 500;                 // maks. antal linjer i hukommelse/persistens

// ── Algo Status (flyttet fra LiveAlgo) ────────────────────────
export type AlgoStatus =
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
  strategy?:  string;          // NY — strategi-prefix til log ([ORB]/[KONF])
}

interface TradeMsg {
  type:         "algo_trade";
  strategy?:    string;        // NY — "Momentum ORB" eller "Konfluens"
  action:       "buy" | "sell" | "sell_short" | "buy_cover";
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
  side?:        string;        // NY — "long" eller "short"
  score?:       number;        // NY — Konfluens entry-score (4-6)
  bricks?:      string;        // NY — Konfluens bricks "TV·H·L"
}

type AlgoMsg = StatusMsg | TradeMsg;

export interface Trade {
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

export interface Position {
  ticker:      string;
  entry_price: number;
  shares:      number;
  entry_time:  string;
}

// ── Strategy Manager Status (fra /ws/strategy) ────────────────
export type StrategyStatusEnum = "idle" | "running" | "paused" | "stopped" | "error";

// Skrinlagte strategier: backend-koden og historiske journaler bevares, men
// de skjules fra frontenden (dropdowns + strategi-side). Fjern fra dette set
// hvis en strategi tages i brug igen.
const SKRINLAGTE_STRATEGIER = new Set(["Momentum ORB", "Konfluens"]);

export interface StrategyInfo {
  name:         string;
  description:  string;
  asset_class:  string;
  status:       StrategyStatusEnum;
  stats: {
    trades_today:    number;
    wins_today:      number;
    losses_today:    number;
    pnl_today:       number;
    open_positions:  number;
    orders_rejected: number;
    last_trade_time: string | null;
  };
  config: {
    max_loss_per_trade: number;
    max_daily_loss:     number;
    max_open_positions: number;
    max_position_size:  number;
    enabled:            boolean;
  };
}

export interface RiskStatus {
  daily_loss_limit:   number;
  total_pnl_today:    number;
  daily_limit_hit:    boolean;
  emergency_active:   boolean;
  total_exposure:     number;
  max_total_exposure: number;
  current_nlv:        number;
}

interface ManagerStatusMsg {
  type:       "manager_status";
  timestamp:  string;
  risk:       RiskStatus;
  strategies: StrategyInfo[];
}

// ── Hjælper (delt mellem handlere og view) ────────────────────
export function usd(v: number) {
  return v.toLocaleString("en-US", {
    style: "currency", currency: "USD", minimumFractionDigits: 2
  });
}

// ── Context-form ──────────────────────────────────────────────
interface LiveLogContextValue {
  log: string[];
  connected: boolean;
  status: AlgoStatus;
  message: string;
  totalPnl: number;
  positions: Position[];
  trades: Trade[];
  universe: string[];
  strategies: StrategyInfo[];
  risk: RiskStatus | null;
  startStrategy: (name: string) => void;
  stopStrategy: (name: string) => void;
  clearLog: () => void;
}

const LiveLogContext = createContext<LiveLogContextValue | null>(null);

export function useLiveLog(): LiveLogContextValue {
  const ctx = useContext(LiveLogContext);
  if (!ctx) throw new Error("useLiveLog skal bruges inden i <LiveLogProvider>");
  return ctx;
}

export function LiveLogProvider({ children }: { children: React.ReactNode }) {
  const [log, setLog] = useState<string[]>(() => {
    try { return JSON.parse(localStorage.getItem(LOG_KEY) || "[]"); }
    catch { return []; }
  });
  const [connected, setConnected] = useState(false);
  const [status, setStatus]       = useState<AlgoStatus>("idle");
  const [message, setMessage]     = useState<string>("Algoritmen er ikke startet");
  const [totalPnl, setTotalPnl]   = useState<number>(0);
  const [positions, setPositions] = useState<Position[]>([]);
  const [trades, setTrades]       = useState<Trade[]>([]);
  const [universe, setUniverse]   = useState<string[]>([]);
  const [strategies, setStrategies] = useState<StrategyInfo[]>([]);
  const [risk, setRisk]             = useState<RiskStatus | null>(null);

  const algoWsRef     = useRef<WebSocket | null>(null);
  const strategyWsRef = useRef<WebSocket | null>(null);
  const didInit       = useRef(false);   // StrictMode-guard mod dobbelt-connect

  function addLog(line: string) {
    setLog(prev => {
      const next = [...prev, line].slice(-LOG_MAX);
      try { localStorage.setItem(LOG_KEY, JSON.stringify(next)); } catch {}
      return next;
    });
  }
  function clearLog() {
    setLog([]);
    try { localStorage.removeItem(LOG_KEY); } catch {}
  }

  function handleManagerStatus(msg: ManagerStatusMsg) {
    setStrategies(msg.strategies.filter(s => !SKRINLAGTE_STRATEGIER.has(s.name)));
    setRisk(msg.risk);
  }

  function handleAlgoMessage(msg: AlgoMsg) {
    if (msg.type === "algo_status") {
      setStatus(msg.status);
      setMessage(msg.message);
      setTotalPnl(msg.total_pnl);
      const stratPrefix = msg.strategy === "Konfluens 2" ? "[KONF2] " :
                          msg.strategy === "Konfluens" ? "[KONF] " :
                          msg.strategy === "Momentum ORB" ? "[ORB] " :
                          msg.strategy === "Europa-reversion" ? "[REV] " : "";
      addLog(`[${msg.time}] ${stratPrefix}${msg.message}`);
      if (msg.status === "universe_ready" && msg.message.includes(":")) {
        const part = msg.message.split(":")[1]?.trim();
        if (part) setUniverse(part.split(", ").filter(Boolean));
      }
    } else if (msg.type === "algo_trade") {
      // Strategi-prefix til log (KONF eller ORB) hvis strategy er sat
      const stratPrefix = msg.strategy === "Konfluens 2" ? "[KONF2] " :
                          msg.strategy === "Konfluens" ? "[KONF] " :
                          msg.strategy === "Momentum ORB" ? "[ORB] " :
                          msg.strategy === "Europa-reversion" ? "[REV] " : "";
      // Konfluens-specifikt: vis bricks/score hvis tilgængelige
      const bricksTag = msg.bricks ? `  [${msg.bricks}]` : "";

      if (msg.action === "buy" || msg.action === "sell_short") {
        setPositions(prev => [...prev, {
          ticker: msg.ticker,
          entry_price: msg.price!,
          shares: msg.shares!,
          entry_time: msg.time!,
        }]);
        const verb = msg.action === "buy" ? "KØB " : "SHRT";
        addLog(`📈 ${stratPrefix}${verb} ${msg.shares} ${msg.ticker} @ ${usd(msg.price!)}${bricksTag}`);
      } else {
        setPositions(prev => prev.filter(p => p.ticker !== msg.ticker));
        setTrades(prev => [...prev, {
          ticker: msg.ticker, entry_price: msg.entry_price!, exit_price: msg.exit_price!,
          shares: msg.shares!, pnl: msg.pnl!, pnl_pct: msg.pnl_pct!,
          reason: msg.reason!, entry_time: msg.entry_time!, exit_time: msg.exit_time!,
        }]);
        const emoji = (msg.pnl || 0) >= 0 ? "✅" : "❌";
        const verb = msg.action === "buy_cover" ? "COVR" : "SÆLG";
        addLog(`📉 ${stratPrefix}${verb} ${msg.shares} ${msg.ticker} @ ${usd(msg.exit_price!)}  ${emoji} ${usd(msg.pnl!)} (${msg.reason})`);
      }
    }
  }

  useEffect(() => {
    if (didInit.current) return;   // undgå dobbelt-WS i StrictMode (dev)
    didInit.current = true;

    // ── Connect /ws/algo (detalje-strøm) ──────────────────────
    function connectAlgo() {
      const ws = new WebSocket("ws://127.0.0.1:8000/ws/algo");
      algoWsRef.current = ws;
      ws.onopen    = () => { setConnected(true);  addLog("✅ Forbundet til backend"); };
      ws.onmessage = (e) => { try { handleAlgoMessage(JSON.parse(e.data)); } catch {} };
      ws.onclose   = () => {
        setConnected(false);
        addLog("⚠ Backend ikke forbundet — genforbinder hvert 3. sek...");
        setTimeout(connectAlgo, 3000);
      };
      ws.onerror   = () => setConnected(false);
    }

    // ── Connect /ws/strategy (manager-status) ─────────────────
    function connectStrategy() {
      const ws = new WebSocket("ws://127.0.0.1:8000/ws/strategy");
      strategyWsRef.current = ws;
      ws.onmessage = (e) => {
        try {
          const msg = JSON.parse(e.data);
          if (msg.type === "manager_status") {
            handleManagerStatus(msg as ManagerStatusMsg);
          } else if (msg.type === "risk_status") {
            setRisk(msg as RiskStatus);
          }
        } catch {}
      };
      ws.onclose = () => setTimeout(connectStrategy, 3000);
    }

    connectAlgo();
    connectStrategy();

    return () => { algoWsRef.current?.close(); strategyWsRef.current?.close(); };
  }, []);

  function startStrategy(name: string) {
    if (strategyWsRef.current?.readyState === WebSocket.OPEN) {
      strategyWsRef.current.send(JSON.stringify({ command: "start", strategy: name }));
    }
  }
  function stopStrategy(name: string) {
    if (strategyWsRef.current?.readyState === WebSocket.OPEN) {
      strategyWsRef.current.send(JSON.stringify({ command: "stop", strategy: name }));
    }
  }

  return (
    <LiveLogContext.Provider value={{
      log, connected, status, message, totalPnl, positions, trades,
      universe, strategies, risk, startStrategy, stopStrategy, clearLog,
    }}>
      {children}
    </LiveLogContext.Provider>
  );
}
