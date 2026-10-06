"""
exit_ordrer.py — PLOSS / TPROF / TRAIL på en NT8-position
════════════════════════════════════════════════════════════════════════════════
Trin 2 i SPEC_exit_ordrer_ninjatrader.md. Al exit-logik bor her; `main.py` har
kun endpoints og to kald.

⚠ DEN VIGTIGSTE REGEL I HELE MODULET står i `overvaag()`: når positionen er nul,
annulleres alle exit-ordrer. En efterladt stop loss på en lukket position er
ikke en harmløs rest — den åbner en NY position i modsat retning når den
udløses. Det er samme ulykke som de otte ejerløse shorts 31-07, bare automatisk.

⚠ OG DEN NÆSTVIGTIGSTE: `netto=None` er UKENDT, ikke flad. Et opslag vi ikke
fik, må aldrig udløse en annullering — og slet ikke en markedsordre.

Bygget på probe-resultatet 06-10 (spec §4a), som er målt, ikke antaget:

  P1 Ja          OCO virker via OIF              -> alle exits deler ét OCO-id
  P2 Ja          CHANGE flytter en stoppris      -> TRAIL flyttes, genlægges ikke
  P3 Ja          tredje ordre i levende gruppe   -> tilføjelse kræver ingen genlægning
  P4 KASKADERER  annullering rammer søskende     -> SLETNING kræver genlægning
  P5 Ja          søster dør når den ene fylder   -> ingen dobbeltfyld-risiko
  P6 Ja          CHANGE flytter et antal         -> tilkøb justerer, genlægger ikke
  P7 AFVIST      OCO-id kan ikke genbruges       -> løbenummer, persisteret
"""
from __future__ import annotations

import asyncio
import datetime
import json
import logging
import pathlib
import time
import zoneinfo
from typing import Any, Optional

import accounts
import nt_forbindelse as NT

logger = logging.getLogger(__name__)

KONFIG_FIL = pathlib.Path(__file__).parent / "exit_config.json"
TICK = 0.25
TYPER = ("PLOSS", "TPROF", "TRAIL")

DK = zoneinfo.ZoneInfo("Europe/Copenhagen")
ET = zoneinfo.ZoneInfo("America/New_York")

# ⚠ Throttle pr. ordre. MES kan sætte nyt high mange gange i minuttet, og hver
# CHANGE er en OIF-fil plus en kvittering hos demo-brokeren. Vi flytter med den
# SENEST beregnede stop, ikke med hver enkelt.
TRAIL_THROTTLE_SEK = 2.0

# Hvor længe vi tåler ikke at kunne læse en kurs, før det siges højt.
KURS_STILHED_SEK = 10.0
# ...og hvor længe positionen må være ulæselig før det samme.
POSITION_BLIND_SEK = 30.0


# ═══════════════════════════════════════════════════════════════════════════
# Konfiguration
# ═══════════════════════════════════════════════════════════════════════════
def _laes_konfig() -> dict:
    try:
        d = json.loads(KONFIG_FIL.read_text(encoding="utf-8"))
    except Exception:
        d = {}
    return {
        "trail_afstand": float(d.get("trail_afstand", 4.0)),
        # ⚠ PERSISTERET LØBENUMMER. P7: NT8 afviser et genbrugt OCO-id, også
        # når alle medlemmer er terminale. En tæller i hukommelsen ville starte
        # forfra efter en genstart og ramme et id NT8 allerede har set i dag.
        "oco_loebenr": int(d.get("oco_loebenr", 0)),
    }


def _skriv_konfig(d: dict) -> None:
    try:
        KONFIG_FIL.write_text(json.dumps(d, indent=2), encoding="utf-8")
    except Exception as e:
        logger.error(f"[ExitOrdrer] kunne ikke skrive {KONFIG_FIL.name}: {e}")


def hent_config() -> dict:
    k = _laes_konfig()
    return {"trail_afstand": k["trail_afstand"], "enhed": "points", "tick": TICK}


def saet_config(trail_afstand: float) -> dict:
    """Validerer og gemmer. Kaster ValueError med dansk tekst."""
    try:
        v = float(trail_afstand)
    except (TypeError, ValueError):
        raise ValueError("Trailing-afstanden skal være et tal.")
    if v <= 0:
        raise ValueError("Trailing-afstanden skal være større end 0.")
    if v > 50:
        raise ValueError("Trailing-afstanden må højst være 50 points.")
    if abs(v / TICK - round(v / TICK)) > 1e-9:
        raise ValueError(f"Trailing-afstanden skal være et helt antal ticks "
                         f"({TICK:g}). {v:g} går ikke op.")
    k = _laes_konfig()
    k["trail_afstand"] = round(v, 2)
    _skriv_konfig(k)
    return hent_config()


def _nyt_oco_id() -> str:
    """TDOCO<YYMMDD><løbenr>. Aldrig det samme to gange — se P7."""
    k = _laes_konfig()
    k["oco_loebenr"] = int(k["oco_loebenr"]) + 1
    _skriv_konfig(k)
    return f"TDOCO{datetime.datetime.now():%y%m%d}{k['oco_loebenr']}"


# ═══════════════════════════════════════════════════════════════════════════
# Hjælp
# ═══════════════════════════════════════════════════════════════════════════
def hele_ticks(pris: float) -> bool:
    return abs(pris / TICK - round(pris / TICK)) < 1e-9


def _dk(v: float) -> str:
    """Dansk talformat til fejltekster: 6812,25."""
    return f"{v:,.2f}".replace(",", " ").replace(".", ",")


def klassificer(netto_foer: Optional[int], action: str) -> str:
    """LONG | SHORT | EXIT | UKENDT ud fra positionen FØR ordren.

    ⚠ NETTOET FØR ORDREN FINDES IKKE BAGEFTER. Derfor gemmes typen på rækken i
    stedet for at blive udledt ved visning — en ordrerække skal kunne læses om
    et år uden at genskabe hvad positionen var da den blev lagt.
    """
    a = (action or "").upper()
    if netto_foer is None:
        return "UKENDT"
    if netto_foer == 0:
        return "LONG" if a == "BUY" else "SHORT"
    if netto_foer > 0:
        return "LONG" if a == "BUY" else "EXIT"
    return "SHORT" if a == "SELL" else "EXIT"


def valider_pris(type_: str, pris: Optional[float], retning: str,
                 kurs: Optional[float]) -> None:
    """Kaster ValueError med dansk tekst hvis prisen ikke kan bruges.

    ⚠ VI VALIDERER, SÅ NT8 IKKE BEHØVER. En ordre NT8 afviser, giver en MODAL
    DIALOGBOKS på Ibens skærm som hun skal lukke (målt 06-10, P7). En afvisning
    skal derfor være en undtagelse — ikke vores valideringsmetode.
    """
    if type_ == "TRAIL":
        if pris is not None:
            raise ValueError("Trailing stop tager ingen pris — afstanden "
                             "kommer fra Konfiguratoren.")
        return
    if pris is None:
        raise ValueError("Der mangler en pris.")
    if not hele_ticks(pris):
        raise ValueError(f"Prisen skal være et helt antal ticks ({TICK:g}). "
                         f"{_dk(pris)} går ikke op.")
    if kurs is None:
        # ⚠ Ingen kurs -> vi kan ikke afgøre siden. At sende alligevel ville
        # lade NT8 om det, og dermed give Iben en dialogboks i stedet for en
        # besked i vinduet hun arbejder i.
        raise ValueError("Ingen aktuel kurs — prisen kan ikke kontrolleres, "
                         "og der sendes ingen ordre.")
    if retning == "LONG":
        if type_ == "PLOSS" and pris >= kurs:
            raise ValueError(f"Stop loss skal ligge under aktuel kurs "
                             f"({_dk(kurs)}) for en long.")
        if type_ == "TPROF" and pris <= kurs:
            raise ValueError(f"Target profit skal ligge over aktuel kurs "
                             f"({_dk(kurs)}) for en long.")
    else:
        if type_ == "PLOSS" and pris <= kurs:
            raise ValueError(f"Stop loss skal ligge over aktuel kurs "
                             f"({_dk(kurs)}) for en short.")
        if type_ == "TPROF" and pris >= kurs:
            raise ValueError(f"Target profit skal ligge under aktuel kurs "
                             f"({_dk(kurs)}) for en short.")


def laes_ordre_fra_log(logliner: list) -> dict:
    """Hvad NT8 selv siger ordren nu står på, efter en PLACE eller CHANGE.

    ⚠ ATI MELDER DET IKKE — kun status. Spec §4a bad os undersøge om LOGGEN
    gør det, så trailing kan verificere sit eget tal frem for at tro på det.

    Svaret er ja, og mere end håbet. Målt 06-10 på en rigtig CHANGE:

        Order='602502520018/DEMO8580770' ... New state='Accepted'
        Limit price=0 Stop price=7824.75 Quantity=1 Type='Stop Market'
        Time in force=DAY Oco='TDOCO1265318' Filled=0 Fill price=0
        Error='No error'

    Altså stoppris, limitpris, antal, OCO-binding OG en fejltekst. Det
    betyder at backenden kan KONTROLLERE at en trailing-stop faktisk flyttede,
    i stedet for at antage det fordi kommandoen blev sendt.

    ⚠ Den sidste `Accepted`/`Working`-linje er den gældende. En CHANGE giver
    først `Change submitted` med de NYE værdier og derefter `Accepted` — læser
    man den første, ser det rigtigt ud uanset hvad der skete bagefter.
    """
    import re
    ud: dict = {}
    for l in logliner or []:
        t = str(l)
        if "Order=" not in t:
            continue
        for navn, moenster, type_ in (
            ("stop",   r"Stop price=([\d.]+)",   float),
            ("limit",  r"Limit price=([\d.]+)",  float),
            ("antal",  r"Quantity=(\d+)",        int),
            ("oco",    r"Oco='([^']*)'",          str),
            ("status", r"New state='([^']*)'",    str),
            ("fejl",   r"Error='([^']*)'",        str),
        ):
            m = re.search(moenster, t)
            if m:
                try:
                    ud[navn] = type_(m.group(1))
                except ValueError:
                    pass
    return ud


def _stop_fra_log(logliner: list) -> Optional[float]:
    """Bagudkompatibel indgang. Se laes_ordre_fra_log."""
    v = laes_ordre_fra_log(logliner).get("stop")
    # ⚠ 0 betyder "ikke en stop-ordre", ikke "stop paa nul".
    return v if v else None


# ═══════════════════════════════════════════════════════════════════════════
# Push — men ikke til Ibens telefon fra Sørens maskine
# ═══════════════════════════════════════════════════════════════════════════
async def _push(besked: str, *, prioritet: int = 4, titel: str = "Trading Dash",
                dedup: str = "") -> None:
    """⚠ notifier.NTFY_TOPIC er hardkodet til Ibens topic. En test herfra må
    ikke ringe på hendes telefon, så push kræver `exit_push: true` i den
    lokale account.yaml — og logges ellers i stedet for at forsvinde.
    """
    profil = accounts.nt_forbindelse() or {}
    if not profil.get("exit_push"):
        logger.info(f"[ExitOrdrer] push undertrykt: {besked}")
        return
    try:
        import notifier
        await notifier.send(besked, title=titel, priority=prioritet,
                            dedup_key=dedup or None)
    except Exception as e:
        logger.error(f"[ExitOrdrer] push fejlede: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# Oprettelse
# ═══════════════════════════════════════════════════════════════════════════
class ExitFejl(Exception):
    """Afvist før noget blev sendt. Teksten er til Iben."""


async def opret_exit(tracker, journal, *, parent_order_id: str, type_: str,
                     pris: Optional[float], instrument: str,
                     hent_kurs) -> dict:
    """Læg en PLOSS/TPROF/TRAIL på den position parent-rækken åbnede.

    `hent_kurs` er en awaitable () -> float|None. Den injiceres, så modulet
    ikke skal kende main.py.
    """
    type_ = (type_ or "").upper()
    if type_ not in TYPER:
        raise ExitFejl(f"Ukendt exit-type {type_!r}.")

    parent = tracker.find(parent_order_id)
    if parent is None:
        raise ExitFejl("Ordren findes ikke i ordrelisten.")
    retning = (parent.get("ordre_type") or "").upper()
    if retning not in ("LONG", "SHORT"):
        raise ExitFejl("Exit-ordrer kan kun lægges på en åbnende ordre "
                       "(LONG eller SHORT).")

    # 1. Vagterne. En spærret vagt sender intet.
    profil = await asyncio.to_thread(NT.klar)
    konto = profil["konto"]
    for adv in (profil.get("advarsler") or []):
        logger.error(f"[ExitOrdrer] {adv}")

    # 2. Positionen SKAL findes og pege samme vej.
    p = await asyncio.to_thread(NT.position, instrument, konto)
    netto = p.get("netto")
    if netto is None:
        raise ExitFejl("Positionen kan ikke bekræftes hos NinjaTrader — "
                       "ingen exit-ordre lagt.")
    if netto == 0:
        raise ExitFejl("Der er ingen åben position at beskytte.")
    if (retning == "LONG" and netto < 0) or (retning == "SHORT" and netto > 0):
        raise ExitFejl(f"Positionen hos NinjaTrader ({netto:+d}) peger ikke "
                       f"samme vej som denne {retning}-ordre.")

    # 3. Typen må ikke allerede være aktiv.
    aktive = tracker.exit_ordrer_for(parent_order_id)
    if any((e.get("ordre_type") or "").upper() == type_ for e in aktive):
        raise ExitFejl(f"Der er allerede en aktiv {type_} på positionen.")

    # 4. FRISK kurs lige før afsendelse — ikke den UI'et viste.
    #    ⚠ Mellem Ibens klik og vores PLACE kan markedet have flyttet sig forbi
    #    hendes pris. Opdager vi det, får hun en dansk besked; opdager NT8 det,
    #    får hun en dialogboks.
    kurs = await hent_kurs()
    konfig = _laes_konfig()
    try:
        valider_pris(type_, pris, retning, kurs)
    except ValueError as e:
        raise ExitFejl(str(e))

    # 5. Ordren.
    modsat = "SELL" if retning == "LONG" else "BUY"
    antal = abs(netto)
    trail_afstand = trail_hoejeste = None
    if type_ == "TRAIL":
        if kurs is None:
            raise ExitFejl("Ingen aktuel kurs — trailing stop kan ikke "
                           "placeres.")
        trail_afstand = konfig["trail_afstand"]
        trail_hoejeste = kurs
        stop = kurs - trail_afstand if retning == "LONG" else kurs + trail_afstand
        stop = round(round(stop / TICK) * TICK, 2)
        ordretype, limit = "STOPMARKET", None
    elif type_ == "PLOSS":
        ordretype, limit, stop = "STOPMARKET", None, pris
    else:
        ordretype, limit, stop = "LIMIT", pris, None

    # ⚠ ÉT OCO-ID PR. POSITION. Findes der allerede aktive exits, lægges denne
    # ind i DERES gruppe (P3 = Ja: man kan føje til en levende gruppe).
    oco = next((e.get("oco_id") for e in aktive if e.get("oco_id")), None) \
        or _nyt_oco_id()

    ref = NT.order_ref(praefiks="NTX")
    svar = await asyncio.to_thread(
        NT.send_ordre, konto=konto, instrument=instrument, action=modsat,
        antal=antal, ordretype=ordretype, limit=limit, stop=stop,
        tif="DAY", ordre_id=ref, oco=oco)
    if svar.get("fil_tilbage"):
        logger.error(f"[ExitOrdrer] ⚠ OIF-fil efterladt: {svar['fil_tilbage']}")

    # 6. Vent på at den er LEVENDE — ikke på at den bliver færdig.
    r = await asyncio.to_thread(NT.afvent_aktiv, ref, 12.0)
    status = r["status"] or ""

    if r["terminal"] and status == "Rejected":
        await journal.log_event(
            ibkr_account=konto or None, source="manual_exit",
            event_type="nt_exit_afvist", symbol=parent.get("ticker"),
            payload={"type": type_, "pris": pris, "stop": stop, "limit": limit,
                     "order_id": ref, "oco_id": oco, "konto": konto,
                     "nt_log": (svar.get("logliner") or [])[:4]})
        raise ExitFejl("NinjaTrader afviste ordren — luk dialogboksen i "
                       "NinjaTrader.")

    # 7. Registrér med den status ATI FAKTISK meldte.
    #    ⚠ Ikke-bekræftet er "afventer", aldrig "aktiv".
    tracker.record_placed(
        order_id=ref, source="manual_exit",
        ticker=parent.get("ticker") or "", action=modsat, shares=antal,
        order_type=ordretype, limit_price=limit,
        ibkr_account=konto or None, broker="NT8",
        maalt={"status": status, "filled": r["filled"],
               "avg_fill": r["avg_fill"]} if r["terminal"] else None,
        ordre_type=type_, parent_order_id=parent_order_id, oco_id=oco,
        trigger_pris=(stop if type_ != "TPROF" else limit),
        trail_hoejeste=trail_hoejeste, trail_afstand=trail_afstand)
    if not r["terminal"]:
        tracker.opdater(ref, status=status or "afventer", bekraeftet=r["aktiv"])

    logger.info(f"[ExitOrdrer] {type_} {ref} oco={oco} status={status or '(ukendt)'}")
    return {"order_id": ref, "status": status or "afventer",
            "oco_id": oco, "aktiv": r["aktiv"],
            "trigger_pris": stop if type_ != "TPROF" else limit}


# ═══════════════════════════════════════════════════════════════════════════
# Annullering — og genlægningen P4 tvinger os til
# ═══════════════════════════════════════════════════════════════════════════
async def annuller_exit(tracker, journal, *, order_id: str,
                        instrument: str) -> dict:
    """Slet én exit-ordre og genlæg de andre.

    ⚠ P4: ANNULLERINGEN KASKADERER. Sletter vi én i en OCO-gruppe, dør hele
    gruppen. Søstrene skal derfor lægges igen — og med et NYT OCO-id, fordi P7
    viste at NT8 afviser et genbrugt.

    Der er et kort hul uden beskyttelse mellem annullering og genlægning. Det
    accepteres (spec §4a punkt 3), men det måles og journaliseres, så ingen
    senere skal gætte på hvor længe positionen stod bar.
    """
    raekke = tracker.find(order_id)
    if raekke is None:
        raise ExitFejl("Exit-ordren findes ikke.")
    parent_id = raekke.get("parent_order_id")
    profil = await asyncio.to_thread(NT.klar)
    konto = profil["konto"]

    t0 = time.time()
    søskende = [e for e in tracker.exit_ordrer_for(parent_id)
                if str(e.get("order_id")) != str(order_id)]

    await asyncio.to_thread(NT.annuller, order_id)
    r = await asyncio.to_thread(NT.afvent_ordre, order_id, 12.0)
    tracker.opdater(order_id, status=r["status"] or "UNKNOWN",
                    bekraeftet=r["terminal"])

    if not søskende:
        logger.info(f"[ExitOrdrer] {order_id} annulleret, ingen soeskende")
        return {"status": r["status"] or "UNKNOWN", "genlagt": []}

    # ⚠ Afvent at ALLE er terminale før genlægning. Lægger vi en ny ordre ind
    # mens kaskaden stadig ruller, kan den nye blive fejet med.
    for e in søskende:
        await asyncio.to_thread(NT.afvent_ordre, e["order_id"], 10.0)

    p = await asyncio.to_thread(NT.position, instrument, konto)
    netto = p.get("netto")
    if not netto:
        # ⚠ Ingen position (eller ukendt) -> genlæg INTET. En exit-ordre uden
        # position er præcis den ordre der åbner en ny.
        for e in søskende:
            tracker.opdater(e["order_id"], status="Cancelled", bekraeftet=True)
        return {"status": r["status"] or "UNKNOWN", "genlagt": [],
                "note": "positionen er lukket eller ukendt — intet genlagt"}

    nyt_oco = _nyt_oco_id()
    genlagt = []
    for e in søskende:
        tracker.opdater(e["order_id"], status="Cancelled", bekraeftet=True)
        t = (e.get("ordre_type") or "").upper()
        modsat = "SELL" if netto > 0 else "BUY"
        ny_ref = NT.order_ref(praefiks="NTX")
        er_limit = t == "TPROF"
        await asyncio.to_thread(
            NT.send_ordre, konto=konto, instrument=instrument, action=modsat,
            antal=abs(netto), ordretype="LIMIT" if er_limit else "STOPMARKET",
            limit=e.get("trigger_pris") if er_limit else None,
            stop=None if er_limit else e.get("trigger_pris"),
            tif="DAY", ordre_id=ny_ref, oco=nyt_oco)
        rr = await asyncio.to_thread(NT.afvent_aktiv, ny_ref, 12.0)
        tracker.record_placed(
            order_id=ny_ref, source="manual_exit", ticker=e.get("ticker") or "",
            action=modsat, shares=abs(netto),
            order_type="LIMIT" if er_limit else "STOPMARKET",
            ibkr_account=konto or None, broker="NT8",
            ordre_type=t, parent_order_id=parent_id, oco_id=nyt_oco,
            trigger_pris=e.get("trigger_pris"),
            trail_hoejeste=e.get("trail_hoejeste"),
            trail_afstand=e.get("trail_afstand"))
        tracker.opdater(ny_ref, status=rr["status"] or "afventer",
                        bekraeftet=rr["aktiv"])
        genlagt.append({"type": t, "fra": e["order_id"], "til": ny_ref,
                        "aktiv": rr["aktiv"]})

    ms = int((time.time() - t0) * 1000)
    await journal.log_event(
        ibkr_account=konto or None, source="manual_exit",
        event_type="exit_genlagt", symbol=raekke.get("ticker"),
        payload={"slettet": order_id, "nyt_oco": nyt_oco, "genlagt": genlagt,
                 "hul_ms": ms,
                 "hvorfor": "P4: annullering kaskaderer i en OCO-gruppe, saa "
                            "soeskende skal laegges igen med nyt OCO-id (P7)"})
    logger.info(f"[ExitOrdrer] genlagt {len(genlagt)} med {nyt_oco} "
                f"(hul {ms} ms)")
    return {"status": r["status"] or "UNKNOWN", "genlagt": genlagt,
            "hul_ms": ms}


async def ryd(tracker, journal, *, parent_order_id: Optional[str] = None,
              instrument: str = "", hvorfor: str = "") -> int:
    """Annullér alle aktive exit-ordrer. Returnerer antallet.

    Kaldes når positionen lukkes manuelt fra Watchlist Futures, så ordrerne
    forsvinder med det samme i stedet for ved næste løkke-gennemløb.
    """
    aktive = []
    if parent_order_id:
        aktive = tracker.exit_ordrer_for(parent_order_id)
    else:
        aktive = [e for e in tracker._entries
                  if e.get("source") == "manual_exit"
                  and (e.get("ordre_type") or "") in TYPER
                  and e.get("status") not in ("Filled", "Cancelled", "Rejected")]
    if not aktive:
        return 0
    for e in aktive:
        try:
            await asyncio.to_thread(NT.annuller, e["order_id"])
        except Exception as ex:
            logger.error(f"[ExitOrdrer] kunne ikke annullere {e['order_id']}: {ex}")
    for e in aktive:
        r = await asyncio.to_thread(NT.afvent_ordre, e["order_id"], 10.0)
        tracker.opdater(e["order_id"], status=r["status"] or "UNKNOWN",
                        bekraeftet=r["terminal"])
    logger.info(f"[ExitOrdrer] ryddede {len(aktive)} exit-ordrer ({hvorfor})")
    return len(aktive)


# ═══════════════════════════════════════════════════════════════════════════
# Lukketidspunkt — regnet, aldrig hardkodet
# ═══════════════════════════════════════════════════════════════════════════
def lukketidspunkt(nu: Optional[datetime.datetime] = None) -> datetime.datetime:
    """min(22:00 dansk, 16:50 ET) på dagens dato. Returnerer et tz-bevidst DK-tid.

    ⚠ DE TO FALDER IKKE SAMMEN HELE ÅRET. EU og USA skifter sommertid på
    forskellige datoer, og i de uger er forskellen 5 timer i stedet for 6:

        normalt            22:00 dansk = 16:00 ET      -> 22:00 dansk
        25-10 til 01-11    22:00 dansk = 17:00 ET      -> loftet gælder, 21:50
        14-03 til 28-03    samme

    17:00 ET er CME's daglige pause, hvor intet fylder. En markedsordre dér
    ville ligge og vente — og en tvangslukning der ikke lukker, er værre end
    ingen, fordi den ser ud som om den virkede.

    Derfor loftet 16:50 ET. Og derfor `zoneinfo` frem for en fast forskel: en
    konstant ville have virket indtil 25. oktober og så været tavst forkert.
    """
    nu = nu or datetime.datetime.now(DK)
    nu = nu.astimezone(DK)
    dansk = nu.replace(hour=22, minute=0, second=0, microsecond=0)
    # Samme kalenderdag i ET — ikke "dansk tid minus seks".
    et_dag = nu.astimezone(ET).date()
    amerikansk = datetime.datetime.combine(
        et_dag, datetime.time(16, 50), tzinfo=ET).astimezone(DK)
    return min(dansk, amerikansk)


def paamindelsestidspunkt(nu: Optional[datetime.datetime] = None) -> datetime.datetime:
    nu = (nu or datetime.datetime.now(DK)).astimezone(DK)
    return nu.replace(hour=21, minute=0, second=0, microsecond=0)


# ═══════════════════════════════════════════════════════════════════════════
# Tvangslukning
# ═══════════════════════════════════════════════════════════════════════════
def _har_handlet_i_dag(tracker, symbol: str = "MES") -> bool:
    """Har trackeren en MES-ordre på kontoen i dag?

    ⚠ DET AFGØR OM EN ULÆSELIG POSITION SKAL VÆKKE NOGEN. Har Iben ikke handlet,
    er "positionen kan ikke læses" en kedelig kendsgerning om en tom konto — og
    en telefon der ringer hver aften hun ikke har handlet, bliver slået fra
    inden den aften hvor den betyder noget.
    """
    i_dag = datetime.datetime.now().date()
    for e in tracker._entries:
        if (e.get("ticker") or "").upper() != symbol.upper():
            continue
        if (e.get("broker") or "").upper() != "NT8":
            continue
        if (e.get("ordre_type") or "") not in ("LONG", "SHORT", "EXIT"):
            continue
        try:
            if datetime.datetime.fromisoformat(
                    str(e.get("placed_at"))).date() == i_dag:
                return True
        except Exception:
            continue
    return False


async def tvangsluk(tracker, journal, *, instrument: str,
                    bogfoer_exit=None, blind_forsoeg: int = 20,
                    blind_pause: float = 15.0) -> dict:
    """Annullér alt og luk positionen. Kører uanset om der er exit-ordrer.

    ⚠ "UANSET HVAD" ER IKKE DET SAMME SOM "ANTAG DET VÆRSTE". Kan positionen
    ikke læses, lukkes der INTET — en markedsordre på en position vi ikke kender,
    kan lige så godt åbne en som lukke en.

    ⚠ MEN DEN MÅ HELLER IKKE VÆRE TAVS. Kravet er "uanset hvad", og en
    tvangslukning der stiltiende gav op, ville efterlade en position natten over
    med fuld børsmargin i stedet for intraday — og ingen ville vide det.

    Derfor: er positionen ulæselig, prøves der igen hvert `blind_pause` sekund i
    fem minutter. Lykkes det stadig ikke, afhænger alarmen af om der overhovedet
    er handlet i dag:

      · handlet i dag  -> push prioritet 5 + journal `tvangsluk_fejlet`
      · ikke handlet   -> kun en linje i loggen

    Den anden halvdel er lige så vigtig som den første: ringer telefonen hver
    aften Iben ikke har handlet, slår hun den fra inden den aften hvor den
    betyder noget.
    """
    t0 = datetime.datetime.now(DK)
    profil = await asyncio.to_thread(NT.klar)
    konto = profil["konto"]

    antal_ryddet = await ryd(tracker, journal, instrument=instrument,
                             hvorfor="tvangslukning")
    # Sidste udvej hvis noget stadig lever.
    try:
        await asyncio.to_thread(NT.annuller_alt, konto)
    except Exception as e:
        logger.error(f"[ExitOrdrer] annuller_alt fejlede: {e}")

    flad = False
    fyld = None
    blinde = 0
    for forsoeg in range(1, 3 + blind_forsoeg):
        p = await asyncio.to_thread(NT.position, instrument, konto)
        netto = p.get("netto")
        if netto is None:
            # ⚠ Prøv igen — men luk ingenting imens.
            blinde += 1
            if blinde <= blind_forsoeg:
                logger.warning(f"[ExitOrdrer] tvangsluk: positionen er UKENDT "
                               f"({blinde}/{blind_forsoeg}) — venter")
                await asyncio.sleep(blind_pause)
                continue
            logger.error("[ExitOrdrer] tvangsluk: positionen forblev UKENDT")
            break
        blinde = 0
        if netto == 0:
            flad = True
            break
        modsat = "SELL" if netto > 0 else "BUY"
        ref = NT.order_ref(praefiks="NTX")
        logger.warning(f"[ExitOrdrer] tvangsluk forsoeg {forsoeg}: "
                       f"{modsat} {abs(netto)} {instrument}")
        await asyncio.to_thread(
            NT.send_ordre, konto=konto, instrument=instrument, action=modsat,
            antal=abs(netto), ordretype="MARKET", tif="DAY", ordre_id=ref)
        r = await asyncio.to_thread(NT.afvent_ordre, ref, 30.0)
        if r["filled"] and r["avg_fill"]:
            fyld = {"order_id": ref, "action": modsat, "antal": r["filled"],
                    "pris": r["avg_fill"]}
            if bogfoer_exit:
                try:
                    await bogfoer_exit(ref, modsat, r["filled"], r["avg_fill"],
                                       r["status"], "TVANGSLUK")
                except Exception as e:
                    logger.error(f"[ExitOrdrer] bogfoering af tvangsluk fejlede: {e}")
        await asyncio.sleep(3)

    ud = {"flad": flad, "ryddet": antal_ryddet, "fyld": fyld,
          "blind": blinde > blind_forsoeg,
          "handlet_i_dag": _har_handlet_i_dag(tracker),
          "dansk": f"{t0:%H:%M}", "et": f"{t0.astimezone(ET):%H:%M}"}
    if not flad and ud["blind"] and not ud["handlet_i_dag"]:
        # ⚠ Ingen handel i dag + ulæselig position = ingenting at lukke.
        # En linje i loggen, ikke en telefon der ringer.
        logger.info("[ExitOrdrer] tvangsluk: positionen kunne ikke læses, men "
                    "der er ikke handlet MES i dag — ingen alarm")
        await journal.log_event(
            ibkr_account=konto or None, source="manual_exit",
            event_type="tvangsluk_uden_handel", symbol=instrument.split()[0],
            payload=ud)
        return ud
    if flad:
        await journal.log_event(
            ibkr_account=konto or None, source="manual_exit",
            event_type="tvangsluk_udfoert", symbol=instrument.split()[0],
            payload=ud)
        logger.info(f"[ExitOrdrer] tvangsluk udfoert {ud['dansk']} dansk "
                    f"/ {ud['et']} ET")
    else:
        # ⚠ RÅB OP. En tvangslukning der ikke lukkede, efterlader en position
        # natten over — med fuld børsmargin i stedet for intraday.
        await journal.log_event(
            ibkr_account=konto or None, source="manual_exit",
            event_type="tvangsluk_fejlet", symbol=instrument.split()[0],
            payload=ud)
        await _push(
            "⚠ Positionen kunne ikke læses — tjek NinjaTrader og luk manuelt."
            if ud["blind"] else
            "⚠ Tvangslukning MISLYKKEDES. Der kan stå en åben MES-position. "
            "Tjek NinjaTrader nu.",
            prioritet=5, titel="Trading Dash — tvangsluk")
        logger.error(f"[ExitOrdrer] ⚠ TVANGSLUK FEJLEDE: {ud}")
    return ud


# ═══════════════════════════════════════════════════════════════════════════
# Overvågningsløkken
# ═══════════════════════════════════════════════════════════════════════════
class Overvaagning:
    """Trailing, positionsopsyn, ordrestatus og tvangslukning.

    ⚠ ALT ATI-/OIF-I/O I asyncio.to_thread. Modulet er synkront og sover; kaldt
    direkte ville det fryse hele backenden — alle WS-klienter og alle kørende
    strategier. Samme begrundelse som i NT8-grenen i main.py.
    """

    def __init__(self, tracker, journal, *, hent_kurs, instrument_for,
                 bogfoer_exit=None):
        self.tracker = tracker
        self.journal = journal
        self.hent_kurs = hent_kurs
        self.instrument_for = instrument_for   # () -> "MES 12-26" | None
        self.bogfoer_exit = bogfoer_exit
        self._sidste_kurs_tid = 0.0
        self._blind_siden = 0.0
        self._sidste_aendr: dict[str, float] = {}
        self._tvangsluk_dato = None
        self._paamindet_dato = None
        self._kurs_advaret = False

    # ── trailing ──────────────────────────────────────────────────────────
    async def _traek_trail(self, e: dict, kurs: float, instrument: str) -> None:
        oid = str(e["order_id"])
        afstand = e.get("trail_afstand")
        hoejeste = e.get("trail_hoejeste")
        if not afstand or hoejeste is None:
            return
        er_long = (e.get("action") or "").upper() == "SELL"   # SELL lukker long

        ny_hoejeste = max(hoejeste, kurs) if er_long else min(hoejeste, kurs)
        ny_stop = (ny_hoejeste - afstand) if er_long else (ny_hoejeste + afstand)
        ny_stop = round(round(ny_stop / TICK) * TICK, 2)
        nu_stop = e.get("trigger_pris")

        # ⚠ FLYT KUN I BESKYTTENDE RETNING. En stop der kan gå tilbage, er ikke
        # en trailing stop — den er en stop der følger markedet ned.
        if nu_stop is not None:
            if er_long and ny_stop < nu_stop + TICK - 1e-9:
                self.tracker.opdater(oid, trail_hoejeste=ny_hoejeste)
                return
            if not er_long and ny_stop > nu_stop - TICK + 1e-9:
                self.tracker.opdater(oid, trail_hoejeste=ny_hoejeste)
                return

        # ⚠ Throttle: højst én CHANGE pr. 2 sek. pr. ordre, med den SENESTE
        # værdi. MES sætter nyt high mange gange i minuttet, og hver CHANGE er
        # en OIF-fil plus en kvittering hos brokeren.
        nu = time.time()
        if nu - self._sidste_aendr.get(oid, 0.0) < TRAIL_THROTTLE_SEK:
            self.tracker.opdater(oid, trail_hoejeste=ny_hoejeste)
            return
        self._sidste_aendr[oid] = nu

        svar = await asyncio.to_thread(NT.aendr, oid, stop=ny_stop)

        # ⚠ VERIFICÉR, ANTAG IKKE. ATI melder ikke stopprisen, men NT8's log
        # gør (målt 06-10, spec §4a). Vi holder vores egen forventede værdi OG
        # kontrollerer den mod det NT8 selv skriver — en CHANGE der blev sendt,
        # er ikke det samme som en stop der flyttede.
        set_i_log = laes_ordre_fra_log(svar.get("logliner") or [])
        afvigelse = (set_i_log.get("stop") is not None
                     and abs(set_i_log["stop"] - ny_stop) > 1e-9)
        if afvigelse or (set_i_log.get("fejl") not in (None, "No error")):
            await self.journal.log_event(
                source="manual_exit", event_type="trail_afvigelse",
                symbol=instrument.split()[0],
                payload={"order_id": oid, "forventet": ny_stop,
                         "i_nt8_log": set_i_log.get("stop"),
                         "nt8_status": set_i_log.get("status"),
                         "nt8_fejl": set_i_log.get("fejl")})
            logger.error(f"[ExitOrdrer] ⚠ trail-afvigelse {oid}: forventet "
                         f"{ny_stop}, NT8 siger {set_i_log}")
        elif set_i_log.get("stop") is None:
            # Loggen svarede slet ikke — så er verifikationen kun status.
            logger.warning(f"[ExitOrdrer] {oid}: ingen Order=-linje efter "
                           f"CHANGE; stoppen er IKKE verificeret")
        self.tracker.opdater(oid, trigger_pris=ny_stop,
                             trail_hoejeste=ny_hoejeste,
                             trail_stop_forventet=ny_stop)

    # ── et gennemløb ──────────────────────────────────────────────────────
    async def _tik(self, instrument: str, konto: str) -> None:
        aktive = [e for e in self.tracker._entries
                  if e.get("source") == "manual_exit"
                  and (e.get("ordre_type") or "") in TYPER
                  and e.get("status") not in ("Filled", "Cancelled", "Rejected")]

        kurs = await self.hent_kurs()
        if kurs:
            self._sidste_kurs_tid = time.time()
            self._kurs_advaret = False
        elif aktive and self._sidste_kurs_tid and                 time.time() - self._sidste_kurs_tid > KURS_STILHED_SEK and                 not self._kurs_advaret:
            # ⚠ Stoppen BLIVER LIGGENDE hvor den er. Den beskytter stadig; den
            # følger bare ikke med. At flytte den på et gæt ville være værre.
            self._kurs_advaret = True
            await self.journal.log_event(
                source="manual_exit", event_type="trail_uden_kurs",
                symbol=instrument.split()[0],
                payload={"sekunder": round(time.time() - self._sidste_kurs_tid),
                         "note": "stoppen staar hvor den stod"})
            logger.error("[ExitOrdrer] ⚠ ingen kurs — trailing staar stille")
            # ⚠ OG DEN SKAL KUNNE SES I VINDUET, ikke kun i journalen. En
            # trailing stop der er holdt op med at foelge med, ser ud praecis
            # som en der foelger med — indtil man opdager at tallet ikke har
            # rykket sig i et kvarter.
            for e in aktive:
                if (e.get("ordre_type") or "").upper() == "TRAIL":
                    self.tracker.opdater(
                        e["order_id"],
                        advarsel="Ingen kurs — stoppen følger ikke med")

        if kurs:
            for e in aktive:
                if (e.get("ordre_type") or "").upper() == "TRAIL":
                    # Kursen er tilbage -> ryd advarslen igen.
                    if e.get("advarsel"):
                        self.tracker.opdater(e["order_id"], advarsel=None)
                    try:
                        await self._traek_trail(e, kurs, instrument)
                    except Exception as ex:
                        logger.error(f"[ExitOrdrer] trail fejlede: {ex}")

        # ── position ──────────────────────────────────────────────────────
        p = await asyncio.to_thread(NT.position, instrument, konto)
        netto = p.get("netto")
        if netto is None:
            # ⚠ UKENDT ER IKKE FLAD. Gør intet. Slet intet. Luk intet.
            if not self._blind_siden:
                self._blind_siden = time.time()
            elif time.time() - self._blind_siden > POSITION_BLIND_SEK:
                self._blind_siden = time.time()
                await self.journal.log_event(
                    source="manual_exit", event_type="exit_overvaagning_blind",
                    symbol=instrument.split()[0],
                    payload={"sekunder": POSITION_BLIND_SEK,
                             "note": "positionen kan ikke laeses; der er IKKE "
                                     "annulleret eller lukket noget"})
                logger.error("[ExitOrdrer] ⚠ positionen er blind")
                for e in aktive:
                    self.tracker.opdater(
                        e["order_id"],
                        advarsel="Positionen kan ikke læses — exit-ordrerne "
                                 "overvåges ikke lige nu")
            return
        if self._blind_siden:
            for e in aktive:
                if (e.get("advarsel") or "").startswith("Positionen"):
                    self.tracker.opdater(e["order_id"], advarsel=None)
        self._blind_siden = 0.0

        if netto == 0 and aktive:
            # ⚠ DEN VIGTIGSTE REGEL I MODULET. En efterladt stop paa en lukket
            # position aabner en NY position naar den udloeses.
            n = await ryd(self.tracker, self.journal,
                          instrument=instrument, hvorfor="positionen er nul")
            logger.info(f"[ExitOrdrer] position 0 -> ryddede {n} exit-ordrer")
            return

        if netto and aktive:
            for e in aktive:
                if int(e.get("shares") or 0) != abs(netto):
                    await asyncio.to_thread(NT.aendr, e["order_id"],
                                            antal=abs(netto))
                    self.tracker.opdater(e["order_id"], shares=abs(netto),
                                         remaining=abs(netto))
                    logger.info(f"[ExitOrdrer] {e['order_id']} antal -> "
                                f"{abs(netto)}")

        # ── ordrestatus ───────────────────────────────────────────────────
        for e in aktive:
            oid = str(e["order_id"])
            st = await asyncio.to_thread(NT.ordre_status, oid)
            if not st:
                continue          # ⚠ "" er UKENDT — lad raekken staa
            if st == "Filled":
                antal, pris = await asyncio.to_thread(NT.fyldning, oid)
                self.tracker.opdater(oid, status=st, bekraeftet=True,
                                     filled=antal, avg_fill=pris, remaining=0)
                if self.bogfoer_exit and antal and pris:
                    try:
                        await self.bogfoer_exit(
                            oid, e.get("action"), antal, pris, st,
                            (e.get("ordre_type") or "").upper())
                    except Exception as ex:
                        logger.error(f"[ExitOrdrer] bogfoering fejlede: {ex}")
            elif st in ("Cancelled", "Rejected"):
                self.tracker.opdater(oid, status=st, bekraeftet=True)

    # ── løkken ────────────────────────────────────────────────────────────
    async def koer(self) -> None:
        logger.info("[ExitOrdrer] overvaagning startet")
        while True:
            try:
                instrument = self.instrument_for()
                profil = accounts.nt_forbindelse()
                if not instrument or not profil:
                    await asyncio.sleep(5)
                    continue
                konto = profil["konto"]

                await self._tidsstyring(instrument)

                har_noget = any(
                    e.get("source") == "manual_exit"
                    and e.get("status") not in ("Filled", "Cancelled", "Rejected")
                    for e in self.tracker._entries)
                if not har_noget:
                    p = await asyncio.to_thread(NT.position, instrument, konto)
                    if not p.get("netto"):
                        await asyncio.sleep(5)   # ⚠ sover naar der intet er
                        continue
                await self._tik(instrument, konto)
                await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                logger.info("[ExitOrdrer] overvaagning stoppet")
                raise
            except Exception as e:
                logger.error(f"[ExitOrdrer] loekkefejl: {e}")
                await asyncio.sleep(5)

    async def _tidsstyring(self, instrument: str) -> None:
        nu = datetime.datetime.now(DK)
        if nu.weekday() > 4:          # weekend
            return
        if self._paamindet_dato != nu.date() and nu >= paamindelsestidspunkt(nu):
            self._paamindet_dato = nu.date()
            profil = accounts.nt_forbindelse()
            if profil:
                p = await asyncio.to_thread(NT.position, instrument,
                                            profil["konto"])
                if p.get("netto"):
                    await _push(
                        "Åben MES-position. Lad PC'en være tændt til 22:15 — "
                        "den lukkes automatisk kl. 22:00.",
                        prioritet=4, dedup="exit_paamindelse")
        if self._tvangsluk_dato != nu.date() and nu >= lukketidspunkt(nu):
            self._tvangsluk_dato = nu.date()
            # ⚠ KUN I ET VINDUE EFTER LUKKETID. Starter backenden kl. 23:30 —
            # efter en genstart, eller fordi nogen taendte PC'en sent — ville
            # den ellers fyre en tvangslukning med det samme. CME holder pause
            # 17:00-18:00 ET (23:00-00:00 dansk), saa markedsordren ville ligge
            # og vente, og "tvangsluk_udfoert" ville staa i journalen om en
            # lukning der ikke skete.
            forsinkelse = (nu - lukketidspunkt(nu)).total_seconds() / 60
            if forsinkelse > 60:
                logger.warning(
                    f"[ExitOrdrer] lukketid var for {forsinkelse:.0f} min "
                    f"siden — tvangslukning springes over. Er der en aaben "
                    f"position, skal den lukkes manuelt.")
                await self.journal.log_event(
                    source="manual_exit", event_type="tvangsluk_sprunget_over",
                    symbol=instrument.split()[0],
                    payload={"minutter_efter_lukketid": round(forsinkelse),
                             "hvorfor": "backenden startede efter lukketid; en "
                                        "markedsordre i CME's pause ville ikke "
                                        "fylde"})
                return
            await tvangsluk(self.tracker, self.journal, instrument=instrument,
                            bogfoer_exit=self.bogfoer_exit)
