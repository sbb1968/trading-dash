import { useState, useEffect, useRef } from "react";
import { openUrl } from "@tauri-apps/plugin-opener";

// ── Typer ─────────────────────────────────────────────────────
interface PriceAlert {
  id:             number;
  ticker:         string;
  price:          number;
  change_percent: number;
  direction:      "up" | "down";
  message:        string;
  time:           string;
}

interface NewsItem {
  id:        number;
  ticker:    string;
  headline:  string;
  source:    string;
  time:      string;
  sentiment: "bullish" | "bearish" | "neutral";
  isNew?:    boolean;
}

// ── Hjælpere ──────────────────────────────────────────────────
function pct(v: number) {
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toFixed(2)}%`;
}

function usd(v: number) {
  return v.toLocaleString("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2 });
}

function openNews(headline: string, ticker: string, source: string) {
  const query = encodeURIComponent(`${ticker} ${headline}`);
  const urls: Record<string, string> = {
    "Benzinga":    `https://www.benzinga.com/search?q=${query}`,
    "Reuters":     `https://www.reuters.com/search/news?blob=${query}`,
    "Bloomberg":   `https://www.bloomberg.com/search?query=${query}`,
    "MarketWatch": `https://www.marketwatch.com/search?q=${query}`,
    "Newsfilter":  `https://newsfilter.io/search?query=${query}`,
    "PRNewswire":  `https://www.prnewswire.com/search/news/?keyword=${query}`,
  };
  const url = urls[source] ?? `https://www.google.com/search?q=${query}+stock+news`;
  openUrl(url);
}

// ── Sektions-header ───────────────────────────────────────────
function SectionHeader({ emoji, title, count, filter, setFilter, accentColor, fsh }: {
  emoji: string; title: string; count: number;
  filter: "all" | "watchlist"; setFilter: (f: "all" | "watchlist") => void;
  accentColor: string; fsh: string;
}) {
  return (
    <div style={{
      display: "flex", alignItems: "center", justifyContent: "space-between",
      padding: "8px 10px 6px", borderBottom: "1px solid var(--border-default)",
      background: "var(--bg-elevated)", flexShrink: 0,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6, minWidth: 0 }}>
        <span style={{ fontSize: fsh, fontWeight: 800, color: accentColor, textTransform: "uppercase", letterSpacing: "0.5px", whiteSpace: "nowrap" }}>
          {emoji} {title}
        </span>
        <span style={{ fontSize: fsh, background: "var(--bg-base)", border: "1px solid var(--border-subtle)", borderRadius: 10, padding: "1px 6px", color: "var(--text-secondary)", fontWeight: 600, flexShrink: 0 }}>
          {count}
        </span>
      </div>
      <div style={{ display: "flex", gap: 4, flexShrink: 0, marginLeft: 6 }}>
        {(["all", "watchlist"] as const).map(f => (
          <button key={f} onClick={() => setFilter(f)} style={{
            background:   filter === f ? accentColor : "transparent",
            border:       `1px solid ${filter === f ? accentColor : "var(--border-subtle)"}`,
            borderRadius: 4,
            padding:      "2px 6px",
            fontSize:     10,
            color:        filter === f ? (accentColor === "var(--bull)" ? "#000" : "#fff") : "var(--text-secondary)",
            cursor:       "pointer",
            fontWeight:   filter === f ? 700 : 400,
            whiteSpace:   "nowrap",
          }}>
            {f === "all" ? "Alle" : "WL"}
          </button>
        ))}
      </div>
    </div>
  );
}

// ── Pris-alert række ──────────────────────────────────────────
function AlertRow({ alert, isNew }: { alert: PriceAlert; isNew: boolean }) {
  const up    = alert.direction === "up";
  const color = up ? "var(--bull)" : "var(--bear)";
  const bg    = up ? "rgba(0,200,100,0.06)" : "rgba(255,60,60,0.06)";

  return (
    <tr style={{
      borderBottom: "1px solid var(--border-subtle)",
      background:   isNew ? (up ? "rgba(0,200,100,0.12)" : "rgba(255,60,60,0.12)") : bg,
      transition:   "background 0.6s",
    }}>
      <td style={{ padding: "4px 6px", color: "var(--text-secondary)", fontSize: "var(--fs-content-alerts, 12px)", fontFamily: "monospace", whiteSpace: "nowrap" }}>
        {alert.time}
      </td>
      <td style={{ padding: "4px 6px", fontWeight: 800, fontSize: "var(--fs-content-alerts, 12px)", color, fontFamily: "monospace" }}>
        {alert.ticker}
      </td>
      <td style={{ padding: "4px 6px", fontSize: "var(--fs-content-alerts, 12px)", color: "var(--text-primary)", textAlign: "right", fontFamily: "monospace" }}>
        {usd(alert.price)}
      </td>
      <td style={{ padding: "4px 6px", fontSize: "var(--fs-content-alerts, 12px)", fontWeight: 700, color, textAlign: "right", fontFamily: "monospace", whiteSpace: "nowrap" }}>
        {up ? "▲" : "▼"} {pct(alert.change_percent)}
      </td>
    </tr>
  );
}

// ── News række ────────────────────────────────────────────────
function NewsRow({ item }: { item: NewsItem }) {
  const [hovered, setHovered] = useState(false);

  const sentimentColor =
    item.sentiment === "bullish" ? "var(--bull)" :
    item.sentiment === "bearish" ? "var(--bear)" :
    "var(--text-secondary)";

  return (
    <div style={{
      display:       "flex",
      flexDirection: "column",
      padding:       "6px 10px",
      borderBottom:  "1px solid var(--border-subtle)",
      background:    item.isNew ? "rgba(100,160,255,0.08)" : "transparent",
      transition:    "background 0.6s",
    }}>
      {/* Øverste linje: tid + ticker + kilde */}
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 3 }}>
        <span style={{ fontSize: 11, color: "var(--text-secondary)", fontFamily: "monospace", whiteSpace: "nowrap", flexShrink: 0 }}>
          {item.time}
        </span>
        <span style={{ fontSize: 11, fontWeight: 700, color: sentimentColor, fontFamily: "monospace", flexShrink: 0 }}>
          ● {item.ticker}
        </span>
        <span style={{ fontSize: 10, color: "var(--text-secondary)", marginLeft: "auto", whiteSpace: "nowrap", flexShrink: 0 }}>
          {item.source}
        </span>
      </div>
      {/* Overskrift — wrapper frit over flere linjer */}
      <div
        onClick={() => openNews(item.headline, item.ticker, item.source)}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        title="Klik for at åbne nyhed i browser"
        style={{
          fontSize:       "var(--fs-content-alerts, 12px)",
          color:          hovered ? "var(--accent)" : "var(--text-primary)",
          lineHeight:     1.45,
          cursor:         "pointer",
          textDecoration: hovered ? "underline" : "none",
          transition:     "color 0.15s",
          wordBreak:      "break-word",
        }}
      >
        {item.headline}
      </div>
    </div>
  );
}

// ── Hoved-komponent ───────────────────────────────────────────
export function AlertsPanel({
  alerts, news, selectedTicker, onSelectTicker, watchlist,
  alertThreshold, onThresholdChange,
}: {
  alerts:            PriceAlert[];
  news:              (NewsItem & { isNew?: boolean })[];
  selectedTicker:    string;
  onSelectTicker:    (ticker: string) => void;
  watchlist:         string[];
  alertThreshold:    number;
  onThresholdChange: (v: number) => void;
}) {
  const [alertFilter, setAlertFilter] = useState<"all" | "watchlist">("all");
  const [newsFilter,  setNewsFilter]  = useState<"all" | "watchlist">("all");
  const [newAlertIds, setNewAlertIds] = useState<Set<number>>(new Set());
  const prevAlertCount = useRef(alerts.length);
  const prevNewsCount  = useRef(news.length);
  const alertsRef      = useRef<HTMLDivElement>(null);
  const newsRef        = useRef<HTMLDivElement>(null);

  const fsh = "var(--fs-header-alerts, 11px)";

  // Flash nye alerts
  useEffect(() => {
    if (alerts.length > prevAlertCount.current) {
      const newIds = new Set(
        alerts.slice(0, alerts.length - prevAlertCount.current).map(a => a.id)
      );
      setNewAlertIds(newIds);
      setTimeout(() => setNewAlertIds(new Set()), 1500);
      if (alertsRef.current && alertsRef.current.scrollTop < 60) {
        alertsRef.current.scrollTop = 0;
      }
    }
    prevAlertCount.current = alerts.length;
  }, [alerts.length]);

  // Scroll nyheder til top ved nye
  useEffect(() => {
    if (news.length > prevNewsCount.current) {
      if (newsRef.current && newsRef.current.scrollTop < 60) {
        newsRef.current.scrollTop = 0;
      }
    }
    prevNewsCount.current = news.length;
  }, [news.length]);

  const filteredAlerts = (alertFilter === "watchlist"
    ? alerts.filter(a => watchlist.includes(a.ticker))
    : alerts
  ).slice(0, 30);

  const filteredNews = newsFilter === "watchlist"
    ? news.filter(n => watchlist.includes(n.ticker))
    : news;

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden", background: "var(--bg-base)" }}>

      {/* ══ ØVERSTE SEKTION — Pris-alerts ══════════════════════ */}
      <div style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0, borderBottom: "2px solid var(--border-default)" }}>

        <SectionHeader
          emoji="🔔" title="Pris Alerts" count={filteredAlerts.length}
          filter={alertFilter} setFilter={setAlertFilter}
          accentColor="var(--bull)" fsh={fsh}
        />

        {/* Tærskel-slider */}
        <div style={{
          display: "flex", alignItems: "center", gap: 6,
          padding: "4px 10px", borderBottom: "1px solid var(--border-subtle)",
          flexShrink: 0, background: "var(--bg-elevated)",
        }}>
          <span style={{ fontSize: 10, color: "var(--text-secondary)", whiteSpace: "nowrap" }}>Tærskel</span>
          <input
            type="range" min={0.1} max={5} step={0.1}
            value={alertThreshold}
            onChange={e => onThresholdChange(parseFloat(e.target.value))}
            style={{ flex: 1, accentColor: "var(--accent)", height: 3, cursor: "pointer" }}
          />
          <span style={{ fontSize: 10, color: "var(--text-primary)", fontWeight: 700, whiteSpace: "nowrap", minWidth: 28, textAlign: "right" }}>
            {alertThreshold.toFixed(1)}%
          </span>
        </div>

        <div ref={alertsRef} style={{ flex: 1, overflowY: "auto", minHeight: 0 }}>
          {filteredAlerts.length === 0 ? (
            <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", color: "var(--text-secondary)", fontSize: fsh, fontStyle: "italic", padding: "16px 10px", textAlign: "center" }}>
              {alertFilter === "watchlist" ? "Ingen alerts for watchlist-aktier" : "Overvåger markedet..."}
            </div>
          ) : (
            <table style={{ width: "100%", borderCollapse: "collapse", tableLayout: "fixed" }}>
              <thead style={{ position: "sticky", top: 0, background: "var(--bg-elevated)", zIndex: 1 }}>
                <tr style={{ borderBottom: "1px solid var(--border-default)" }}>
                  <th style={{ padding: "3px 6px", textAlign: "left",  width: "58px", fontSize: fsh, fontWeight: 700, color: "var(--text-primary)", textTransform: "uppercase", letterSpacing: "0.4px" }}>Tid</th>
                  <th style={{ padding: "3px 6px", textAlign: "left",  width: "50px", fontSize: fsh, fontWeight: 700, color: "var(--text-primary)", textTransform: "uppercase", letterSpacing: "0.4px" }}>Ticker</th>
                  <th style={{ padding: "3px 6px", textAlign: "right", width: "68px", fontSize: fsh, fontWeight: 700, color: "var(--text-primary)", textTransform: "uppercase", letterSpacing: "0.4px" }}>Pris</th>
                  <th style={{ padding: "3px 6px", textAlign: "right",               fontSize: fsh, fontWeight: 700, color: "var(--text-primary)", textTransform: "uppercase", letterSpacing: "0.4px" }}>Ændring</th>
                </tr>
              </thead>
              <tbody>
                {filteredAlerts.map(alert => (
                  <AlertRow key={alert.id} alert={alert} isNew={newAlertIds.has(alert.id)} />
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {/* ══ NEDERSTE SEKTION — Nyheder ═════════════════════════ */}
      <div style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>
        <SectionHeader
          emoji="📰" title="Nyheder" count={filteredNews.length}
          filter={newsFilter} setFilter={setNewsFilter}
          accentColor="var(--accent)" fsh={fsh}
        />
        <div ref={newsRef} style={{ flex: 1, overflowY: "auto", minHeight: 0 }}>
          {filteredNews.length === 0 ? (
            <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", color: "var(--text-secondary)", fontSize: fsh, fontStyle: "italic", padding: "16px 10px", textAlign: "center" }}>
              {newsFilter === "watchlist" ? "Ingen nyheder for watchlist-aktier" : "Ingen nyheder endnu"}
            </div>
          ) : (
            filteredNews.map(item => (
              <NewsRow key={item.id} item={item} />
            ))
          )}
        </div>
      </div>

    </div>
  );
}
