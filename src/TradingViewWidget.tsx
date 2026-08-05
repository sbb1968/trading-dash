import { useEffect, useRef } from "react";
import { tvSymbol } from "./futures";

interface Props {
  ticker: string;
  timeframe: string;
}

const TIMEFRAME_MAP: Record<string, string> = {
  // Sekunder
  "1 sek":  "1S",
  "5 sek":  "5S",
  "10 sek": "10S",
  "15 sek": "15S",
  "30 sek": "30S",
  // Minutter
  "1 min":  "1",
  "2 min":  "2",
  "3 min":  "3",
  "5 min":  "5",
  "10 min": "10",
  "15 min": "15",
  "30 min": "30",
  // Timer
  "1 time": "60",
  "2 time": "120",
  "4 time": "240",
  // Dag+
  "Daily":  "D",
  "Weekly": "W",
};

export function TradingViewWidget({ ticker, timeframe }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  function buildWidget(w: number, h: number) {
    if (!containerRef.current) return;
    if (w === 0 || h === 0) return;

    containerRef.current.innerHTML = "";

    const script = document.createElement("script");
    script.src = "https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js";
    script.type = "text/javascript";
    script.async = true;
    script.innerHTML = JSON.stringify({
      autosize: false,
      width: w,
      height: h,
      // ⚠ IKKE det bare `ticker`. TradingView vaelger selv boers for et
      // ukvalificeret symbol, og for "MES" finder den GETTEX:MES (Mitsubishi
      // Estate) foer CME. Charten viste da et japansk ejendomsselskab under
      // titlen "MES · MICRO E-MINI S&P 500". tvSymbol() giver CME_MINI:MES1!.
      symbol: tvSymbol(ticker),
      interval: TIMEFRAME_MAP[timeframe] || "5",
      timezone: "America/New_York",
      theme: "dark",
      style: "1",
      locale: "en",
      allow_symbol_change: false,
      calendar: false,
      support_host: "https://www.tradingview.com",
      backgroundColor: "#161b22",
      gridColor: "#21262d",
    });

    containerRef.current.appendChild(script);
  }

  useEffect(() => {
    if (!containerRef.current) return;

    const observer = new ResizeObserver(entries => {
      for (const entry of entries) {
        const w = Math.floor(entry.contentRect.width);
        const h = Math.floor(entry.contentRect.height);
        if (w === 0 || h === 0) continue;
        if (timerRef.current) clearTimeout(timerRef.current);
        timerRef.current = setTimeout(() => {
          buildWidget(w, h);
        }, 300);
      }
    });

    observer.observe(containerRef.current);

    setTimeout(() => {
      if (containerRef.current) {
        buildWidget(
          containerRef.current.offsetWidth,
          containerRef.current.offsetHeight
        );
      }
    }, 100);

    return () => {
      observer.disconnect();
      if (timerRef.current) clearTimeout(timerRef.current);
      if (containerRef.current) containerRef.current.innerHTML = "";
    };
  }, [ticker, timeframe]);

  return (
    <div
      ref={containerRef}
      style={{ flex: 1, width: "100%", height: "100%", minHeight: 0, overflow: "hidden" }}
    />
  );
}