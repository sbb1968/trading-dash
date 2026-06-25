import { useRef, useState, useEffect, useMemo } from "react";

interface Bar { t: string; o: number; h: number; l: number; c: number; }
export interface BuyHoldChartData {
  bars: Bar[]; ma10: (number | null)[]; ma30: (number | null)[]; ma40: (number | null)[];
  ath: number; current_price: number;
}

const BULL = "#15803d", BEAR = "#b91c1c", MUT = "var(--text-muted)", GRID = "var(--border-subtle)";
const MA10 = "#34c759", MA30 = "#ff9500", MA40 = "#0a84ff", ATHC = "#8e8e93";
const W = 900, H = 380, TOP = 24, BOTM = 44, XL = 14, GUTTER = 62;

export function BuyHoldChart({ chart }: { chart: BuyHoldChartData }) {
  const ref = useRef<HTMLDivElement>(null);
  const [, setWidth] = useState(900);
  useEffect(() => {
    const el = ref.current; if (!el) return;
    const ro = new ResizeObserver(e => { const w = e[0].contentRect.width; if (w > 0) setWidth(w); });
    ro.observe(el); return () => ro.disconnect();
  }, []);

  const L = useMemo(() => {
    const bars = chart.bars, n = bars.length, BOT = H - BOTM, xr = W - GUTTER;
    let lo = Infinity, hi = -Infinity;
    bars.forEach(b => { if (b.l < lo) lo = b.l; if (b.h > hi) hi = b.h; });
    if (chart.ath > hi) hi = chart.ath;
    const pad = (hi - lo) * 0.06 || 1, pmin = lo - pad, pmax = hi + pad;
    const slot = (xr - XL) / Math.max(n, 1);
    const x = (i: number) => XL + (i + 0.5) * slot;
    const y = (p: number) => BOT - (p - pmin) / (pmax - pmin) * (BOT - TOP);
    const span = pmax - pmin, step = span > 400 ? 100 : span > 200 ? 50 : span > 80 ? 25 : span > 30 ? 10 : 5;
    const ticks: number[] = [];
    for (let p = Math.ceil(pmin / step) * step; p < pmax; p += step) ticks.push(p);
    const years: { x: number; label: string }[] = [];
    let prevY = "";
    bars.forEach((b, i) => { const yr = b.t.slice(0, 4); if (yr !== prevY) { years.push({ x: x(i), label: yr }); prevY = yr; } });
    const poly = (arr: (number | null)[]) => {
      let d = "", started = false;
      arr.forEach((v, i) => { if (v == null) return; d += (started ? "L" : "M") + x(i) + " " + y(v) + " "; started = true; });
      return d;
    };
    return { bars, n, x, y, slot, xr, BOT, ticks, years, pmin, pmax,
             d10: poly(chart.ma10), d30: poly(chart.ma30), d40: poly(chart.ma40) };
  }, [chart]);

  const bw = Math.min(6, L.slot * 0.6);
  return (
    <div ref={ref} style={{ width: "100%" }}>
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: "auto" }} preserveAspectRatio="xMidYMid meet">
        {L.ticks.map((p, i) => (<g key={"t" + i}>
          <line x1={XL} y1={L.y(p)} x2={L.xr} y2={L.y(p)} stroke={GRID} strokeWidth={0.5} />
          <text x={L.xr + 4} y={L.y(p) + 4} fontSize={13} fill={MUT}>{p}</text></g>))}
        {L.years.map((m, i) => (<g key={"y" + i}>
          <line x1={m.x} y1={TOP} x2={m.x} y2={L.BOT + 4} stroke={MUT} strokeWidth={0.5} />
          <text x={m.x} y={L.BOT + 20} textAnchor="middle" fontSize={14} fill={MUT}>{m.label}</text></g>))}
        <line x1={XL} y1={L.y(chart.ath)} x2={L.xr} y2={L.y(chart.ath)} stroke={ATHC} strokeWidth={1.2} strokeDasharray="5 4" />
        <text x={XL + 4} y={L.y(chart.ath) - 4} fontSize={12} fill={ATHC}>ATH {chart.ath.toFixed(2)}</text>
        {L.bars.map((b, i) => { const up = b.c >= b.o, col = up ? BULL : BEAR, cx = L.x(i);
          const yb = L.y(Math.max(b.o, b.c)), hb = Math.max(Math.abs(L.y(b.o) - L.y(b.c)), 1);
          return (<g key={"b" + i}>
            <line x1={cx} y1={L.y(b.h)} x2={cx} y2={L.y(b.l)} stroke={col} strokeWidth={0.8} />
            <rect x={cx - bw / 2} y={yb} width={bw} height={hb} fill={col} /></g>); })}
        <path d={L.d40} fill="none" stroke={MA40} strokeWidth={1.5} />
        <path d={L.d30} fill="none" stroke={MA30} strokeWidth={1.5} />
        <path d={L.d10} fill="none" stroke={MA10} strokeWidth={1.5} />
        <line x1={XL} y1={L.y(chart.current_price)} x2={L.xr} y2={L.y(chart.current_price)} stroke={MUT} strokeWidth={0.5} strokeDasharray="2 3" />
        <rect x={L.xr + 2} y={L.y(chart.current_price) - 11} width={GUTTER - 6} height={22} rx={4} fill="#374151" />
        <text x={L.xr + 2 + (GUTTER - 6) / 2} y={L.y(chart.current_price) + 5} textAnchor="middle" fontSize={14} fontWeight={700} fill="#fff">{chart.current_price.toFixed(2)}</text>
        {([["10u", MA10, 0], ["30u", MA30, 70], ["40u", MA40, 140]] as [string, string, number][]).map(([t, c, dx], i) => (<g key={"l" + i}>
          <line x1={XL + 4 + dx} y1={TOP + 12} x2={XL + 20 + dx} y2={TOP + 12} stroke={c} strokeWidth={2} />
          <text x={XL + 24 + dx} y={TOP + 16} fontSize={12} fill={MUT}>{t}</text></g>))}
      </svg>
    </div>
  );
}
