// ── Futures i frontenden ─────────────────────────────────────────────────────
// Spejler backend/futures_katalog.py, som er ÉN sandhedskilde for symbol, boers,
// multiplikator og TradingView-symbol.
//
// Hvorfor en spejling og ikke et fetch: et fetch der fejler paa en handelsdag ville
// spaerre futures-handel, og listen af symboler aendrer sig ikke midt i en session.
// Prisen er at den kan glemmes. Betalingen er backend/test_futures_katalog.py, som
// laeser DENNE fil og FEJLER hvis den ikke stemmer med kataloget.
// Tilfoej derfor i kataloget foerst, koer testen, ret her.
//
// Filen ligger for sig, fordi baade App.tsx og TradingViewWidget.tsx skal bruge den
// — og App.tsx importerer widget'en, saa viden kan ikke bo i App.tsx uden at lave
// en cirkulaer import.
//
// Kontrakt-MAANEDEN staar der bevidst intet om: den vaelges af backendens
// qualify_future, som tager den mest handlede kontrakt — ikke bare den naermeste
// ikke-udloebne. Man skriver altid det rene symbol: "MES", aldrig "MESU6".

export const FUTURES_SYMBOLS = new Set(["MES", "M2K", "MNQ"]);

// TradingViews symbol for det samme instrument. MAA IKKE udelades:
// widget'en faar ellers det bare "MES", og TradingView vaelger selv boers —
// den finder GETTEX:MES (Mitsubishi Estate Company, et japansk ejendomsselskab)
// foer den finder CME. Charten viste da et helt andet papir under den rigtige titel.
//
// "1!" er TradingViews kontinuerte FRONT-maaned; "2!" ville vaere naeste. Front er
// det rigtige til chart — det er dér likviditeten og prisdannelsen er.
const TV_SYMBOL: Record<string, string> = {
  MES: "CME_MINI:MES1!",
  M2K: "CME_MINI:M2K1!",
  MNQ: "CME_MINI:MNQ1!",
};

export function isFutureSymbol(ticker: string): boolean {
  return FUTURES_SYMBOLS.has(norm(ticker));
}

/** Symbolet TradingView skal bruge. Aktier sendes videre uaendret (TradingView
 *  finder selv den rigtige US-boers for dem — det er kun futures der kolliderer
 *  med et andet papir paa samme bogstaver). */
export function tvSymbol(ticker: string): string {
  const t = norm(ticker);
  return TV_SYMBOL[t] ?? t;
}

function norm(ticker: string): string {
  return (ticker || "").toUpperCase().trim();
}
