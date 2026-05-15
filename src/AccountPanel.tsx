import { useState, useEffect, useRef } from "react";

// ── Typer ─────────────────────────────────────────────────────
interface AccountPosition {
  ticker:     string;
  position:   number;
  avg_cost:   number | null;
  last_price: number | null;
  pnl:        number | null;
  pnl_pct:    number | null;
}

interface AccountSnapshot {
  ok:              boolean;
  error?:          string;
  ibkr_account?:   string;
  paper_trading?:  boolean;
  net_liquidation?: number;
  cash_balance?:   number;
  unrealized_pnl?: number;
  realized_pnl?:   number;
  positions?:      AccountPosition[];
  checked_at?:     string;
}

// ── Hjælpere ──────────────────────────────────────────────────
function fmtMoney(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  const sign = v < 0 ? "-" : "";
  return sign + "$" + Math.abs(v).toLocaleString("da-DK", {
    minimumFractionDigits: 2, maximumFractionDigits: 2
  });
}

function fmtMoneySigned(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  const sign = v >= 0 ? "+" : "-";
  return sign + "$" + Math.abs(v).toLocaleString("da-DK", {
    minimumFractionDigits: 2, maximumFractionDigits: 2
  });
}

function fmtPct(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  const sign = v >= 0 ? "+" : "";
  return sign + v.toFixed(2) + "%";
}

function pnlClass(v: number | null | undefined): string {
  if (v === null || v === undefined || v === 0) return "";
  return v > 0 ? "positive" : "negative";
}

// ── Hoved-komponent ───────────────────────────────────────────
export function AccountPanel({ onSelectTicker }: { onSelectTicker?: (t: string) => void }) {
  const [snapshot,   setSnapshot]   = useState<AccountSnapshot | null>(null);
  const [loading,    setLoading]    = useState(true);
  const [error,      setError]      = useState("");
  const [lastUpdate, setLastUpdate] = useState("");
  const timerRef = useRef<number | null>(null);

  async function fetchSnapshot() {
    try {
      const resp = await fetch("http://127.0.0.1:8000/account/dash-snapshot");
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data: AccountSnapshot = await resp.json();
      setSnapshot(data);
      setLastUpdate(new Date().toLocaleTimeString("da-DK"));
      if (!data.ok) {
        setError(data.error || "Ukendt fejl");
      } else {
        setError("");
      }
    } catch (e) {
      setError("Kunne ikke hente data — er backend kørende?");
    } finally {
      setLoading(false);
    }
  }

  // Initial fetch + auto-refresh hvert 10. sekund
  useEffect(() => {
    fetchSnapshot();
    timerRef.current = window.setInterval(fetchSnapshot, 10_000);
    return () => {
      if (timerRef.current) window.clearInterval(timerRef.current);
    };
  }, []);

  // ── Render ──────────────────────────────────────────────
  const stat = (label: string, value: string, extraCls?: string): React.CSSProperties => ({});

  // Card-styling
  const card: React.CSSProperties = {
    background: "var(--bg-elevated)",
    border: "1px solid var(--border-subtle)",
    borderRadius: 6,
    padding: "10px 12px",
  };

  const statLabel: React.CSSProperties = {
    fontSize: "var(--fs-header-account, 11px)",
    color: "var(--text-muted)",
    textTransform: "uppercase",
    letterSpacing: 0.5,
    marginBottom: 4,
  };

  const statValue: React.CSSProperties = {
    fontSize: "var(--fs-title-account, 18px)",
    fontWeight: 600,
    color: "var(--text-primary)",
  };

  return (
    <div style={{
      padding: 10,
      height: "100%",
      overflow: "auto",
      display: "flex",
      flexDirection: "column",
      gap: 10,
    }}>
      {/* Konto-header */}
      <div style={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        fontSize: "var(--fs-content-account, 12px)",
        color: "var(--text-secondary)",
      }}>
        <div>
          {snapshot?.ok ? (
            <>
              IBKR-konto: <code style={{
                background: "var(--bg-base)",
                padding: "2px 6px",
                borderRadius: 3,
                color: "var(--text-primary)",
              }}>{snapshot.ibkr_account}</code>
              {snapshot.paper_trading && (
                <span style={{
                  marginLeft: 8,
                  padding: "2px 6px",
                  background: "var(--accent)",
                  color: "var(--bg-base)",
                  borderRadius: 3,
                  fontSize: 10,
                  fontWeight: 700,
                }}>PAPER</span>
              )}
            </>
          ) : (
            <span style={{ color: "var(--text-muted)" }}>—</span>
          )}
        </div>
        <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
          {lastUpdate && `Opdateret ${lastUpdate}`}
        </div>
      </div>

      {/* Fejl-banner */}
      {error && (
        <div style={{
          padding: "8px 12px",
          background: "rgba(248, 113, 113, 0.1)",
          border: "1px solid var(--bear)",
          borderRadius: 4,
          color: "var(--bear)",
          fontSize: "var(--fs-content-account, 12px)",
        }}>
          ⚠ {error}
        </div>
      )}

      {/* Loading-tilstand */}
      {loading && !snapshot && (
        <div style={{
          padding: 20,
          textAlign: "center",
          color: "var(--text-muted)",
          fontStyle: "italic",
        }}>
          Indlæser konto-data...
        </div>
      )}

      {/* Stats-grid */}
      {snapshot?.ok && (
        <>
          <div style={{
            display: "grid",
            gridTemplateColumns: "repeat(2, 1fr)",
            gap: 8,
          }}>
            <div style={card}>
              <div style={statLabel}>Net Liquidation</div>
              <div style={statValue}>{fmtMoney(snapshot.net_liquidation)}</div>
            </div>
            <div style={card}>
              <div style={statLabel}>Cash Balance</div>
              <div style={statValue}>{fmtMoney(snapshot.cash_balance)}</div>
            </div>
            <div style={card}>
              <div style={statLabel}>Urealiseret P&L</div>
              <div style={{
                ...statValue,
                color: snapshot.unrealized_pnl && snapshot.unrealized_pnl > 0
                  ? "var(--bull)"
                  : snapshot.unrealized_pnl && snapshot.unrealized_pnl < 0
                  ? "var(--bear)"
                  : "var(--text-primary)",
              }}>
                {fmtMoneySigned(snapshot.unrealized_pnl)}
              </div>
            </div>
            <div style={card}>
              <div style={statLabel}>Realiseret P&L</div>
              <div style={{
                ...statValue,
                color: snapshot.realized_pnl && snapshot.realized_pnl > 0
                  ? "var(--bull)"
                  : snapshot.realized_pnl && snapshot.realized_pnl < 0
                  ? "var(--bear)"
                  : "var(--text-primary)",
              }}>
                {fmtMoneySigned(snapshot.realized_pnl)}
              </div>
            </div>
          </div>

          {/* Positioner */}
          <div style={{
            ...card,
            padding: 0,
            overflow: "hidden",
          }}>
            <div style={{
              padding: "8px 12px",
              fontSize: "var(--fs-header-account, 11px)",
              color: "var(--text-muted)",
              textTransform: "uppercase",
              letterSpacing: 0.5,
              borderBottom: "1px solid var(--border-subtle)",
            }}>
              Åbne positioner ({snapshot.positions?.length ?? 0})
            </div>

            {(snapshot.positions?.length ?? 0) === 0 ? (
              <div style={{
                padding: 16,
                textAlign: "center",
                color: "var(--text-muted)",
                fontStyle: "italic",
                fontSize: "var(--fs-content-account, 12px)",
              }}>
                Ingen åbne positioner
              </div>
            ) : (
              <table className="scanner-table" style={{ width: "100%" }}>
                <thead>
                  <tr>
                    <th>Ticker</th>
                    <th style={{ textAlign: "right" }}>Side</th>
                    <th style={{ textAlign: "right" }}>Antal</th>
                    <th style={{ textAlign: "right" }}>Avg Cost</th>
                    <th style={{ textAlign: "right" }}>Sidste Pris</th>
                    <th style={{ textAlign: "right" }}>P&L</th>
                    <th style={{ textAlign: "right" }}>%</th>
                  </tr>
                </thead>
                <tbody>
                  {snapshot.positions?.map(p => (
                    <tr
                      key={p.ticker}
                      style={{ cursor: onSelectTicker ? "pointer" : "default" }}
                      onClick={() => onSelectTicker?.(p.ticker)}
                    >
                      <td><strong>{p.ticker}</strong></td>
                      <td style={{ textAlign: "right" }}>
                        {p.position > 0 ? "Long" : p.position < 0 ? "Short" : "—"}
                      </td>
                      <td style={{ textAlign: "right" }}>
                        {Math.abs(p.position).toLocaleString("da-DK")}
                      </td>
                      <td style={{ textAlign: "right" }}>{fmtMoney(p.avg_cost)}</td>
                      <td style={{ textAlign: "right" }}>{fmtMoney(p.last_price)}</td>
                      <td
                        style={{ textAlign: "right" }}
                        className={pnlClass(p.pnl)}
                      >
                        {fmtMoneySigned(p.pnl)}
                      </td>
                      <td
                        style={{ textAlign: "right" }}
                        className={pnlClass(p.pnl_pct)}
                      >
                        {fmtPct(p.pnl_pct)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </>
      )}
    </div>
  );
}
