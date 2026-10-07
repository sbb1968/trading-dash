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


def test_exit_mulig() -> None:
    """Forsvinder knapperne naar positionen lukkes?

    ⚠ FUNDET I TRIN 6 AF DEN MANUELLE TEST (06-10): efter et salg fra
    watchlisten stod LONG-raekken stadig med tre blaa knapper, selv om
    positionen var flad og alle exit-ordrer var annulleret i NT8.

    `exit_mulig` saa kun paa om raekken var den NYESTE aabnende — aldrig paa om
    positionen fandtes. Et tryk ville have lagt en stop loss paa ingenting, og
    den foerste ordre der fylder paa en flad konto, AABNER en position.

    ⚠ Og det skal kunne afgoeres af TRACKEREN ALENE. Er ATI tavs, skal
    knapperne stadig forsvinde — ATI's netto er en ekstra bekraeftelse, ikke en
    betingelse.
    """
    print("\n  -- exit_mulig --")

    def r(tid, type_, action, filled, status="Filled", oid=None):
        return {"order_id": oid or f"o{tid}", "placed_at": f"2026-10-06T{tid}",
                "ordre_type": type_, "action": action, "filled": filled,
                "status": status, "ticker": "MES"}

    # 1. Kun en aaben long.
    raekker = [r("08:00", "LONG", "BUY", 1)]
    kraev(EX.netto_fra_raekker(raekker) == 1, "long 1 -> netto 1")
    kraev(EX.seneste_aabnende(raekker) == "o08:00", "…og den er den seneste")

    # 2. ⚠ KERNEN: long + exit der lukker -> netto 0.
    raekker.append(r("09:00", "EXIT", "SELL", 1))
    kraev(EX.netto_fra_raekker(raekker) == 0,
          "⚠ long + exit -> netto 0, altsaa INGEN knapper")

    # 3. Ny long bagefter -> knapper igen, paa den NYE raekke.
    raekker.append(r("10:00", "LONG", "BUY", 1))
    kraev(EX.netto_fra_raekker(raekker) == 1, "ny long -> netto 1 igen")
    kraev(EX.seneste_aabnende(raekker) == "o10:00",
          "…og knapperne sidder paa den NYE raekke")

    # 4. Short-siden spejlet.
    s2 = [r("08:00", "SHORT", "SELL", 2)]
    kraev(EX.netto_fra_raekker(s2) == -2, "short 2 -> netto -2")
    s2.append(r("09:00", "EXIT", "BUY", 2))
    kraev(EX.netto_fra_raekker(s2) == 0, "short + daekning -> 0")

    # 5. Delvis lukning efterlader en position.
    s3 = [r("08:00", "LONG", "BUY", 3), r("09:00", "EXIT", "SELL", 1)]
    kraev(EX.netto_fra_raekker(s3) == 2, "3 koebt, 1 solgt -> netto 2")

    # ── Det der ikke maa taelle med ──────────────────────────────────────
    # ⚠ En SLOSS-raekke der fylder, faar sin EGEN EXIT-raekke skrevet af
    # _exit_bogfoer. Taltes begge, ville samme fyldning blive regnet to gange,
    # og nettoet ville vippe til den forkerte side.
    s4 = [r("08:00", "LONG", "BUY", 1),
          r("09:00", "SLOSS", "SELL", 1),            # selve stop-ordren
          r("09:00:01", "EXIT", "SELL", 1)]          # bogfoeringen af fyldningen
    kraev(EX.netto_fra_raekker(s4) == 0,
          "⚠ en fyldt SLOSS taelles ÉN gang, ikke to")

    # En ordre der ikke fyldte, flytter ingenting.
    s5 = [r("08:00", "LONG", "BUY", 0, status="Cancelled"),
          r("09:00", "LONG", "BUY", 1)]
    kraev(EX.netto_fra_raekker(s5) == 1, "en annulleret ordre taeller ikke med")

    s6 = [r("08:00", "LONG", "BUY", 0, status="Working")]
    kraev(EX.netto_fra_raekker(s6) == 0,
          "⚠ en ordre der endnu ikke har fyldt, aabner ingen position")

    # ⚠ Raekkefoelgen maa ikke afhaenge af hvordan listen kom ind. Trackeren
    # leverer nyeste foerst; en naiv gennemgang ville regne exit'en foer entry.
    omvendt = list(reversed(s3))
    kraev(EX.netto_fra_raekker(omvendt) == 2,
          "nettoet er det samme uanset listens raekkefoelge")

    # -- SELVE BESLUTNINGEN ------------------------------------------
    # ⚠ Fejlen i trin 6 sad i SAMMENSTILLINGEN: begge funktioner ovenfor
    # var rigtige hver for sig, men "er du nyeste?" blev stillet uden
    # "findes positionen?". Derfor proeves exit_mulig_for direkte — det er
    # den der afgoer om knapperne staar der.
    aaben = [r("08:00", "LONG", "BUY", 1)]
    kraev(EX.exit_mulig_for(aaben) == "o08:00",
          "aaben long -> knapper paa den raekke")

    lukket = aaben + [r("09:00", "EXIT", "SELL", 1)]
    kraev(EX.exit_mulig_for(lukket) is None,
          "⚠ TRIN 6: lukket position -> INGEN raekke faar knapper")

    igen = lukket + [r("10:00", "LONG", "BUY", 1)]
    kraev(EX.exit_mulig_for(igen) == "o10:00",
          "ny position -> knapper paa den NYE raekke, ikke den gamle")
    kraev(EX.exit_mulig_for([]) is None, "ingen raekker -> ingen knapper")

    # ⚠ BEGGE MAADER EN POSITION KAN LUKKE SKAL FJERNE KNAPPERNE, og de ser
    # forskellige ud i trackeren. Fund b) i trin 8 havde netop to kilder:
    #   · manuel SAELG fra watchlisten  -> én EXIT-raekke (ovenfor)
    #   · en exit-ordre der FYLDER      -> TO raekker: selve SLOSS/TPROF/TRAIL
    #     og den EXIT-raekke _exit_bogfoer skriver ved siden af
    # Den anden form blev proevet paa nettoet, men ikke paa selve beslutningen.
    fyldt_stop = [r("08:00", "LONG", "BUY", 1),
                  r("09:00", "SLOSS", "SELL", 1),
                  r("09:00:01", "EXIT", "SELL", 1)]
    kraev(EX.exit_mulig_for(fyldt_stop) is None,
          "⚠ fyldt SLOSS -> INGEN knapper (fyldningen taelles én gang)")
    for t in ("TPROF", "TRAIL"):
        f = [r("08:00", "LONG", "BUY", 1), r("09:00", t, "SELL", 1),
             r("09:00:01", "EXIT", "SELL", 1)]
        kraev(EX.exit_mulig_for(f) is None, f"…og det samme for {t}")

    # ⚠ DAGENS FAKTISKE FORLOEB 06-10, hvor en TRAIL paa en SHORT er et KOEB.
    # Taltes TRAIL-raekken med, ville nettoet blive +1 i stedet for 0 — altsaa
    # knapper paa en flad konto, og med forkert fortegn.
    dagen = [r("08:36:59", "LONG",  "BUY",  1, oid="NTM..618394"),
             r("13:01:39", "EXIT",  "SELL", 1, oid="NTM..498355"),
             r("13:05:22", "SHORT", "SELL", 1, oid="NTM..721417"),
             r("13:06:34", "TRAIL", "BUY",  1, oid="NTX..793605"),
             r("13:32:56", "EXIT",  "BUY",  1, oid="NTX..793605_x")]
    kraev(EX.netto_fra_raekker(dagen) == 0,
          f"⚠ dagens forloeb -> netto 0 ({EX.netto_fra_raekker(dagen)})")
    kraev(EX.exit_mulig_for(dagen) is None,
          "⚠ …og ingen raekke faar knapper")
    # Og midt i forloebet, hvor shorten var aaben, skulle den HAVE knapper.
    kraev(EX.exit_mulig_for(dagen[:3]) == "NTM..721417",
          "midt i forloebet: knapperne sidder paa shorten")

    kraev(EX.seneste_aabnende([]) is None, "tom liste -> ingen seneste")
    kraev(EX.netto_fra_raekker([]) == 0, "tom liste -> netto 0")


def test_omklassificering() -> None:
    """⚠ FRISK NT8-START: ATI HAR INGEN MarketPosition-NOEGLE.

    Maalt 07-10 paa en nystartet NinjaTrader med flad konto. Stroemmen bar
    Orders, Strategies, BuyingPower, CashValue og RealizedPnL — altsaa den
    LEVEDE — men der var ingen MarketPosition| overhovedet. Noeglen dukker
    foerst op naar kontoen HAR haft en position i sessionen; derefter bliver
    den liggende paa 0. Dagen foer virkede det, fordi NT8 havde haft en
    MES-position.

    Konsekvensen var TAVS: klassificer(None, ...) gav "UKENDT", og en
    UKENDT-raekke taeller ingenting i netto_fra_raekker og faar ingen
    exit-knapper — uden at der staar hvorfor. Den FOERSTE handel efter en
    frisk NT8-start kunne altsaa ikke beskyttes, og det ville se ud som om
    exit-ordrerne var i stykker.

    Nettoet foer ordren er ikke tabt — det kan REGNES baglaens fra nettoet
    efter fyldningen, fordi ordren ved hvad den selv gjorde.
    """
    print()
    print("  -- omklassificering efter fyldning --")

    def r(oid, type_, action, filled=1, tid="08:00"):
        return {"order_id": oid, "placed_at": f"2026-10-07T{tid}",
                "ordre_type": type_, "action": action, "filled": filled,
                "status": "Filled", "ticker": "MES"}

    # ── 1. Foerste BUY efter frisk start ────────────────────────
    # Foer ordren: ingen noegle -> UKENDT. Efter fyldningen: ATI siger +1.
    kraev(EX.klassificer(None, "BUY") == "UKENDT",
          "⚠ uden noeglen er typen UKENDT foer ordren — som den skal vaere")
    t, adv = EX.omklassificering(1, "BUY", 1)
    kraev(t == "LONG", f"⚠ foerste BUY -> LONG efter fyldningen ({t})")
    kraev(adv is None, "…og ingen advarsel, for typen BLEV afgjort")
    # Og raekken skal nu BAERE en position og FAA knapper.
    raekke = r("NTM_1", t, "BUY")
    kraev(EX.netto_fra_raekker([raekke]) == 1,
          f"…raekken taeller nu i nettoet ({EX.netto_fra_raekker([raekke])})")
    kraev(EX.exit_mulig_for([raekke]) == "NTM_1",
          "⚠ …OG DEN FAAR EXIT-KNAPPER — hele pointen")

    # ── 2. Foerste SELL efter frisk start ──────────────────────
    print()
    t, adv = EX.omklassificering(-1, "SELL", 1)
    kraev(t == "SHORT", f"⚠ foerste SELL -> SHORT ({t})")
    kraev(adv is None, "…og ingen advarsel")
    raekke = r("NTM_2", t, "SELL")
    kraev(EX.netto_fra_raekker([raekke]) == -1, "…nettoet er negativt")
    kraev(EX.exit_mulig_for([raekke]) == "NTM_2",
          "…og shorten faar ogsaa knapper")

    # ── 3. ⚠ NETTO OGSAA UKENDT EFTER FYLDNINGEN ───────────────
    # Saa gaettes der IKKE. Raekken bliver UKENDT — men den siger det hoejt.
    print()
    t, adv = EX.omklassificering(None, "BUY", 1)
    kraev(t == "UKENDT", f"⚠ ukendt netto efter fyldning -> UKENDT ({t})")
    kraev(adv == EX.ADVARSEL_UKENDT_POSITION,
          f"⚠ …OG DER FOELGER EN ADVARSEL MED ({adv!r})")
    kraev("NinjaTrader" in adv and "exit-knapper" in adv,
          "…der siger baade hvor fejlen er og hvad konsekvensen er")
    raekke = r("NTM_3", t, "BUY")
    kraev(EX.exit_mulig_for([raekke]) is None,
          "…og raekken faar rigtigt nok ingen knapper")

    # ── 4. Lukninger skal ogsaa kunne udledes ──────────────────
    print()
    for netto_efter, action, ventet in [(0, "SELL", "EXIT"),
                                        (0, "BUY",  "EXIT"),
                                        (2, "BUY",  "LONG"),
                                        (-2, "SELL", "SHORT")]:
        t, _ = EX.omklassificering(netto_efter, action, 1)
        kraev(t == ventet,
              f"netto efter {netto_efter:+d} + {action} -> {ventet} ({t})")

    # ── 5. Regnestykket selv ────────────────────────────
    print()
    kraev(EX.netto_foer_fra_efter(1, "BUY", 1) == 0,
          "netto 1 efter et koeb paa 1 -> 0 foer")
    kraev(EX.netto_foer_fra_efter(-3, "SELL", 2) == -1,
          "netto -3 efter et salg paa 2 -> -1 foer")
    kraev(EX.netto_foer_fra_efter(None, "BUY", 1) is None,
          "⚠ ukendt ind -> ukendt ud. Der gaettes ikke.")
    # ⚠ Ingen fyldning: ordren gjorde ingenting, saa nettoet er uaendret. At
    # regne et antal fra ville opfinde en bevaegelse der ikke skete.
    kraev(EX.netto_foer_fra_efter(2, "BUY", 0) == 2,
          "⚠ nul fyldt -> nettoet er uaendret, ikke forskudt")
    kraev(EX.netto_foer_fra_efter(1, "buy", 1) == 0,
          "action er ikke versalfoelsom")


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

    # ── Fornuftsgraensen: points i stedet for en pris ──────────────
    # ⚠ DET ER IKKE EN RISIKOGRAENSE. MES koster $5 pr. point, saa "20" ment
    # som 20 points er i et marked paa 7850 en stop loss 7830 points vaek =
    # $39.150. NT8 ville tage imod den uden at blinke, og den ville ligge der
    # resten af dagen og se ud som en beskyttelse.
    print()
    g, b = ok("SLOSS", 20.0, "LONG", 7850.0)
    kraev(not g and "points i stedet for en pris" in b,
          f"⚠ 20 paa en long -> fanget som points: {b[:52]}")
    # ⚠ Og paa en TPROF, hvor side-kontrollen ellers vinder foerst og siger
    # "skal ligge over aktuel kurs" — sandt, og ubrugeligt.
    g, b = ok("TPROF", 20.0, "LONG", 7850.0)
    kraev(not g and "points i stedet for en pris" in b,
          f"⚠ …og paa en TPROF vinder STOERRELSEN over siden: {b[:40]}")
    g, b = ok("TPROF", 20.0, "SHORT", 7850.0)
    kraev(not g and "points i stedet for en pris" in b,
          "…og det samme paa en short")

    # Lige inden for og lige uden for graensen.
    graense = 7850.0 * EX.FORNUFT_PCT          # 235,5 points
    g, _ = ok("SLOSS", 7850.0 - 235.5, "LONG", 7850.0)
    kraev(g, f"praecis {EX._dk(graense)} points under godkendes")
    g, b = ok("SLOSS", 7850.0 - 236.0, "LONG", 7850.0)
    kraev(not g, "en halv point laengere ude afvises")

    # ⚠ En NORMAL stop maa ikke rammes af graensen. 5 points paa MES er en
    # helt saedvanlig stop, og en vagt der spaerrer det daglige arbejde, bliver
    # slaaet fra.
    for afst in (1.0, 5.0, 20.0, 100.0):
        g, b = ok("SLOSS", 7850.0 - afst, "LONG", 7850.0)
        kraev(g, f"…og en stop {EX._dk(afst)} points under er fin")

    # Graensen maa ikke afhaenge af kursens stoerrelse i absolutte points:
    # paa en billig kurs er 236 points helt urimeligt, paa en dyr er det ikke.
    g, _ = ok("SLOSS", 19400.0, "LONG", 19600.0)   # 200 points, 1,0 %
    kraev(g, "⚠ 200 points paa en kurs i 19.600 er under 3 % og godkendes")
    g, b = ok("SLOSS", 1900.0, "LONG", 2000.0)     # 100 points, 5,0 %
    kraev(not g, "⚠ …men 100 points paa en kurs i 2.000 er 5 % og afvises")


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


async def test_tvangsluk_sprunget_over() -> None:
    """⚠ SPRINGES TVANGSLUKNINGEN OVER, SKAL TELEFONEN RINGE.

    At springe over er det RIGTIGE valg: starter backenden kl. 23:30, ville en
    markedsordre ligge i CMEs pause (23:00-00:00 dansk) uden at fylde, og
    "tvangsluk_udfoert" ville staa i journalen om en lukning der ikke skete.

    Men beslutningen efterlader et MENNESKE med arbejdet, og en haendelse i
    loggen er ikke en besked til nogen. Uden push opdager Iben den aabne
    position naeste morgen — efter en nat med margin paa en uafdaekket future.
    """
    print()
    print("  -- oversprunget tvangslukning --")
    import datetime as _dt

    def nu_efter(minutter):
        """Et tidspunkt `minutter` efter dagens lukketid (en hverdag)."""
        d = _dt.datetime(2026, 10, 6, 12, 0, tzinfo=EX.DK)   # tirsdag
        return EX.lukketidspunkt(d) + _dt.timedelta(minutes=minutter)

    async def koer(netto, minutter=90, rejser=False):
        """Returnerer (pushkald, journaltyper, journalhaendelser, lukkekald).

        ⚠ tvangsluk stubbes. Lades den koere, genforsoeger den mod en mock
        der aldrig bliver flad — 18 forsoeg med pauser, og suiten gik fra 5
        til 66 sekunder. En langsom suite bliver koert sjaeldnere, og dét er
        samme fejlklasse som resten af modulet. Og det er alligevel OM den
        kaldes der er spoergsmaalet her, ikke hvad den selv goer.
        """
        pushet = []
        lukkekald = []

        async def falsk_push(besked, *, prioritet=4, titel="", dedup=""):
            pushet.append({"besked": besked, "prioritet": prioritet})

        def rejs(*a, **kw):
            raise EX.NT.NtTilstandUkendt("ATI svarede tomt")

        tr, jo = FalskTracker([]), FalskJournal()
        kald, orig = mock_nt(position={"netto": netto})
        if rejser:
            EX.NT.position = rejs
        async def falsk_tvangsluk(*a, **kw):
            lukkekald.append(kw.get("instrument"))
            return {"flad": True, "ryddet": 0}

        gl_push, gl_profil = EX._push, EX.accounts.nt_forbindelse
        gl_luk = EX.tvangsluk
        EX._push = falsk_push
        EX.tvangsluk = falsk_tvangsluk
        EX.accounts.nt_forbindelse = lambda: {"konto": "DEMO8580770"}
        try:
            o = EX.Overvaagning(tr, jo, hent_kurs=lambda: _ingen(),
                                instrument_for=lambda: "MES 12-26")
            # ⚠ Paamindelsen er en anden gren; sat som allerede sendt, saa
            # testen maaler kun overspringelsen.
            o._paamindet_dato = nu_efter(minutter).date()
            await o._tidsstyring("MES 12-26", nu=nu_efter(minutter))
            return pushet, jo.typer(), jo.events, lukkekald
        finally:
            EX._push, EX.accounts.nt_forbindelse = gl_push, gl_profil
            EX.tvangsluk = gl_luk
            gendan(orig)

    # 1. Aaben position -> push med prioritet 5.
    pu, typer, ev, luk = await koer(-4)
    kraev("tvangsluk_sprunget_over" in typer,
          f"overspringelsen journaliseres ({typer})")
    kraev(len(pu) == 1, f"⚠ ...OG der pushes ({len(pu)})")
    if pu:
        prio = pu[0]["prioritet"]
        besk = pu[0]["besked"]
        kraev(prio == 5, f"⚠ ...med prioritet 5, ikke 4 ({prio})")
        kraev("-4" in besk, f"...og beskeden siger HVOR meget: {besk[:56]}")
    p = [e for e in ev if e.get("event_type") == "tvangsluk_sprunget_over"][0]
    kraev(p["payload"].get("netto") == -4,
          "...og haendelsen baerer nettoet, saa den kan laeses bagefter")
    kraev(not luk,
          "⚠ ...men der tvangslukkes IKKE — det var hele pointen")

    # 2. Flad konto -> INGEN push. En besked hver aften hun ikke har noget
    #    aabent, bliver slaaet fra inden den betyder noget.
    print()
    pu, typer, ev, _ = await koer(0)
    kraev("tvangsluk_sprunget_over" in typer, "flad: stadig journaliseret")
    kraev(not pu, f"⚠ flad konto -> telefonen ringer IKKE ({len(pu)})")

    # 3. ⚠ ULAESELIG POSITION -> push alligevel. Netop her maa tavshed ikke
    #    blive til "alt er fint": vi har lige undladt at lukke noget.
    print()
    pu, typer, ev, _ = await koer(None, rejser=True)
    kraev(len(pu) == 1, f"⚠ ulaeselig position -> der pushes ({len(pu)})")
    if pu:
        kraev(pu[0]["prioritet"] == 5, "...ogsaa med prioritet 5")
        kraev("kan ikke laeses" in pu[0]["besked"],
              "...og beskeden siger at vi ikke VED det")
    p = [e for e in ev if e.get("event_type") == "tvangsluk_sprunget_over"][0]
    kraev(p["payload"].get("position_ukendt") is True,
          "...og haendelsen skelner UKENDT fra flad")

    # 4. ⚠ Og den NORMALE vej maa ikke vaere brudt: inden for vinduet skal
    #    tvangslukningen faktisk koere.
    print()
    pu, typer, ev, luk = await koer(-1, minutter=5)
    kraev("tvangsluk_sprunget_over" not in typer,
          f"⚠ 5 min efter lukketid springes der IKKE over ({typer})")
    kraev(luk == ["MES 12-26"],
          f"⚠ ...der tvangslukkes i stedet ({luk})")
    kraev(not pu, "...og der pushes ikke om en oversprunget lukning")

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


async def test_fyldt_uden_pris() -> None:
    """⚠ FILLED UDEN FYLDPRIS ER IKKE FAERDIGT — det er et nyt forsoeg.

    Dette var den ANDEN aarsag til at shorten 06-10 aldrig blev lukket, og den
    ville have staaet tilbage efter at rekkefoelgen var rettet.

    ATI pusher status og fyldpris i hver sit felt, og de kommer ikke
    noedvendigvis i samme oplaeg. `fyldning()` siger selv at (0, 0.0) betyder
    "intet set", ikke "nul fyldt" — men loekken skrev raekken terminal med
    nuller, og saa var den ude af `aktive` for altid.

    Maalt: ordre 602502520254 fyldte 1 @ 7850,25 (NT8's egen log 13:07:31),
    mens raekken stod som status=Filled filled=0 avg_fill=0.0.
    """
    print()
    print("  -- Filled uden fyldpris --")
    bogfoert = []

    async def bogfoer(oid, action, antal, pris, status, aarsag):
        bogfoert.append({"pris": pris, "antal": antal, "aarsag": aarsag})

    e = {"order_id": "NTX_U", "source": "manual_exit", "ordre_type": "TRAIL",
         "parent_order_id": "NTM1", "status": "Working", "bekraeftet": True,
         "shares": 1, "action": "BUY", "trigger_pris": 7850.25,
         "ticker": "MES", "trail_afstand": 1.0, "trail_hoejeste": 7849.0}
    tr, jo = FalskTracker([dict(e)]), FalskJournal()

    # Gennemloeb 1: ATI siger Filled, men fyldfelterne mangler i oplaegget.
    kald, orig = mock_nt(position={"netto": 0, "noegle": "MES DEC26"},
                         ordre_status="Filled", fyldning=(0, 0.0))
    try:
        o = EX.Overvaagning(tr, jo, hent_kurs=lambda: _ingen(),
                            instrument_for=lambda: "MES 12-26",
                            bogfoer_exit=bogfoer)
        await o._tik("MES 12-26", "DEMO8580770")
        r = tr.find("NTX_U")
        kraev(not bogfoert, "uden fyldpris bogfoeres der INTET")
        kraev(r.get("avg_fill") in (None, 0) and not r.get("filled"),
              f"⚠ og der skrives ingen nuller i raekken "
              f"(filled={r.get('filled')} avg={r.get('avg_fill')})")
        kraev(r.get("fyld_forsoeg") == 1,
              f"raekken taeller forsoeget ({r.get('fyld_forsoeg')})")
        kraev(bool(r.get("advarsel")),
              f"⚠ ...og det kan SES i vinduet ({r.get('advarsel')!r})")
        kraev(not any(k[0] == "annuller" for k in kald),
              "⚠ en fyldt ordre annulleres IKKE som en efterladt rest")
    finally:
        gendan(orig)

    # Gennemloeb 2: naeste oplaeg baerer tallene — nu skal den bogfoeres.
    kald, orig = mock_nt(position={"netto": 0, "noegle": "MES DEC26"},
                         ordre_status="Filled", fyldning=(1, 7850.25))
    try:
        o = EX.Overvaagning(tr, jo, hent_kurs=lambda: _ingen(),
                            instrument_for=lambda: "MES 12-26",
                            bogfoer_exit=bogfoer)
        await o._tik("MES 12-26", "DEMO8580770")
        r = tr.find("NTX_U")
        kraev(len(bogfoert) == 1,
              f"⚠ naeste oplaeg bogfoerer fyldningen ({len(bogfoert)})")
        if bogfoert:
            kraev(bogfoert[0]["pris"] == 7850.25,
                  f"    ...til den rigtige pris ({bogfoert[0]['pris']})")
        kraev(r.get("filled") == 1, f"raekken baerer antallet ({r.get('filled')})")
        kraev(r.get("fyld_bogfoert") is True, "...og er markeret bogfoert")
        kraev(not r.get("advarsel"), "...og advarslen er vaek igen")
    finally:
        gendan(orig)

    # Gennemloeb 3: den maa IKKE bogfoeres to gange.
    kald, orig = mock_nt(position={"netto": 0, "noegle": "MES DEC26"},
                         ordre_status="Filled", fyldning=(1, 7850.25))
    try:
        o = EX.Overvaagning(tr, jo, hent_kurs=lambda: _ingen(),
                            instrument_for=lambda: "MES 12-26",
                            bogfoer_exit=bogfoer)
        await o._tik("MES 12-26", "DEMO8580770")
        kraev(len(bogfoert) == 1,
              f"⚠ og den bogfoeres ikke igen naeste gennemloeb "
              f"({len(bogfoert)})")
    finally:
        gendan(orig)

    # Og tavsheden skal siges hoejt — ellers staar handlen aaben i stilhed.
    print()
    tr2, jo2 = FalskTracker([dict(e, order_id="NTX_V")]), FalskJournal()
    kald, orig = mock_nt(position={"netto": 0, "noegle": "MES DEC26"},
                         ordre_status="Filled", fyldning=(0, 0.0))
    try:
        o = EX.Overvaagning(tr2, jo2, hent_kurs=lambda: _ingen(),
                            instrument_for=lambda: "MES 12-26",
                            bogfoer_exit=bogfoer)
        for _ in range(EX.FYLD_FORSOEG_ALARM):
            await o._tik("MES 12-26", "DEMO8580770")
        typer = jo2.typer()
        kraev("exit_fyldt_uden_pris" in typer,
              f"⚠ efter {EX.FYLD_FORSOEG_ALARM} forsoeg siges det HOEJT ({typer})")
        kraev(typer.count("exit_fyldt_uden_pris") == 1,
              "...én gang, ikke hvert gennemloeb")
    finally:
        gendan(orig)


async def test_fyldt_exit_bogfoeres() -> None:
    """⚠ FYLDER EN EXIT, SKAL DEN BOGFOERES — ikke annulleres.

    Fundet i trin 8 (06-10 kl. 13:07): NT8 fyldte en TRAIL paa en short, og
    Trading Dash bogfoerte det aldrig. Handlen stod aaben i journalen med
    exit_price=None, SHORT-raekken beholdt sine knapper, og Fyldt-kolonnen sagde
    "—" paa en ordre der var fyldt.

    Aarsagen var raekkefoelgen i _tik: positionen blev laest FOER ordrestatus.
    Naar TRAIL'en fyldte, gik positionen til nul, og loekken laeste det som
    "positionen er lukket, ryd resterne" — annullerede den netop fyldte ordre og
    returnerede, foer den naaede at se at den var fyldt.

    ⚠ Loekken kunne ikke skelne "exit'en fyldte" fra "positionen blev lukket et
    andet sted". En fyldt exit FORKLARER hvorfor positionen er nul, saa den skal
    laeses foerst. Tre symptomer, én aarsag.
    """
    print("\n  -- fyldt exit bogfoeres --")
    bogfoert = []

    async def bogfoer(oid, action, antal, pris, status, aarsag):
        bogfoert.append({"order_id": oid, "antal": antal, "pris": pris,
                         "aarsag": aarsag})

    for navn, type_, aktion in [("TRAIL paa short", "TRAIL", "BUY"),
                                ("SLOSS paa long", "SLOSS", "SELL"),
                                ("TPROF paa long", "TPROF", "SELL")]:
        bogfoert.clear()
        e = {"order_id": "NTX_F", "source": "manual_exit", "ordre_type": type_,
             "parent_order_id": "NTM1", "status": "Working", "bekraeftet": True,
             "shares": 1, "action": aktion, "trigger_pris": 7850.25,
             "ticker": "MES", "trail_afstand": 1.0, "trail_hoejeste": 7849.0}
        tr, jo = FalskTracker([dict(e)]), FalskJournal()
        # ⚠ Praecis situationen: ATI melder Filled OG positionen er nul.
        kald, orig = mock_nt(position={"netto": 0, "noegle": "MES DEC26"},
                             ordre_status="Filled", fyldning=(1, 7850.25))
        try:
            o = EX.Overvaagning(tr, jo, hent_kurs=lambda: _ingen(),
                                instrument_for=lambda: "MES 12-26",
                                bogfoer_exit=bogfoer)
            await o._tik("MES 12-26", "DEMO8580770")
            kraev(len(bogfoert) == 1,
                  f"⚠ {navn}: fyldningen BOGFOERES ({len(bogfoert)})")
            if bogfoert:
                kraev(bogfoert[0]["aarsag"] == type_,
                      f"    …med exit_aarsag={bogfoert[0]['aarsag']}")
                kraev(bogfoert[0]["pris"] == 7850.25,
                      f"    …og fyldprisen ({bogfoert[0]['pris']})")
            # Og raekken skal baere fyldningen, saa "Fyldt" ikke viser "—".
            r2 = tr.find("NTX_F")
            kraev(r2.get("status") == "Filled", "    raekken staar som Filled")
            kraev(r2.get("filled") == 1,
                  f"    ⚠ og Fyldt er 1, ikke tom ({r2.get('filled')})")
        finally:
            gendan(orig)

    # ⚠ Og det modsatte skal stadig virke: lukkes positionen et ANDET sted,
    # skal de tilbagevaerende exit-ordrer ryddes.
    print()
    e = {"order_id": "NTX_R", "source": "manual_exit", "ordre_type": "SLOSS",
         "parent_order_id": "NTM1", "status": "Working", "bekraeftet": True,
         "shares": 1, "action": "SELL", "trigger_pris": 7800.0, "ticker": "MES"}
    tr, jo = FalskTracker([dict(e)]), FalskJournal()
    bogfoert.clear()
    # Position nul, men ordren er IKKE fyldt — den blev lukket manuelt.
    kald, orig = mock_nt(position={"netto": 0, "noegle": "MES DEC26"},
                         ordre_status="")
    try:
        o = EX.Overvaagning(tr, jo, hent_kurs=lambda: _ingen(),
                            instrument_for=lambda: "MES 12-26",
                            bogfoer_exit=bogfoer)
        await o._tik("MES 12-26", "DEMO8580770")
        kraev(not bogfoert, "lukket et andet sted -> der bogfoeres INGEN exit")
        kraev(any(k[0] == "annuller" for k in kald),
              "⚠ …men den efterladte stop ryddes stadig")
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
    test_exit_mulig()
    test_omklassificering()
    test_validering()
    test_config()
    test_log_aflaesning()
    test_lukketid()
    asyncio.run(test_overvaagning())
    asyncio.run(test_opret())
    asyncio.run(test_fyldt_uden_pris())
    asyncio.run(test_fyldt_exit_bogfoeres())
    asyncio.run(test_genstart())
    asyncio.run(test_tvangsluk_sprunget_over())
    asyncio.run(test_tvangsluk_blind())
    print(f"\n  {'ALLE BESTAAET' if not fejl else f'⚠ {len(fejl)} FEJLEDE'}")
    return 1 if fejl else 0


if __name__ == "__main__":
    sys.exit(main())
