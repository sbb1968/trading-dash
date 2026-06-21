import { useMemo, useRef, useState, useEffect } from "react";

// Selvstaendige typer (struktur-kompatible med SwingReports chart-felt).
interface ChartLevel { price: number; kind: string; touches: number; size: string; }
interface ChartCandle { name: string; when: string; }
interface ChartData {
  current_price: number; structure: string;
  nearest_support: ChartLevel | null; nearest_resistance: ChartLevel | null;
  levels: ChartLevel[]; candles: ChartCandle[];
  swing_high: { t: string; price: number } | null;
  swing_low: { t: string; price: number } | null;
  bars: { t: string; o: number; h: number; l: number; c: number }[];
  pattern_needs_human: boolean;
}

// Charten renderes i RIGTIGE pixels: viewBox = maalt bredde x fast hoejde, saa
// skrift/streger/staenger har literal stoerrelse og ikke skaleres uniformt.
const CHART_H = 440;
const TOP = 28, BOTM = 52, XL = 14, GUTTER = 62;
const PILL_H = 22, GAP = 8;
const MONTHS_DA = ["jan", "feb", "mar", "apr", "maj", "jun", "jul", "aug", "sep", "okt", "nov", "dec"];

const pillW = (t: string) => t.length * 7 + 18;
const structureColor = (s: string) =>
  s.startsWith("HH/HL") ? "var(--bull)" : s.startsWith("LH/LL") ? "var(--bear)" : "var(--neutral)";

interface PlacedPill { kind: string; text: string; x0: number; x1: number; y0: number; y1: number; ax: number; ay: number; color: string; }

function resolveLabels(
  labels: { kind: string; text: string; cx: number; anchorY: number; w: number; ax: number; ay: number; color: string }[],
  dir: "above" | "below",
  bounds: { minX: number; maxX: number; minY: number; maxY: number },
): PlacedPill[] {
  const out: PlacedPill[] = [];
  const sorted = labels.slice().sort((a, b) => a.cx - b.cx);
  for (const lab of sorted) {
    const w = lab.w;
    const x0 = Math.max(bounds.minX, Math.min(lab.cx - w / 2, bounds.maxX - w));
    const x1 = x0 + w;
    let y0 = dir === "above" ? lab.anchorY - GAP - PILL_H : lab.anchorY + GAP;
    const overlaps = (yy: number) => out.some(p => x0 < p.x1 && x1 > p.x0 && yy < p.y1 && (yy + PILL_H) > p.y0);
    let guard = 0;
    while (overlaps(y0) && guard++ < 50) {
      const bl = out.filter(p => x0 < p.x1 && x1 > p.x0);
      y0 = dir === "above" ? Math.min(...bl.map(p => p.y0)) - PILL_H - 4 : Math.max(...bl.map(p => p.y1)) + 4;
    }
    y0 = Math.max(bounds.minY, Math.min(y0, bounds.maxY - PILL_H));
    out.push({ kind: lab.kind, text: lab.text, x0, x1, y0, y1: y0 + PILL_H, ax: lab.ax, ay: lab.ay, color: lab.color });
  }
  return out;
}

function buildLayout(chart: ChartData, W: number, H: number) {
  const BOT = H - BOTM, xr = W - GUTTER;
  const bars = chart.bars;
  const n = bars.length;
  let lo = Infinity, hi = -Infinity;
  for (const b of bars) { lo = Math.min(lo, b.l); hi = Math.max(hi, b.h); }
  for (const lv of chart.levels) { lo = Math.min(lo, lv.price); hi = Math.max(hi, lv.price); }
  if (chart.swing_high) hi = Math.max(hi, chart.swing_high.price);
  if (chart.swing_low) lo = Math.min(lo, chart.swing_low.price);
  const pad = (hi - lo) * 0.06 || 1;
  const pmin = lo - pad, pmax = hi + pad;
  const slot = (xr - XL) / Math.max(n, 1);
  const x = (i: number) => XL + (i + 0.5) * slot;
  const y = (p: number) => BOT - (p - pmin) / (pmax - pmin) * (BOT - TOP);

  const idxByDate: Record<string, number> = {};
  bars.forEach((b, i) => { idxByDate[b.t] = i; });

  const optrend = chart.structure.startsWith("HH/HL");
  const downtrend = chart.structure.startsWith("LH/LL");
  const trendCol = structureColor(chart.structure);

  const above: any[] = [];
  if (chart.swing_high && chart.swing_high.t in idxByDate) {
    const i = idxByDate[chart.swing_high.t];
    const txt = optrend ? "Higher High" : downtrend ? "Lower High" : "Sving-top";
    above.push({ kind: "hh", text: txt, cx: x(i), anchorY: y(chart.swing_high.price), w: pillW(txt), ax: x(i), ay: y(chart.swing_high.price), color: trendCol });
  }
  chart.candles.forEach(c => {
    const i = n - 1;
    above.push({ kind: "candle", text: c.name, cx: x(i), anchorY: y(bars[i].h), w: pillW(c.name), ax: x(i), ay: y(bars[i].h), color: "var(--neutral)" });
  });

  const below: any[] = [];
  if (chart.swing_low && chart.swing_low.t in idxByDate) {
    const i = idxByDate[chart.swing_low.t];
    const txt = optrend ? "Higher Low" : downtrend ? "Lower Low" : "Sving-bund";
    below.push({ kind: "hl", text: txt, cx: x(i), anchorY: y(chart.swing_low.price), w: pillW(txt), ax: x(i), ay: y(chart.swing_low.price), color: trendCol });
  }

  const bounds = { minX: XL, maxX: xr, minY: 4, maxY: BOT - 2 };
  const pills = [...resolveLabels(above, "above", bounds), ...resolveLabels(below, "below", bounds)];

  const ticks: number[] = [];
  const span = pmax - pmin;
  const step = span > 400 ? 100 : span > 200 ? 50 : span > 80 ? 25 : span > 30 ? 10 : 5;
  for (let p = Math.ceil(pmin / step) * step; p < pmax; p += step) ticks.push(p);

  const months: { x: number; label: string }[] = [];
  let prevM = "";
  bars.forEach((b, i) => {
    const m = b.t.slice(5, 7);
    if (m !== prevM) { months.push({ x: x(i), label: MONTHS_DA[parseInt(m, 10) - 1] || m }); prevM = m; }
  });

  return { bars, n, x, y, slot, xr, BOT, pills, ticks, months, trendCol };
}

export function SwingChart({ chart }: { chart: ChartData }) {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(1000);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(entries => {
      const w = entries[0].contentRect.width;
      if (w > 0) setWidth(w);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const H = CHART_H;
  const L = useMemo(() => buildLayout(chart, width, H), [chart, width, H]);
  const bw = Math.min(8, L.slot * 0.6);

  const srResistance = chart.levels.filter(l => l.kind === "resistance");
  const srSupport = chart.levels.filter(l => l.kind === "support");
  const srText = chart.levels.length === 0
    ? "ingen i båndet — pris nær periodens top/bund"
    : [
        srResistance.length ? "Modstand " + srResistance.map(l => `${l.price.toFixed(2)} (${l.touches}×)`).join(", ") : "",
        srSupport.length ? "Støtte " + srSupport.map(l => `${l.price.toFixed(2)} (${l.touches}×)`).join(", ") : "",
      ].filter(Boolean).join("  ·  ");

  return (
    <div ref={ref} style={{ background: "var(--bg-surface)", border: "1px solid var(--border-subtle)", borderRadius: 8, padding: 12 }}>
      <svg viewBox={`0 0 ${width} ${H}`} width="100%" height={H} role="img" style={{ display: "block" }}>
        <defs>
          <marker id="swc-arrow" markerWidth="9" markerHeight="9" refX="6.5" refY="3" orient="auto">
            <path d="M0,0 L7,3 L0,6 Z" fill="var(--text-muted)" />
          </marker>
        </defs>

        {L.ticks.map((p, i) => (
          <g key={`t${i}`}>
            <line x1={XL} y1={L.y(p)} x2={L.xr} y2={L.y(p)} stroke="var(--border-subtle)" strokeWidth={0.5} />
            <text x={L.xr + 6} y={L.y(p) + 5} fontSize={15} fill="var(--text-muted)">{p.toFixed(0)}</text>
          </g>
        ))}

        {L.months.map((m, i) => (
          <g key={`m${i}`}>
            <line x1={m.x} y1={L.BOT} x2={m.x} y2={L.BOT + 4} stroke="var(--text-muted)" strokeWidth={0.5} />
            <text x={m.x} y={L.BOT + 22} textAnchor="middle" fontSize={15} fill="var(--text-muted)">{m.label}</text>
          </g>
        ))}

        {chart.levels.map((lv, i) => (
          <line key={`l${i}`} x1={XL} y1={L.y(lv.price)} x2={L.xr} y2={L.y(lv.price)}
            stroke={lv.kind === "resistance" ? "var(--bear)" : "var(--bull)"} strokeWidth={1.3} strokeDasharray="5 4" opacity={0.8} />
        ))}

        {L.bars.map((b, i) => {
          const up = b.c >= b.o;
          const col = up ? "var(--bull)" : "var(--bear)";
          const cx = L.x(i);
          const yb = L.y(Math.max(b.o, b.c));
          const hb = Math.max(Math.abs(L.y(b.o) - L.y(b.c)), 1.2);
          return (
            <g key={`b${i}`}>
              <line x1={cx} y1={L.y(b.h)} x2={cx} y2={L.y(b.l)} stroke={col} strokeWidth={1} />
              <rect x={cx - bw / 2} y={yb} width={bw} height={hb} fill={col} rx={1} />
            </g>
          );
        })}

        <line x1={XL} y1={L.y(chart.current_price)} x2={L.xr} y2={L.y(chart.current_price)} stroke="var(--text-muted)" strokeWidth={0.5} strokeDasharray="2 3" />
        <rect x={L.xr + 2} y={L.y(chart.current_price) - 11} width={GUTTER - 6} height={22} rx={4} fill="var(--text-secondary)" />
        <text x={L.xr + 2 + (GUTTER - 6) / 2} y={L.y(chart.current_price) + 5} textAnchor="middle" fontSize={15} fontWeight={700} fill="var(--bg-base)">{chart.current_price.toFixed(2)}</text>

        {L.pills.map((p, i) => {
          const above = p.y1 <= p.ay;
          const lx = Math.max(p.x0 + 6, Math.min(p.ax, p.x1 - 6));
          const ly = above ? p.y1 : p.y0;
          return (
            <g key={`p${i}`}>
              {p.kind !== "candle" && <circle cx={p.ax} cy={p.ay} r={3.5} fill={p.color} />}
              <line x1={lx} y1={ly} x2={p.ax} y2={p.ay} stroke="var(--text-muted)" strokeWidth={1.2} markerEnd="url(#swc-arrow)" />
              <rect x={p.x0} y={p.y0} width={p.x1 - p.x0} height={PILL_H} rx={6} fill="var(--bg-overlay)" stroke={p.color} strokeWidth={1.3} />
              <text x={(p.x0 + p.x1) / 2} y={p.y0 + 15} textAnchor="middle" fontSize={12} fontWeight={600} fill={p.color}>{p.text}</text>
            </g>
          );
        })}

        <rect x={12} y={6} width={pillW(chart.structure)} height={22} rx={6} fill="var(--bg-overlay)" stroke={L.trendCol} strokeWidth={1.3} />
        <text x={12 + pillW(chart.structure) / 2} y={21} textAnchor="middle" fontSize={12} fontWeight={700} fill={L.trendCol}>{chart.structure}</text>
      </svg>

      <div style={{ marginTop: 8, fontSize: 14, color: "var(--text-secondary)" }}>
        <span style={{ color: "var(--text-muted)" }}>Støtte/modstand: </span>{srText}
      </div>
      <div style={{ marginTop: 8, display: "flex", gap: 8, alignItems: "flex-start", padding: "8px 10px", background: "var(--neutral-dim)", borderRadius: 6 }}>
        <span style={{ fontSize: 16, lineHeight: "18px" }}>👁</span>
        <span style={{ fontSize: 14, color: "var(--text-secondary)" }}>
          <b style={{ color: "var(--neutral)" }}>Chart-mønster kræver menneskelig vurdering.</b>{" "}
          Flag, trekant, hoved-skulder, cup &amp; handle, range — kig på charten. Resten er beregnet og tegnet automatisk.
        </span>
      </div>
    </div>
  );
}
