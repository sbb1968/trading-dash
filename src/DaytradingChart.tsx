import { useMemo, useRef, useState, useEffect } from "react";

// Day trading-candlestick-chart der spejler Pine-tvillingen "Day trading konfluens v1":
// candlesticks + VWAP + EMA9/EMA20 + ORB/PDH/PDL/PDC/HOD-niveauer + volumen-strip.
// Real-pixel SVG (viewBox = maalt bredde x fast hoejde) som SwingChart.

interface DaytradingBar { t: string; o: number; h: number; l: number; c: number; v: number; }
interface DaytradingLevel { kind: string; label: string; price: number; }
interface DaytradingChartData {
  current_price: number;
  bars: DaytradingBar[];
  overlays: {
    vwap: (number | null)[]; ema9: (number | null)[]; ema20: (number | null)[];
    vw_upper: (number | null)[]; vw_lower: (number | null)[];
  };
  levels: DaytradingLevel[];
}

const CHART_H = 440;
const TOP = 22, BOTM = 46, XL = 14, GUTTER = 58;
const VOL_H = 56, PILL_H = 20;

const pillW = (t: string) => t.length * 6.5 + 14;

function levelColor(kind: string): string {
  if (kind === "pdh" || kind === "pdl") return "#ffd60a";       // gul (som Pine)
  if (kind === "orb_high" || kind === "orb_low") return "#34c759";
  if (kind === "hod") return "#ff453a";
  return "#8e8e93";                                             // pdc
}
function levelDash(kind: string): string {
  if (kind === "orb_high" || kind === "orb_low") return "5 4";
  if (kind === "hod") return "2 3";
  return "0";
}

const OVERLAY_STYLE: Record<string, { color: string; w: number; op: number; dash: string }> = {
  vwap:     { color: "#ffd60a", w: 1.6, op: 0.95, dash: "0" },
  ema9:     { color: "#ff9500", w: 1.4, op: 0.9,  dash: "0" },
  ema20:    { color: "#0a84ff", w: 1.4, op: 0.9,  dash: "0" },
  vw_upper: { color: "#ffd60a", w: 0.8, op: 0.25, dash: "4 4" },
  vw_lower: { color: "#ffd60a", w: 0.8, op: 0.25, dash: "4 4" },
};

function segments(arr: (number | null)[], x: (i: number) => number, y: (p: number) => number): string[] {
  const segs: string[] = [];
  let cur: string[] = [];
  arr.forEach((v, i) => {
    if (v == null) { if (cur.length > 1) segs.push(cur.join(" ")); cur = []; }
    else cur.push(`${x(i).toFixed(1)},${y(v).toFixed(1)}`);
  });
  if (cur.length > 1) segs.push(cur.join(" "));
  return segs;
}

function buildLayout(chart: DaytradingChartData, W: number, H: number) {
  const xr = W - GUTTER;
  const priceBot = H - BOTM - VOL_H;
  const volTop = priceBot + 8, volBot = H - BOTM;
  const bars = chart.bars;
  const n = bars.length;
  let lo = Infinity, hi = -Infinity;
  for (const b of bars) { lo = Math.min(lo, b.l); hi = Math.max(hi, b.h); }
  for (const lv of chart.levels) { lo = Math.min(lo, lv.price); hi = Math.max(hi, lv.price); }
  for (const k of ["vwap", "ema9", "ema20"] as const)
    for (const v of chart.overlays[k]) if (v != null) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
  if (!isFinite(lo) || !isFinite(hi)) { lo = 0; hi = 1; }
  const pad = (hi - lo) * 0.06 || 1;
  const pmin = lo - pad, pmax = hi + pad;
  const slot = (xr - XL) / Math.max(n, 1);
  const x = (i: number) => XL + (i + 0.5) * slot;
  const y = (p: number) => priceBot - (p - pmin) / (pmax - pmin) * (priceBot - TOP);
  let vmax = 0; for (const b of bars) vmax = Math.max(vmax, b.v || 0);
  const vy = (v: number) => volBot - (vmax > 0 ? v / vmax : 0) * (volBot - volTop);

  const ticks: number[] = [];
  const span = pmax - pmin;
  const step = span > 400 ? 100 : span > 200 ? 50 : span > 80 ? 25 : span > 30 ? 10 : span > 12 ? 5 : span > 5 ? 2 : span > 2 ? 1 : 0.5;
  for (let p = Math.ceil(pmin / step) * step; p < pmax; p += step) ticks.push(p);

  const timeTicks: { x: number; label: string }[] = [];
  const everyN = Math.max(1, Math.round(n / 8));
  bars.forEach((b, i) => { if (i % everyN === 0) timeTicks.push({ x: x(i), label: b.t }); });

  // Niveau-etiketter: pill ved hoejre akse, vertikalt af-overlappet (sorteret paa y).
  const lvls = chart.levels
    .map(lv => {
      const txt = `${lv.label} ${lv.price.toFixed(2)}`;
      return { kind: lv.kind, color: levelColor(lv.kind), dash: levelDash(lv.kind), lineY: y(lv.price), txt, w: pillW(txt) };
    })
    .sort((a, b) => a.lineY - b.lineY);
  const levelPills: any[] = [];
  let lastBottom = -Infinity;
  for (const it of lvls) {
    let y0 = it.lineY - PILL_H / 2;
    if (y0 < lastBottom + 2) y0 = lastBottom + 2;
    y0 = Math.max(2, Math.min(y0, priceBot - PILL_H));
    lastBottom = y0 + PILL_H;
    levelPills.push({ ...it, x0: xr - it.w, x1: xr, y0 });
  }

  return { bars, n, x, y, slot, xr, priceBot, volTop, volBot, vy, ticks, timeTicks, levelPills };
}

export function DaytradingChart({ data }: { data: DaytradingChartData }) {
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
  const L = useMemo(() => buildLayout(data, width, H), [data, width, H]);
  if (!data.bars?.length) return null;
  const bw = Math.min(7, L.slot * 0.6);

  return (
    <div ref={ref} style={{ background: "var(--bg-surface)", border: "1px solid var(--border-subtle)", borderRadius: 8, padding: 12 }}>
      <svg viewBox={`0 0 ${width} ${H}`} width="100%" height={H} role="img" style={{ display: "block" }}>
        {/* pris-gridlinjer + akse-labels */}
        {L.ticks.map((p, i) => (
          <g key={`t${i}`}>
            <line x1={XL} y1={L.y(p)} x2={L.xr} y2={L.y(p)} stroke="var(--border-subtle)" strokeWidth={0.5} />
            <text x={L.xr + 5} y={L.y(p) + 4} fontSize={12} fill="var(--text-muted)">{p.toFixed(p < 10 ? 1 : 0)}</text>
          </g>
        ))}

        {/* niveau-linjer (ORB/PDH/PDL/PDC/HOD) */}
        {L.levelPills.map((lv, i) => (
          <line key={`lv${i}`} x1={XL} y1={lv.lineY} x2={L.xr} y2={lv.lineY}
            stroke={lv.color} strokeWidth={1.2} strokeDasharray={lv.dash} opacity={0.85} />
        ))}

        {/* candlesticks */}
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

        {/* overlays: VWAP/EMA9/EMA20 (+ svagt VWAP-baand) — brudt ved null */}
        {(["vw_upper", "vw_lower", "vwap", "ema20", "ema9"] as const).map(key => {
          const st = OVERLAY_STYLE[key];
          return segments(data.overlays[key], L.x, L.y).map((pts, j) => (
            <polyline key={`${key}${j}`} points={pts} fill="none" stroke={st.color}
              strokeWidth={st.w} opacity={st.op} strokeDasharray={st.dash}
              strokeLinejoin="round" strokeLinecap="round" />
          ));
        })}

        {/* current price-linje + tag */}
        <line x1={XL} y1={L.y(data.current_price)} x2={L.xr} y2={L.y(data.current_price)} stroke="var(--text-muted)" strokeWidth={0.5} strokeDasharray="2 3" />
        <rect x={L.xr + 2} y={L.y(data.current_price) - 10} width={GUTTER - 5} height={20} rx={4} fill="var(--text-secondary)" />
        <text x={L.xr + 2 + (GUTTER - 5) / 2} y={L.y(data.current_price) + 4} textAnchor="middle" fontSize={12} fontWeight={700} fill="var(--bg-base)">{data.current_price.toFixed(2)}</text>

        {/* niveau-etiketter (kollisionsfrit stacket) */}
        {L.levelPills.map((lv, i) => (
          <g key={`lp${i}`}>
            <rect x={lv.x0} y={lv.y0} width={lv.x1 - lv.x0} height={PILL_H} rx={5} fill="var(--bg-overlay)" stroke={lv.color} strokeWidth={1.1} />
            <text x={(lv.x0 + lv.x1) / 2} y={lv.y0 + 14} textAnchor="middle" fontSize={11} fontWeight={600} fill={lv.color}>{lv.txt}</text>
          </g>
        ))}

        {/* volumen-strip */}
        <line x1={XL} y1={L.volTop - 3} x2={L.xr} y2={L.volTop - 3} stroke="var(--border-subtle)" strokeWidth={0.5} />
        {L.bars.map((b, i) => {
          const up = b.c >= b.o;
          const vyTop = L.vy(b.v);
          return <rect key={`v${i}`} x={L.x(i) - bw / 2} y={vyTop} width={bw} height={Math.max(L.volBot - vyTop, 0.5)}
            fill={up ? "var(--bull)" : "var(--bear)"} opacity={0.45} />;
        })}

        {/* x-akse: tid (HH:MM) */}
        {L.timeTicks.map((tk, i) => (
          <g key={`x${i}`}>
            <line x1={tk.x} y1={L.volBot} x2={tk.x} y2={L.volBot + 4} stroke="var(--text-muted)" strokeWidth={0.5} />
            <text x={tk.x} y={L.volBot + 18} textAnchor="middle" fontSize={12} fill="var(--text-muted)">{tk.label}</text>
          </g>
        ))}

        {/* legende */}
        <g>
          <text x={XL} y={14} fontSize={11} fontWeight={700} fill="#ffd60a">VWAP</text>
          <text x={XL + 44} y={14} fontSize={11} fontWeight={700} fill="#ff9500">EMA9</text>
          <text x={XL + 88} y={14} fontSize={11} fontWeight={700} fill="#0a84ff">EMA20</text>
        </g>
      </svg>
    </div>
  );
}
