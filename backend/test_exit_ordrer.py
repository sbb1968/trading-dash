"""
test_exit_ordrer.py — kan exit-logikken fejle på de måder der koster penge?
════════════════════════════════════════════════════════════════════════════════
Uden NT8. `nt_forbindelse` er mocket, så hver regel kan prøves isoleret —
også de tilstande man ikke kan fremkalde på en demokonto, som "ATI svarer ikke".

De fire der betyder mest:

  1. ⚠ `netto=None` udløser INGEN annullering. UKENDT er ikke flad, og en
     annullering på et opslag vi ikke fik, fjerner beskyttelsen fra en position
     der stadig findes.
  2. ⚠ `netto=0` annullerer ALT. En efterladt stop på en lukket position åbner
     en NY position i modsat retning når den udløses.
  3. ⚠ Trailing flytter KUN i beskyttende retning. En stop der kan gå tilbage,
     følger markedet ned i stedet for at beskytte mod det.
  4. ⚠ Lukketidspunktet i sommertidsugerne. 22:00 dansk er 17:00 ET i de uger —
     CME's daglige pause, hvor en markedsordre ikke fylder.

    python test_exit_ordrer.py
"""
from __future__ import annotations

import asyncio
import datetime
import pathlib
import sys
import zoneinfo

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import exit_ordrer as EX
import nt_forbindelse as NT

DK = zoneinfo.ZoneInfo("Europe/Copenhagen")
ET = zoneinfo.ZoneInfo("America/New_York")

fejl: list[str] = []


def kraev(betingelse: bool, hvad: str) -> None:
    print(f"  {'OK  ' if betingelse else 'FEJL'} {hvad}")
    if not betingelse:
        fejl.append(hvad)


# ══════════════════════════════════════════════════════════════════════════
class FalskTracker:
    def __init__(self, raekker=None):
        self._entries = list(raekker or [])
        self.annullerede: list[str] = []
        self.opdateringer: list[tuple] = []

    def find(self, oid):
        return next((dict(e) for e in self._entries
                     if str(e.get("order_id")) == str(oid)), None)

    def opdater(self, oid, **f):
        self.opdateringer.append((str(oid), f))
        for e in self._entries:
            if str(e.get("order_id")) == str(oid):
                e.update(f)
                return True
        return False

    def exit_ordrer_for(self, pid, kun_aktive=True):
        ud = []
        for e in self._entries:
            if str(e.get("parent_order_id") or "") != str(pid):
                continue
            if kun_aktive and e.get("status") in ("Filled", "Cancelled", "Rejected"):
                continue
            ud.append(dict(e))
        return ud

    def record_placed(self, **kw):
        self._entries.append(dict(kw, status=kw.get("maalt", {}).get("status")
                                  if kw.get("maalt") else "afventer"))


class FalskJournal:
    def __init__(self):
        self.events: list[dict] = []

    async def log_event(self, **kw):
        self.events.append(kw)

    def typer(self):
        return [e.get("event_type") for e in self.events]


def mock_nt(**svar):
    """Erstat nt_forbindelse's funktioner. Returnerer en liste over kald."""
    kald: list[tuple] = []
    orig = {n: getattr(NT, n) for n in
            ("klar", "position", "annuller", "afvent_ordre", "aendr",
             "send_ordre", "afvent_aktiv", "annuller_alt", "ordre_status",
             "fyldning", "order_ref")}

    def reg(navn, retur):
        def f(*a, **kw):
            kald.append((navn, a, kw))
            return retur(*a, **kw) if callable(retur) else retur
        return f

    NT.klar = reg("klar", svar.get("klar", {"konto": "DEMO8580770"}))
    NT.position = reg("position", svar.get("position", {"netto": 1, "noegle": "MES DEC26"}))
    NT.annuller = reg("annuller", {"logliner": []})
    NT.afvent_ordre = reg("afvent_ordre", svar.get("afvent_ordre",
                          {"status": "Cancelled", "terminal": True,
                           "filled": 0, "avg_fill": 0.0}))
    NT.aendr = reg("aendr", svar.get("aendr", {"logliner": []}))
    NT.send_ordre = reg("send_ordre", {"logliner": [], "fil_tilbage": ""})
    NT.afvent_aktiv = reg("afvent_aktiv", svar.get("afvent_aktiv",
                          {"status": "Working", "aktiv": True, "terminal": False,
                           "filled": 0, "avg_fill": 0.0}))
    NT.annuller_alt = reg("annuller_alt", {"logliner": []})
    NT.ordre_status = reg("ordre_status", svar.get("ordre_status", ""))
    NT.fyldning = reg("fyldning", svar.get("fyldning", (0, 0.0)))
    return kald, orig


def gendan(orig):
    for n, f in orig.items():
        setattr(NT, n, f)


# ══════════════════════════════════════════════════════════════════════════
def test_klassifikation() -> None:
    print("  ── klassifikation: typen gemmes, den udledes ikke senere ──")
    for netto, action, forventet in [
        (0,  "BUY",  "LONG"),  (0,  "SELL", "SHORT"),
        (2,  "BUY",  "LONG"),  (2,  "SELL", "EXIT"),    # tilkoeb vs reduktion
        (-2, "SELL", "SHORT"), (-2, "BUY",  "EXIT"),
        (None, "BUY", "UKENDT"), (None, "SELL", "UKENDT"),
    ]:
        f = EX.klassificer(netto, action)
        kraev(f == forventet, f"netto={netto} {action} -> {f}")
    # ⚠ UKENDT maa ikke blive til LONG. En raekke der paastaar at vaere en
    # aabning, faar exit-knapper paa en position der maaske ikke findes.
    kraev(EX.klassificer(None, "BUY") != "LONG",
          "⚠ ukendt netto bliver ALDRIG til LONG")


def test_legacy_navn() -> None:
    """⚠ En raekke lagt FOER omdoebningen skal stadig kunne vises og slettes."""
    print("\n  -- PLOSS -> SLOSS --")
    kraev(EX.TYPER == ("SLOSS", "TPROF", "TRAIL"),
          f"typerne hedder nu SLOSS ({EX.TYPER})")
    kraev(EX.normaliser_type("PLOSS") == "SLOSS",
          "⚠ den gamle stavemaade oversaettes")
    kraev(EX.er_exit_type("PLOSS"), "⚠ …og genkendes stadig som en exit-ordre")
    kraev(EX.er_exit_type("sloss"), "store/smaa bogstaver er lige gyldige")
    kraev(not EX.er_exit_type("LONG"), "LONG er ikke en exit-type")
    kraev(not EX.er_exit_type(""), "tom type er ikke en exit-type")

    # ⚠ Det der ville vaere gaaet galt: en levende stop loss lagt i gaar
    # forsvinder fra vinduet, mens den stadig ligger hos NinjaTrader.
    gammel = {"order_id": "NTX_G", "source": "manual_exit",
              "ordre_type": "PLOSS", "parent_order_id": "NTM1",
              "status": "Working", "shares": 1, "action": "SELL",
              "trigger_pris": 6800.0, "ticker": "MES"}
    tr = FalskTracker([gammel])
    fundet = [e for e in tr._entries if EX.er_exit_type(e.get("ordre_type"))]
    kraev(len(fundet) == 1,
          "⚠ en PLOSS-raekke fra i gaar findes stadig som exit-ordre")


def test_validering() -> None:
    print("\n  ── prisvalidering (så NT8 ikke behøver afvise) ──")

    def ok(t, pris, retning, kurs):
        try:
            EX.valider_pris(t, pris, retning, kurs)
            return True, ""
        except ValueError as e:
            return False, str(e)

    kraev(EX.hele_ticks(6812.25) and EX.hele_ticks(6812.0),
          "hele ticks genkendes")
    kraev(not EX.hele_ticks(6812.30), "6812,30 er ikke et helt tick")

    g, b = ok("SLOSS", 6812.30, "LONG", 6820.0)
    kraev(not g and "tick" in b, f"skæv pris afvises: {b[:46]}")

    g, b = ok("SLOSS", 6830.0, "LONG", 6820.0)
    kraev(not g and "under" in b, f"long: stop over kurs afvises: {b[:46]}")
    g, _ = ok("SLOSS", 6810.0, "LONG", 6820.0)
    kraev(g, "long: stop under kurs godkendes")

    g, b = ok("TPROF", 6810.0, "LONG", 6820.0)
    kraev(not g and "over" in b, f"long: target under kurs afvises: {b[:46]}")

    # ⚠ SPEJLET FOR SHORT. 25-08 stod Iben og kunne ikke handle, fordi reglen
    # var skrevet som om kun long fandtes.
    g, b = ok("SLOSS", 6810.0, "SHORT", 6820.0)
    kraev(not g and "over" in b, f"short: stop under kurs afvises: {b[:46]}")
    g, _ = ok("SLOSS", 6830.0, "SHORT", 6820.0)
    kraev(g, "short: stop over kurs godkendes")
    g, _ = ok("TPROF", 6810.0, "SHORT", 6820.0)
    kraev(g, "short: target under kurs godkendes")

    # ⚠ Ingen kurs -> afvis. At sende alligevel ville lade NT8 om det, og NT8
    # svarer med en modal dialogboks paa Ibens skaerm (maalt 06-10, P7).
    g, b = ok("SLOSS", 6810.0, "LONG", None)
    kraev(not g and "kurs" in b, f"ingen kurs -> afvis: {b[:46]}")

    g, b = ok("TRAIL", 6810.0, "LONG", 6820.0)
    kraev(not g, "TRAIL med pris afvises — afstanden kommer fra Konfiguratoren")
    g, _ = ok("TRAIL", None, "LONG", 6820.0)
    kraev(g, "TRAIL uden pris godkendes")


def test_config() -> None:
    print("\n  ── trail-afstand ──")
    for v, skal_virke in [(4.0, True), (0.25, True), (50.0, True),
                          (0, False), (-1, False), (51, False), (4.1, False)]:
        try:
            EX.saet_config(v)
            g = True
        except ValueError:
            g = False
        kraev(g == skal_virke, f"{v} -> {'godkendt' if g else 'afvist'}")
    EX.saet_config(4.0)
    kraev(EX.hent_config()["trail_afstand"] == 4.0, "værdien gemmes og læses")

    # ⚠ OCO-LØBENUMMERET SKAL OVERLEVE EN GENSTART. P7: NT8 afviser et genbrugt
    # id, og en tæller i hukommelsen ville starte forfra efter en genstart og
    # ramme et id NT8 allerede har set samme dag.
    a, b = EX._nyt_oco_id(), EX._nyt_oco_id()
    kraev(a != b, f"to OCO-id'er i træk er forskellige ({a}, {b})")
    kraev(a.startswith("TDOCO"), f"formen er TDOCO<dato><nr> ({a})")
    gemt = EX._laes_konfig()["oco_loebenr"]
    c = EX._nyt_oco_id()
    kraev(EX._laes_konfig()["oco_loebenr"] == gemt + 1,
          "⚠ løbenummeret persisteres — overlever en genstart")
    kraev(c not in (a, b), "og det tæller kun op")


def test_log_aflaesning() -> None:
    """⚠ NT8's log baerer det ATI ikke melder. Maalt 06-10 — se spec §4a."""
    print("\n  ── aflaesning af NT8's Order=-linje ──")
    STOP = ("2026-10-06 07:42:01:710|1|32|Order='602502520018/DEMO8580770' "
            "Name='' New state='Accepted' Instrument='MES DEC26' Action='Sell' "
            "Limit price=0 Stop price=7824.75 Quantity=1 Type='Stop Market' "
            "Time in force=DAY Oco='TDOCO1265318' Filled=0 Fill price=0 "
            "Error='No error' Native error=''")
    d = EX.laes_ordre_fra_log([STOP])
    kraev(d.get("stop") == 7824.75, f"stoppris laeses ({d.get('stop')})")
    kraev(d.get("antal") == 1, f"antal laeses ({d.get('antal')})")
    kraev(d.get("oco") == "TDOCO1265318", f"OCO-binding laeses ({d.get('oco')})")
    kraev(d.get("status") == "Accepted", "status laeses")
    kraev(d.get("fejl") == "No error", "fejlteksten laeses")

    LIMIT = ("Order='x/y' New state='Accepted' Limit price=7832 Stop price=0 "
             "Quantity=1 Type='Limit' Oco='TDOCO9' Error='No error'")
    # ⚠ Stop price=0 paa en limit-ordre betyder "ikke en stop-ordre", ikke
    # "stop paa nul". Blev den laest som 0, ville trailing tro at stoppen laa
    # paa nul og melde afvigelse ved hvert eneste gennemloeb.
    kraev(EX._stop_fra_log([LIMIT]) is None,
          "⚠ Stop price=0 bliver IKKE til en stop paa nul")
    kraev(EX.laes_ordre_fra_log([LIMIT]).get("limit") == 7832.0,
          "limitpris laeses")

    # ⚠ Den SIDSTE linje gaelder. En CHANGE giver 'Change submitted' med de nye
    # vaerdier og derefter 'Accepted'; laeser man den foerste, ser alt rigtigt
    # ud uanset hvad der skete bagefter.
    to = [STOP.replace("Accepted", "Change submitted").replace("7824.75", "7820.00"),
          STOP]
    kraev(EX.laes_ordre_fra_log(to).get("stop") == 7824.75,
          "sidste Order=-linje gaelder, ikke den foerste")

    kraev(EX.laes_ordre_fra_log([]) == {}, "ingen logliner -> tomt svar")
    kraev(EX._stop_fra_log(["noget uden Order="]) is None,
          "en linje uden Order= giver None")


def test_lukketid() -> None:
    print("\n  ── lukketidspunkt (sommertid) ──")
    for dato, forventet, hvorfor in [
        ("2026-10-06", "22:00", "normal dag"),
        ("2026-10-23", "22:00", "sidste fredag før EU-skiftet"),
        ("2026-10-27", "21:50", "⚠ EU på vintertid, USA ikke"),
        ("2026-10-30", "21:50", "⚠ samme uge"),
        ("2026-11-03", "22:00", "USA fulgte efter"),
        ("2027-03-16", "21:50", "⚠ USA på sommertid, EU ikke"),
        ("2027-03-30", "22:00", "EU fulgte efter"),
    ]:
        nu = datetime.datetime.fromisoformat(dato + "T12:00").replace(tzinfo=DK)
        t = EX.lukketidspunkt(nu)
        kraev(f"{t:%H:%M}" == forventet,
              f"{dato} -> {t:%H:%M} dansk ({t.astimezone(ET):%H:%M} ET) · {hvorfor}")
        # ⚠ Og ALDRIG 17:00 ET — CME's daglige pause, hvor intet fylder.
        kraev(t.astimezone(ET).hour < 17,
              f"    …og aldrig i CME's pause ({t.astimezone(ET):%H:%M} ET)")


async def test_overvaagning() -> None:
    print("\n  ── overvågningen: de to regler der koster penge ──")
    EXIT = {"order_id": "NTX1", "source": "manual_exit", "ordre_type": "SLOSS",
            "parent_order_id": "NTM1", "status": "Working", "bekraeftet": True,
            "shares": 1, "action": "SELL", "trigger_pris": 6800.0,
            "ticker": "MES"}

    # ⚠ 1. netto=None -> ANNULLÉR INTET.
    tr, jo = FalskTracker([dict(EXIT)]), FalskJournal()
    kald, orig = mock_nt(position={"netto": None, "noegle": "MES DEC26"})
    try:
        o = EX.Overvaagning(tr, jo, hent_kurs=lambda: _ingen(),
                            instrument_for=lambda: "MES 12-26")
        await o._tik("MES 12-26", "DEMO8580770")
        kraev(not any(k[0] == "annuller" for k in kald),
              "⚠ netto=UKENDT -> der annulleres INTET")
        kraev(not any(k[0] == "send_ordre" for k in kald),
              "⚠ …og der sendes ingen markedsordre")
    finally:
        gendan(orig)

    # ⚠ 2. netto=0 -> ANNULLÉR ALT.
    tr, jo = FalskTracker([dict(EXIT)]), FalskJournal()
    kald, orig = mock_nt(position={"netto": 0, "noegle": "MES DEC26"})
    try:
        o = EX.Overvaagning(tr, jo, hent_kurs=lambda: _ingen(),
                            instrument_for=lambda: "MES 12-26")
        await o._tik("MES 12-26", "DEMO8580770")
        kraev(any(k[0] == "annuller" for k in kald),
              "⚠ netto=0 -> alle exit-ordrer annulleres")
    finally:
        gendan(orig)

    # ── 3. Trailing flytter kun i beskyttende retning ──────────────────
    print("\n  ── trailing ──")
    for navn, hoejeste, kurs, nu_stop, skal_flytte in [
        ("kursen stiger 2 point", 6820.0, 6822.0, 6816.0, True),
        ("kursen falder",         6820.0, 6818.0, 6816.0, False),
        ("kursen står stille",    6820.0, 6820.0, 6816.0, False),
        # ⚠ 6820,05 -> stop 6816,05 -> rundes til 6816,00 = uaendret.
        #   (6820,20 ville give 6816,20 -> rundes OP til 6816,25, altsaa
        #    praecis ét tick, og saa SKAL den flytte. Afrundingen kan
        #    goere en bevaegelse under et tick til et helt tick — men kun
        #    i strammende retning, saa det er ufarligt.)
        ("stigning under 1 tick", 6820.0, 6820.05, 6816.0, False),
    ]:
        e = dict(EXIT, ordre_type="TRAIL", trail_afstand=4.0,
                 trail_hoejeste=hoejeste, trigger_pris=nu_stop)
        tr, jo = FalskTracker([e]), FalskJournal()
        kald, orig = mock_nt()
        try:
            o = EX.Overvaagning(tr, jo, hent_kurs=lambda: _ingen(),
                                instrument_for=lambda: "MES 12-26")
            await o._traek_trail(e, kurs, "MES 12-26")
            flyttet = any(k[0] == "aendr" for k in kald)
            kraev(flyttet == skal_flytte,
                  f"{navn}: {'flytter' if flyttet else 'flytter ikke'}")
        finally:
            gendan(orig)

    # ⚠ Og spejlet for short: stoppen maa kun flytte NED.
    e = dict(EXIT, ordre_type="TRAIL", action="BUY", trail_afstand=4.0,
             trail_hoejeste=6820.0, trigger_pris=6824.0)
    tr, jo = FalskTracker([e]), FalskJournal()
    kald, orig = mock_nt()
    try:
        o = EX.Overvaagning(tr, jo, hent_kurs=lambda: _ingen(),
                            instrument_for=lambda: "MES 12-26")
        await o._traek_trail(e, 6816.0, "MES 12-26")   # kursen falder
        kraev(any(k[0] == "aendr" for k in kald),
              "short: kursen falder -> stoppen flytter ned")
        kald.clear()
        await o._traek_trail(dict(e, trigger_pris=6820.0), 6830.0, "MES 12-26")
        kraev(not any(k[0] == "aendr" for k in kald),
              "short: kursen stiger -> stoppen bliver")
    finally:
        gendan(orig)

    # ── 4. Throttle ───────────────────────────────────────────────────
    e = dict(EXIT, ordre_type="TRAIL", trail_afstand=4.0,
             trail_hoejeste=6820.0, trigger_pris=6816.0)
    tr, jo = FalskTracker([e]), FalskJournal()
    kald, orig = mock_nt()
    try:
        o = EX.Overvaagning(tr, jo, hent_kurs=lambda: _ingen(),
                            instrument_for=lambda: "MES 12-26")
        for i in range(5):
            await o._traek_trail(dict(e, trigger_pris=6816.0 + i),
                                 6830.0 + i, "MES 12-26")
        n = sum(1 for k in kald if k[0] == "aendr")
        kraev(n == 1, f"⚠ fem ryk inden for 2 sek. giver ÉN CHANGE ({n})")
    finally:
        gendan(orig)


async def test_opret() -> None:
    print("\n  ── oprettelse ──")
    PARENT = {"order_id": "NTM1", "ordre_type": "LONG", "ticker": "MES",
              "status": "Filled"}

    async def kurs():
        return 6820.0

    # ⚠ Dobbelt-oprettelse af samme type afvises.
    aktiv = {"order_id": "NTX1", "source": "manual_exit", "ordre_type": "SLOSS",
             "parent_order_id": "NTM1", "status": "Working", "oco_id": "TDOCO1"}
    tr, jo = FalskTracker([PARENT, aktiv]), FalskJournal()
    _, orig = mock_nt()
    try:
        try:
            await EX.opret_exit(tr, jo, parent_order_id="NTM1", type_="SLOSS",
                                pris=6800.0, instrument="MES 12-26",
                                hent_kurs=kurs)
            kraev(False, "dobbelt SLOSS burde afvises")
        except EX.ExitFejl as e:
            kraev("allerede" in str(e), f"dobbelt SLOSS afvises: {str(e)[:44]}")

        # ⚠ Position None -> ingen ordre.
        gendan(orig)
        _, orig = mock_nt(position={"netto": None})
        tr2 = FalskTracker([PARENT])
        try:
            await EX.opret_exit(tr2, jo, parent_order_id="NTM1", type_="SLOSS",
                                pris=6800.0, instrument="MES 12-26",
                                hent_kurs=kurs)
            kraev(False, "ukendt position burde afvises")
        except EX.ExitFejl as e:
            kraev("bekræftes" in str(e) or "bekraeftes" in str(e),
                  f"⚠ ukendt position -> ingen ordre: {str(e)[:44]}")

        # ⚠ Position modsat retning -> ingen ordre.
        gendan(orig)
        _, orig = mock_nt(position={"netto": -1})
        try:
            await EX.opret_exit(FalskTracker([PARENT]), jo,
                                parent_order_id="NTM1", type_="SLOSS",
                                pris=6800.0, instrument="MES 12-26",
                                hent_kurs=kurs)
            kraev(False, "modsat position burde afvises")
        except EX.ExitFejl as e:
            kraev("peger ikke" in str(e), f"modsat retning afvises: {str(e)[:40]}")

        # ⚠ NT8 afviser -> dansk besked om dialogboksen.
        gendan(orig)
        _, orig = mock_nt(afvent_aktiv={"status": "Rejected", "aktiv": False,
                                        "terminal": True, "filled": 0,
                                        "avg_fill": 0.0})
        jo2 = FalskJournal()
        try:
            await EX.opret_exit(FalskTracker([PARENT]), jo2,
                                parent_order_id="NTM1", type_="SLOSS",
                                pris=6800.0, instrument="MES 12-26",
                                hent_kurs=kurs)
            kraev(False, "Rejected burde kaste")
        except EX.ExitFejl as e:
            kraev("dialogboksen" in str(e),
                  f"⚠ afvisning peger paa dialogboksen: {str(e)[:50]}")
            kraev("nt_exit_afvist" in jo2.typer(), "…og journaliseres")
    finally:
        gendan(orig)


async def test_genstart() -> None:
    """⚠ OVERLEVER EN BACKEND-GENSTART ALT DET DER SKAL?

    En backend-genstart midt i en session har kostet dette projekt dyrt før:
    27-09 doede K2 ved en genstart efter auto-start-vinduet, og 15:45-lukningen
    fyrede aldrig. En exit-ordre der "forsvandt" ved en genstart ville vaere
    vaerre: stoppen ligger stadig hos NT8, men backenden ved det ikke laengere
    — saa den bliver hverken flyttet eller ryddet naar positionen lukkes.

    Her proeves det med RIGTIG disk: en tracker skrives, en ny tracker laeses,
    og loekken genoptager paa den.
    """
    print("\n  ── genstart ──")
    import json
    import tempfile
    import orders_tracker as OT

    with tempfile.TemporaryDirectory() as d:
        aegte = OT.ORDERS_LOG
        try:
            OT.ORDERS_LOG = pathlib.Path(d) / "orders_log.json"

            # ── FØR genstarten: en TRAIL der har fulgt kursen et stykke op ──
            t1 = OT.OrdersTracker()
            t1._entries = []
            t1.record_placed(
                order_id="NTM_P", source="manual_watchlist", ticker="MES",
                action="BUY", shares=1, broker="NT8", ordre_type="LONG",
                maalt={"status": "Filled", "filled": 1, "avg_fill": 6800.0})
            t1.record_placed(
                order_id="NTX_T", source="manual_exit", ticker="MES",
                action="SELL", shares=1, order_type="STOPMARKET", broker="NT8",
                ordre_type="TRAIL", parent_order_id="NTM_P", oco_id="TDOCO1",
                trigger_pris=6816.0, trail_hoejeste=6820.0, trail_afstand=4.0)
            t1.opdater("NTX_T", status="Working", bekraeftet=True,
                       trail_stop_forventet=6816.0)

            kraev(OT.ORDERS_LOG.exists(), "trackeren skrev til disk")
            raa = json.loads(OT.ORDERS_LOG.read_text(encoding="utf-8"))
            gemt = next(e for e in raa if e["order_id"] == "NTX_T")
            kraev(gemt.get("trail_hoejeste") == 6820.0,
                  "⚠ trail_hoejeste ligger PAA DISKEN, ikke kun i hukommelsen")
            kraev(gemt.get("trigger_pris") == 6816.0, "…og den aktuelle stop")
            kraev(gemt.get("trail_afstand") == 4.0, "…og afstanden ordren blev "
                                                    "oprettet med")
            kraev(gemt.get("oco_id") == "TDOCO1", "…og OCO-bindingen")

            # ── GENSTARTEN: ny tracker, samme fil ───────────────────────────
            t2 = OT.OrdersTracker()
            e = t2.find("NTX_T")
            kraev(e is not None, "exit-ordren findes efter genstart")
            kraev(e.get("trail_hoejeste") == 6820.0,
                  "⚠ trail_hoejeste genopbygget fra disken")
            kraev(e.get("trigger_pris") == 6816.0, "…og stoppen")
            kraev(len(t2.exit_ordrer_for("NTM_P")) == 1,
                  "…og den hoerer stadig til sin position")

            # ── Loekken maa IKKE lægge nye ordrer ved opstart ───────────────
            jo = FalskJournal()
            kald, orig = mock_nt(position={"netto": 1, "noegle": "MES DEC26"})
            try:
                o = EX.Overvaagning(t2, jo, hent_kurs=lambda: _kurs(6818.0),
                                    instrument_for=lambda: "MES 12-26")
                await o._tik("MES 12-26", "DEMO8580770")
                kraev(not any(k[0] == "send_ordre" for k in kald),
                      "⚠ der laegges INGEN nye ordrer ved opstart")
                # Kursen (6818) er UNDER hoejeste (6820), saa stoppen skal
                # heller ikke flyttes.
                kraev(not any(k[0] == "aendr" for k in kald),
                      "⚠ og en uaendret kurs flytter ikke stoppen")
            finally:
                gendan(orig)

            # ── Men den SKAL indhente hvis markedet loeb mens vi var nede ──
            # ⚠ Det er ikke det samme som "aendrer ved opstart". En stop der
            # bliver staaende hvor den stod for en time siden, beskytter mod
            # et marked der ikke findes laengere.
            jo = FalskJournal()
            kald, orig = mock_nt(position={"netto": 1, "noegle": "MES DEC26"})
            try:
                o = EX.Overvaagning(t2, jo, hent_kurs=lambda: _kurs(6840.0),
                                    instrument_for=lambda: "MES 12-26")
                await o._tik("MES 12-26", "DEMO8580770")
                kraev(any(k[0] == "aendr" for k in kald),
                      "⚠ men den INDHENTER hvis kursen loeb mens vi var nede")
                e2 = t2.find("NTX_T")
                kraev(e2.get("trail_hoejeste") == 6840.0,
                      f"…og det nye hoejeste gemmes ({e2.get('trail_hoejeste')})")
                kraev(e2.get("trigger_pris") == 6836.0,
                      f"…med stoppen 4 points under ({e2.get('trigger_pris')})")
            finally:
                gendan(orig)

            # ── OCO-loebenummeret overlever ogsaa ──────────────────────────
            foer = EX._laes_konfig()["oco_loebenr"]
            EX._nyt_oco_id()
            kraev(EX._laes_konfig()["oco_loebenr"] == foer + 1,
                  "⚠ OCO-loebenummeret staar i exit_config.json, ikke i RAM")
        finally:
            OT.ORDERS_LOG = aegte


async def test_tvangsluk_blind() -> None:
    """⚠ Ukendt position: proev igen, og alarmér kun hvis der ER handlet."""
    print("\n  ── tvangsluk med ulaeselig position ──")

    # 1. Ingen handel i dag -> ingen alarm, kun en linje.
    tr, jo = FalskTracker([]), FalskJournal()
    kald, orig = mock_nt(position={"netto": None})
    try:
        ud = await EX.tvangsluk(tr, jo, instrument="MES 12-26",
                                blind_forsoeg=2, blind_pause=0.01)
        kraev(not any(k[0] == "send_ordre" for k in kald),
              "⚠ ulaeselig position -> der sendes INGEN markedsordre")
        kraev("tvangsluk_uden_handel" in jo.typer(),
              f"ikke handlet i dag -> ingen alarm ({jo.typer()})")
        kraev("tvangsluk_fejlet" not in jo.typer(),
              "⚠ …og telefonen ringer IKKE. En alarm hver aften hun ikke har "
              "handlet, bliver slaaet fra inden den betyder noget.")
    finally:
        gendan(orig)

    # 2. Handlet i dag -> alarm.
    import datetime as _dt
    i_dag = _dt.datetime.now().isoformat()
    tr = FalskTracker([{"order_id": "NTM_X", "ticker": "MES", "broker": "NT8",
                        "ordre_type": "LONG", "placed_at": i_dag}])
    jo = FalskJournal()
    kald, orig = mock_nt(position={"netto": None})
    try:
        ud = await EX.tvangsluk(tr, jo, instrument="MES 12-26",
                                blind_forsoeg=2, blind_pause=0.01)
        kraev("tvangsluk_fejlet" in jo.typer(),
              f"⚠ handlet i dag + ulaeselig -> ALARM ({jo.typer()})")
        kraev(ud["blind"] and ud["handlet_i_dag"], "og begge flag er sat")
    finally:
        gendan(orig)

    # 3. Og den proever faktisk igen foer den giver op.
    tr = FalskTracker([]); jo = FalskJournal()
    kald, orig = mock_nt(position={"netto": None})
    try:
        await EX.tvangsluk(tr, jo, instrument="MES 12-26",
                           blind_forsoeg=4, blind_pause=0.01)
        n = sum(1 for k in kald if k[0] == "position")
        kraev(n >= 4, f"der blev spurgt {n} gange foer den gav op")
    finally:
        gendan(orig)


async def _kurs(v):
    return v


async def _ingen():
    return None


def main() -> int:
    print("  ── exit-ordrer ──")
    test_klassifikation()
    test_legacy_navn()
    test_validering()
    test_config()
    test_log_aflaesning()
    test_lukketid()
    asyncio.run(test_overvaagning())
    asyncio.run(test_opret())
    asyncio.run(test_genstart())
    asyncio.run(test_tvangsluk_blind())
    print(f"\n  {'ALLE BESTAAET' if not fejl else f'⚠ {len(fejl)} FEJLEDE'}")
    return 1 if fejl else 0


if __name__ == "__main__":
    sys.exit(main())
