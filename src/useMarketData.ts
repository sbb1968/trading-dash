import { useState, useEffect, useRef, useCallback } from "react";
import ReconnectingWebSocket from "reconnecting-websocket";

const WS_URL = "ws://127.0.0.1:8000/ws";

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

export type ConnectionStatus = "connecting" | "connected" | "disconnected";

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
export function useMarketData() {
  const [stocks,    setStocks]    = useState<Map<string, StockData>>(new Map());
  const [news,      setNews]      = useState<NewsData[]>([]);
  const [status,    setStatus]    = useState<ConnectionStatus>("connecting");
  const [lastOrderResult, setLastOrderResult] = useState<IbkrOrderResult | null>(null);

  const wsRef         = useRef<ReconnectingWebSocket | null>(null);

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

        } else if (message.type === "news") {
          const item = { ...message.data as NewsData, isNew: true };
          setNews(prev => [item, ...prev].slice(0, 50));
          setTimeout(() => {
            setNews(prev => prev.map(n => n.id === item.id ? { ...n, isNew: false } : n));
          }, 3000);

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

  // Trin 3: bed backenden abonnere på watchlist-tickers' live-kurs (så "Aktuel pris"
  // virker for enhver ticker, ikke kun feed-universet).
  const subscribeTickers = useCallback((tickers: string[]) => {
    if (wsRef.current?.readyState === WebSocket.OPEN && tickers.length) {
      wsRef.current.send(JSON.stringify({ type: "watchlist_subscribe", tickers }));
    }
  }, []);

  const stocksArray = Array.from(stocks.values());
  return {
    stocksArray, news, status, sendMessage,
    ibkrBuy, ibkrSell, lastOrderResult, clearLastOrderResult, subscribeTickers,
  };
}
