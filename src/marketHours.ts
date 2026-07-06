// US-marked (NYSE/NASDAQ) FULD-lukkede dage: weekender + helligdage.
// Iben kender ikke de amerikanske helligdage — menulinien viser tydeligt om markedet
// er lukket i dag (og diskret dagen før). Datoer i ET (markedets tidszone).
// Halve handelsdage (tidlig luk) regnes som ÅBNE her.
const NYSE_HOLIDAYS: Record<string, string> = {
  "2026-01-01": "Nytårsdag",
  "2026-01-19": "Martin Luther King Day",
  "2026-02-16": "Presidents' Day",
  "2026-04-03": "Langfredag",
  "2026-05-25": "Memorial Day",
  "2026-06-19": "Juneteenth",
  "2026-07-03": "Independence Day",
  "2026-09-07": "Labor Day",
  "2026-11-26": "Thanksgiving",
  "2026-12-25": "Juledag",
  "2027-01-01": "Nytårsdag",
  "2027-01-18": "Martin Luther King Day",
  "2027-02-15": "Presidents' Day",
  "2027-03-26": "Langfredag",
  "2027-05-31": "Memorial Day",
  "2027-06-18": "Juneteenth",
  "2027-07-05": "Independence Day",
  "2027-09-06": "Labor Day",
  "2027-11-25": "Thanksgiving",
  "2027-12-24": "Juledag",
};

// ET-dato (YYYY-MM-DD) + ugedag (0=søn … 6=lør) for i dag + offsetDays.
function etParts(offsetDays: number): { ymd: string; dow: number } {
  const d = new Date(Date.now() + offsetDays * 86400000);
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/New_York",
    year: "numeric", month: "2-digit", day: "2-digit", weekday: "short",
  }).formatToParts(d);
  const g = (t: string) => parts.find(x => x.type === t)?.value ?? "";
  const dowMap: Record<string, number> = { Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6 };
  return { ymd: `${g("year")}-${g("month")}-${g("day")}`, dow: dowMap[g("weekday")] ?? -1 };
}

// Er US-markedet fuldt lukket den dag (offsetDays fra i dag, ET)? + årsag.
export function usMarketClosure(offsetDays = 0): { closed: boolean; reason: string } {
  const { ymd, dow } = etParts(offsetDays);
  if (dow === 0 || dow === 6) return { closed: true, reason: "weekend" };
  if (NYSE_HOLIDAYS[ymd]) return { closed: true, reason: NYSE_HOLIDAYS[ymd] };
  return { closed: false, reason: "" };
}
