import { useState, useEffect, useRef } from "react";

// ── Typer ─────────────────────────────────────────────────────
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
    "UNKNOWN":        "Status ukendt",
  };
  return map[status] || status;
}

// ── Hoved-komponent ───────────────────────────────────────────
export function OrdersWindow() {
  const [orders, setOrders]       = useState<OrderEntry[]>([]);
  const [loading, setLoading]     = useState(true);
  const [error, setError]         = useState("");
  const [periodHours, setPeriodHours] = useState<number>(() => {
    const saved = localStorage.getItem("orders_period_hours");
    return saved ? parseInt(saved) : 24;
  });
  const [lastUpdate, setLastUpdate] = useState("");
  const [cancellingId, setCancellingId] = useState<number | null>(null);
  const timerRef = useRef<number | null>(null);

  // Hvilke status-grupper skal vises? Iben kan toggle med badges øverst.
  const [showOpen, setShowOpen]           = useState<boolean>(() =>
    localStorage.getItem("orders_show_open") !== "false");
  const [showFilled, setShowFilled]       = useState<boolean>(() =>
    localStorage.getItem("orders_show_filled") !== "false");
  const [showCancelled, setShowCancelled] = useState<boolean>(() =>
    localStorage.getItem("orders_show_cancelled") !== "false");

  useEffect(() => { localStorage.setItem("orders_show_open",      String(showOpen)); },      [showOpen]);
  useEffect(() => { localStorage.setItem("orders_show_filled",    String(showFilled)); },    [showFilled]);
  useEffect(() => { localStorage.setItem("orders_show_cancelled", String(showCancelled)); }, [showCancelled]);

  // Gem periode-valg
  useEffect(() => {
    localStorage.setItem("orders_period_hours", String(periodHours));
  }, [periodHours]);

  async function fetchOrders() {
    try {
      const resp = await fetch(
        `http://127.0.0.1:8000/orders/list?period_hours=${periodHours}`
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
  useEffect(() => {
    fetchOrders();
    timerRef.current = window.setInterval(fetchOrders, 2_000);
    return () => {
      if (timerRef.current) window.clearInterval(timerRef.current);
    };
  }, [periodHours]);

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

  // Filtrer ordrer baseret på Iben's valg
  const visibleOrders = orders.filter(o => {
    if (o.status_group === "open"      && !showOpen)      return false;
    if (o.status_group === "filled"    && !showFilled)    return false;
    if (o.status_group === "cancelled" && !showCancelled) return false;
    if (o.status_group === "unknown"   && !showOpen && !showFilled && !showCancelled) return false;
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
            <option value={24}>I dag (24 timer)</option>
            <option value={72}>Sidste 3 dage</option>
            <option value={168}>Sidste 7 dage</option>
            <option value={720}>Sidste 30 dage</option>
          </select>

          <div style={{ marginLeft: 10, display: "flex", gap: 6 }}>
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
      {!loading && orders.length === 0 && !error && (
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
      {!loading && orders.length > 0 && visibleOrders.length === 0 && (
        <div style={{
          padding: 20,
          textAlign: "center",
          color: "var(--text-muted)",
          fontStyle: "italic",
        }}>
          Ingen ordrer matcher det valgte filter — klik på et badge for at vise flere
        </div>
      )}

      {/* ── Tabel ── */}
      {visibleOrders.length > 0 && (
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
                    <td style={{ color: statusColor(o.status_group), fontWeight: 600 }}>
                      {statusEmoji(o.status_group)} {statusText(o.status)}
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
