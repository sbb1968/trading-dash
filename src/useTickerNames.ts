import { useState, useEffect } from "react";

const API = "http://127.0.0.1:8000";

/**
 * Slår firmanavne op for en liste af tickers via /ticker/info (TradingView-primær,
 * robust — samme kilde som Handels-charten). Returnerer { TICKER: navn }.
 *
 * Henter kun de tickers der endnu ikke er kendt; resten caches i state. Tom streng
 * hvis navnet ikke kan findes. Bruges af top-15-vinduerne så firmanavnet er korrekt
 * uanset hvad der (evt. forkert) er bagt ind i data-rækkerne.
 */
export function useTickerNames(tickers: string[]): Record<string, string> {
  const [names, setNames] = useState<Record<string, string>>({});
  const key = [...new Set(tickers.filter(Boolean))].sort().join(",");
  useEffect(() => {
    const missing = [...new Set(tickers.filter(Boolean))].filter((s) => !(s in names));
    if (missing.length === 0) return;
    let alive = true;
    (async () => {
      const res = await Promise.allSettled(
        missing.map(async (sym) => {
          const r = await fetch(`${API}/ticker/info?ticker=${encodeURIComponent(sym)}`);
          if (!r.ok) throw new Error();
          return ((await r.json()).name || "") as string;
        })
      );
      if (!alive) return;
      setNames((prev) => {
        const next = { ...prev };
        res.forEach((r, i) => {
          next[missing[i]] = r.status === "fulfilled" ? r.value : "";
        });
        return next;
      });
    })();
    return () => {
      alive = false;
    };
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps
  return names;
}
