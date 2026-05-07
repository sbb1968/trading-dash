// ── Mock journal-data — 20 fiktive handler ───────────────────

export type SetupType =
  | "ORB"
  | "Momentum"
  | "News Play"
  | "VWAP Bounce"
  | "Bull Flag"
  | "Reversal"
  | "Anden";

export type Emotion = "Rolig" | "Nervøs" | "Overmodig" | "Disciplineret" | "FOMO";

export interface JournalTrade {
  id:            string;
  date:          string;
  entry_time:    string;   // "09:32"
  exit_time:     string;   // "09:50"
  ticker:        string;
  setup:         SetupType;
  side:          "Long" | "Short";
  entry_price:   number;
  exit_price:    number;
  shares:        number;
  pnl:           number;
  pnl_pct:       number;
  duration_min:  number;
  scanner:       string;
  timeframe:     string;
  emotion:       Emotion;
  followed_plan: boolean;
  notes:         string;
  tags:          string[];
}

const today = new Date();
function daysAgo(n: number) {
  const d = new Date(today);
  d.setDate(d.getDate() - n);
  return d.toISOString().split("T")[0];
}

export const MOCK_JOURNAL_TRADES: JournalTrade[] = [
  {
    id: "j001", date: daysAgo(0), entry_time: "09:32", exit_time: "09:50",
    ticker: "NVAX", setup: "News Play", side: "Long",
    entry_price: 4.82, exit_price: 6.14, shares: 2000,
    pnl: 2640, pnl_pct: 27.4, duration_min: 18,
    scanner: "Small Cap Scanner", timeframe: "1 min",
    emotion: "Disciplineret", followed_plan: true,
    notes: "FDA-godkendelse pre-market. Ventede på bekræftelse over $5 inden entry. God exit nær HOD.",
    tags: ["gap-and-go", "catalyst", "FDA"],
  },
  {
    id: "j002", date: daysAgo(0), entry_time: "10:14", exit_time: "10:21",
    ticker: "MULN", setup: "Momentum", side: "Long",
    entry_price: 2.31, exit_price: 2.18, shares: 3000,
    pnl: -390, pnl_pct: -5.6, duration_min: 7,
    scanner: "Running Up", timeframe: "1 min",
    emotion: "FOMO", followed_plan: false,
    notes: "Chased toppen. Ingen klar catalyst. Skulle ikke have taget denne trade.",
    tags: ["chased", "no-catalyst"],
  },
  {
    id: "j003", date: daysAgo(1), entry_time: "09:35", exit_time: "10:09",
    ticker: "BBIO", setup: "ORB", side: "Long",
    entry_price: 8.45, exit_price: 9.87, shares: 500,
    pnl: 710, pnl_pct: 16.8, duration_min: 34,
    scanner: "Small Cap Scanner", timeframe: "5 min",
    emotion: "Rolig", followed_plan: true,
    notes: "Klassisk ORB setup. Ventede på 5-min bekræftelse. Holdt til R3.",
    tags: ["ORB", "clean-setup"],
  },
  {
    id: "j004", date: daysAgo(1), entry_time: "10:45", exit_time: "11:07",
    ticker: "ATER", setup: "Bull Flag", side: "Long",
    entry_price: 3.72, exit_price: 4.31, shares: 1500,
    pnl: 885, pnl_pct: 15.9, duration_min: 22,
    scanner: "Small Cap Scanner", timeframe: "1 min",
    emotion: "Disciplineret", followed_plan: true,
    notes: "Flot bull flag på 1-min chart. Entry ved breakout af flag. Target ramt.",
    tags: ["bull-flag", "pattern"],
  },
  {
    id: "j005", date: daysAgo(1), entry_time: "11:30", exit_time: "11:35",
    ticker: "INDO", setup: "Momentum", side: "Long",
    entry_price: 12.40, exit_price: 11.85, shares: 400,
    pnl: -220, pnl_pct: -4.4, duration_min: 5,
    scanner: "Running Up", timeframe: "1 min",
    emotion: "Nervøs", followed_plan: false,
    notes: "Stoppet ud. Markedet var for volatilt. Stop for tæt sat.",
    tags: ["stopped-out", "volatile"],
  },
  {
    id: "j006", date: daysAgo(2), entry_time: "10:02", exit_time: "10:30",
    ticker: "CLOV", setup: "VWAP Bounce", side: "Long",
    entry_price: 5.15, exit_price: 5.68, shares: 1000,
    pnl: 530, pnl_pct: 10.3, duration_min: 28,
    scanner: "Watchlist", timeframe: "5 min",
    emotion: "Rolig", followed_plan: true,
    notes: "VWAP hold ved 2. test. Volume bekræftede bounce.",
    tags: ["VWAP", "bounce"],
  },
  {
    id: "j007", date: daysAgo(2), entry_time: "09:31", exit_time: "10:16",
    ticker: "SPRT", setup: "ORB", side: "Long",
    entry_price: 7.80, exit_price: 9.42, shares: 600,
    pnl: 972, pnl_pct: 20.8, duration_min: 45,
    scanner: "Small Cap Scanner", timeframe: "5 min",
    emotion: "Disciplineret", followed_plan: true,
    notes: "Perfekt ORB. Holdt positionen gennem pullback. Stort udbrud kl. 10:15.",
    tags: ["ORB", "held-through-pullback"],
  },
  {
    id: "j008", date: daysAgo(3), entry_time: "09:55", exit_time: "10:47",
    ticker: "NAKD", setup: "Reversal", side: "Short",
    entry_price: 1.95, exit_price: 1.62, shares: 5000,
    pnl: 1650, pnl_pct: 16.9, duration_min: 52,
    scanner: "Top Gainers", timeframe: "15 min",
    emotion: "Rolig", followed_plan: true,
    notes: "Reversal short efter massive spike. Ventede på bekræftelse under VWAP.",
    tags: ["reversal", "short", "overextended"],
  },
  {
    id: "j009", date: daysAgo(3), entry_time: "11:10", exit_time: "11:14",
    ticker: "XELA", setup: "Momentum", side: "Long",
    entry_price: 2.88, exit_price: 2.91, shares: 2000,
    pnl: 60, pnl_pct: 1.0, duration_min: 4,
    scanner: "Running Up", timeframe: "1 min",
    emotion: "Nervøs", followed_plan: true,
    notes: "Tog tidlig profit. Aktien fortsatte til $3.40. Burde have holdt længere.",
    tags: ["early-exit", "left-money"],
  },
  {
    id: "j010", date: daysAgo(4), entry_time: "09:38", exit_time: "10:45",
    ticker: "EXPR", setup: "News Play", side: "Long",
    entry_price: 6.20, exit_price: 8.75, shares: 800,
    pnl: 2040, pnl_pct: 41.1, duration_min: 67,
    scanner: "Small Cap Scanner", timeframe: "5 min",
    emotion: "Disciplineret", followed_plan: true,
    notes: "Meme-aktie momentum. Holdt hele vejen. Bedste trade i ugen.",
    tags: ["meme", "held-runner", "best-trade"],
  },
  {
    id: "j011", date: daysAgo(4), entry_time: "11:22", exit_time: "11:34",
    ticker: "OCGN", setup: "ORB", side: "Long",
    entry_price: 9.10, exit_price: 8.65, shares: 500,
    pnl: -225, pnl_pct: -4.9, duration_min: 12,
    scanner: "Small Cap Scanner", timeframe: "5 min",
    emotion: "Overmodig", followed_plan: false,
    notes: "For stor position størrelse. ORB fejlede. Tab acceptabelt men undgåeligt.",
    tags: ["size-too-big", "failed-ORB"],
  },
  {
    id: "j012", date: daysAgo(5), entry_time: "10:18", exit_time: "10:49",
    ticker: "SKLZ", setup: "Bull Flag", side: "Long",
    entry_price: 4.45, exit_price: 5.10, shares: 1200,
    pnl: 780, pnl_pct: 14.6, duration_min: 31,
    scanner: "Watchlist", timeframe: "5 min",
    emotion: "Disciplineret", followed_plan: true,
    notes: "God konsolidering. Breakout med høj volumen. Target ramt præcist.",
    tags: ["bull-flag", "volume-confirmation"],
  },
  {
    id: "j013", date: daysAgo(5), entry_time: "09:44", exit_time: "09:53",
    ticker: "WKHS", setup: "Momentum", side: "Long",
    entry_price: 3.18, exit_price: 2.95, shares: 2500,
    pnl: -575, pnl_pct: -7.2, duration_min: 9,
    scanner: "Running Up", timeframe: "1 min",
    emotion: "FOMO", followed_plan: false,
    notes: "FOMO-trade. Var ikke på watchlist. Ingen plan. Stoppet ud hurtigt.",
    tags: ["FOMO", "no-plan", "chased"],
  },
  {
    id: "j014", date: daysAgo(6), entry_time: "10:33", exit_time: "11:14",
    ticker: "SNDL", setup: "VWAP Bounce", side: "Long",
    entry_price: 0.85, exit_price: 0.97, shares: 8000,
    pnl: 960, pnl_pct: 14.1, duration_min: 41,
    scanner: "Watchlist", timeframe: "5 min",
    emotion: "Rolig", followed_plan: true,
    notes: "Lav float. VWAP hold bekræftet 3 gange. Skalerede ud undervejs.",
    tags: ["VWAP", "scaled-out", "low-float"],
  },
  {
    id: "j015", date: daysAgo(6), entry_time: "11:05", exit_time: "11:43",
    ticker: "RIDE", setup: "Reversal", side: "Short",
    entry_price: 8.90, exit_price: 8.15, shares: 600,
    pnl: 450, pnl_pct: 8.4, duration_min: 38,
    scanner: "Top Gainers", timeframe: "15 min",
    emotion: "Disciplineret", followed_plan: true,
    notes: "Overextended short. Ventede på daglig R3 som entry-signal.",
    tags: ["reversal", "short", "R3"],
  },
  {
    id: "j016", date: daysAgo(7), entry_time: "09:33", exit_time: "09:58",
    ticker: "BBIG", setup: "News Play", side: "Long",
    entry_price: 3.55, exit_price: 4.20, shares: 1400,
    pnl: 910, pnl_pct: 18.3, duration_min: 25,
    scanner: "Small Cap Scanner", timeframe: "1 min",
    emotion: "Disciplineret", followed_plan: true,
    notes: "Pressemeddelse pre-market om partnership. Klar catalyst. Clean entry.",
    tags: ["news", "catalyst", "gap-and-go"],
  },
  {
    id: "j017", date: daysAgo(7), entry_time: "10:55", exit_time: "10:58",
    ticker: "NKLA", setup: "Momentum", side: "Long",
    entry_price: 5.40, exit_price: 5.40, shares: 1000,
    pnl: 0, pnl_pct: 0, duration_min: 3,
    scanner: "Running Up", timeframe: "1 min",
    emotion: "Nervøs", followed_plan: true,
    notes: "Break-even. Markedet vendte hurtigt. God stop-disciplin.",
    tags: ["break-even", "good-stop"],
  },
  {
    id: "j018", date: daysAgo(8), entry_time: "09:31", exit_time: "10:59",
    ticker: "MVIS", setup: "ORB", side: "Long",
    entry_price: 14.20, exit_price: 16.85, shares: 300,
    pnl: 795, pnl_pct: 18.7, duration_min: 88,
    scanner: "Small Cap Scanner", timeframe: "5 min",
    emotion: "Disciplineret", followed_plan: true,
    notes: "Holdt position i 88 min. Svært psykologisk men planen sagde hold.",
    tags: ["held-position", "patience", "ORB"],
  },
  {
    id: "j019", date: daysAgo(8), entry_time: "11:40", exit_time: "11:54",
    ticker: "CTRM", setup: "Bull Flag", side: "Long",
    entry_price: 0.62, exit_price: 0.58, shares: 10000,
    pnl: -400, pnl_pct: -6.5, duration_min: 14,
    scanner: "Small Cap Scanner", timeframe: "1 min",
    emotion: "Overmodig", followed_plan: false,
    notes: "For stor position på billig aktie. Flag fejlede. Tab begrænset af stop.",
    tags: ["penny-stock", "failed-flag", "size"],
  },
  {
    id: "j020", date: daysAgo(9), entry_time: "10:22", exit_time: "11:17",
    ticker: "TLRY", setup: "VWAP Bounce", side: "Long",
    entry_price: 11.30, exit_price: 12.45, shares: 700,
    pnl: 805, pnl_pct: 10.2, duration_min: 55,
    scanner: "Watchlist", timeframe: "5 min",
    emotion: "Rolig", followed_plan: true,
    notes: "Cannabis-sektor momentum. VWAP holdt perfekt. Skaleret ud i 3 dele.",
    tags: ["VWAP", "sector-play", "scaled-out"],
  },
];
