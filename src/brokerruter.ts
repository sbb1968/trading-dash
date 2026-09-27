/**
 * brokerruter.ts — hvilken broker en watchlist handler igennem
 * ═══════════════════════════════════════════════════════════════════════════
 * Reglen er FASTLÅST efter aftale 27-09-2026: Iben skal ikke tage stilling til
 * broker pr. ordre.
 *
 *     Watchlist Futures  →  NinjaTrader (NT8)
 *     Watchlist Stocks   →  IBKR
 *
 * ⚠ DERFOR ET EGET MODUL. Reglen skal læses ét sted, ikke udledes af et `if`
 * i en knap-handler. Den bruges af både panelet (som ved hvilken liste klikket
 * kom fra) og WebSocket-laget (som skriver den i beskeden) — og de to filer
 * kan ikke importere fra hinanden.
 *
 * ⚠ OG DERFOR INGEN DEFAULT. Der er ingen "hvis vi ikke ved det"-gren, hverken
 * her eller i backenden. MES kan handles hos BEGGE brokere, så en fejlrutet
 * futures-ordre er ikke en fejlmeddelelse — det er en rigtig position på den
 * forkerte konto, til $2.863 initial margin i stedet for $50.
 */
export type Broker = "IBKR" | "NT8";
export type WatchVariant = "futures" | "stocks";

export const BROKER_FOR_LISTE: Record<WatchVariant, Broker> = {
  futures: "NT8",
  stocks:  "IBKR",
};

/** Hvad brugeren skal kalde brokeren. Vises i bekræftelses-pop-up'en. */
export const BROKER_NAVN: Record<Broker, string> = {
  IBKR: "IBKR",
  NT8:  "NinjaTrader",
};
