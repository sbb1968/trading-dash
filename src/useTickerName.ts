import { useState, useEffect } from "react";

// ── Globalt in-memory cache, delt mellem alle komponenter ──────
// Initialiseres fra localStorage så navne overlever side-reload
const STORAGE_KEY = "td_ticker_names";

function loadCache(): Record<string, string> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

function saveCache(cache: Record<string, string>) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(cache));
  } catch {
    // Quota exceeded — drop oldest entries
    const entries = Object.entries(cache);
    if (entries.length > 100) {
      const trimmed = Object.fromEntries(entries.slice(-100));
      localStorage.setItem(STORAGE_KEY, JSON.stringify(trimmed));
    }
  }
}

const globalCache: Record<string, string> = loadCache();

// Tickers vi har sendt request for (men endnu ikke fået svar) — undgår dobbelt-fetch
const pendingTickers = new Set<string>();

// Subscribere: hver hook der bruger en ticker registrerer en callback
// så vi kan opdatere alle komponenter når navnet ankommer
const subscribers = new Map<string, Set<() => void>>();

function notify(ticker: string) {
  const subs = subscribers.get(ticker);
  if (subs) subs.forEach(cb => cb());
}

async function fetchName(ticker: string) {
  if (pendingTickers.has(ticker) || ticker in globalCache) return;
  pendingTickers.add(ticker);

  try {
    const resp = await fetch(`http://127.0.0.1:8000/ticker/info?ticker=${encodeURIComponent(ticker)}`);
    if (resp.ok) {
      const data = await resp.json();
      globalCache[ticker] = data.name || "";
      saveCache(globalCache);
      notify(ticker);
    } else {
      globalCache[ticker] = "";
      notify(ticker);
    }
  } catch {
    // Backend nede — ikke fatal, prøv igen næste gang komponenten mounter
  } finally {
    pendingTickers.delete(ticker);
  }
}

/**
 * Hook der returnerer firma-navnet for en ticker.
 * Returnerer "" mens den henter, og det smart-forkortede navn når det ankommer.
 * Returnerer "(ukendt)" hvis tickeren ikke kan findes hos Finnhub.
 */
export function useTickerName(ticker: string | undefined | null): string {
  const [, setTick] = useState(0);

  useEffect(() => {
    if (!ticker) return;
    const t = ticker.toUpperCase();

    // Registrer subscriber så vi får besked når navnet ankommer
    let subs = subscribers.get(t);
    if (!subs) {
      subs = new Set();
      subscribers.set(t, subs);
    }
    const cb = () => setTick(x => x + 1);
    subs.add(cb);

    // Start fetch hvis vi ikke har det allerede
    if (!(t in globalCache)) {
      fetchName(t);
    }

    return () => {
      subs!.delete(cb);
    };
  }, [ticker]);

  if (!ticker) return "";
  const t = ticker.toUpperCase();
  const name = globalCache[t];
  if (name === undefined) return "";       // henter stadig
  if (name === "")        return "(ukendt)";
  return name;
}

/**
 * Batch-variant: firmanavne for en LISTE af tickers. Deler præcis samme globale
 * cache + kilde (/ticker/info) som useTickerName, så navne er NØJAGTIGT ens uanset
 * hvilket vindue de vises i. Returnerer { TICKER: navn } — tom streng mens den
 * henter eller hvis navnet ikke kan findes (kalderen vælger selv fallback).
 * Bruges af liste-vinduer (top-15 osv.) der mapper rækker inline.
 */
export function useTickerNames(tickers: (string | undefined | null)[]): Record<string, string> {
  const [, setTick] = useState(0);
  const upper = [...new Set(tickers.filter(Boolean).map((t) => String(t).toUpperCase()))];
  const key = [...upper].sort().join(",");

  useEffect(() => {
    const cb = () => setTick((x) => x + 1);
    const cleanups: Array<() => void> = [];
    for (const t of upper) {
      let subs = subscribers.get(t);
      if (!subs) {
        subs = new Set();
        subscribers.set(t, subs);
      }
      subs.add(cb);
      cleanups.push(() => subs!.delete(cb));
      if (!(t in globalCache)) fetchName(t);
    }
    return () => cleanups.forEach((fn) => fn());
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps

  const out: Record<string, string> = {};
  for (const t of upper) {
    const n = globalCache[t];
    out[t] = n && n !== "" ? n : ""; // "" mens den henter / ukendt → kalderen falder tilbage
  }
  return out;
}

/**
 * Hjælper til at formatere "TICKER · Firmanavn".
 * Hvis navnet endnu ikke er hentet returneres bare ticker'en.
 */
export function formatTickerWithName(ticker: string, name: string): string {
  if (!name) return ticker;
  return `${ticker} · ${name}`;
}
