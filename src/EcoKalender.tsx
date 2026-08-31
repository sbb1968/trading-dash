// ── Økonomisk kalender — dagens events ───────────────────────────────────────
// Morgenoverblikket: hvad sker der i dag, hvornår, og hvor længe til det næste.
//
// ⚠ VINDUET SKAL KUNNE SKIMMES PÅ FEM SEKUNDER. Derfor er tier 1 rød og stor,
// tier 2 dæmpet, og det næste event står øverst med nedtælling. Alt andet er
// underordnet — man kigger her mens man har travlt.
//
// ⚠ DATA ER ALLEREDE I DRIFT. eco_kalender.py har høstet dagligt siden 18-08;
// det eneste der manglede var en vej ind i Trading Dash. Studio har haft siden
// hele tiden, men Iben sidder ikke i Studio om morgenen.
//
// ⚠ TRE TILSTANDE DER IKKE MÅ SE ENS UD:
//     ingen events i dag      -> "Ingen events i dag" (en rolig dag)
//     kalenderen svarer ikke  -> fejlbesked (vi ved det ikke)
//     høsten er forældet      -> events VISES, men med advarsel ovenover
// En stille fejl der ligner en rolig dag er præcis den fejlklasse resten af
// projektet jager. Backenden sender `ok` og `stale` netop for at kunne skelne.
import { useState, useEffect, useCallback } from "react";

const API = "http://127.0.0.1:8000";

interface EcoEvent {
  ts_utc: string | null;
  titel: string;
  land: string;
  dato_dk: string | null;
  klokke_dk: string | null;
  tier: number;
  begrundelse?: string | null;
  forecast: string | null;
  previous: string | null;
  actual: string | null;
  har_klokkeslet: boolean;
  i_oevevindue: boolean;
  minutter_til: number | null;
  pladsholder?: boolean;
}

interface Svar {
  ok: boolean;
  dato: string;
  fejl?: string;
  events: EcoEvent[] | null;
  count?: number;
  naeste: EcoEvent | null;
  stale: boolean | null;
  sidste_hoest?: string | null;
  hoest_alder_timer?: number | null;
}

function iDag(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function skiftDag(dato: string, dage: number): string {
  const [y, m, d] = dato.split("-").map(Number);
  const dt = new Date(y, m - 1, d + dage);
  return `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, "0")}-${String(dt.getDate()).padStart(2, "0")}`;
}

/** "om 1 t 24 min" / "for 12 min siden". null -> tankestreg. */
function nedtaelling(min: number | null): string {
  if (min == null) return "—";
  const forbi = min < 0;
  const a = Math.abs(min);
  const t = Math.floor(a / 60), m = a % 60;
  const tekst = t > 0 ? `${t} t ${m} min` : `${m} min`;
  return forbi ? `for ${tekst} siden` : `om ${tekst}`;
}

/** "i dag kl. 15:05" · "i morgen (tirsdag 01-09) kl. 15:05" · "torsdag 03-09 kl. 14:30".
 *
 *  ⚠ ABSOLUT TID ER HOVEDSVARET, IKKE NEDTAELLINGEN. "om 29 t 11 min" tvinger
 *  laeseren til hovedregning — og Soeren regnede 15:14 hvor der stod 15:05,
 *  fordi han regnede fra det tidspunkt han LAESTE det og ikke fra det tidspunkt
 *  tallet blev dannet. Nedtaellingen er nu en parentes, ikke svaret. */
function hvornaar(dato: string | null, klokke: string | null): string {
  if (!dato) return "—";
  const d = iDag();
  const naevn = dato === d ? "i dag"
    : dato === skiftDag(d, 1) ? `i morgen (${ugedag(dato)} ${kort(dato)})`
    : dato === skiftDag(d, -1) ? `i går (${ugedag(dato)} ${kort(dato)})`
    : `${ugedag(dato)} ${kort(dato)}`;
  return klokke ? `${naevn} kl. ${klokke}` : `${naevn}, hele dagen`;
}

const kort = (dato: string) => dato.slice(8, 10) + "-" + dato.slice(5, 7);

const ugedag = (dato: string) => {
  const [y, m, d] = dato.split("-").map(Number);
  return ["søndag", "mandag", "tirsdag", "onsdag", "torsdag", "fredag", "lørdag"][
    new Date(y, m - 1, d).getDay()];
};

export default function EcoKalender() {
  const [dato, setDato] = useState<string>(iDag());
  const [svar, setSvar] = useState<Svar | null>(null);
  const [henter, setHenter] = useState(true);
  const [netfejl, setNetfejl] = useState("");
  const [visAlle, setVisAlle] = useState(false);   // tier 2 med eller ej

  const hent = useCallback(async (d: string, tier: number) => {
    try {
      // ⚠ Samme tier til BEGGE dele. Ellers kan "naeste" vise et event der er
      // filtreret vaek af listen nedenfor — praecis dét der skete med
      // "FOMC Member Barr Speaks".
      const r = await fetch(`${API}/eco/dash-dag?dato=${d}&tier=${tier}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setSvar(await r.json());
      setNetfejl("");
    } catch (e) {
      // ⚠ Backenden nede er IKKE det samme som en rolig dag.
      setSvar(null);
      setNetfejl(e instanceof Error ? e.message : "ukendt fejl");
    } finally {
      setHenter(false);
    }
  }, []);

  const tier = visAlle ? 2 : 1;
  useEffect(() => { setHenter(true); hent(dato, tier); }, [dato, tier, hent]);
  // Nedtællingen skal leve. Et minut er rigeligt — events har minutopløsning.
  useEffect(() => {
    const id = window.setInterval(() => hent(dato, tier), 60_000);
    return () => window.clearInterval(id);
  }, [dato, tier, hent]);

  const erIDag = dato === iDag();
  const alle = svar?.events ?? [];
  const viste = visAlle ? alle : alle.filter(e => e.tier === 1);
  const antalT1 = alle.filter(e => e.tier === 1).length;
  const antalT2 = alle.filter(e => e.tier === 2).length;

  return (
    <div className="eco-kalender" style={{ height: "100%", display: "flex", flexDirection: "column",
                                           fontSize: "var(--fs-content-scanner, 13px)" }}>
      {/* ── Datovælger ─────────────────────────────────────────── */}
      <div style={{ display: "flex", alignItems: "center", gap: 6, padding: "6px 8px",
                    borderBottom: "1px solid var(--border)", flexWrap: "wrap" }}>
        <button onClick={() => setDato(skiftDag(dato, -1))} style={knapStil}>◀</button>
        <button onClick={() => setDato(iDag())}
                style={{ ...knapStil, ...(erIDag ? aktivStil : {}) }}>I dag</button>
        <button onClick={() => setDato(skiftDag(dato, 1))} style={knapStil}>▶</button>
        <span style={{ fontWeight: 700, marginLeft: 4 }}>
          {ugedag(dato)} {dato.split("-").reverse().join("-")}
        </span>
        <span style={{ flex: 1 }} />
        <button onClick={() => setVisAlle(v => !v)} style={{ ...knapStil, ...(visAlle ? aktivStil : {}) }}
                title="Tier 2 er events der KAN flytte markedet, mindre paalideligt">
          {visAlle ? `Tier 1+2 (${antalT1 + antalT2})` : `Kun tier 1 (${antalT1})`}
        </button>
      </div>

      {/* ── Advarsler. De skal stå FØR listen, ellers læses de ikke ── */}
      {netfejl && (
        <Bjaelke farve="var(--bear)">
          Kalenderen svarer ikke ({netfejl}). <b>Det betyder ikke at der ingen events er</b> — kører backenden?
        </Bjaelke>
      )}
      {svar && !svar.ok && (
        <Bjaelke farve="var(--bear)">
          Kalenderen kunne ikke læses: {svar.fejl}. <b>Listen er ukendt, ikke tom.</b>
        </Bjaelke>
      )}
      {svar?.ok && svar.stale && (
        <Bjaelke farve="var(--neutral)">
          ⚠ Høsten er forældet
          {svar.hoest_alder_timer != null && ` (${svar.hoest_alder_timer.toFixed(0)} timer gammel)`}
          . Events nedenfor kan mangle eller have forkerte tidspunkter.
        </Bjaelke>
      )}

      {/* ── Næste event, med nedtælling ────────────────────────── */}
      {svar?.ok && svar.naeste && (
        <div style={{ padding: "7px 9px", background: "var(--accent-bg)",
                      borderBottom: "1px solid var(--border)" }}>
          <div style={{ fontSize: 10, color: "var(--text-muted)", letterSpacing: 0.4,
                        textTransform: "uppercase" }}>Næste</div>
          <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
            {/* ⚠ DATOEN SKAL MED. Uden den laeste "15:05" som i dag, mens
                eventet laa i morgen — og saa kunne det ikke findes i listen. */}
            <b style={{ fontSize: "1.06em" }}>
              {hvornaar(svar.naeste.dato_dk, svar.naeste.klokke_dk)}
            </b>
            <span style={{ fontWeight: 700 }}>{svar.naeste.titel}</span>
            <TierMaerke tier={svar.naeste.tier} />
            <span style={{ color: "var(--text-muted)" }}>
              ({nedtaelling(svar.naeste.minutter_til)})
            </span>
            {svar.naeste.dato_dk && svar.naeste.dato_dk !== dato && (
              <button onClick={() => setDato(svar.naeste!.dato_dk!)}
                      style={{ ...knapStil, fontSize: 10 }}
                      title="Gaa til den dag eventet ligger">gå til dagen →</button>
            )}
          </div>
        </div>
      )}

      {/* ── Dagens liste ───────────────────────────────────────── */}
      <div style={{ flex: 1, overflowY: "auto" }}>
        {henter && !svar && <Tom>Henter …</Tom>}
        {svar?.ok && alle.length === 0 && (
          <Tom>Ingen events {erIDag ? "i dag" : "denne dag"}.</Tom>
        )}
        {svar?.ok && alle.length > 0 && viste.length === 0 && (
          <Tom>Ingen tier 1-events — {antalT2} tier 2 er skjult. Slå dem til foroven.</Tom>
        )}
        {viste.length > 0 && (
          <table className="scanner-table" style={{ width: "100%" }}>
            <thead>
              <tr>
                {/* ⚠ nowrap + bredde nok til overskriften. Uden det blev
                    "Prognose" til "PROG..." og "Forrige" til "FORRI...", og en
                    afkortet overskrift over en talkolonne er en gaade, ikke en
                    etiket. */}
                <th style={{ ...H, textAlign: "left", width: 64 }}>Tid</th>
                <th style={{ ...H, textAlign: "left" }}>Event</th>
                <th style={{ ...H, width: 78 }}>Prognose</th>
                <th style={{ ...H, width: 74 }}>Forrige</th>
                <th style={{ ...H, width: 74 }}>Faktisk</th>
                <th style={{ ...H, width: 112 }}>Om</th>
              </tr>
            </thead>
            <tbody>
              {viste.map((e, i) => (
                <tr key={`${e.ts_utc}-${e.titel}-${i}`}
                    style={{ opacity: e.minutter_til != null && e.minutter_til < 0 ? 0.55 : 1 }}>
                  {/* ⚠ Uden klokkeslæt skrives "hele dagen" — ikke 00:00, som
                      ville påstå et tidspunkt kilden ikke har oplyst. */}
                  <td style={{ fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>
                    {e.klokke_dk ?? <i style={{ fontWeight: 400, color: "var(--text-muted)" }}>hele dagen</i>}
                  </td>
                  <td>
                    <span style={{ fontWeight: e.tier === 1 ? 700 : 500 }}>{e.titel}</span>
                    {" "}<TierMaerke tier={e.tier} />
                    {e.i_oevevindue && (
                      <span title="Ligger i oevevinduet 08:00–15:00 dansk"
                            style={{ marginLeft: 5, fontSize: 9.5, color: "var(--accent)" }}>●</span>
                    )}
                    {e.pladsholder && (
                      <span style={{ marginLeft: 6, color: "var(--bear)", fontSize: 10 }}>
                        ⚠ ubrugelig række
                      </span>
                    )}
                  </td>
                  <Tal v={e.forecast} />
                  <Tal v={e.previous} />
                  {/* Faktisk fremhæves: det er dét der flytter prisen. */}
                  <td style={{ textAlign: "right", fontVariantNumeric: "tabular-nums",
                               fontWeight: e.actual ? 700 : 400,
                               color: e.actual ? "var(--text-primary)" : "var(--text-muted)" }}>
                    {e.actual || "—"}
                  </td>
                  <td style={{ textAlign: "right", color: "var(--text-secondary)",
                               whiteSpace: "nowrap" }}
                      title={hvornaar(e.dato_dk, e.klokke_dk)}>
                    {nedtaelling(e.minutter_til)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* ── Fodnote: hvor data kommer fra ──────────────────────── */}
      {svar?.ok && (
        <div style={{ padding: "4px 8px", borderTop: "1px solid var(--border)",
                      fontSize: 10, color: "var(--text-muted)",
                      display: "flex", gap: 10, flexWrap: "wrap" }}>
          <span>ForexFactory · tider i dansk tid</span>
          {svar.sidste_hoest && <span>høstet {svar.sidste_hoest.replace("T", " ").replace("Z", " UTC")}</span>}
          <span>● = i øvevinduet 08:00–15:00</span>
        </div>
      )}
    </div>
  );
}

// ── Småting ─────────────────────────────────────────────────────────────────
// Talkolonnernes overskrifter: hoejrestillet og aldrig ombrudt.
const H: React.CSSProperties = { textAlign: "right", whiteSpace: "nowrap" };

const knapStil: React.CSSProperties = {
  background: "var(--bg-secondary)", border: "1px solid var(--border)",
  color: "var(--text-secondary)", borderRadius: 3, fontSize: 11,
  padding: "2px 8px", cursor: "pointer",
};
const aktivStil: React.CSSProperties = {
  borderColor: "var(--accent)", color: "var(--accent)", fontWeight: 700,
};

function TierMaerke({ tier }: { tier: number }) {
  if (tier === 1) return (
    <span title="Tier 1 — flytter markedet direkte og forudsigeligt"
          style={{ fontSize: 9, fontWeight: 800, padding: "1px 4px", borderRadius: 3,
                   background: "var(--bear-muted)", color: "var(--bear)",
                   border: "1px solid var(--bear)", whiteSpace: "nowrap" }}>T1</span>
  );
  if (tier === 2) return (
    <span title="Tier 2 — kan flytte markedet, mindre paalideligt"
          style={{ fontSize: 9, fontWeight: 700, padding: "1px 4px", borderRadius: 3,
                   background: "var(--bg-tertiary)", color: "var(--text-muted)",
                   border: "1px solid var(--border)", whiteSpace: "nowrap" }}>T2</span>
  );
  return null;
}

function Tal({ v }: { v: string | null }) {
  return (
    <td style={{ textAlign: "right", fontVariantNumeric: "tabular-nums",
                 color: v ? "var(--text-secondary)" : "var(--text-muted)" }}>
      {v || "—"}
    </td>
  );
}

function Bjaelke({ farve, children }: { farve: string; children: React.ReactNode }) {
  return (
    <div style={{ padding: "5px 9px", borderBottom: "1px solid var(--border)",
                  background: "var(--bg-tertiary)", color: farve, fontSize: 11.5 }}>
      {children}
    </div>
  );
}

function Tom({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ padding: 18, textAlign: "center", color: "var(--text-muted)" }}>
      {children}
    </div>
  );
}
