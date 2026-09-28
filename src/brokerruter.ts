/**
 * brokerruter.ts — hvilken broker en watchlist handler igennem
 * ═══════════════════════════════════════════════════════════════════════════
 * Reglen blev aftalt 27-09-2026: Iben skal ikke tage stilling til broker pr.
 * ordre. Den hensigt er uændret — men hvor svaret kommer fra, er det ikke.
 *
 *     Watchlist Stocks   →  IBKR        (altid; NT8 handler ikke aktier)
 *     Watchlist Futures   →  maskinens opsætning, via GET /ordre/rute
 *
 * ⚠ HVORFOR FUTURES IKKE ER EN KONSTANT MERE.
 * Den stod her som `futures: "NT8"`, hårdkodet. Det er rigtigt som slutmål og
 * forkert som overgang: Iben handler i dag MES på IBKR (DUQ441063) gennem
 * netop den knap, og hendes maskine har hverken NinjaTrader eller en
 * nt_forbindelse-blok i account.yaml. Med konstanten ville hendes første klik
 * svare "NinjaTrader-ordrevejen er spærret" — og hun kunne ikke handle.
 *
 * ⚠ ARMERINGEN ER RUTEN. Har maskinen en nt_forbindelse-blok, HAR den en
 * NinjaTrader-vej og bruger den til futures. Har den ikke, går futures til
 * IBKR som hidtil. De to kan ikke komme i utakt, fordi det er samme faktum.
 * Backendens opstartsbanner skriver hvad der gælder.
 *
 * ⚠ OG DER ER STADIG INGEN DEFAULT. Kan ruten ikke hentes, spærres knapperne
 * med besked — der gættes ikke. MES kan handles hos BEGGE brokere, så en
 * fejlrutet futures-ordre er ikke en fejlmeddelelse, men en rigtig position på
 * den forkerte konto, til $2.863 initial margin i stedet for $50.
 */
export type Broker = "IBKR" | "NT8";
export type WatchVariant = "futures" | "stocks";

/** Svaret fra GET /ordre/rute. `null` = vi ved det ikke endnu. */
export interface OrdreRute {
  stocks:     Broker;
  futures:    Broker;
  nt_armeret: boolean;
  nt_konto:   string;
  nt_live:    boolean;
  instans:    string;
}

/** Hvad brugeren skal kalde brokeren. Vises i bekræftelses-pop-up'en. */
export const BROKER_NAVN: Record<Broker, string> = {
  IBKR: "IBKR",
  NT8:  "NinjaTrader",
};

/** Brokeren for én liste, eller null hvis ruten ikke er kendt endnu. */
export function brokerFor(variant: WatchVariant, rute: OrdreRute | null): Broker | null {
  if (!rute) return null;
  const b = variant === "futures" ? rute.futures : rute.stocks;
  return b === "NT8" || b === "IBKR" ? b : null;
}
