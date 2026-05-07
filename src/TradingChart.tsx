import { useEffect, useRef } from "react";
import { createChart, IChartApi, ISeriesApi, CandlestickData, Time, CandlestickSeries } from "lightweight-charts";

interface PricePoint {
  price: number;
}

interface Props {
  ticker: string;
  timeframe: string;
  data: PricePoint[];
}

export function TradingChart({ ticker, timeframe, data }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);

  // Opret chart én gang
  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      layout: {
        background: { color: "#161b22" },
        textColor: "#8b949e",
      },
      grid: {
        vertLines: { color: "#21262d" },
        horzLines: { color: "#21262d" },
      },
      crosshair: {
        vertLine: { color: "#58a6ff", labelBackgroundColor: "#1f6feb" },
        horzLine: { color: "#58a6ff", labelBackgroundColor: "#1f6feb" },
      },
      rightPriceScale: {
        borderColor: "#30363d",
      },
      timeScale: {
        borderColor: "#30363d",
        timeVisible: true,
        secondsVisible: false,
      },
      width: containerRef.current.clientWidth,
      height: containerRef.current.clientHeight,
    });

    const series = chart.addSeries(CandlestickSeries, {
      upColor:         "#3fb950",
      downColor:       "#f85149",
      borderUpColor:   "#3fb950",
      borderDownColor: "#f85149",
      wickUpColor:     "#3fb950",
      wickDownColor:   "#f85149",
    });

    chartRef.current = chart;
    seriesRef.current = series;

    const observer = new ResizeObserver(() => {
      if (containerRef.current) {
        chart.applyOptions({
          width: containerRef.current.clientWidth,
          height: containerRef.current.clientHeight,
        });
      }
    });
    observer.observe(containerRef.current);

    return () => {
      observer.disconnect();
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, []);

  // Opdater data når nye priser kommer
  useEffect(() => {
    if (!seriesRef.current || data.length < 2) return;

    const now = Math.floor(Date.now() / 1000);

    const candles: CandlestickData[] = data.map((point, i) => {
      const prev = data[i - 1];
      const open  = prev ? prev.price : point.price;
      const close = point.price;
      const high  = Math.max(open, close) * (1 + Math.random() * 0.001);
      const low   = Math.min(open, close) * (1 - Math.random() * 0.001);
      return {
        time: (now - (data.length - i) * 5) as Time,
        open,
        high,
        low,
        close,
      };
    });

    try {
      seriesRef.current.setData(candles);
      chartRef.current?.timeScale().fitContent();
    } catch (e) {
      // Ignorer fejl
    }

  }, [data]);

  const isUp = data.length > 1 && data[data.length - 1].price >= data[0].price;
  const currentPrice = data.length > 0 ? data[data.length - 1].price : 0;
  const firstPrice = data.length > 0 ? data[0].price : 0;
  const changePct = firstPrice > 0 ? ((currentPrice - firstPrice) / firstPrice * 100) : 0;

  return (
    <div className="chart-panel">
      <div className="chart-header">
        <span className="chart-ticker">{ticker}</span>
        <span className="chart-timeframe">{timeframe}</span>
        <span className={isUp ? "positive" : "negative"}>${currentPrice.toFixed(2)}</span>
        <span className={isUp ? "positive" : "negative"}>{changePct >= 0 ? "+" : ""}{changePct.toFixed(2)}%</span>
      </div>
      <div ref={containerRef} className="chart-canvas" />
    </div>
  );
}