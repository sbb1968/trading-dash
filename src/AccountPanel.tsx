import { useState, useEffect, useRef, type CSSProperties } from "react";

// ── Typer ─────────────────────────────────────────────────────
interface AccountPosition {
  ticker:        string;
  position:      number;
  avg_cost:      number | null;
  multiplier:    number | null;
  sec_type:      string | null;
  last_price:    number | null;
  current_price: number | null;
  market_value:  number | null;
  pnl:           number | null;
  pnl_pct:       number | null;
}

interface RiskStrategy { strategy: string; pnl_today: number; limit: number | null; }
interface RiskInfo {
  total_pnl_today:  number;
  daily_loss_limit: number;
  per_strategy:     RiskStrategy[];
}

interface AccountSnapshot {
  ok:                    boolean;
  error?:                string;
  ibkr_account?:         string;
  paper_trading?:        boolean;
  net_liquidation?:      number;
  cash_balance?:         number;
  unrealized_pnl?:       number;
  realized_pnl?:         number;
  buying_power?:         number;
  available_funds?:      number;
  excess_liquidity?:     number;
  maint_margin?:         number;
  gross_position_value?: number;
  positions?:            AccountPosition[];
  risk?:                 RiskInfo;
  checked_at?:           string;
}

// ── Hjælpere ──────────────────────────────────────────────────
function fmtMoney(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  const sign = v < 0 ? "-" : "";
  return sign + "$" + Math.abs(v).toLocaleString("da-DK", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
function fmtMoneySigned(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  const sign = v >= 0 ? "+" : "-";
  return sign + "$" + Math.abs(v).toLocaleString("da-DK", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
function fmtPct(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return (v >= 0 ? "+" : "") + v.toFixed(2) + "%";
}
function pnlClass(v: number | null | undefined): string {
  if (v === null || v === undefined || v === 0) return "";
  return v > 0 ? "positive" : "negative";
}
function pnlColor(v: number | null | undefined): string {
  if (v === null || v === undefined || v === 0) return "var(--text-primary)";
  return v > 0 ? "var(--bull)" : "var(--bear)";
}

const card: CSSProperties = {
  background: "var(--bg-elevated)", border: "1px solid var(--border-subtle)",
  borderRadius: 6, padding: "10px 12px",
};
const statLabel: CSSProperties = {
  fontSize: "var(--fs-header-account, 11px)", color: "var(--text-muted)",
  textTransform: "uppercase", letterSpacing: 0.5, marginBottom: 4,
};
const statValue: CSSProperties = {
  fontSize: "var(--fs-title-account, 18px)", fontWeight: 600, color: "var(--text-primary)",
};

function Stat({ label, value, color, accent }: { label: string; value: string; color?: string; accent?: boolean }) {
  return (
    <div style={{ ...card, ...(accent ? { borderColor: "var(--accent)" } : {}) }}>
      <div style={statLabel}>{label}</div>
      <div style={{ ...statValue, ...(color ? { color } : {}) }}>{value}</div>
    </div>
  );
}

// ── Risiko-sektion ────────────────────────────────────────────
function RiskSection({ risk }: { risk: RiskInfo }) {
  const rows = risk.per_strategy ?? [];
  return (
    <div style={{ ...card, padding: 0, overflow: "hidden" }}>
      <div style={{
        padding: "8px 12px", fontSize: "var(--fs-header-account, 11px)", color: "var(--text-muted)",
        textTransform: "uppercase", letterSpacing: 0.5, borderBottom: "1px solid var(--border-subtle)",
        display: "flex", justifyContent: "space-between",
      }}>
        <span>Risiko — dagens tab mod grænse</span>
        <span style={{ textTransform: "none" }}>Konto-bagstopper: {fmtMoney(risk.daily_loss_limit)}</span>
      </div>

      {rows.length === 0 ? (
        <div style={{ padding: 16, textAlign: "center", color: "var(--text-muted)", fontStyle: "italic", fontSize: "var(--fs-content-account, 12px)" }}>
          Ingen strategi har handlet i dag.
        </div>
      ) : (
        <div style={{ padding: "8px 12px", display: "flex", flexDirection: "column", gap: 10 }}>
          {rows.map(r => {
            const loss = r.pnl_today < 0 ? -r.pnl_today : 0;          // kun tab forbruger graensen
            const frac = r.limit && r.limit > 0 ? Math.min(loss / r.limit, 1) : 0;
            const near = frac >= 0.8;
            const barColor = near ? "var(--bear)" : frac >= 0.5 ? "#d97706" : "var(--bull)";
            return (
              <div key={r.strategy}>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: "var(--fs-content-account, 12px)", marginBottom: 3 }}>
                  <span style={{ fontWeight: 700, color: "var(--text-primary)" }}>{r.strategy}</span>
                  <span style={{ color: "var(--text-secondary)" }}>
                    dagens P&L: <span style={{ color: pnlColor(r.pnl_today), fontWeight: 600 }}>{fmtMoneySigned(r.pnl_today)}</span>
                    {r.limit != null && <> / grænse −{fmtMoney(r.limit)}</>}
                  </span>
                </div>
                {r.limit != null && (
                  <div style={{ height: 6, background: "var(--bg-base)", borderRadius: 3, overflow: "hidden" }}>
                    <div style={{ width: `${frac * 100}%`, height: "100%", background: barColor, transition: "width 0.3s" }} />
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ── Hoved-komponent ───────────────────────────────────────────
export function AccountPanel({ onSelectTicker }: { onSelectTicker?: (t: string) => void }) {
  const [snapshot,   setSnapshot]   = useState<AccountSnapshot | null>(null);
  const [loading,    setLoading]    = useState(true);
  const [error,      setError]      = useState("");
  const [lastUpdate, setLastUpdate] = useState("");
  const [datoer,     setDatoer]     = useState<Record<string, string>>({});
  const timerRef = useRef<number | null>(null);

  async function fetchSnapshot() {
    try {
      const resp = await fetch("http://127.0.0.1:8000/account/dash-snapshot");
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data: AccountSnapshot = await resp.json();
      setSnapshot(data);
      setLastUpdate(new Date().toLocaleTimeString("da-DK"));
      setError(data.ok ? "" : (data.error || "Ukendt fejl"));
    } catch {
      setError("Kunne ikke hente data — er backend kørende?");
    } finally {
      setLoading(false);
    }
  }

  // Anskaffelsesdatoen kommer fra VORES journal — IBKR-positioner bærer den ikke.
  // Mangler datoen, er positionen ejerløs, og tomme felt ER selve oplysningen.
  // Otte sådanne lå på algoserveren i en uge uden at nogen opdagede det.
  async function fetchDatoer() {
    try {
      const resp = await fetch("http://127.0.0.1:8000/journal/open-positions");
      if (!resp.ok) return;
      const d = await resp.json();
      const ud: Record<string, string> = {};
      for (const p of (d.positions ?? d ?? [])) {
        const sym = String(p.symbol ?? "").toUpperCase();
        const t = p.entry_time_et ?? p.entry_time_utc;
        if (sym && t && !ud[sym]) ud[sym] = String(t);
      }
      setDatoer(ud);
    } catch { /* uden datoer viser vi stadig positionerne */ }
  }

  useEffect(() => {
    const hent = () => { fetchSnapshot(); fetchDatoer(); };
    hent();
    timerRef.current = window.setInterval(hent, 10_000);
    return () => { if (timerRef.current) window.clearInterval(timerRef.current); };
  }, []);

  const dagensResultat = (snapshot?.unrealized_pnl ?? 0) + (snapshot?.realized_pnl ?? 0);

  return (
    <div style={{ padding: 10, height: "100%", overflow: "auto", display: "flex", flexDirection: "column", gap: 10 }}>
      {/* Konto-header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", fontSize: "var(--fs-content-account, 12px)", color: "var(--text-secondary)" }}>
        <div>
          {snapshot?.ok ? (
            <>
              IBKR-konto: <code style={{ background: "var(--bg-base)", padding: "2px 6px", borderRadius: 3, color: "var(--text-primary)" }}>{snapshot.ibkr_account}</code>
              {/* LIVE skal skrige. Paper er hverdagen; en live-konto er undtagelsen
                  man skal opdage UDEN at lede efter den. Derfor eget mærke — ikke
                  bare fraværet af "PAPIR". */}
              {snapshot.paper_trading === false ? (
                <span style={{ marginLeft: 8, padding: "2px 7px", background: "var(--bear)", color: "#fff", borderRadius: 3, fontSize: 10, fontWeight: 800, letterSpacing: 0.5 }}>● LIVE — RIGTIGE PENGE</span>
              ) : (
                <span style={{ marginLeft: 8, padding: "2px 6px", background: "var(--accent)", color: "#fff", borderRadius: 3, fontSize: 10, fontWeight: 700 }}>PAPIR</span>
              )}
            </>
          ) : (
            <span style={{ color: "var(--text-muted)" }}>—</span>
          )}
        </div>
        <div style={{ fontSize: 11, color: "var(--text-muted)" }}>{lastUpdate && `Opdateret ${lastUpdate}`}</div>
      </div>

      {error && (
        <div style={{ padding: "8px 12px", background: "rgba(248, 113, 113, 0.1)", border: "1px solid var(--bear)", borderRadius: 4, color: "var(--bear)", fontSize: "var(--fs-content-account, 12px)" }}>
          ⚠ {error}
        </div>
      )}

      {loading && !snapshot && (
        <div style={{ padding: 20, textAlign: "center", color: "var(--text-muted)", fontStyle: "italic" }}>Indlæser konto-data...</div>
      )}

      {snapshot?.ok && (
        <>
          {/* Dagens resultat — det tal der betyder noget intradag */}
          <div style={{ ...card, textAlign: "center", padding: "12px" }}>
            <div style={statLabel}>Dagens resultat (urealiseret + realiseret)</div>
            <div style={{ fontSize: "calc(var(--fs-title-account, 18px) * 1.6)", fontWeight: 800, color: pnlColor(dagensResultat) }}>
              {fmtMoneySigned(dagensResultat)}
            </div>
          </div>

          {/* Nøgletal */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: 8 }}>
            <Stat label="Kontoværdi" value={fmtMoney(snapshot.net_liquidation)} />
            <Stat label="Købekraft" value={fmtMoney(snapshot.buying_power)} accent />
            <Stat label="Kontantbeholdning" value={fmtMoney(snapshot.cash_balance)} />
            <Stat label="Tilgængelige midler" value={fmtMoney(snapshot.available_funds)} />
            <Stat label="Overskydende likviditet" value={fmtMoney(snapshot.excess_liquidity)} />
            <Stat label="Vedligeholdelsesmargin" value={fmtMoney(snapshot.maint_margin)} />
            <Stat label="Urealiseret P&L" value={fmtMoneySigned(snapshot.unrealized_pnl)} color={pnlColor(snapshot.unrealized_pnl)} />
            <Stat label="Realiseret P&L" value={fmtMoneySigned(snapshot.realized_pnl)} color={pnlColor(snapshot.realized_pnl)} />
          </div>

          {/* Risiko */}
          {snapshot.risk && <RiskSection risk={snapshot.risk} />}

          {/* Positioner */}
          <div style={{ ...card, padding: 0, overflow: "hidden" }}>
            <div style={{ padding: "8px 12px", fontSize: "var(--fs-header-account, 11px)", color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: 0.5, borderBottom: "1px solid var(--border-subtle)" }}>
              Åbne positioner ({snapshot.positions?.length ?? 0})
            </div>

            {(snapshot.positions?.length ?? 0) === 0 ? (
              <div style={{ padding: 16, textAlign: "center", color: "var(--text-muted)", fontStyle: "italic", fontSize: "var(--fs-content-account, 12px)" }}>
                Ingen åbne positioner
              </div>
            ) : (
              <table className="scanner-table" style={{ width: "100%" }}>
                <thead>
                  <tr>
                    <th>Dato</th>
                    <th>Ticker</th>
                    <th>Type</th>
                    <th style={{ textAlign: "right" }}>Antal</th>
                    <th style={{ textAlign: "right" }}>Gns. kurs</th>
                    <th style={{ textAlign: "right" }}>Aktuel kurs</th>
                    <th style={{ textAlign: "right" }}>Markedsværdi</th>
                    <th style={{ textAlign: "right" }}>P&L</th>
                    <th style={{ textAlign: "right" }}>P&L%</th>
                  </tr>
                </thead>
                <tbody>
                  {snapshot.positions?.map(p => {
                    const t = datoer[p.ticker.toUpperCase()];
                    const mult = Number(p.multiplier) || 1;
                    return (
                    <tr key={p.ticker} style={{ cursor: onSelectTicker ? "pointer" : "default" }} onClick={() => onSelectTicker?.(p.ticker)}>
                      <td title={t ? t.slice(0, 19).replace("T", " ") : "Ingen åben journal-række — ejerløs position"}
                          style={t ? undefined : { color: "var(--text-muted)" }}>
                        {t ? `${t.slice(8, 10)}-${t.slice(5, 7)}` : "—"}
                      </td>
                      <td><strong>{p.ticker}</strong></td>
                      {/* Multiplikatoren vist, for uden den ser 1 MES ud som en
                          ubetydelig position ved siden af 100 aktier. */}
                      <td>{p.sec_type || "—"}
                        {p.sec_type === "FUT" && <span style={{ color: "var(--text-muted)" }}> ×{mult}</span>}
                      </td>
                      <td style={{ textAlign: "right", color: p.position < 0 ? "var(--bear)" : "var(--text-primary)" }}>
                        {p.position.toLocaleString("da-DK")}
                      </td>
                      <td style={{ textAlign: "right" }}>{fmtMoney(p.avg_cost)}</td>
                      <td style={{ textAlign: "right" }}>{fmtMoney(p.current_price)}</td>
                      <td style={{ textAlign: "right" }}>{fmtMoney(p.market_value)}</td>
                      <td style={{ textAlign: "right" }} className={pnlClass(p.pnl)}>{fmtMoneySigned(p.pnl)}</td>
                      <td style={{ textAlign: "right" }} className={pnlClass(p.pnl_pct)}>{fmtPct(p.pnl_pct)}</td>
                    </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>
        </>
      )}
    </div>
  );
}
