import { useState, useEffect, useRef } from "react";

// ── Typer ─────────────────────────────────────────────────────
// ⚠ TO VISNINGER AF DE SAMME HANDLER, IKKE ÉN DER ERSTATTER DEN ANDEN.
// "Ordrer" viser hver ordre for sig med tidsstempel — dét er sporet man følger
// når man vil vide præcis hvornår noget skete. "Handler" viser den parrede
// transaktion med P&L, som er det man vil se når man gør dagen op.
// De besvarer forskellige spørgsmål, så begge bliver.
type Fane = "ordrer" | "handler";

interface OrderEntry {
  order_id:     number;
  source:       string;
  ticker:       string;
  action:       "BUY" | "SELL";
  shares:       number;
  order_type:   string;
  limit_price:  number | null;
  placed_at:    string;
  status:       string;
  filled:       number;
  remaining:    number;
  avg_fill:     number;
  status_group: "filled" | "open" | "cancelled" | "unknown";
  /** ⚠ Hvorfor status ikke kan fastslaas. Saettes af backenden naar en ordre
   *  aldrig blev bekraeftet af en live-aflaesning — fx fordi den blev lagt paa
   *  en anden konto end den forbindelsen styrer. Uden den ville "Status
   *  ukendt" se ud som en fejl i stedet for som en graense for hvad vi kan vide. */
  note?: string | null;

  // ── Exit-ordrer (backend: orders_tracker + /orders/list) ──────────────
  /** LONG | SHORT | EXIT | STOP | TARGET | TRAIL | UKENDT. Gamle raekker har
   *  den ikke, og viser saa `order_type` som hidtil. */
  ordre_type?:      string | null;
  parent_order_id?: string | null;
  oco_id?:          string | null;
  /** STOP/TARGET: Ibens pris. TRAIL: den aktuelle stop. */
  trigger_pris?:    number | null;
  trail_hoejeste?:  number | null;
  trail_afstand?:   number | null;
  exit_aarsag?:     string | null;
  /** ⚠ KUN PAA PARENT-RAEKKER, og KUN fra backenden. Se noten ved knapperne. */
  exit_knapper?:    Record<string, "ingen" | "afventer" | "aktiv">;
  exit_mulig?:      boolean;
  /** Kort besked fra overvaagningsloekken, fx "Ingen kurs — stoppen følger
   *  ikke med". ⚠ En trailing stop der er holdt op med at foelge markedet,
   *  ser ud praecis som en der foelger med. */
  advarsel?:        string | null;
  /** ⚠ Raekken blev ikke slettet — den blev FEJET MED af OCO-kaskaden og lagt
   *  igen som denne ordre. "Annulleret" alene ville se ud som om beskyttelsen
   *  forsvandt. */
  genlagt_som?:     string | null;
  /** Realiseret P/L for den handel denne ordre LUKKEDE. Kun paa EXIT-
   *  raekker. ⚠ Tallet kommer fra `trades` — det er DET SAMME som
   *  forensikken regnede, ikke et nyt regnestykke der kan drive fra det. */
  pnl?:             number | null;
  trade_id?:        string | null;
}

const EXIT_TYPER = ["STOP", "TARGET", "TRAIL"] as const;
type ExitType = (typeof EXIT_TYPER)[number];

const EXIT_NAVN: Record<ExitType, string> = {
  STOP: "Stop loss",
  TARGET: "Target profit",
  TRAIL: "Trailing stop",
};

/** ⚠ Navnene står KUN i EXIT_TYPER her og i TYPER i exit_ordrer.py. Alt
 *  andet sammenligner mod dem, så en omdøbning er to steder.
 *
 *  "PLOSS" var den allerførste stavemåde og beholdes som alias. ⚠ "SLOSS" og
 *  "TPROF" (06-10) er bevidst IKKE aliaser: de rækker er alle annullerede og
 *  historiske. De vises stadig med deres egen tekst, men tæller ikke som
 *  exit-typer. Besluttet 07-10. */
function exitType(t: string | null | undefined): ExitType | null {
  const v = (t || "").toUpperCase();
  const n = v === "PLOSS" ? "STOP" : v;
  return (EXIT_TYPER as readonly string[]).includes(n) ? (n as ExitType) : null;
}

/** Én handel = entry + exit på samme linje. Fra `trades`-tabellen, ikke fra
 *  ordre-trackeren — det er journalens parrede rækker, med P&L regnet med
 *  instrumentets multiplikator (MES: $5 pr. point). */
interface HandelRow {
  trade_id:       string;
  symbol:         string;
  side:           "long" | "short";
  shares:         number;
  entry_time_utc: string;
  entry_price:    number;
  exit_time_utc:  string | null;
  exit_price:     number | null;
  exit_reason:    string | null;
  pnl:            number | null;
  pnl_pct:        number | null;
  ibkr_account:   string | null;
  payload?:       { broker?: string; konto?: string } | null;
}

/** Lille modal til STOP/TARGET. TRAIL har ingen — afstanden kommer fra
 *  Konfiguratoren, og et felt man skal udfylde hver gang, bliver udfyldt
 *  forkert en travl dag. */
function ExitPrisModal({ type, ticker, kurs, retning, cfg, fejl, travl,
                         onOpret, onLuk }: {
  type: ExitType;
  ticker: string;
  kurs: number | null;
  retning: "LONG" | "SHORT";
  cfg: {multiplikator: number | null; fornuft_pct: number} | null;
  fejl: string;
  travl: boolean;
  onOpret: (pris: number) => void;
  onLuk: () => void;
}) {
  const [pris, setPris] = useState<string>("");
  const ref = useRef<HTMLInputElement>(null);
  // ⚠ FORUDFYLDT MED KURSEN, markeret. Et tomt felt inviterer til at
  // skrive "20" — et antal points — og MES koster $5 pr. point, saa det er
  // en stop loss 7830 points vaek. Staar kursen der i forvejen, er det
  // tydeligt at feltet vil have en PRIS, og hun retter de sidste cifre.
  // Kursen kommer asynkront, saa den skal saettes naar den lander.
  useEffect(() => {
    if (kurs != null && pris === "") {
      setPris(kurs.toLocaleString("da-DK",
        {minimumFractionDigits: 2, maximumFractionDigits: 2}));
      requestAnimationFrame(() => ref.current?.select());
    }
  }, [kurs]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    ref.current?.focus();
    const k = (e: KeyboardEvent) => { if (e.key === "Escape") onLuk(); };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onLuk]);

  // ⚠ KOMMA SKAL VIRKE. Iben taster 7831,25 — det er sådan man skriver tal i
  // Danmark. `Number("7831,25")` er NaN, og med et <input type="number"> er
  // man prisgivet om browseren tilfældigvis normaliserer det. Gør den ikke
  // det, bliver knappen bare grå uden at sige hvorfor, og ordren bliver aldrig
  // lagt. Vi oversætter selv i stedet for at håbe.
  // ⚠ BEGGE SKRIVEMÅDER. Forudfyldningen er dansk formateret ("7.845,75"),
  // saa punktummet dér er et TUSINDSKILLETEGN — fjernes det ikke, er feltet
  // ugyldigt i det sekund det aabner. Men Iben kan ogsaa taste "7845.75", og
  // dét punktum er et decimaltegn. Kommaet afgoer hvilket af de to det er.
  const raat = pris.replace(/\s/g, "");
  const rent = raat.includes(",")
    ? raat.replace(/\./g, "").replace(",", ".")
    : raat;
  const vaerdi = Number(rent);
  const etTal = rent !== "" && isFinite(vaerdi) && vaerdi > 0;

  // Live afstand, saa "7845,75" ikke bare er fire cifre. Dollar kraever $
  // pr. point, og det tal kommer fra backenden — ikke fra en konstant her.
  const afstand = etTal && kurs != null ? Math.abs(vaerdi - kurs) : null;
  const dollar = afstand != null && cfg?.multiplikator != null
    ? afstand * cfg.multiplikator : null;
  const graense = cfg?.fornuft_pct ?? 0.03;
  // ⚠ Samme grænse som backenden, hentet FRA backenden. Den afviser
  // alligevel — det her er bare for at hun ser det MENS hun taster, i
  // stedet for efter et klik. Er de to uenige, vinder backenden.
  const urimelig = afstand != null && kurs != null
    && afstand > kurs * graense;
  // Hvilken side skal prisen ligge paa? STOP beskytter, TARGET tager gevinst.
  const skalOver = retning === "LONG" ? type === "TARGET" : type === "STOP";
  const forkertSide = etTal && kurs != null && vaerdi !== kurs
    && (skalOver ? vaerdi < kurs : vaerdi > kurs);
  const gyldig = etTal && !urimelig && !forkertSide && vaerdi !== kurs;

  return (
    <div style={{
      position: "fixed", inset: 0, background: "rgba(0,0,0,0.55)",
      display: "flex", alignItems: "center", justifyContent: "center",
      zIndex: 1000,
    }} onMouseDown={e => { if (e.target === e.currentTarget) onLuk(); }}>
      <div style={{
        background: "var(--bg-elevated)", border: "1px solid var(--border-strong)",
        borderRadius: 8, padding: 20, minWidth: 320,
        boxShadow: "0 12px 40px rgba(0,0,0,0.5)",
      }}>
        <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 4,
                      color: "var(--text-primary)" }}>
          {EXIT_NAVN[type]} på {ticker}
        </div>
        <div style={{ fontSize: 11.5, color: "var(--text-muted)", marginBottom: 12 }}>
          {kurs != null
            ? <>Aktuel kurs: <b>{tal(kurs)}</b></>
            : "⚠ Ingen aktuel kurs"}
        </div>
        <input
          ref={ref} type="text" inputMode="decimal" value={pris}
          placeholder={type === "STOP" ? "Stop loss-pris" : "Target profit-pris"}
          onChange={e => setPris(e.target.value)}
          onKeyDown={e => { if (e.key === "Enter" && gyldig && !travl) onOpret(vaerdi); }}
          style={{
            width: "100%", padding: "7px 10px", fontSize: 13,
            background: "var(--bg-input)", color: "var(--text-primary)",
            border: "1px solid var(--border-default)", borderRadius: 4,
            fontVariantNumeric: "tabular-nums",
          }} />
        {/* ⚠ AFSTANDEN, LIVE. Et prisfelt alene siger ikke om 7845,75 er en
            fornuftig stop — det goer "5,50 points = $27,50". Og det er dét
            tal hun traeffer beslutningen paa. Dollar kommer fra backendens
            multiplikator; mangler den, vises points alene frem for et gaet. */}
        {afstand != null && (
          <div style={{ fontSize: 11.5, marginTop: 8, lineHeight: 1.5,
                        color: urimelig ? "var(--bear)"
                             : vaerdi === kurs ? "var(--text-muted)"
                             : forkertSide ? "var(--bear)" : "var(--text-secondary)",
                        fontVariantNumeric: "tabular-nums" }}>
            {vaerdi === kurs
              ? "Det er kursen selv — flyt prisen."
              : <>Afstand: <b>{tal(afstand)}</b> points
                  {dollar != null && <> = <b>{usd(dollar)}</b></>}
                  {" "}{vaerdi > (kurs ?? 0) ? "over" : "under"} kursen</>}
            {urimelig && (
              <div style={{ marginTop: 4, fontWeight: 600 }}>
                ⚠ Det er mere end {tal(graense * 100, 0)} % fra kursen.
                Har du skrevet et antal points i stedet for en pris?
              </div>
            )}
            {!urimelig && forkertSide && (
              <div style={{ marginTop: 4, fontWeight: 600 }}>
                ⚠ {EXIT_NAVN[type]} paa en {retning.toLowerCase()} skal ligge
                {" "}{skalOver ? "OVER" : "UNDER"} kursen.
              </div>
            )}
          </div>
        )}
        {/* ⚠ Backendens tekst, ikke vores egen gaet. Den ved hvilken side af
            kursen prisen skal ligge, og den har hentet en FRISK kurs lige foer
            afsendelse — modalen her viser den kurs der var da den blev aabnet. */}
        {fejl && (
          <div style={{ color: "var(--bear)", fontSize: 11.5, marginTop: 10,
                        lineHeight: 1.45 }}>{fejl}</div>
        )}
        <div style={{ display: "flex", gap: 8, marginTop: 16,
                      justifyContent: "flex-end" }}>
          <button onClick={onLuk} disabled={travl}
            style={{ padding: "6px 14px", fontSize: 12, borderRadius: 4,
                     background: "transparent", color: "var(--text-secondary)",
                     border: "1px solid var(--border-default)",
                     cursor: travl ? "wait" : "pointer" }}>Annuller</button>
          <button onClick={() => gyldig && onOpret(vaerdi)}
            disabled={!gyldig || travl}
            style={{ padding: "6px 16px", fontSize: 12, fontWeight: 700,
                     borderRadius: 4, background: "var(--accent)", color: "#fff",
                     border: "1px solid var(--accent)",
                     opacity: (!gyldig || travl) ? 0.45 : 1,
                     cursor: (!gyldig || travl) ? "not-allowed" : "pointer" }}>
            {travl ? "Opretter…" : "Opret"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Hjælpere ──────────────────────────────────────────────
function fmtTime(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString("da-DK", {
      hour: "2-digit", minute: "2-digit", second: "2-digit"
    });
  } catch {
    return "—";
  }
}

/** Hvor længe positionen var åben. "—" hvis den stadig er det. */
function varighed(fra: string, til: string | null): string {
  if (!til) return "—";
  try {
    const ms = new Date(til).getTime() - new Date(fra).getTime();
    if (!isFinite(ms) || ms < 0) return "—";
    const m = Math.floor(ms / 60000), sek = Math.floor((ms % 60000) / 1000);
    if (m >= 60) return `${Math.floor(m / 60)}t ${m % 60}m`;
    return m > 0 ? `${m}m ${sek}s` : `${sek}s`;
  } catch { return "—"; }
}

/** Dansk talformat: 7.841,25 — komma som decimalseparator.
 *
 * ⚠ VINDUET BLANDEDE DE TO. Entry price stod som "$7841.25" (punktum) mens
 * tooltip'en lige ved siden af sagde "Højeste 7845,75" (komma). Det er ikke
 * kun grimt: Iben INDTASTER med komma, og et vindue der svarer med punktum,
 * sår tvivl om den pris man lige har lagt en stop loss på.
 */
function tal(v: number | null | undefined, decimaler = 2): string {
  if (v == null || !isFinite(v)) return "—";
  return v.toLocaleString("da-DK", {
    minimumFractionDigits: decimaler, maximumFractionDigits: decimaler});
}

function usd(v: number | null | undefined): string {
  if (v == null || !isFinite(v)) return "—";
  return (v < 0 ? "-$" : "$") + tal(Math.abs(v));
}

function fmtDate(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleDateString("da-DK", {
      day: "2-digit", month: "2-digit"
    });
  } catch {
    return "—";
  }
}

function statusEmoji(group: string): string {
  switch (group) {
    case "filled":    return "✅";
    case "open":      return "🟡";
    case "cancelled": return "❌";
    default:          return "⚪";
  }
}

function statusColor(group: string): string {
  switch (group) {
    case "filled":    return "var(--bull)";
    case "open":      return "#f59e0b";  // gul/orange
    case "cancelled": return "var(--text-muted)";
    default:          return "var(--text-secondary)";
  }
}

function sourceLabel(source: string): string {
  if (source === "manual_watchlist" || source === "manual") return "Manuel";
  return source;
}

function statusText(status: string): string {
  // Oversæt IBKR's tekniske statusser til menneskeligt sprog
  const map: Record<string, string> = {
    "Filled":         "Udført",
    "Submitted":      "Afsendt",
    "PreSubmitted":   "Afsendt",
    "PendingSubmit":  "Afsendt",
    "PendingCancel":  "Annullerer...",
    "Cancelled":      "Annulleret",
    "ApiCancelled":   "Annulleret",
    "Inactive":       "Inaktiv",
    "ApiPending":     "Behandler...",
    // ⚠ Kort med vilje. "Status ukendt" blev klippet til "Status uk…" i
    // STATUS-kolonnen, og en afklippet forklaring forklarer ingenting.
    // Begrundelsen ligger i ⓘ-tooltippet ved siden af.
    "UNKNOWN":        "Ukendt",
  };
  return map[status] || status;
}

// ── Hoved-komponent ───────────────────────────────────────────
export function OrdersWindow() {
  const [orders, setOrders]       = useState<OrderEntry[]>([]);
  const [loading, setLoading]     = useState(true);
  const [error, setError]         = useState("");
  // 0 = AKTUEL DAG (fra midnat). Alt andet = rullende vindue i timer.
  //
  // ⚠ DE TO ER IKKE DET SAMME, og valget hed før "I dag (24 timer)" som om de
  // var. Kl. 09:30 viste det tilbage til 09:30 i går — hele gårsdagens session.
  // Målt 28-09: 8 ordrer vist, 2 af dem fra i går. Iben meldte det som en fejl
  // i vinduet; det var etiketten der løj.
  const [periodHours, setPeriodHours] = useState<number>(() => {
    const saved = localStorage.getItem("orders_period_hours");
    // ⚠ MIGRÉR DEN GAMLE 24. Den var default og hed "I dag" — den der stod med
    // den, havde bedt om i dag og fået noget andet. At lade den blive ville
    // efterlade netop de brugere med fejlen de meldte.
    if (saved === null || saved === "24") return 0;
    return parseInt(saved);
  });
  const [lastUpdate, setLastUpdate] = useState("");
  const [fane, setFane] = useState<Fane>(() =>
    (localStorage.getItem("orders_fane") as Fane) || "ordrer");
  const [handler, setHandler] = useState<HandelRow[]>([]);
  useEffect(() => { localStorage.setItem("orders_fane", fane); }, [fane]);
  const [cancellingId, setCancellingId] = useState<number | null>(null);
  const [prisModal, setPrisModal] = useState<
    {type: ExitType; parent: string; ticker: string;
     retning: "LONG" | "SHORT"} | null>(null);
  // ⚠ tick, multiplikator og fornuftsgrænse kommer FRA BACKENDEN. $ pr.
  // point maa ikke skrives op her: futures_katalog er én sandhedskilde, og
  // en kopi i frontenden ville drive fra den uden at nogen opdagede det.
  const [exitCfg, setExitCfg] = useState<
    {multiplikator: number | null; fornuft_pct: number} | null>(null);
  const [modalFejl, setModalFejl] = useState("");
  const [travl, setTravl] = useState(false);
  const [kurs, setKurs] = useState<number | null>(null);

  async function hentExitCfg() {
    try {
      const r = await fetch("http://127.0.0.1:8000/exit-config");
      const d = await r.json();
      setExitCfg({multiplikator: typeof d.multiplikator === "number"
                    ? d.multiplikator : null,
                  fornuft_pct: typeof d.fornuft_pct === "number"
                    ? d.fornuft_pct : 0.03});
    } catch { setExitCfg(null); }
  }

  async function hentKurs(ticker: string) {
    try {
      const r = await fetch(`http://127.0.0.1:8000/quote/${ticker}`);
      const d = await r.json();
      setKurs(typeof d.price === "number" ? d.price : null);
    } catch { setKurs(null); }
  }

  /** Opret en exit-ordre. ⚠ Knaptilstanden saettes IKKE her — se noten ved
   *  knapperne. Vi henter listen igen og lader backenden svare. */
  async function opretExit(parent: string, type: ExitType, pris: number | null) {
    setTravl(true); setModalFejl("");
    try {
      const r = await fetch("http://127.0.0.1:8000/exit-ordre", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({parent_order_id: parent, type, pris}),
      });
      const d = await r.json();
      if (!d.success) { setModalFejl(d.error || "Ukendt fejl."); return; }
      setPrisModal(null);
      await fetchOrders();     // ⚠ straks igen, saa knappen ikke staar forkert
    } catch (e: any) {
      setModalFejl(`Kunne ikke nå backenden: ${e?.message || e}`);
    } finally { setTravl(false); }
  }

  async function annullerExit(o: OrderEntry) {
    const t = exitType(o.ordre_type);
    const navn = t ? EXIT_NAVN[t] : "exit-ordren";
    const pris = o.trigger_pris != null
      ? ` på ${tal(o.trigger_pris)}` : "";
    if (!window.confirm(`Annullér ${navn.toLowerCase()}${pris}?`)) return;
    try {
      const r = await fetch("http://127.0.0.1:8000/exit-ordre/annuller", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({order_id: String(o.order_id)}),
      });
      const d = await r.json();
      if (!d.success) setError(d.error || "Kunne ikke annullere.");
      // ⚠ Genlaegningen af soeskende sker i backenden (OCO kaskaderer). Vi
      // venter paa listen frem for at gaette hvad der nu er aktivt.
      await fetchOrders();
    } catch (e: any) { setError(`Kunne ikke nå backenden: ${e?.message || e}`); }
  }
  const timerRef = useRef<number | null>(null);

  // Hvilke status-grupper skal vises? Iben kan toggle med badges øverst.
  const [showOpen, setShowOpen]           = useState<boolean>(() =>
    localStorage.getItem("orders_show_open") !== "false");
  const [showFilled, setShowFilled]       = useState<boolean>(() =>
    localStorage.getItem("orders_show_filled") !== "false");
  // ⚠ "Ukendt" er en FJERDE gruppe, ikke en restkategori. Uden sin egen taeller
  // stod vinduet med "0 aabne · 0 udfoerte · 0 annul." OG to synlige raekker —
  // og et vindue hvis tal ikke stemmer med det man ser, laeses som i stykker.
  const [showUnknown, setShowUnknown]     = useState<boolean>(() =>
    localStorage.getItem("orders_show_unknown") !== "false");
  const [showCancelled, setShowCancelled] = useState<boolean>(() =>
    localStorage.getItem("orders_show_cancelled") !== "false");

  useEffect(() => { localStorage.setItem("orders_show_open",      String(showOpen)); },      [showOpen]);
  useEffect(() => { localStorage.setItem("orders_show_filled",    String(showFilled)); },    [showFilled]);
  useEffect(() => { localStorage.setItem("orders_show_cancelled", String(showCancelled)); }, [showCancelled]);
  useEffect(() => { localStorage.setItem("orders_show_unknown",   String(showUnknown)); },   [showUnknown]);

  // Gem periode-valg
  useEffect(() => {
    localStorage.setItem("orders_period_hours", String(periodHours));
  }, [periodHours]);

  // ⚠ SAMME PERIODE SOM ORDRELISTEN, og det er derfor de to ligger i samme
  // vindue. Et separat vindue ville have sit eget filter, og to filtre der
  // betyder det samme men kan staa forskelligt, er en kilde til at tallene ikke
  // stemmer uden at nogen kan se hvorfor.
  //
  // `date_from` filtrerer paa ET-handelsdag i backenden. For "aktuel dag"
  // sendes dagens dato; for et rullende vindue sendes datoen for vinduets start,
  // saa der hellere kommer lidt for meget med end for lidt — listen viser
  // tidspunktet, saa man kan se hvad der er hvad.
  async function fetchHandler() {
    try {
      const nu = new Date();
      const start = periodHours === 0
        ? nu
        : new Date(nu.getTime() - periodHours * 3600_000);
      const d = `${start.getFullYear()}-${String(start.getMonth() + 1).padStart(2, "0")}`
              + `-${String(start.getDate()).padStart(2, "0")}`;
      const resp = await fetch(
        `http://127.0.0.1:8000/journal/trades?source=manual&date_from=${d}&limit=300`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      setHandler(data.trades || []);
      setError("");
    } catch (e: any) {
      setError(`Kunne ikke hente handler: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }

  async function fetchOrders() {
    try {
      const resp = await fetch(
        periodHours === 0
          ? "http://127.0.0.1:8000/orders/list?fra_midnat=true"
          : `http://127.0.0.1:8000/orders/list?period_hours=${periodHours}`
      );
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      setOrders(data.orders || []);
      setLastUpdate(new Date().toLocaleTimeString("da-DK"));
      setError("");
    } catch (e: any) {
      setError(`Kunne ikke hente ordrer: ${e.message || e}`);
    } finally {
      setLoading(false);
    }
  }

  // Initial fetch + auto-refresh hvert 2. sekund
  // ⚠ KUN DEN SYNLIGE FANE HENTES. Begge lister hvert 2. sekund ville koste
  // to kald for noget man ikke kigger paa — og handelslisten laeser databasen,
  // ikke en cache. Skift af fane henter med det samme, saa der ikke staar
  // forældede tal mens man venter paa naeste tik.
  useEffect(() => {
    const hent = () => { if (fane === "ordrer") fetchOrders(); else fetchHandler(); };
    hent();
    timerRef.current = window.setInterval(hent, 2_000);
    return () => {
      if (timerRef.current) window.clearInterval(timerRef.current);
    };
  }, [periodHours, fane]);

  async function handleCancel(order: OrderEntry) {
    const confirmText = `Annullér ${order.action} ${order.shares} ${order.ticker}?`;
    if (!window.confirm(confirmText)) return;

    setCancellingId(order.order_id);
    try {
      const resp = await fetch("http://127.0.0.1:8000/orders/cancel", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ order_id: order.order_id }),
      });
      const result = await resp.json();
      if (!result.success) {
        alert(`Kunne ikke annullere: ${result.error}`);
      } else {
        // Refresh straks så Iben ser status ændre
        fetchOrders();
      }
    } catch (e: any) {
      alert(`Cancel fejl: ${e.message || e}`);
    } finally {
      setCancellingId(null);
    }
  }

  const openCount      = orders.filter(o => o.status_group === "open").length;
  const filledCount    = orders.filter(o => o.status_group === "filled").length;
  const cancelledCount = orders.filter(o => o.status_group === "cancelled").length;
  const unknownCount   = orders.filter(o => o.status_group === "unknown").length;

  // Filtrer ordrer baseret på Iben's valg
  const visibleOrders = orders.filter(o => {
    if (o.status_group === "open"      && !showOpen)      return false;
    if (o.status_group === "filled"    && !showFilled)    return false;
    if (o.status_group === "cancelled" && !showCancelled) return false;
    if (o.status_group === "unknown"   && !showUnknown)   return false;
    return true;
  });

  /** Exit-raekker lige under deres parent, let indrykket.
   *
   * ⚠ TRACKEREN LEVERER NYESTE FOERST, saa en exit-ordre staar normalt OVER
   * den position den beskytter. Laest ovenfra ville det se ud som om stoppen
   * hoerte til noget andet — eller til ingenting. */
  function grupper(raekker: OrderEntry[]): {o: OrderEntry; barn: boolean}[] {
    const exits = new Map<string, OrderEntry[]>();
    for (const o of raekker) {
      if (!exitType(o.ordre_type)) continue;
      const pid = String(o.parent_order_id || "");
      if (!pid) continue;
      (exits.get(pid) || exits.set(pid, []).get(pid)!).push(o);
    }
    const ud: {o: OrderEntry; barn: boolean}[] = [];
    for (const o of raekker) {
      if (exitType(o.ordre_type) && o.parent_order_id) continue;
      ud.push({o, barn: false});
      for (const b of exits.get(String(o.order_id)) || []) ud.push({o: b, barn: true});
    }
    return ud;
  }

  // Genbrugelig badge-style
  function badgeStyle(active: boolean, color: string): React.CSSProperties {
    return {
      background: active ? `${color}22` : "transparent",
      border: `1px solid ${active ? color : "var(--border-default)"}`,
      color: active ? color : "var(--text-muted)",
      borderRadius: 3,
      padding: "3px 10px",
      fontSize: 11,
      fontWeight: 600,
      cursor: "pointer",
      userSelect: "none",
      transition: "all 150ms ease",
    };
  }
  return (
    <div style={{
      padding: 10,
      height: "100%",
      overflow: "hidden",
      display: "flex",
      flexDirection: "column",
      gap: 10,
    }}>
      {/* ── Toolbar ── */}
      <div style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 10,
        flexShrink: 0,
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          {/* ⚠ To visninger af de samme handler. "Ordrer" er sporet — hver
              ordre med tidsstempel, saa man kan se praecis hvornaar man gjorde
              hvad. "Handler" er opgoerelsen — entry og exit paret, med P&L.
              De besvarer forskellige spoergsmaal, saa ingen af dem erstatter
              den anden. */}
          <div style={{ display: "flex", gap: 5, marginRight: 8 }}>
            {([["ordrer", "Ordrer"], ["handler", "Handler"]] as [Fane, string][])
              .map(([v, navn]) => (
              <span key={v} onClick={() => setFane(v)}
                title={fane === v ? `${navn} vises` : `Skift til ${navn}`}
                style={{
                  padding: "3px 12px", fontSize: 11,
                  cursor: "pointer", userSelect: "none",
                  borderStyle: "solid", borderWidth: 1,
                  borderRadius: 3,
                  // ⚠ SAMME SIGNAL SOM DEN AKTIVE WATCHLIST. Neongul betyder
                  // "det er denne der er i brug" — ét sprog, ikke to.
                  //
                  // ⚠ OG DEN INAKTIVE BRUGER IKKE --text-muted. I stealth er
                  // den sat til #e2e8f0, naesten hvid, saa den inaktive fane
                  // blev MERE fremtraedende end den aktive og signalet vendte
                  // om. "Muted" er ikke daempet i alle temaer.
                  fontWeight: fane === v ? 700 : 500,
                  background:  fane === v ? "var(--aktiv-glod)" : "transparent",
                  borderColor: fane === v ? "var(--aktiv-markering)"
                                          : "var(--inaktiv-tekst)",
                  color:       fane === v ? "var(--aktiv-markering)"
                                          : "var(--inaktiv-tekst)",
                  opacity:     fane === v ? 1 : 0.75,
                }}>{navn}</span>
            ))}
          </div>
          <span style={{ fontSize: 11, color: "var(--text-muted)" }}>Periode:</span>
          <select
            value={periodHours}
            onChange={e => setPeriodHours(parseInt(e.target.value))}
            style={{
              background: "var(--bg-input)",
              border: "1px solid var(--border-default)",
              borderRadius: 3,
              color: "var(--text-primary)",
              fontSize: 12,
              padding: "3px 8px",
              cursor: "pointer",
              fontFamily: "inherit",
            }}
          >
            <option value={1}>Sidste time</option>
            <option value={8}>Sidste 8 timer</option>
            <option value={0}>Aktuel dag (fra midnat)</option>
            <option value={24}>Rullende 24 timer</option>
            <option value={72}>Sidste 3 dage</option>
            <option value={168}>Sidste 7 dage</option>
            <option value={720}>Sidste 30 dage</option>
          </select>

          {/* Status-filtrene gaelder ORDRER. En handel er enten aaben eller
              lukket, og det staar i raekken — den har ikke ordrernes fire
              tilstande, saa badges her ville ikke betyde noget. */}
          <div style={{ marginLeft: 10, display: "flex", gap: 6,
                        visibility: fane === "ordrer" ? "visible" : "hidden" }}>
            <span
              onClick={() => setShowOpen(s => !s)}
              style={badgeStyle(showOpen, "#f59e0b")}
              title={showOpen ? "Klik for at skjule åbne ordrer" : "Klik for at vise åbne ordrer"}
            >
              🟡 {openCount} åbne
            </span>
            <span
              onClick={() => setShowFilled(s => !s)}
              style={badgeStyle(showFilled, "var(--bull)")}
              title={showFilled ? "Klik for at skjule udførte ordrer" : "Klik for at vise udførte ordrer"}
            >
              ✅ {filledCount} udførte
            </span>
            <span
              onClick={() => setShowCancelled(s => !s)}
              style={badgeStyle(showCancelled, "var(--bear)")}
              title={showCancelled ? "Klik for at skjule annullerede ordrer" : "Klik for at vise annullerede ordrer"}
            >
              ❌ {cancelledCount} annul.
            </span>
            {unknownCount > 0 && (
              <span
                onClick={() => setShowUnknown(s => !s)}
                style={badgeStyle(showUnknown, "var(--text-muted)")}
                title="Ordrer hvis status ingen forbindelse har kunnet bekræfte — hold musen over statussen for at se hvorfor"
              >
                ⚪ {unknownCount} ukendt
              </span>
            )}
          </div>
        </div>

        <span style={{ fontSize: 10, color: "var(--text-muted)" }}>
          {lastUpdate && `Opdateret ${lastUpdate}`}
        </span>
      </div>

      {prisModal && (
        <ExitPrisModal
          type={prisModal.type} ticker={prisModal.ticker} kurs={kurs}
          retning={prisModal.retning} cfg={exitCfg}
          fejl={modalFejl} travl={travl}
          onOpret={pris => opretExit(prisModal.parent, prisModal.type, pris)}
          onLuk={() => { setPrisModal(null); setModalFejl(""); }} />
      )}

      {/* ── Fejl ── */}
      {error && (
        <div style={{
          padding: "6px 10px",
          background: "rgba(248, 113, 113, 0.1)",
          border: "1px solid var(--bear)",
          borderRadius: 3,
          color: "var(--bear)",
          fontSize: 11,
          flexShrink: 0,
        }}>
          ⚠ {error}
        </div>
      )}

      {/* ── Loading ── */}
      {loading && orders.length === 0 && (
        <div style={{
          padding: 20,
          textAlign: "center",
          color: "var(--text-muted)",
          fontStyle: "italic",
        }}>
          Indlæser ordrer...
        </div>
      )}

      {/* ── Tom liste ── */}
      {fane === "ordrer" && !loading && orders.length === 0 && !error && (
        <div style={{
          padding: 20,
          textAlign: "center",
          color: "var(--text-muted)",
          fontStyle: "italic",
        }}>
          Ingen ordrer i den valgte periode
        </div>
      )}

      {/* ── Alle ordrer er filtreret bort ── */}
      {fane === "ordrer" && !loading && orders.length > 0 && visibleOrders.length === 0 && (
        <div style={{
          padding: 20,
          textAlign: "center",
          color: "var(--text-muted)",
          fontStyle: "italic",
        }}>
          Ingen ordrer matcher det valgte filter — klik på et badge for at vise flere
        </div>
      )}

      {/* ══ HANDLER: én linje pr. transaktion ══════════════════════════ */}
      {fane === "handler" && !loading && handler.length === 0 && !error && (
        <div style={{ padding: 20, textAlign: "center",
                      color: "var(--text-muted)", fontStyle: "italic" }}>
          Ingen handler i den valgte periode
        </div>
      )}

      {fane === "handler" && handler.length > 0 && (() => {
        const lukkede = handler.filter(h => h.exit_time_utc);
        // ⚠ Summen daekker KUN de lukkede. En aaben position har ingen
        // realiseret P&L, og at taelle den med som 0 ville vaere en paastand
        // om at den gik i nul.
        const sum = lukkede.reduce((a, h) => a + (h.pnl || 0), 0);
        const vundne = lukkede.filter(h => (h.pnl || 0) > 0).length;
        return (
        <>
          <div style={{
            flexShrink: 0, display: "flex", gap: 16, alignItems: "baseline",
            padding: "4px 10px", fontSize: 11,
            background: "var(--bg-surface)", borderRadius: 4,
          }}>
            <span style={{ color: "var(--text-muted)" }}>
              {lukkede.length} lukkede
              {handler.length > lukkede.length &&
                ` · ${handler.length - lukkede.length} åbne`}
            </span>
            <span style={{ color: "var(--text-muted)" }}>
              Træfprocent {lukkede.length
                ? `${Math.round((vundne / lukkede.length) * 100)}%` : "—"}
            </span>
            <span style={{ fontWeight: 700, fontSize: 12,
                           color: sum >= 0 ? "var(--bull)" : "var(--bear)" }}>
              Realiseret {usd(sum)}
            </span>
          </div>

          <div style={{ flex: 1, overflow: "auto", minHeight: 0 }}>
            {/* ⚠ minWidth som i Ordrer-tabellen. Uden den klipper den
                sidste kolonne naar vinduet er smalt — samme fejl som da
                TRAIL-knappen forsvandt ud over kanten. Beholderen ruller. */}
            <table className="scanner-table"
                   style={{ width: "100%", minWidth: 940 }}>
              <thead>
                <tr>
                  <th style={{ textAlign: "left" }}>Ind</th>
                  <th style={{ textAlign: "left" }}>Ud</th>
                  <th style={{ textAlign: "right" }}>Varighed</th>
                  <th style={{ textAlign: "left" }}>Ticker</th>
                  <th style={{ textAlign: "left" }}>Retning</th>
                  <th style={{ textAlign: "right" }}>Antal</th>
                  <th style={{ textAlign: "right" }}>Entry</th>
                  <th style={{ textAlign: "right" }}>Exit</th>
                  <th style={{ textAlign: "right" }}>P&amp;L</th>
                  {/* ⚠ MAA IKKE KLIPPES. `.scanner-table td` saetter
                      overflow:hidden + ellipsis globalt, og det goer cellens
                      mindstebredde til NUL. Auto-layout klemmer derfor netop
                      den kolonne med det laengste indhold — kontoen — mens
                      talkolonnerne beholder deres luft. Maalt 08-10:
                      "NT8 · DEMO85807…" med rigeligt plads i vinduet.
                      Med clip+visible faar kolonnen sin naturlige bredde. */}
                  <th style={{ textAlign: "left", whiteSpace: "nowrap",
                               overflow: "visible", textOverflow: "clip" }}>
                    Konto</th>
                </tr>
              </thead>
              <tbody>
                {handler.map(h => {
                  const aaben = !h.exit_time_utc;
                  const pnl = h.pnl;
                  return (
                    <tr key={h.trade_id}
                        title={h.exit_reason ? `Exit: ${h.exit_reason}` : "Stadig åben"}>
                      <td>{fmtTime(h.entry_time_utc)}</td>
                      <td>{h.exit_time_utc ? fmtTime(h.exit_time_utc)
                        : <span style={{ color: "#f59e0b", fontWeight: 700 }}>ÅBEN</span>}</td>
                      <td style={{ textAlign: "right", color: "var(--text-muted)" }}>
                        {varighed(h.entry_time_utc, h.exit_time_utc)}</td>
                      <td style={{ fontWeight: 700 }}>{h.symbol}</td>
                      <td style={{
                        color: h.side === "long" ? "var(--bull)" : "var(--bear)",
                        fontWeight: 700, fontSize: 10.5,
                      }}>{h.side === "long" ? "LONG" : "SHORT"}</td>
                      <td style={{ textAlign: "right" }}>{h.shares}</td>
                      <td style={{ textAlign: "right" }}>{h.entry_price}</td>
                      <td style={{ textAlign: "right" }}>{h.exit_price ?? "—"}</td>
                      {/* ⚠ Tom for aabne handler, ikke $0,00. En urealiseret
                          position har ikke et resultat endnu. */}
                      <td style={{
                        textAlign: "right", fontWeight: 700,
                        color: aaben ? "var(--text-muted)"
                             : (pnl || 0) >= 0 ? "var(--bull)" : "var(--bear)",
                      }}>{aaben ? "—" : usd(pnl)}</td>
                      <td style={{ fontSize: 10, color: "var(--text-muted)",
                                   whiteSpace: "nowrap", overflow: "visible",
                                   textOverflow: "clip" }}
                          title={`${h.payload?.broker || ""} ${h.ibkr_account || ""}`.trim()}>
                        {h.payload?.broker ? `${h.payload.broker} · ` : ""}
                        {h.ibkr_account || "—"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
        );
      })()}

      {/* ── Tabel ── */}
      {fane === "ordrer" && visibleOrders.length > 0 && (
        <div style={{ flex: 1, overflow: "auto", minHeight: 0 }}>
          {/* ⚠ minWidth FREM FOR REN 100 %. Handlingskolonnen har nowrap, og
              med width:100 % alene skrumper tabellen med vinduet indtil den
              SKAERER knapperne af — TRAIL forsvandt ud over kanten. En knap man
              ikke kan se, findes ikke, og det er ikke en kosmetisk mangel naar
              knappen laegger en stop loss.
              Nu faar tabellen en mindstebredde, og beholderen ruller i stedet. */}
          <table className="scanner-table"
                 style={{ width: "100%", minWidth: 1020 }}>
            <thead style={{ position: "sticky", top: 0, background: "var(--bg-elevated)", zIndex: 1 }}>
              <tr>
                <th style={{ textAlign: "left" }}>Tid</th>
                <th style={{ textAlign: "left" }}>Kilde</th>
                <th style={{ textAlign: "left" }}>Ticker</th>
                <th style={{ textAlign: "center" }}>Side</th>
                <th style={{ textAlign: "right" }}>Stk</th>
                <th style={{ textAlign: "center" }}>Type</th>
                <th style={{ textAlign: "left" }}>Status</th>
                <th style={{ textAlign: "right" }}>Fyldt</th>
                {/* ⚠ "Snit pris" passede kun paa fyldte ordrer. En STOP har
                    ingen snitpris — den har en TRIGGERPRIS, og det er den
                    Iben skal kunne se. Samme kolonne, aerligt navn. */}
                <th style={{ textAlign: "right" }}>Entry price</th>
                {/* ⚠ Kun udfyldt paa raekker der LUKKEDE en handel. En
                    aabning har ingen P/L endnu, og et nul dér ville se ud
                    som en handel der gik i nul. */}
                <th style={{ textAlign: "right" }}>P&amp;L</th>
                {/* Plads nok til STOP + TARGET + TRAIL med mellemrum. */}
                <th style={{ textAlign: "center", width: 190, minWidth: 190 }}></th>
              </tr>
            </thead>
            <tbody>
              {grupper(visibleOrders).map(({o, barn}) => {
                const isOpen = o.status_group === "open";
                const sideColor = o.action === "BUY" ? "var(--bull)" : "var(--bear)";
                const type = (o.ordre_type || "").toUpperCase();
                const exitT = exitType(o.ordre_type);
                const erExit = exitT !== null;
                const knapper = o.exit_knapper;
                return (
                  <tr key={o.order_id}
                      style={barn ? {background: "var(--bg-surface)"} : undefined}>
                    {/* ⚠ INDRYKNINGEN MAA IKKE LIGGE HER. Den skubbede baade
                        klokkeslaet og dato til hoejre paa exit-raekker, saa
                        kolonnen stod forskudt fra raekke til raekke — og en
                        tidskolonne man ikke kan laese lodret, er svaer at bruge
                        til dét den er til: at se hvornaar man gjorde hvad.
                        Slaegtskabet vises med baggrunden og med indrykning af
                        TICKER-cellen i stedet. */}
                    <td style={{ color: "var(--text-secondary)", whiteSpace: "nowrap" }}>
                      <div>{fmtTime(o.placed_at)}</div>
                      <div style={{ fontSize: 10, color: "var(--text-muted)" }}>
                        {fmtDate(o.placed_at)}
                      </div>
                    </td>
                    <td style={{ color: "var(--text-muted)", fontSize: 11 }}>
                      {sourceLabel(o.source)}
                    </td>
                    <td style={{ paddingLeft: barn ? 16 : undefined }}>
                      {barn && <span style={{ color: "var(--text-muted)",
                                              marginRight: 4 }}>└</span>}
                      <strong>{o.ticker}</strong>
                    </td>
                    <td style={{ textAlign: "center", color: sideColor, fontWeight: 700 }}>
                      {o.action}
                    </td>
                    <td style={{ textAlign: "right" }}>{o.shares.toLocaleString("da-DK")}</td>
                    {/* ⚠ ordre_type FOERST. Gamle raekker har den ikke og viser
                        order_type (MKT/LMT) som hidtil — historikken skal stadig
                        kunne laeses. */}
                    <td style={{ textAlign: "center", fontSize: 11,
                                 fontWeight: o.ordre_type ? 700 : 400,
                                 color: erExit ? "var(--ur-us)"
                                      : type === "EXIT" ? "var(--text-secondary)"
                                      : type === "LONG" ? "var(--bull)"
                                      : type === "SHORT" ? "var(--bear)"
                                      : "var(--text-muted)" }}
                        title={o.exit_aarsag ? `Lukket af ${o.exit_aarsag}` : undefined}>
                      {o.ordre_type || o.order_type}
                      {!o.ordre_type && o.limit_price
                        ? ` @ ${usd(o.limit_price)}` : ""}
                      {o.exit_aarsag ? ` · ${o.exit_aarsag}` : ""}
                      {o.advarsel && (
                        <div style={{ color: "var(--bear)", fontSize: 9.5,
                                      fontWeight: 600, marginTop: 1,
                                      whiteSpace: "normal", maxWidth: 150,
                                      lineHeight: 1.25 }}>
                          ⚠ {o.advarsel}
                        </div>
                      )}
                    </td>
                    <td style={{ color: o.genlagt_som ? "var(--text-muted)"
                                        : statusColor(o.status_group),
                                 fontWeight: 600 }}
                        title={o.genlagt_som
                          ? `Lagt igen som ${o.genlagt_som}. NinjaTrader `
                            + `annullerer hele OCO-gruppen når én ordre slettes, `
                            + `så søskende skal lægges på ny med et nyt OCO-id.`
                          : (o.note || undefined)}>
                      {o.genlagt_som
                        ? <>↻ Genlagt</>
                        : <>{statusEmoji(o.status_group)} {statusText(o.status)}</>}
                      {o.note && <span style={{ marginLeft: 4, cursor: "help",
                                                color: "var(--text-muted)" }}>ⓘ</span>}
                    </td>
                    <td style={{ textAlign: "right" }}>
                      {o.filled > 0
                        ? `${o.filled}${o.remaining > 0 ? ` / ${o.filled + o.remaining}` : ""}`
                        : "—"}
                    </td>
                    {/* Entry price: fyldpris for LONG/SHORT/EXIT, triggerpris
                        for STOP/TARGET, aktuel stop for TRAIL. */}
                    <td style={{ textAlign: "right", fontVariantNumeric: "tabular-nums" }}
                        title={type === "TRAIL" && o.trail_hoejeste != null
                          ? `${o.action === "SELL" ? "Højeste" : "Laveste"} `
                            + `${tal(o.trail_hoejeste)}`
                            + ` · afstand ${tal(o.trail_afstand ?? 0)}`
                          : undefined}>
                      {erExit
                        ? (o.trigger_pris != null ? usd(o.trigger_pris) : "—")
                        : (o.avg_fill > 0 ? usd(o.avg_fill) : "—")}
                      {type === "TRAIL" && <span style={{ opacity: 0.6 }}> ↗</span>}
                    </td>
                    <td style={{ textAlign: "right", fontWeight: 700,
                                 fontVariantNumeric: "tabular-nums",
                                 color: o.pnl == null ? "var(--text-muted)"
                                      : o.pnl >= 0 ? "var(--bull)" : "var(--bear)" }}
                        title={o.pnl != null && o.trade_id
                          ? `Realiseret på handel ${o.trade_id.slice(0, 8)}`
                          : undefined}>
                      {o.pnl == null
                        ? "—"
                        : `${o.pnl >= 0 ? "+" : ""}${usd(o.pnl)}`}
                    </td>
                    <td style={{ textAlign: "center", whiteSpace: "nowrap" }}>
                      {/* ── Exit-raekke: et kryds, som i watchlisten ──────── */}
                      {erExit && isOpen && (
                        <span onClick={() => annullerExit(o)}
                          title={`Annullér ${EXIT_NAVN[exitT!]}`}
                          style={{ cursor: "pointer", color: "var(--text-muted)",
                                   fontSize: 14, padding: "0 6px",
                                   userSelect: "none" }}>×</span>
                      )}

                      {/* ── Parent-raekke: de tre knapper ─────────────────
                          ⚠ TILSTANDEN KOMMER KUN FRA BACKENDEN (exit_knapper),
                          aldrig fra klikket. Farvede vi gul ved klik, ville en
                          AFVIST stop loss se aktiv ud — og det opdager man
                          foerst den dag man faar brug for den.
                          Derfor: klik -> POST -> hent listen -> farven skifter
                          naar ATI har bekraeftet. */}
                      {!erExit && o.exit_mulig && knapper && (
                        <span style={{ display: "inline-flex", gap: 4,
                                       whiteSpace: "nowrap" }}>
                          {EXIT_TYPER.map(t => {
                            const st = knapper[t] || "ingen";
                            const aktiv = st === "aktiv";
                            const venter = st === "afventer";
                            return (
                              <button key={t}
                                disabled={aktiv || venter || travl}
                                onClick={() => {
                                  if (t === "TRAIL") {
                                    // ⚠ Ingen modal. Afstanden staar i
                                    // Konfiguratoren; et felt man skal udfylde
                                    // hver gang, bliver udfyldt forkert en
                                    // travl dag.
                                    opretExit(String(o.order_id), t, null);
                                  } else {
                                    setModalFejl(""); setKurs(null);
                                    hentKurs(o.ticker);
                                    hentExitCfg();
                                    setPrisModal({type: t, parent: String(o.order_id),
                                                  ticker: o.ticker,
                                                  retning: (o.ordre_type || "") === "SHORT"
                                                    ? "SHORT" : "LONG"});
                                  }
                                }}
                                title={aktiv ? `${EXIT_NAVN[t]} er aktiv`
                                     : venter ? `${EXIT_NAVN[t]} afventer bekræftelse`
                                     : `Opret ${EXIT_NAVN[t].toLowerCase()}`}
                                style={{
                                  fontSize: 10, fontWeight: 700, padding: "2px 7px",
                                  borderRadius: 3, borderStyle: "solid", borderWidth: 1,
                                  background: aktiv ? "var(--aktiv-glod)" : "transparent",
                                  borderColor: aktiv ? "var(--ur-us)" : "var(--accent)",
                                  color: aktiv ? "var(--ur-us)" : "var(--accent)",
                                  opacity: venter ? 0.5 : 1,
                                  cursor: (aktiv || venter) ? "default" : "pointer",
                                }}>
                                {venter ? "…" : t}
                              </button>
                            );
                          })}
                        </span>
                      )}

                      {!erExit && isOpen && (
                        <button
                          onClick={() => handleCancel(o)}
                          disabled={cancellingId === o.order_id}
                          style={{
                            background: "var(--bear-muted)",
                            border: "1px solid var(--bear)",
                            color: "var(--bear)",
                            borderRadius: 3,
                            fontSize: 10,
                            fontWeight: 700,
                            padding: "2px 8px",
                            cursor: cancellingId === o.order_id ? "wait" : "pointer",
                            opacity: cancellingId === o.order_id ? 0.5 : 1,
                          }}
                        >
                          {cancellingId === o.order_id ? "..." : "Annullér"}
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
