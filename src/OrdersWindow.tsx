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

// ── Hjælpere ──────────────────────────────────────────────────
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

function usd(v: number | null | undefined): string {
  if (v == null || !isFinite(v)) return "—";
  return (v < 0 ? "-$" : "$") + Math.abs(v).toFixed(2);
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
          <div style={{ display: "flex", gap: 0, marginRight: 4 }}>
            {([["ordrer", "Ordrer"], ["handler", "Handler"]] as [Fane, string][])
              .map(([v, navn], i) => (
              <span key={v} onClick={() => setFane(v)}
                style={{
                  padding: "3px 12px", fontSize: 11, fontWeight: 700,
                  cursor: "pointer", userSelect: "none",
                  border: "1px solid var(--border-default)",
                  borderRightWidth: i === 0 ? 0 : 1,
                  borderRadius: i === 0 ? "3px 0 0 3px" : "0 3px 3px 0",
                  background: fane === v ? "var(--accent, #4a9eff)22" : "transparent",
                  borderColor: fane === v ? "var(--accent, #4a9eff)" : "var(--border-default)",
                  color: fane === v ? "var(--accent, #4a9eff)" : "var(--text-muted)",
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
            <table className="scanner-table" style={{ width: "100%" }}>
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
                  <th style={{ textAlign: "left" }}>Konto</th>
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
                      <td style={{ fontSize: 10, color: "var(--text-muted)" }}>
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
          <table className="scanner-table" style={{ width: "100%" }}>
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
                <th style={{ textAlign: "right" }}>Snit pris</th>
                <th style={{ textAlign: "center" }}></th>
              </tr>
            </thead>
            <tbody>
              {visibleOrders.map(o => {
                const isOpen = o.status_group === "open";
                const sideColor = o.action === "BUY" ? "var(--bull)" : "var(--bear)";
                return (
                  <tr key={o.order_id}>
                    <td style={{ color: "var(--text-secondary)", whiteSpace: "nowrap" }}>
                      <div>{fmtTime(o.placed_at)}</div>
                      <div style={{ fontSize: 10, color: "var(--text-muted)" }}>
                        {fmtDate(o.placed_at)}
                      </div>
                    </td>
                    <td style={{ color: "var(--text-muted)", fontSize: 11 }}>
                      {sourceLabel(o.source)}
                    </td>
                    <td>
                      <strong>{o.ticker}</strong>
                    </td>
                    <td style={{ textAlign: "center", color: sideColor, fontWeight: 700 }}>
                      {o.action}
                    </td>
                    <td style={{ textAlign: "right" }}>{o.shares.toLocaleString("da-DK")}</td>
                    <td style={{ textAlign: "center", color: "var(--text-muted)", fontSize: 11 }}>
                      {o.order_type}
                      {o.limit_price ? ` @ $${o.limit_price.toFixed(2)}` : ""}
                    </td>
                    <td style={{ color: statusColor(o.status_group), fontWeight: 600 }}
                        title={o.note || undefined}>
                      {statusEmoji(o.status_group)} {statusText(o.status)}
                      {o.note && <span style={{ marginLeft: 4, cursor: "help",
                                                color: "var(--text-muted)" }}>ⓘ</span>}
                    </td>
                    <td style={{ textAlign: "right" }}>
                      {o.filled > 0
                        ? `${o.filled}${o.remaining > 0 ? ` / ${o.filled + o.remaining}` : ""}`
                        : "—"}
                    </td>
                    <td style={{ textAlign: "right" }}>
                      {o.avg_fill > 0 ? `$${o.avg_fill.toFixed(2)}` : "—"}
                    </td>
                    <td style={{ textAlign: "center" }}>
                      {isOpen && (
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
