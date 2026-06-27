import { useEffect, useState, useCallback } from "react";

// Sektor- & niche-overblik. Henter /sectors/overview (TradingView ETF-data):
// de 11 SPDR-sektorer med ydeevne nu/1U/1M + AUM-andel (summerer til 100), og
// pr. sektor en niche-underinddeling hvor %'en er nichens andel INDEN FOR sektoren.

const API = "http://127.0.0.1:8000/sectors/overview";
const REFRESH_MS = 60_000;
const NAME_W = 340;   // fast bredde paa navne-kolonnen, saa tallene staar LIGE efter teksten
                      // (ikke skubbet ud til hoejre kant) og kolonnerne flugter paa tvaers af raekker

interface Niche {
  ticker: string; tickers: string[]; label: string;
  change: number | null; perf_w: number | null; perf_m: number | null;
  aum: number | null; price: number | null; pct: number | null;
}
interface Sector extends Niche { key: string; name: string; emoji: string; niches: Niche[]; }
interface Overview {
  sectors: Sector[]; cross_cutting: Niche[];
  pct_basis: string; missing: string[]; ok: boolean; cached?: boolean;
}

function pct(v: number | null): string {
  return v === null || v === undefined ? "n/a" : `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}
function pctColor(v: number | null): string {
  if (v === null || v === undefined) return "var(--text-muted)";
  return v >= 0 ? "var(--bull)" : "var(--bear)";
}
function shareStr(v: number | null): string {
  return v === null || v === undefined ? "—" : `${v.toFixed(1)}%`;
}

export function SectorNiche({ onSelectTicker }: { onSelectTicker?: (t: string) => void }) {
  const [data, setData] = useState<Overview | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [updated, setUpdated] = useState<string>("");

  const load = useCallback(async (force: boolean) => {
    setLoading(true); setError(null);
    try {
      const r = await fetch(API + (force ? "?force=true" : ""));
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j: Overview = await r.json();
      setData(j);
      setUpdated(new Date().toLocaleTimeString("da-DK"));
    } catch (e: any) {
      setError(e?.message || "Kunne ikke hente sektor-data");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load(false);
    const iv = setInterval(() => load(false), REFRESH_MS);
    return () => clearInterval(iv);
  }, [load]);

  const toggle = (key: string) =>
    setExpanded(prev => {
      const n = new Set(prev);
      n.has(key) ? n.delete(key) : n.add(key);
      return n;
    });

  const cell = (v: number | null, w = 64): JSX.Element => (
    <span style={{ display: "inline-block", width: w, textAlign: "right",
      color: pctColor(v), fontVariantNumeric: "tabular-nums" }}>{pct(v)}</span>
  );

  const tickerBtn = (t: string) => (
    <span
      onClick={() => onSelectTicker?.(t)}
      style={{ cursor: onSelectTicker ? "pointer" : "default", fontWeight: 700,
        color: "var(--text-primary)" }}
      title={onSelectTicker ? `Vis ${t}` : undefined}
    >{t}</span>
  );

  return (
    <div style={{ height: "100%", overflow: "auto", background: "var(--bg-base)",
      color: "var(--text-primary)", fontSize: "var(--fs-content-scanner, 13px)" }}>
      {/* Header */}
      <div style={{ position: "sticky", top: 0, zIndex: 2, display: "flex",
        alignItems: "center", justifyContent: "space-between", gap: 8, padding: "8px 12px",
        background: "var(--bg-elevated)", borderBottom: "1px solid var(--border-subtle)" }}>
        <div style={{ fontWeight: 700 }}>
          Sektorer & nicher
          <span style={{ color: "var(--text-muted)", fontWeight: 400, marginLeft: 8, fontSize: "0.85em" }}>
            AUM-andel · ydeevne nu / 1U / 1M
          </span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          {updated && <span style={{ color: "var(--text-muted)", fontSize: "0.8em" }}>
            {data?.cached ? "cache " : ""}{updated}</span>}
          <button onClick={() => load(true)} disabled={loading}
            style={{ cursor: "pointer", padding: "3px 10px", borderRadius: 5,
              border: "1px solid var(--border-subtle)", background: "var(--bg-base)",
              color: "var(--text-primary)" }}>
            {loading ? "…" : "↻ Opdatér"}
          </button>
        </div>
      </div>

      {error && <div style={{ padding: 12, color: "var(--bear)" }}>⚠ {error}
        — er backenden startet?</div>}
      {!data && !error && <div style={{ padding: 12, color: "var(--text-muted)" }}>Henter…</div>}

      {data && (
        <div style={{ padding: "6px 0" }}>
          {/* Kolonne-header */}
          <div style={{ display: "flex", alignItems: "center", padding: "2px 12px",
            color: "var(--text-muted)", fontSize: "0.78em", textTransform: "uppercase",
            letterSpacing: "0.5px" }}>
            <span style={{ width: NAME_W }}>Sektor</span>
            <span style={{ width: 56, textAlign: "right" }}>Andel</span>
            <span style={{ width: 64, textAlign: "right" }}>Nu</span>
            <span style={{ width: 64, textAlign: "right" }}>1U</span>
            <span style={{ width: 64, textAlign: "right" }}>1M</span>
            <span style={{ width: 16 }} />
          </div>

          {data.sectors.map(s => {
            const open = expanded.has(s.key);
            return (
              <div key={s.key}>
                {/* Sektor-række */}
                <div onClick={() => toggle(s.key)}
                  style={{ display: "flex", alignItems: "center", padding: "6px 12px",
                    cursor: "pointer", borderBottom: "1px solid var(--border-subtle)",
                    background: open ? "var(--bg-elevated)" : "transparent" }}>
                  <span style={{ width: NAME_W, display: "flex", alignItems: "center", gap: 8,
                    minWidth: 0, boxSizing: "border-box" }}>
                    <span style={{ width: 12, color: "var(--text-muted)" }}>{open ? "▾" : "▸"}</span>
                    <span>{s.emoji}</span>
                    <span style={{ fontWeight: 600, whiteSpace: "nowrap", overflow: "hidden",
                      textOverflow: "ellipsis" }}>{s.name}</span>
                    <span style={{ color: "var(--text-muted)", fontSize: "0.85em" }}>{s.key}</span>
                  </span>
                  {/* Andel m. bar */}
                  <span style={{ width: 56, textAlign: "right", fontVariantNumeric: "tabular-nums",
                    fontWeight: 600 }}>{shareStr(s.pct)}</span>
                  {cell(s.change)} {cell(s.perf_w)} {cell(s.perf_m)}
                  <span style={{ width: 16 }} />
                </div>
                {/* Andels-bar (tynd, under rækken) */}
                <div style={{ height: 2, background: "var(--bg-elevated)" }}>
                  <div style={{ height: 2, width: `${s.pct ?? 0}%`,
                    background: "var(--accent, #3b82f6)" }} />
                </div>

                {/* Niche-underinddeling */}
                {open && s.niches.map(n => (
                  <div key={s.key + n.ticker + n.label}
                    style={{ display: "flex", alignItems: "center", padding: "4px 12px",
                      borderBottom: "1px solid var(--border-subtle)",
                      background: "var(--bg-base)", fontSize: "0.95em" }}>
                    <span style={{ width: NAME_W, paddingLeft: 18, display: "flex",
                      alignItems: "center", gap: 6, minWidth: 0, boxSizing: "border-box" }}>
                      <span style={{ minWidth: 78 }}>
                        {n.tickers.map((t, i) => (
                          <span key={t}>{i > 0 && <span style={{ color: "var(--text-muted)" }}>/</span>}{tickerBtn(t)}</span>
                        ))}
                      </span>
                      <span style={{ color: "var(--text-muted)", whiteSpace: "nowrap",
                        overflow: "hidden", textOverflow: "ellipsis" }}>{n.label}</span>
                    </span>
                    <span style={{ width: 56, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                      {shareStr(n.pct)}</span>
                    {cell(n.change)} {cell(n.perf_w)} {cell(n.perf_m)}
                    <span style={{ width: 16 }} />
                  </div>
                ))}
              </div>
            );
          })}

          {/* Tværgående temaer */}
          {data.cross_cutting?.length > 0 && (
            <div style={{ marginTop: 10 }}>
              <div style={{ padding: "4px 12px", color: "var(--text-muted)",
                fontSize: "0.78em", textTransform: "uppercase", letterSpacing: "0.5px" }}>
                🌐 Ægte tværgående (ingen primær sektor)
              </div>
              {data.cross_cutting.map(n => (
                <div key={"cc" + n.ticker}
                  style={{ display: "flex", alignItems: "center", padding: "4px 12px",
                    borderBottom: "1px solid var(--border-subtle)" }}>
                  <span style={{ width: NAME_W, display: "flex", gap: 6, alignItems: "center",
                    minWidth: 0, boxSizing: "border-box" }}>
                    <span style={{ minWidth: 78 }}>
                      {n.tickers.map((t, i) => (
                        <span key={t}>{i > 0 && <span style={{ color: "var(--text-muted)" }}>/</span>}{tickerBtn(t)}</span>
                      ))}
                    </span>
                    <span style={{ color: "var(--text-muted)" }}>{n.label}</span>
                  </span>
                  <span style={{ width: 56 }} />
                  {cell(n.change)} {cell(n.perf_w)} {cell(n.perf_m)}
                  <span style={{ width: 16 }} />
                </div>
              ))}
            </div>
          )}

          {data.missing?.length > 0 && (
            <div style={{ padding: "8px 12px", color: "var(--text-muted)", fontSize: "0.8em" }}>
              Ingen TV-data for: {data.missing.join(", ")} (uddøde/illikvide ETF'er — udeladt af %)
            </div>
          )}
          <div style={{ padding: "6px 12px", color: "var(--text-muted)", fontSize: "0.75em" }}>
            Kilde: TradingView (ETF'er, ~15 min forsinket) · andel = AUM · klik en sektor for nicher
          </div>
        </div>
      )}
    </div>
  );
}
