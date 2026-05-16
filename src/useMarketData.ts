import { useState, useEffect, useRef, useCallback } from "react";
import ReconnectingWebSocket from "reconnecting-websocket";

const WS_URL = "ws://127.0.0.1:8000/ws";

const globalSoundEnabled = { value: true };

export interface StockData {
  ticker:         string;
  price:          number;
  prev_price:     number;
  change_percent: number;
  volume:         number;
  rel_vol_daily:  number;
  rel_vol_5min:   number;
  gap_percent:    number;
  float:          string;
  news:           boolean;
  timestamp:      string;
  bid?:           number;
  ask?:           number;
  high?:          number;
  low?:           number;
  open?:          number;
  source?:        string;
}

export interface AlertData {
  id:             number;
  ticker:         string;
  price:          number;
  change_percent: number;
  direction:      "up" | "down";
  message:        string;
  time:           string;
  timestamp:      string;
}

export interface NewsData {
  id:        number;
  ticker:    string;
  headline:  string;
  sentiment: "bullish" | "bearish" | "neutral";
  source:    string;
  time:      string;
  timestamp: string;
  isNew?:    boolean;
}

export interface Position {
  ticker:             string;
  shares:             number;
  avg_price:          number;
  entry_price:        number;
  current_price:      number;
  position_value:     number;
  unrealized_pnl:     number;
  unrealized_pnl_pct: number;
  opened_at:          string;
}

export interface Trade {
  ticker:      string;
  action:      string;
  shares:      number;
  entry_price: number;
  exit_price:  number;
  proceeds:    number;
  cost_basis:  number;
  pnl:         number;
  pnl_pct:     number;
  opened_at:   string;
  closed_at:   string;
}

export interface Portfolio {
  balance:              number;
  starting_balance:     number;
  total_position_value: number;
  total_equity:         number;
  total_pnl:            number;
  total_pnl_pct:        number;
  unrealized_pnl:       number;
  realized_pnl:         number;
  positions:            Position[];
  trades:               Trade[];
  num_positions:        number;
  num_trades:           number;
}

export type ConnectionStatus = "connecting" | "connected" | "disconnected";

const EMPTY_PORTFOLIO: Portfolio = {
  balance: 100000, starting_balance: 100000,
  total_position_value: 0, total_equity: 100000,
  total_pnl: 0, total_pnl_pct: 0,
  unrealized_pnl: 0, realized_pnl: 0,
  positions: [], trades: [],
  num_positions: 0, num_trades: 0,
};

// ── Lyd ───────────────────────────────────────────────────────
const lastSoundTime    = { value: 0 };
const tickerCooldowns: Record<string, number> = {};

function playAlertSound(direction: "up" | "down", ticker: string) {
  const soundEnabled = localStorage.getItem("soundEnabled");
  if (soundEnabled === "false") return;
  const now = Date.now();
  if (now - lastSoundTime.value < 1000) return;
  if (tickerCooldowns[ticker] && now - tickerCooldowns[ticker] < 30000) return;
  lastSoundTime.value      = now;
  tickerCooldowns[ticker]  = now;
  try {
    const ctx        = new (window.AudioContext || (window as any).webkitAudioContext)();
    const oscillator = ctx.createOscillator();
    const gainNode   = ctx.createGain();
    oscillator.connect(gainNode);
    gainNode.connect(ctx.destination);
    if (direction === "up") {
      oscillator.frequency.setValueAtTime(600, ctx.currentTime);
      oscillator.frequency.setValueAtTime(900, ctx.currentTime + 0.1);
    } else {
      oscillator.frequency.setValueAtTime(500, ctx.currentTime);
      oscillator.frequency.setValueAtTime(300, ctx.currentTime + 0.1);
    }
    oscillator.type = "sine";
    gainNode.gain.setValueAtTime(0.3, ctx.currentTime);
    gainNode.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.3);
    oscillator.start(ctx.currentTime);
    oscillator.stop(ctx.currentTime + 0.3);
  } catch {}
}

// ── IBKR ordre-resultat (fra manuel watchlist-handel) ─────────
export interface IbkrOrderResult {
  type:     "ibkr_order_result";
  success:  boolean;
  ticker:   string;
  action:   "BUY" | "SELL";
  shares:   number;
  status?:  string;
  filled?:  number;
  avg_fill?: number;
  error?:   string;
}

// ── Hook ──────────────────────────────────────────────────────
export function useMarketData(alertThreshold: number, soundEnabled: boolean = true) {
  const [stocks,    setStocks]    = useState<Map<string, StockData>>(new Map());
  const [alerts,    setAlerts]    = useState<AlertData[]>([]);
  const [news,      setNews]      = useState<NewsData[]>([]);
  const [portfolio, setPortfolio] = useState<Portfolio>(EMPTY_PORTFOLIO);
  const [status,    setStatus]    = useState<ConnectionStatus>("connecting");
  const [lastOrderResult, setLastOrderResult] = useState<IbkrOrderResult | null>(null);

  const wsRef         = useRef<ReconnectingWebSocket | null>(null);
  const thresholdRef  = useRef(alertThreshold);

  useEffect(() => {
    globalSoundEnabled.value = soundEnabled;
  }, [soundEnabled]);

  useEffect(() => {
    thresholdRef.current = alertThreshold;
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "set_threshold", value: alertThreshold }));
    }
  }, [alertThreshold]);

  useEffect(() => {
    const ws = new ReconnectingWebSocket(WS_URL, [], {
      maxRetries:                  Infinity,
      reconnectionDelayGrowFactor: 1.3,
      minReconnectionDelay:        1000,
      maxReconnectionDelay:        10000,
    });

    wsRef.current = ws;

    ws.onopen = () => {
      setStatus("connected");
      ws.send(JSON.stringify({ type: "set_threshold", value: thresholdRef.current }));
    };

    ws.onclose = () => setStatus("disconnected");
    ws.onerror = () => setStatus("disconnected");

    ws.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data);

        if (message.type === "ticks") {
          setStocks(prev => {
            const next = new Map(prev);
            for (const tick of message.data as StockData[]) {
              next.set(tick.ticker, tick);
            }
            return next;
          });

        } else if (message.type === "alerts") {
          const newAlerts = message.data as AlertData[];
          newAlerts.forEach(a => {
            if (globalSoundEnabled.value) playAlertSound(a.direction, a.ticker);
          });
          setAlerts(prev => [...newAlerts, ...prev].slice(0, 50));

        } else if (message.type === "news") {
          const item = { ...message.data as NewsData, isNew: true };
          setNews(prev => [item, ...prev].slice(0, 50));
          setTimeout(() => {
            setNews(prev => prev.map(n => n.id === item.id ? { ...n, isNew: false } : n));
          }, 3000);

        } else if (message.type === "portfolio") {
          setPortfolio(message.data as Portfolio);

        } else if (message.type === "ibkr_order_result") {
          // Manuel watchlist-ordre — gem resultat så UI kan vise toast
          setLastOrderResult(message as IbkrOrderResult);
        }

      } catch (e) {
        console.error("[WS] Parse fejl:", e);
      }
    };

    return () => {
      ws.close();
    };
  }, []);

  const sendMessage = useCallback((message: object) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(message));
    }
  }, []);

  const buyStock = useCallback((ticker: string, shares: number, price: number) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "buy", ticker, shares, price }));
    }
  }, []);

  const sellStock = useCallback((ticker: string, shares: number, price: number) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "sell", ticker, shares, price }));
    }
  }, []);

  const resetPortfolio = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "reset_portfolio" }));
    }
  }, []);

  // ── IBKR direkte ordrer (fra watchlist-rækker) ──────────────
  const ibkrBuy = useCallback((ticker: string, shares: number) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "ibkr_buy", ticker, shares }));
    }
  }, []);

  const ibkrSell = useCallback((ticker: string, shares: number) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "ibkr_sell", ticker, shares }));
    }
  }, []);

  const clearLastOrderResult = useCallback(() => setLastOrderResult(null), []);

  const stocksArray = Array.from(stocks.values());
  return {
    stocksArray, alerts, news, portfolio, status, sendMessage,
    buyStock, sellStock, resetPortfolio,
    ibkrBuy, ibkrSell, lastOrderResult, clearLastOrderResult,
  };
}
