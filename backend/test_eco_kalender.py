#!/usr/bin/env python3
r"""
test_eco_kalender.py — hver kontrol proeves BEGGE veje
════════════════════════════════════════════════════════════════════════════════
En test der ikke kan fejle, maaler ingenting. Derfor har hver kontrol her baade
en kendt-positiv (det rigtige slipper igennem) og en kendt-negativ (det forkerte
bliver stoppet) — specens §8.

    python test_eco_kalender.py

⚠ TO STEDER HVOR SPECEN ER RETTET FREM FOR FULGT, og hvor testene er beviset:

1. §3.3 siger "normalisering: fjern m/m, q/q, y/y". DET VILLE SLETTE DATA.
   "CPI m/m" og "CPI y/y" udgives samme dag paa samme klokkeslaet (maalt i
   cachen 12-08: begge 08:30 ET). Fjernes perioden, faar de identisk
   fletnings-noegle, og den ene forsvinder — lydloest, paa maanedens vigtigste
   dag. `test_fletning` kraever at de forbliver to raekker.

2. §8's tidszone-kendt-negativ siger "08:30 ET -> 14:30 dansk OGSAA i december;
   fast offset ville give 13:30". Den proeve DISKRIMINERER IKKE: forskellen er 6
   timer baade sommer og vinter, saa et fast +6 ville bestaa den. Den rigtige
   faelde er de ~4 uger hvor EU og USA ikke har skiftet samtidig — i marts er
   forskellen 5 timer, og dér giver et fast +6 et forkert svar. Det er den proeve
   der staar her.
"""
from __future__ import annotations

import datetime as dt
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import eco_kalender as ek

FEJL: list[str] = []


def kraev(betingelse, hvad: str) -> None:
    if betingelse:
        print(f"  OK    {hvad}")
    else:
        print(f"  FEJL  {hvad}")
        FEJL.append(hvad)


def kraev_rejser(fn, hvad: str, ventet: type = ek.KildeFejl) -> None:
    try:
        fn()
    except ventet:
        print(f"  OK    {hvad}")
        return
    except Exception as e:
        print(f"  FEJL  {hvad}  (rejste {type(e).__name__}, ventede {ventet.__name__})")
        FEJL.append(hvad)
        return
    print(f"  FEJL  {hvad}  (rejste INTET)")
    FEJL.append(hvad)


# ── Hjaelpere ───────────────────────────────────────────────────────────────
def unix(iso_et: str) -> int:
    """'2026-08-12 08:30' i ET -> unix-sekunder."""
    naiv = dt.datetime.strptime(iso_et, "%Y-%m-%d %H:%M")
    return int(ek.ET.localize(naiv).timestamp())


def r(navn, valuta="USD", tid="2026-08-12 08:30", kilde="proeve", **kw):
    return ek._raekke(navn, valuta, unix(tid), kilde, **kw)


# ════════════════════════════════════════════════════════════════════════════
def test_filter():
    """Klassifikation paa TITEL. En kildes vigtighedsmarkering maa loefte op,
    aldrig smide ud."""
    print("\n[1] Filter — titel, ikke impact")
    # Kendt-positiv: Michigan overlever selv naar kilden rater den "Low".
    # ⚠ NAVNGIVEN PROEVESAG. Den fik MES til at falde 14-08, og et impact-filter
    # ville have skjult den.
    m = r("Prelim UoM Consumer Sentiment", kilde_vigtighed="Low")
    kraev(m["tier"] == 2, "Michigan faar tier 2 selv med impact=Low")
    kraev(r("CPI m/m")["tier"] == 1, "CPI m/m faar tier 1")
    kraev(r("FOMC Member Waller Speaks")["tier"] == 2, "FOMC Member <navn> Speaks -> tier 2 (praefiks)")

    # Kendt-negativ: stoej falder ud.
    kraev(r("Natural Gas Storage")["tier"] == 0, "Natural Gas Storage -> tier 0")
    kraev(r("Official Cash Rate", "NZD")["tier"] == 0, "NZD-stoej -> tier 0")
    kraev(r("MPC Member Pill Speaks", "GBP")["tier"] == 0, "GBP-stoej -> tier 0")
    # ⚠ DEN SKARPE PROEVE: SAMME TITEL, FORSKELLIGT LAND. Titlerne er ikke
    # enestaaende — Storbritannien har ogsaa "Unemployment Rate". Uden landefilter
    # stod britisk arbejdsloeshed som tier 1 paa en side om MES. Maalt paa den
    # foerste rigtige hoest 18-08: 26 ikke-USD-raekker var klassificeret forkert.
    kraev(r("Unemployment Rate", "USD")["tier"] == 1, "USD Unemployment Rate -> tier 1")
    kraev(r("Unemployment Rate", "GBP")["tier"] == 0, "GBP Unemployment Rate -> tier 0")
    kraev(r("Housing Starts", "CAD")["tier"] == 0, "CAD Housing Starts -> tier 0")
    kraev(r("Trade Balance", "EUR")["tier"] == 0, "EUR Trade Balance -> tier 0")
    # ⚠ Og en ukendt titel gaettes ikke ind i tier 1.
    kraev(r("CPI m/m ")["tier"] == 1, "trailing space trimmes")
    kraev(r("Core CPI Something Else")["tier"] == 0, "ukendt titel -> tier 0, ikke gaettet")


def test_tidszone():
    """DK udledes af tidsstemplet, aldrig af et fast offset."""
    print("\n[2] Tidszone")
    # Kendt-positiv: sommer og vinter er begge 6 timer.
    kraev(r("CPI m/m", tid="2026-08-12 08:30")["klokke_dk"] == "14:30",
          "08:30 ET -> 14:30 DK om sommeren")
    kraev(r("CPI m/m", tid="2026-12-10 08:30")["klokke_dk"] == "14:30",
          "08:30 ET -> 14:30 DK i december")

    # ⚠ KENDT-NEGATIV DER FAKTISK DISKRIMINERER. 2026: USA skifter 8. marts, EU
    # foerst 29. marts. I mellemtiden er forskellen 5 timer, ikke 6. Et fast +6
    # ville her give 14:30 og vaere forkert.
    x = r("CPI m/m", tid="2026-03-11 08:30")
    kraev(x["klokke_dk"] == "13:30",
          "08:30 ET -> 13:30 DK i DST-hullet marts (fast +6 ville give 14:30)")
    # Og den anden vej, i efteraarshullet: USA skifter 1. nov, EU 25. okt.
    y = r("CPI m/m", tid="2026-10-28 08:30")
    kraev(y["klokke_dk"] == "13:30",
          "08:30 ET -> 13:30 DK i DST-hullet oktober")


def test_oevevindue():
    """08:00-15:00 dansk paa en NYSE-handelsdag."""
    print("\n[3] Oevevindue")
    # Kendt-positiv
    kraev(r("CPI m/m", tid="2026-08-12 08:30")["i_oevevindue"] == 1,
          "14:30 DK paa en handelsdag flagges")
    # Kendt-negativ
    kraev(r("Prelim UoM Consumer Sentiment", tid="2026-08-14 10:00")["i_oevevindue"] == 0,
          "16:00 DK (Michigan) flagges IKKE")
    kraev(r("FOMC Statement", tid="2026-08-12 14:00")["i_oevevindue"] == 0,
          "20:00 DK (FOMC) flagges IKKE")
    # ⚠ NYSE-helligdag, ikke bare weekend. 2026-12-25 er juledag (fredag).
    helligdag = dt.date(2026, 12, 25)
    kraev(not ek.nyse_kalender.er_handelsdag(helligdag), "25-12-2026 er NYSE-lukket")
    kraev(r("CPI m/m", tid="2026-12-25 08:30")["i_oevevindue"] == 0,
          "event paa NYSE-helligdag flagges IKKE (weekday()<5 ville have sagt ja)")
    kraev(r("CPI m/m", tid="2026-08-15 08:30")["i_oevevindue"] == 0,
          "loerdag flagges IKKE")


def test_kun_dato():
    """Et gaettet klokkeslaet paa CPI er vaerre end intet."""
    print("\n[4] Kun-dato-events")
    med = r("CPI m/m", tid="2026-08-12 08:30", har_klokkeslet=True)
    uden = r("CPI m/m", tid="2026-08-12 08:30", har_klokkeslet=False)
    kraev(med["har_klokkeslet"] == 1 and med["i_oevevindue"] == 1,
          "event med klokkeslaet faar i_oevevindue")
    kraev(uden["har_klokkeslet"] == 0 and uden["klokke_dk"] is None,
          "kun-dato-event faar har_klokkeslet=0 og ingen tid")
    kraev(uden["i_oevevindue"] == 0,
          "kun-dato-event taeller IKKE som oevevindue-event")


def test_fletning():
    """Samme event fra to kilder -> én raekke. Forskellige events -> to."""
    print("\n[5] Sammenfletning")
    # Kendt-positiv: synonym + formatvariation flettes.
    a = r("CPI m/m", kilde="ff")
    b = r("CPI (MoM)", kilde="investing")        # synonym -> "CPI m/m"
    ud, rap = ek.flet([a, b])
    kraev(len(ud) == 1, "'CPI (MoM)' flettes med 'CPI m/m'")
    kraev(rap["dubletter_flettet"] == 1, "dubletten taelles i rapporten")
    kraev("ff" in ud[0]["kilde"] and "investing" in ud[0]["kilde"],
          "begge kilder staar paa den flettede raekke")

    c = r("Inflation Rate MoM", kilde="te")      # TE-synonym -> "CPI m/m"
    kraev(len(ek.flet([a, c])[0]) == 1, "'Inflation Rate MoM' (TE) flettes med 'CPI m/m'")

    # ⚠ KENDT-NEGATIV 1 (specens egen): Core maa ikke flette med ikke-Core.
    kraev(len(ek.flet([r("CPI m/m"), r("Core CPI m/m")])[0]) == 2,
          "'Core CPI m/m' flettes IKKE med 'CPI m/m'")

    # ⚠ KENDT-NEGATIV 2 (min rettelse til §3.3): m/m maa ikke flette med y/y.
    # De udgives samme dag paa samme klokkeslaet — havde vi fjernet perioden som
    # specen skriver, ville den ene forsvinde lydloest.
    mm, yy = r("CPI m/m", tid="2026-08-12 08:30"), r("CPI y/y", tid="2026-08-12 08:30")
    kraev(len(ek.flet([mm, yy])[0]) == 2,
          "'CPI y/y' flettes IKKE med 'CPI m/m' trods samme dag OG samme klokkeslaet")
    kraev(ek.normaliser_titel("CPI m/m") != ek.normaliser_titel("CPI y/y"),
          "normaliseringen skiller m/m fra y/y")

    # Kendt-negativ 3: uenighed om tid LOGGES, skjules ikke.
    t1 = r("Retail Sales m/m", tid="2026-08-14 08:30", kilde="ff")
    t2 = r("Retail Sales m/m", tid="2026-08-14 09:30", kilde="te")
    ud2, rap2 = ek.flet([t1, t2])
    kraev(len(rap2["klokkeslaets_uenigheder"]) == 1,
          "to kilder uenige om klokkeslaet -> uenigheden logges")
    kraev(len(ud2) == 1, "men de er stadig ét event, ikke to")


def test_kilde_sundhed():
    """Tomt, afkortet og foraeldet skal ALLE rejse."""
    print("\n[6] Kilde-sundhed")
    feed = ek.ForexFactoryFeed()
    i_dag = dt.datetime.now(ek.ET)

    def raa(n, dage_gammel=0):
        d = (i_dag - dt.timedelta(days=dage_gammel)).replace(microsecond=0)
        return [{"title": "CPI m/m", "country": "USD", "date": d.isoformat(),
                 "impact": "High"} for _ in range(n)]

    # Kendt-positiv
    feed.sundhedstjek(raa(40))
    print("  OK    sundt svar passerer")
    # Kendt-negativ, alle tre
    kraev_rejser(lambda: feed.sundhedstjek([]), "TOMT svar rejser")
    kraev_rejser(lambda: feed.sundhedstjek(raa(5)), "AFKORTET svar rejser (5 events paa en uge)")
    kraev_rejser(lambda: feed.sundhedstjek(raa(40, dage_gammel=40)),
                 "FORAELDET svar rejser (40 dage gammelt)")
    kraev_rejser(lambda: feed.sundhedstjek([{"country": "USD", "date": "x"}]),
                 "manglende felt rejser")

    # ⚠ HTML-KILDEN SKAL VAERE STRENGERE (rev. A1): en parser der returnerer nul
    # raekker fordi et klassenavn skiftede, maa ALDRIG ligne en rolig uge.
    uge = ek.ForexFactoryUge(html="<html>ingen kalender her</html>")
    kraev_rejser(lambda: uge.hent(dt.date(2026, 8, 9), dt.date(2026, 8, 15)),
                 "HTML uden kalender-struktur rejser (layoutaendring != rolig uge)")
    tom = "calendarComponentStates[1] = { days: [] }"
    kraev_rejser(lambda: ek.ForexFactoryUge(html=tom).hent(dt.date(2026, 8, 9),
                                                           dt.date(2026, 8, 15)),
                 "HTML med tomt days-array rejser")


# ── Databasetests ───────────────────────────────────────────────────────────
class Sandkasse:
    """Midlertidig db + eksportmappe. Modulets stier omdirigeres, saa en test
    aldrig roerer trading_dash.db eller den rigtige droplog."""

    def __enter__(self):
        self.dir = Path(tempfile.mkdtemp(prefix="eco_test_"))
        self._gem = (ek.EKSPORT_DIR, ek.EKSPORT_FIL, ek.DROPLOG)
        ek.EKSPORT_DIR = self.dir / "eksport"
        ek.EKSPORT_FIL = ek.EKSPORT_DIR / "eco_events.csv"
        ek.DROPLOG = ek.EKSPORT_DIR / "droppede_titler.csv"
        self.db = self.dir / "proeve.db"
        self.con = ek.forbind(self.db)
        return self

    def __exit__(self, *a):
        self.con.close()
        ek.EKSPORT_DIR, ek.EKSPORT_FIL, ek.DROPLOG = self._gem
        shutil.rmtree(self.dir, ignore_errors=True)


def _snapshot(con, med_tid=False):
    kol = ("ts_utc,titel,land,ts_dk,dato_dk,klokke_dk,tier,begrundelse,kilde,"
           "forecast,previous,actual,forecast_ved_release,har_klokkeslet,i_oevevindue")
    if med_tid:
        kol += ",foerst_set,sidst_bekraeftet"
    return [tuple(x) for x in con.execute(
        f"SELECT {kol} FROM eco_events ORDER BY ts_utc, titel").fetchall()]


def test_idempotens():
    print("\n[7] Idempotens")
    with Sandkasse() as s:
        raekker = [r("CPI m/m"), r("Core CPI m/m"), r("Natural Gas Storage")]
        ek.gem(s.con, raekker)
        foer = _snapshot(s.con)
        ek.gem(s.con, raekker)
        efter = _snapshot(s.con)
        # Kendt-positiv
        kraev(foer == efter, "to gem-koersler giver identisk base")
        kraev(len(foer) == 3, "og ingen dubletter (3 raekker ind, 3 ud)")

        # ⚠ KENDT-NEGATIV: KAN TESTEN OVERHOVEDET SE ET HENTETIDSSTEMPEL?
        # Uden den her proeve maaler idempotens-testen ingenting — den ville
        # ogsaa bestaa hvis sammenligningen var blind. Her tvinges den til at
        # opdage en tidsafhaengig kolonne. (Revision G: vis det input der VILLE
        # faa kontrollen til at fejle.)
        import time
        med_foer = _snapshot(s.con, med_tid=True)
        time.sleep(1.1)
        ek.gem(s.con, raekker)
        med_efter = _snapshot(s.con, med_tid=True)
        kraev(med_foer != med_efter,
              "sammenligningen KAN se et hentetidsstempel (ellers maalte [7] intet)")
        kraev(_snapshot(s.con) == foer,
              "men datafelterne er stadig uaendrede — tiden bor kun i sidst_bekraeftet")


def test_bred_hoest():
    """Rev. B1: hoest bredt, filtrér ved visning."""
    print("\n[8] Bred hoest")
    with Sandkasse() as s:
        raekker = [r("CPI m/m"), r("Natural Gas Storage"),
                   r("Official Cash Rate", "NZD")]
        ek.gem(s.con, raekker)
        n = ek.log_droppede(raekker)
        # Kendt-positiv: tier 0 GEMMES og LOGGES med kilde.
        kraev(s.con.execute("SELECT COUNT(*) c FROM eco_events").fetchone()["c"] == 3,
              "alle tre raekker gemmes, ogsaa tier 0")
        kraev(n == 2, "de to tier 0-raekker logges")
        tekst = ek.DROPLOG.read_text(encoding="utf-8")
        kraev("proeve" in tekst, "droploggen baerer kilden")
        kraev(any(t == "Natural Gas Storage" for t, _, _ in ek.droplog_optaelling()),
              "droploggen kan slaas op pr. titel med optaelling")

        # Kendt-negativ: en tier 0-titel naar ALDRIG visningen.
        titler = {x["titel"] for x in ek.hent_dag(s.con, "2026-08-12")}
        kraev("Natural Gas Storage" not in titler, "tier 0 naar ikke hent_dag")
        kraev("Official Cash Rate" not in titler, "NZD-stoej naar ikke hent_dag")
        kraev("CPI m/m" in titler, "men tier 1 goer")
        alle = {x["titel"] for x in ek.hent_events(s.con, "2026-01-01", "2026-12-31")}
        kraev("Natural Gas Storage" not in alle, "tier 0 naar ikke hent_events")


def test_omklassificer():
    """Rev. B2's loefte: en forfremmet titel skal gaelde BAGUD, ikke kun fremad."""
    print("\n[8b] Omklassificering")
    with Sandkasse() as s:
        ek.gem(s.con, [r("Natural Gas Storage"), r("CPI m/m")])
        kraev("Natural Gas Storage" not in
              {x["titel"] for x in ek.hent_dag(s.con, "2026-08-12")},
              "foer forfremmelse: titlen vises ikke")

        # Forfrem titlen, som man ville goere efter et opslag i droploggen.
        ek.TIER2_TITLER.add("Natural Gas Storage")
        try:
            # ⚠ KENDT-NEGATIV FOERST: uden omklassificering gaelder forfremmelsen
            # KUN fremtidige hoester. B2's loefte om "data bagud med det samme"
            # ville vaere usandt, og ingen ville opdage det.
            kraev("Natural Gas Storage" not in
                  {x["titel"] for x in ek.hent_dag(s.con, "2026-08-12")},
                  "uden omklassificering gaelder forfremmelsen IKKE bagud")
            rap = ek.omklassificer(s.con)
            kraev(rap["aendret"] == 1, "omklassificering aendrer praecis én raekke")
            kraev("Natural Gas Storage" in
                  {x["titel"] for x in ek.hent_dag(s.con, "2026-08-12")},
                  "efter omklassificering vises historikken ogsaa")
            kraev(ek.omklassificer(s.con)["aendret"] == 0,
                  "og en anden koersel aendrer intet (idempotent)")
        finally:
            ek.TIER2_TITLER.discard("Natural Gas Storage")


def test_stale():
    """En fejlet hoest og en stille dag maa ALDRIG se ens ud."""
    print("\n[9] stale")
    with Sandkasse() as s:
        nu = dt.datetime.now(dt.timezone.utc)
        # Kendt-positiv: frisk hoest -> stale=False
        s.con.execute("INSERT INTO eco_hoest (ts_utc,kilde,ok,antal) VALUES (?,?,1,50)",
                      (nu.strftime("%Y-%m-%dT%H:%M:%SZ"), "forexfactory_feed"))
        s.con.commit()
        kraev(ek.status(s.con)["stale"] is False, "frisk hoest -> stale=False")

        # Kendt-negativ: for gammel hoest -> stale=True, IKKE en tom liste
        s.con.execute("DELETE FROM eco_hoest")
        gammel = nu - dt.timedelta(hours=ek.MAX_HOEST_ALDER_TIMER + 5)
        s.con.execute("INSERT INTO eco_hoest (ts_utc,kilde,ok,antal) VALUES (?,?,1,50)",
                      (gammel.strftime("%Y-%m-%dT%H:%M:%SZ"), "forexfactory_feed"))
        s.con.commit()
        st = ek.status(s.con)
        kraev(st["stale"] is True, "hoest aeldre end graensen -> stale=True")
        kraev(st["sidste_hoest"] is not None and st["alder_timer"] > ek.MAX_HOEST_ALDER_TIMER,
              "og svaret baerer tidsstempel + alder, ikke bare et flag")

        # Kendt-negativ 2: ALDRIG hoestet -> ogsaa stale. En tom database maa
        # ikke se ud som en rolig dag.
        s.con.execute("DELETE FROM eco_hoest")
        s.con.commit()
        kraev(ek.status(s.con)["stale"] is True, "aldrig hoestet -> stale=True")

        # Kendt-negativ 3: en FEJLET hoest maa ikke taelle som en vellykket.
        s.con.execute("INSERT INTO eco_hoest (ts_utc,kilde,ok,antal,besked) "
                      "VALUES (?,?,0,0,?)",
                      (nu.strftime("%Y-%m-%dT%H:%M:%SZ"), "forexfactory_feed", "TOMT svar"))
        s.con.commit()
        st2 = ek.status(s.con)
        kraev(st2["stale"] is True, "en fejlet hoest lige nu -> stadig stale")
        kraev(st2["kilder"][0]["seneste_ok"] is False and st2["kilder"][0]["seneste_besked"],
              "og fejlbeskeden staar i svaret")


def test_spraengradius():
    """Ét korrupt event maa ikke tage hele vinduet med sig."""
    print("\n[10] Spraengradius")
    with Sandkasse() as s:
        ek.gem(s.con, [r("CPI m/m"), r("Core CPI m/m"), r("Retail Sales m/m")])
        # ⚠ Et ts_utc der ikke kan laeses. Det er ikke hypotetisk: journal-500
        # var praecis den slags — én userialiserbar vaerdi der slog hele svaret
        # ihjel, saa 500 handler forsvandt paa grund af én.
        s.con.execute(
            "INSERT INTO eco_events (ts_utc,titel,land,ts_dk,dato_dk,klokke_dk,tier,"
            "begrundelse,kilde,har_klokkeslet,i_oevevindue,foerst_set,sidst_bekraeftet)"
            " VALUES ('IKKE-EN-DATO','Defekt event','USD','x','2026-08-12','08:30',1,"
            "'proeve','proeve',1,1,'x','x')")
        s.con.commit()

        ud = ek.hent_dag(s.con, "2026-08-12")
        pladsholdere = [x for x in ud if x.get("pladsholder")]
        gode = [x for x in ud if not x.get("pladsholder")]
        kraev(len(pladsholdere) == 1, "det defekte event bliver til ÉN pladsholder")
        kraev(len(gode) == 3, "og de tre brugbare events leveres alligevel")
        kraev(pladsholdere[0]["fejl"], "pladsholderen siger HVAD der gik galt")
        kraev(json.dumps(ud, ensure_ascii=False),
              "hele svaret kan stadig serialiseres til JSON")


def test_forecast_fryses():
    """Rev. A3: forecast_ved_release fryses ved release og overskrives aldrig."""
    print("\n[11] forecast_ved_release")
    with Sandkasse() as s:
        # 1) Foer release: forecast findes, actual goer ikke.
        ek.gem(s.con, [r("CPI m/m", forecast="0.3%", previous="0.2%")])
        x = s.con.execute("SELECT * FROM eco_events").fetchone()
        kraev(x["forecast_ved_release"] is None, "foer release er der intet frosset tal")
        # 2) Ved release: actual kommer -> forecast fryses.
        ek.gem(s.con, [r("CPI m/m", forecast="0.3%", previous="0.2%", actual="0.4%")])
        x = s.con.execute("SELECT * FROM eco_events").fetchone()
        kraev(x["forecast_ved_release"] == "0.3%", "forecast fryses i det oejeblik actual kommer")
        # 3) Kendt-negativ: en senere REVISION af forecast maa ikke aendre det frosne.
        ek.gem(s.con, [r("CPI m/m", forecast="0.9%", previous="0.2%", actual="0.4%")])
        x = s.con.execute("SELECT * FROM eco_events").fetchone()
        kraev(x["forecast"] == "0.9%", "det levende forecast opdateres")
        kraev(x["forecast_ved_release"] == "0.3%",
              "men det FROSNE staar fast (en revision maa ikke omskrive historien)")


def test_skema_drift():
    """db_schema.sql og eco_kalender.SKEMA maa ikke drive fra hinanden."""
    print("\n[12] Skema-drift")
    t = (Path(__file__).resolve().parent / "db_schema.sql").read_text(encoding="utf-8")
    kraev(">>> ECO_SKEMA" in t and "<<< ECO_SKEMA" in t, "markoererne findes i db_schema.sql")
    blok = t.split(">>> ECO_SKEMA", 1)[1].split("<<< ECO_SKEMA", 1)[0]
    blok = "\n".join(l for l in blok.splitlines() if not l.strip().startswith("--"))
    norm = lambda s: " ".join(s.split())
    kraev(norm(blok) == norm(ek.SKEMA),
          "db_schema.sql-blokken er ORDRET identisk med eco_kalender.SKEMA")


def test_eksport():
    """Fladfilen til Trading Practice — kun tier 1/2, ingen HTTP."""
    print("\n[13] Eksport til Trading Practice")
    with Sandkasse() as s:
        ek.gem(s.con, [r("CPI m/m"), r("Natural Gas Storage"),
                       r("FOMC Statement", tid="2026-08-12 14:00", har_klokkeslet=False)])
        sti = ek.eksporter(s.con)
        linjer = sti.read_text(encoding="utf-8").strip().splitlines()
        kraev(len(linjer) == 3, "kun tier 1/2 eksporteres (2 raekker + hoved)")
        kraev("Natural Gas Storage" not in "\n".join(linjer), "tier 0 er ikke i filen")
        kraev(any(l.split(",")[1] == "" for l in linjer[1:]),
              "kun-dato-event eksporteres UDEN klokkeslaet, ikke med et gaettet")


# ════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print(__doc__.split("____\n")[0].strip()[:0] or "test_eco_kalender")
    for f in (test_filter, test_tidszone, test_oevevindue, test_kun_dato,
              test_fletning, test_kilde_sundhed, test_idempotens, test_bred_hoest,
              test_omklassificer, test_stale, test_spraengradius, test_forecast_fryses,
              test_skema_drift, test_eksport):
        f()
    print("\n" + "=" * 70)
    if FEJL:
        print(f"{len(FEJL)} FEJL:")
        for x in FEJL:
            print(f"  · {x}")
        sys.exit(1)
    print("ALLE KONTROLLER BESTAAET — baade de kendt-positive og de kendt-negative")
