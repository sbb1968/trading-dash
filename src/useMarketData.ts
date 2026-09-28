import { useState, useEffect, useRef, useCallback } from "react";
import ReconnectingWebSocket from "reconnecting-websocket";
import type { Broker } from "./brokerruter";

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
  halted?:        boolean;
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
  // ⚠ Hvilken konto og hvilken forbindelse ordren FAKTISK gik igennem. Vises i
  // watchlisten, saa en ordre gennem den forkerte backend bliver synlig med det
  // samme — ikke fundet i journalen bagefter, hvis nogen kigger.
  konto?:       string | null;
  forbindelse?: "ordre" | "delt";
  port?:        number | null;
  order_ref?:   string;
}

// ── Hook ──────────────────────────────────────────────────────
export function useMarketData() {
  const [stocks,    setStocks]    = useState<Map<string, StockData>>(new Map());
  const [news,      setNews]      = useState<NewsData[]>([]);
  const [status,    setStatus]    = useState<ConnectionStatus>("connecting");
  const [lastOrderResult, setLastOrderResult] = useState<IbkrOrderResult | null>(null);

  // ⚠ ER DER EN ORDRE UNDERVEJS? Det dyrest lærte felt i denne fil.
  //
  // En NT8-ordre er 5-11 sekunder undervejs (ATI-oplæg + OIF-filen + fyldningen),
  // og indtil 28-09 skete der INTET synligt imens. Den der klikker, konkluderer
  // rimeligt nok at klikket ikke gik igennem, og klikker igen.
  //
  // Målt 28-09 på Sim101: ét køb, FEM salg, nettoposition -4 MES. Ingen af de
  // fire ekstra var ønsket. På Sim101 kostede det ingenting; på en live-konto
  // er fire utilsigtede MES-kontrakter til markedspris en anden historie.
  //
  // ⚠ OG HASTIGHED ER IKKE LØSNINGEN. Uanset hvor hurtig vejen bliver, er der
  // et vindue hvor svaret ikke er kommet endnu — og et vindue man kan klikke i,
  // bliver klikket i. Derfor spærres knappen, og der vises at der arbejdes.
  // ⚠ Den baerer HVAD der er bestilt, ikke kun AT noget er undervejs. En
  // kvittering der ikke kan naevne ordren, beroliger ikke nogen.
  const [ordreUndervejs, setOrdreUndervejs] =
    useState<{ action: "BUY" | "SELL"; ticker: string; shares: number;
               broker: Broker; sendt: number } | null>(null);
  const ordreTimerRef = useRef<number | null>(null);

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
          // Svaret er kommet — luk op igen.
          setOrdreUndervejs(null);
          if (ordreTimerRef.current) {
            clearTimeout(ordreTimerRef.current);
            ordreTimerRef.current = null;
          }
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


  // ── Manuelle ordrer fra watchlist-rækker ────────────────────
  // ⚠ BROKEREN ER ET KRAV, IKKE EN INDSTILLING. Den udledes af HVILKEN liste
  // klikket kom fra (Futures -> NT8, Stocks -> IBKR) og kan ikke vælges i
  // brugerfladen. Iben skal ikke tage stilling til det pr. ordre.
  //
  // ⚠ OG DEN HAR INGEN DEFAULT — heller ikke i backenden. En manglende broker
  // er en gammel exe, og så skal ordren SPÆRRES med den besked, ikke rutes et
  // sted hen ingen har valgt. MES findes hos BEGGE brokere: en fejlrutet
  // futures-ordre ville lande på IBKR til $2.863 initial margin i stedet for
  // NT8's $50, og den slags må ikke kunne ske tavst. `git pull` henter ikke
  // app.exe, så netop dén skævhed er sket før.
  const sendOrdre = useCallback(
    (action: "BUY" | "SELL", ticker: string, shares: number, broker: Broker) => {
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        setOrdreUndervejs({ action, ticker, shares, broker, sendt: Date.now() });
        // ⚠ EN SPÆRRING DER KAN HÆNGE, ER VÆRRE END INGEN. Kommer svaret
        // aldrig (tabt WebSocket, backend genstartet midt i), må knappen ikke
        // være død for altid. 60 s er rigeligt over den målte værste vej og
        // kort nok til at man ikke giver op.
        if (ordreTimerRef.current) clearTimeout(ordreTimerRef.current);
        ordreTimerRef.current = window.setTimeout(() => {
          setOrdreUndervejs(null);
          ordreTimerRef.current = null;
          setLastOrderResult({
            type: "ibkr_order_result", success: false, ticker, action, shares,
            error: "Intet svar fra backenden inden for 60 sekunder. ⚠ ORDREN "
                 + "KAN VÆRE GÅET IGENNEM ALLIGEVEL — tjek i " + broker
                 + " før du prøver igen.",
          } as IbkrOrderResult);
        }, 60000);
        wsRef.current.send(JSON.stringify({
          type: action === "BUY" ? "ordre_buy" : "ordre_sell",
          ticker, shares, broker,
        }));
      }
    }, []);

  const ibkrBuy  = useCallback((ticker: string, shares: number, broker: Broker) =>
    sendOrdre("BUY", ticker, shares, broker), [sendOrdre]);
  const ibkrSell = useCallback((ticker: string, shares: number, broker: Broker) =>
    sendOrdre("SELL", ticker, shares, broker), [sendOrdre]);

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
    ordreUndervejs,
  };
}
