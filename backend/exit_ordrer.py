"""
exit_ordrer.py — STOP / TARGET / TRAIL på en NT8-position
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
# Navnene Iben bruger. ⚠ De staar KUN her og i EXIT_TYPER i OrdersWindow;
# alt andet i modulet sammenligner mod TYPER, saa en omdoebning er ét sted.
#
# ⚠ "STOP" er IKKE NT8's ordretype. OIF'ens ordretype er en selvstaendig
# variabel (STOPMARKET / LIMIT), sat i opret_exit. De to navnerum maa ikke
# blandes: en STOP laegges som STOPMARKET, en TARGET som LIMIT.
TYPER = ("STOP", "TARGET", "TRAIL")
# Tidligere stavemaader. ⚠ SLOSS og TPROF er BEVIDST ikke med: raekker fra
# 06-10 med de navne er alle annullerede og historiske, og de vises stadig
# med deres egen tekst i vinduet — de taelles blot ikke laengere som
# exit-typer. Besluttet 07-10.
LEGACY_TYPER = {"PLOSS": "STOP"}


def normaliser_type(t: str) -> str:
    """Den gaeldende stavemaade for en exit-type."""
    t = (t or "").upper()
    return LEGACY_TYPER.get(t, t)


def er_exit_type(t: str) -> bool:
    return normaliser_type(t) in TYPER

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
# Hvor mange gennemløb en fyldt ordre må mangle sin fyldpris, før det siges
# højt. ⚠ Den SKAL siges højt: uden fyldpris er der ingen P&L, ingen
# entry/exit-parring og intet chart — handlen står åben i journalen.
FYLD_FORSOEG_ALARM = 5
# Hvor langt en STOP/TARGET-pris maa ligge fra kursen. ⚠ Det er IKKE en
# risikogrænse — det er en tastefejls-fælde. MES koster $5 pr. point, så en
# pris på "20" (ment som 20 points) i et marked på 7850 er ikke en dårlig
# ordre, den er en stop loss 7830 points væk = $39.150. NT8 ville tage imod
# den uden at blinke.
FORNUFT_PCT = 0.03


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
    """⚠ BÆRER OGSÅ TICK, MULTIPLIKATOR OG GRÆNSE, så frontenden ikke gaetter.

    Modalen viser afstanden i points OG i dollar. Dollar kræver $ pr. point,
    og det tal må ikke skrives op i en .tsx-fil: futures_katalog er ÉN
    sandhedskilde (bekræftet mod reqPositions-avgCost), og en kopi i
    frontenden ville drive fra den uden at nogen opdagede det — tallet ser
    rigtigt ud lige indtil kontrakten skifter.
    """
    k = _laes_konfig()
    try:
        from futures_katalog import multiplikator as _mult
        mult = _mult("MES")
    except Exception as e:
        logger.warning(f"[ExitOrdrer] multiplikator kunne ikke hentes: {e}")
        mult = None
    return {"trail_afstand": k["trail_afstand"], "enhed": "points",
            "tick": TICK, "multiplikator": mult,
            "fornuft_pct": FORNUFT_PCT}


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
                         f"({_dk(TICK)}). {_dk(v)} går ikke op.")
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
    """Dansk talformat: 7.841,25 — punktum som tusind, komma som decimal.

    ⚠ SAMME FORM SOM VINDUET. Den gav foer "7 841,25" med mellemrum, mens
    frontendens toLocaleString("da-DK") giver "7.841,25". To skrivemaader for
    samme tal i samme arbejdsgang saar tvivl om hvad man faktisk har indtastet
    — og her er tallet en stop loss-pris.

    translate() bytter de to tegn i ÉT greb; en pladsholder undervejs er netop
    dét der gik galt da denne linje blev skrevet.
    """
    return f"{v:,.2f}".translate(str.maketrans(",.", ".,"))


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


def netto_foer_fra_efter(netto_efter: Optional[int], action: str,
                        antal) -> Optional[int]:
    """Nettoet FØR ordren, udregnet af nettoet EFTER og hvad ordren selv gjorde.

    ⚠ FORDI ATI IKKE ALTID HAR NØGLEN NÅR VI SPØRGER FØRSTE GANG. Måltes
    07-10 på en frisk NinjaTrader: strømmen bar `Orders`, `Strategies`,
    `BuyingPower`, `CashValue` og `RealizedPnL` — men INGEN `MarketPosition|`.
    Den nøgle dukker først op når kontoen HAR haft en position i sessionen;
    derefter bliver den liggende på 0. Dagen før virkede det, fordi NT8 havde
    haft en MES-position.

    Konsekvensen var tavs og alvorlig: `klassificer(None, …)` gav "UKENDT",
    og en UKENDT-række tæller ingenting i `netto_fra_raekker` og får ingen
    exit-knapper — uden at der står hvorfor. Den FØRSTE handel efter en frisk
    NT8-start kunne altså ikke beskyttes, og det ville se ud som om
    exit-ordrerne var i stykker.

    Men nettoet før ordren er ikke tabt — det kan REGNES. Efter fyldningen HAR
    ATI nøglen (der ER en position nu), og ordren ved selv hvad den gjorde:

        netto_foer = netto_efter − (+antal for BUY, −antal for SELL)

    ⚠ None ER STADIG None. Er nettoet ukendt OGSÅ efter fyldningen, gætter vi
    ikke — rækken forbliver UKENDT og siger det højt i vinduet.
    """
    if netto_efter is None:
        return None
    try:
        q = abs(int(antal))
        n = int(netto_efter)
    except (TypeError, ValueError):
        return None
    if not q:
        # Ingen fyldning -> ordren gjorde ingenting, og nettoet efter er
        # nettoet før. At regne videre ville være at opfinde en bevægelse.
        return n
    return n - (q if (action or "").upper() == "BUY" else -q)


# Hvad der staar i Ordrer-vinduet naar typen ikke kunne afgoeres. ⚠ Raekken
# faar ingen exit-knapper, og uden en tekst ser det ud som om funktionen er i
# stykker i stedet for at positionen ikke kunne laeses.
ADVARSEL_UKENDT_POSITION = ("Positionen kunne ikke læses fra NinjaTrader "
                            "— exit-knapper utilgængelige")


def omklassificer(netto_efter: Optional[int], action: str,
                  antal) -> str:
    """LONG | SHORT | EXIT | UKENDT — afgjort EFTER fyldningen.

    Samme regel som `klassificer`, kun med nettoet udregnet bagud. Lagt ved
    siden af den, saa de to aldrig kan komme til at bruge hver sin regel.
    """
    return klassificer(netto_foer_fra_efter(netto_efter, action, antal),
                       action)


def omklassificering(netto_efter: Optional[int], action: str,
                     antal) -> tuple:
    """(type, advarsel) — hele beslutningen, saa den kan proeves ét sted.

    ⚠ LAGT HER OG IKKE I main.py. Praecis den slags sammenstilling var fejlen
    i trin 6: `netto_fra_raekker` og `seneste_aabnende` var hver for sig
    rigtige, men beslutningen der brugte dem laa i et endpoint og blev derfor
    aldrig proevet. Typen og advarslen hoerer sammen — lykkes den ene ikke,
    SKAL den anden staa der — og to steder ville kunne komme i utakt.

    advarsel er None naar typen blev afgjort. Kalderen skriver begge felter,
    saa en raekke der bliver afklaret, ogsaa faar advarslen fjernet igen.
    """
    t = omklassificer(netto_efter, action, antal)
    return t, (None if t != "UKENDT" else ADVARSEL_UKENDT_POSITION)


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
        raise ValueError(f"Prisen skal være et helt antal ticks ({_dk(TICK)}). "
                         f"{_dk(pris)} går ikke op.")
    if kurs is None:
        # ⚠ Ingen kurs -> vi kan ikke afgøre siden. At sende alligevel ville
        # lade NT8 om det, og dermed give Iben en dialogboks i stedet for en
        # besked i vinduet hun arbejder i.
        raise ValueError("Ingen aktuel kurs — prisen kan ikke kontrolleres, "
                         "og der sendes ingen ordre.")
    # ⚠ STØRRELSESORDEN FØR SIDE. En pris på "20" paa en long ligger under
    # kursen og slipper derfor gennem STOP-kontrollen nedenfor; paa en TARGET
    # faar man "skal ligge over aktuel kurs", hvilket er sandt og ubrugeligt.
    # Den rigtige besked er "det ser ud som et antal points".
    afstand = abs(pris - kurs)
    if afstand > kurs * FORNUFT_PCT:
        raise ValueError(
            f"{_dk(pris)} ligger {_dk(afstand)} points fra aktuel kurs "
            f"({_dk(kurs)}) — det er mere end "
            f"{_dk(FORNUFT_PCT * 100)} %. Har du skrevet et antal points i "
            f"stedet for en pris? Feltet vil have PRISEN ordren skal "
            f"udløses paa.")
    if retning == "LONG":
        if type_ == "STOP" and pris >= kurs:
            raise ValueError(f"Stop loss skal ligge under aktuel kurs "
                             f"({_dk(kurs)}) for en long.")
        if type_ == "TARGET" and pris <= kurs:
            raise ValueError(f"Target profit skal ligge over aktuel kurs "
                             f"({_dk(kurs)}) for en long.")
    else:
        if type_ == "STOP" and pris <= kurs:
            raise ValueError(f"Stop loss skal ligge over aktuel kurs "
                             f"({_dk(kurs)}) for en short.")
        if type_ == "TARGET" and pris >= kurs:
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


def netto_fra_raekker(raekker) -> int:
    """Nettopositionen udledt af trackerens egne raekker.

    ⚠ TRACKEREN SKAL KUNNE SVARE ALENE. `exit_mulig` saa foer kun paa om en
    raekke var den NYESTE aabnende — aldrig paa om positionen stadig fandtes.
    Efter et salg fra watchlisten stod LONG-raekken derfor med tre blaa knapper
    paa en position der var lukket (maalt 06-10, trin 6 i §9). At trykke paa dem
    ville have lagt en stop loss paa ingenting — og den foerste ordre der fylder
    paa en flad konto, AABNER en position.

    ATI's netto er en ekstra bekraeftelse, ikke en betingelse: er ATI tavs,
    skal knapperne stadig forsvinde.

    ⚠ KUN FYLDTE RAEKKER TAELLER, og kun LONG/SHORT/EXIT. En STOP-raekke der
    fylder, faar sin egen EXIT-raekke skrevet af _exit_bogfoer; taltes begge,
    ville samme fyldning blive regnet to gange.

    `action` afgoer fortegnet — ikke typen. En LONG er et koeb, en SHORT et
    salg, og en EXIT er dét der lukker. BUY er plus, SELL er minus, uanset hvad
    raekken hedder.
    """
    netto = 0
    for e in sorted(raekker, key=lambda x: str(x.get("placed_at") or "")):
        if (e.get("ordre_type") or "").upper() not in ("LONG", "SHORT", "EXIT"):
            continue
        if e.get("status") != "Filled":
            continue
        try:
            q = int(float(e.get("filled") or 0))
        except (TypeError, ValueError):
            continue
        if not q:
            continue
        netto += q if (e.get("action") or "").upper() == "BUY" else -q
    return netto


def exit_mulig_for(raekker) -> Optional[str]:
    """Hvilken raekke skal have exit-knapper? None = ingen.

    ⚠ SELVE BESLUTNINGEN, saa den kan proeves uden at starte en webserver.
    Den laa foer inde i main.py's _berig_med_exit, og derfor var det netop den
    der ikke blev testet — mens de to funktioner under den var daekket.
    Fejlen i trin 6 sad praecis i sammenstillingen: begge dele var rigtige hver
    for sig, og "er du nyeste?" blev stillet uden "findes positionen?".

    Knapperne hoerer kun paa den SENESTE aabnende raekke, og kun saa laenge der
    er en position. Exit-ordrer daekker hele positionen (spec §13 punkt 2), saa
    knapper paa en aeldre raekke ville lade som om der var to at beskytte.
    """
    if not netto_fra_raekker(raekker):
        return None
    return seneste_aabnende(raekker)


def seneste_aabnende(raekker) -> Optional[str]:
    """order_id paa den nyeste LONG/SHORT-raekke, eller None."""
    aabnende = [e for e in raekker
                if (e.get("ordre_type") or "").upper() in ("LONG", "SHORT")]
    if not aabnende:
        return None
    nyeste = max(aabnende, key=lambda x: str(x.get("placed_at") or ""))
    return str(nyeste.get("order_id"))


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
    """Læg en STOP/TARGET/TRAIL på den position parent-rækken åbnede.

    `hent_kurs` er en awaitable () -> float|None. Den injiceres, så modulet
    ikke skal kende main.py.
    """
    type_ = normaliser_type(type_)
    if normaliser_type(type_) not in TYPER:
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
    if any(normaliser_type(e.get("ordre_type")) == type_ for e in aktive):
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
    elif type_ == "STOP":
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
        trigger_pris=(stop if type_ != "TARGET" else limit),
        trail_hoejeste=trail_hoejeste, trail_afstand=trail_afstand)
    if not r["terminal"]:
        tracker.opdater(ref, status=status or "afventer", bekraeftet=r["aktiv"])

    logger.info(f"[ExitOrdrer] {type_} {ref} oco={oco} status={status or '(ukendt)'}")
    return {"order_id": ref, "status": status or "afventer",
            "oco_id": oco, "aktiv": r["aktiv"],
            "trigger_pris": stop if type_ != "TARGET" else limit}


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
        # ⚠ "Annulleret" er sandt og misvisende. Raekken blev ikke slettet
        # fordi nogen ville af med den — den blev fejet med af OCO-kaskaden og
        # lagt igen et oejeblik senere. Staar der bare "Annulleret", ser det ud
        # som om beskyttelsen forsvandt, og det er praecis den tvivl man ikke
        # skal sidde med midt i en handel.
        tracker.opdater(e["order_id"], status="Cancelled", bekraeftet=True,
                        genlagt_som=None)
        t = normaliser_type(e.get("ordre_type"))
        modsat = "SELL" if netto > 0 else "BUY"
        ny_ref = NT.order_ref(praefiks="NTX")
        er_limit = t == "TARGET"
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
        # Peg den gamle raekke paa sin afloeser, saa vinduet kan sige
        # "Genlagt" i stedet for "Annulleret".
        tracker.opdater(e["order_id"], genlagt_som=ny_ref)
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
                  and er_exit_type(e.get("ordre_type") or "")
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
                  and er_exit_type(e.get("ordre_type") or "")
                  and e.get("status") not in ("Filled", "Cancelled", "Rejected")]

        # ⚠ FYLDT, MEN UDEN TAL. ATI pusher status og fyldpris i hver sit felt,
        # og de kommer ikke noedvendigvis i samme oplaeg. `fyldning()` siger det
        # selv i sin egen docstring: (0, 0.0) betyder "intet set", ikke "nul
        # fyldt". Loekken behandlede det alligevel som "intet at bogfoere" og
        # skrev SAMTIDIG raekken terminal med nuller — hvorefter den faldt ud af
        # `aktive` for altid og fyldningen var vaek.
        #
        # Maalt 06-10: ordre 602502520254 fyldte 1 @ 7850,25 (NT8's egen log),
        # raekken stod som status=Filled filled=0 avg_fill=0.0, og shorten blev
        # aldrig lukket i journalen. Rekkefoelge-fejlen ovenfor var den ene
        # aarsag; DEN HER var den anden, og den ville have staaet tilbage.
        #
        # Saadanne raekker bliver spurgt igen hvert gennemloeb. De trailes IKKE
        # — ordren er fyldt, der er intet at flytte.
        uafklarede = [e for e in self.tracker._entries
                      if e.get("source") == "manual_exit"
                      and er_exit_type(e.get("ordre_type") or "")
                      and e.get("status") == "Filled"
                      and not e.get("fyld_bogfoert")
                      and not e.get("avg_fill")]

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
                if normaliser_type(e.get("ordre_type")) == "TRAIL":
                    self.tracker.opdater(
                        e["order_id"],
                        advarsel="Ingen kurs — stoppen følger ikke med")

        if kurs:
            for e in aktive:
                if normaliser_type(e.get("ordre_type")) == "TRAIL":
                    # Kursen er tilbage -> ryd advarslen igen.
                    if e.get("advarsel"):
                        self.tracker.opdater(e["order_id"], advarsel=None)
                    try:
                        await self._traek_trail(e, kurs, instrument)
                    except Exception as ex:
                        logger.error(f"[ExitOrdrer] trail fejlede: {ex}")

        # ── ordrestatus FOERST ─────────────────────────────────────────
        # ⚠ REKKEFOELGEN ER IKKE LIGEGYLDIG, og den var forkert.
        # Positionen blev laest foer ordrestatus, og naar en TRAIL fyldte,
        # gik positionen til nul. Loekken laeste det som "positionen er
        # lukket, ryd resterne", annullerede den netop fyldte ordre og
        # returnerede — FOER den naaede at se at den var fyldt.
        #
        # Maalt 06-10 kl. 13:07: NT8 fyldte en TRAIL paa en short, og
        # Trading Dash bogfoerte det aldrig. Handlen stod aaben i journalen
        # med exit_price=None, SHORT-raekken beholdt sine knapper, og
        # Fyldt-kolonnen sagde "—" paa en ordre der var fyldt.
        #
        # ⚠ Loekken kunne ikke skelne "exit'en fyldte" fra "positionen blev
        # lukket et andet sted" — og den stillede det forkerte spoergsmaal
        # foerst. En fyldt exit FORKLARER hvorfor positionen er nul, saa
        # den skal laeses foerst.
        for e in list(aktive) + list(uafklarede):
            oid = str(e["order_id"])
            st = await asyncio.to_thread(NT.ordre_status, oid)
            # En raekke vi allerede VED er fyldt, skal spoerges om fyldprisen
            # selv om status-feltet er faldet ud af oplaegget igen.
            fyldt_foer = e.get("status") == "Filled"
            if not st and not fyldt_foer:
                continue          # ⚠ "" er UKENDT — lad raekken staa
            if st == "Filled" or fyldt_foer:
                antal, pris = await asyncio.to_thread(NT.fyldning, oid)
                if antal and pris:
                    self.tracker.opdater(oid, status="Filled", bekraeftet=True,
                                         filled=antal, avg_fill=pris,
                                         remaining=0, fyld_bogfoert=True,
                                         fyld_forsoeg=None, advarsel=None)
                    if self.bogfoer_exit:
                        try:
                            await self.bogfoer_exit(
                                oid, e.get("action"), antal, pris, "Filled",
                                normaliser_type(e.get("ordre_type")))
                        except Exception as ex:
                            logger.error(
                                f"[ExitOrdrer] bogfoering fejlede: {ex}")
                    continue
                # ⚠ INGEN NULLER I RAEKKEN. At skrive filled=0 avg_fill=0.0
                # her ville gemme "vi ved det ikke" bag et tal der ser ud som
                # et svar — og samtidig lukke raekken for et nyt forsoeg.
                forsoeg = int(e.get("fyld_forsoeg") or 0) + 1
                self.tracker.opdater(
                    oid, status="Filled", bekraeftet=True,
                    fyld_forsoeg=forsoeg,
                    advarsel="Fyldt — fyldprisen er ikke læst endnu")
                if forsoeg == FYLD_FORSOEG_ALARM:
                    await self.journal.log_event(
                        source="manual_exit", event_type="exit_fyldt_uden_pris",
                        symbol=instrument.split()[0],
                        payload={"order_id": oid, "forsoeg": forsoeg,
                                 "type": normaliser_type(e.get("ordre_type")),
                                 "note": "ATI melder Filled, men Filled|/"
                                         "AvgFillPrice| mangler. Handlen staar "
                                         "AABEN i journalen — se NT8's "
                                         "Orders-fane"})
                    logger.error(f"[ExitOrdrer] ⚠ {oid} er fyldt, men "
                                 f"fyldprisen er stadig ikke laest efter "
                                 f"{forsoeg} forsoeg — handlen staar aaben")
            elif st in ("Cancelled", "Rejected"):
                self.tracker.opdater(oid, status=st, bekraeftet=True)

        # ⚠ GENBEREGN. Listen blev lavet FOER status-loekken, og en ordre
        # der lige er bogfoert som fyldt, staar stadig i den. Uden det ville
        # ryd() blive kaldt paa noget der allerede er vaek — harmloest i sig
        # selv, men det ville ogsaa skjule at der INTET var at rydde.
        aktive = [e for e in self.tracker._entries
                  if e.get("source") == "manual_exit"
                  and er_exit_type(e.get("ordre_type") or "")
                  and e.get("status") not in ("Filled", "Cancelled",
                                             "Rejected")]

        # ── position ──────────────────────────────────────────────────────
        # ⚠ Nu hvor fyldninger er bogfoert ovenfor, betyder netto 0 og
        # tilbagevaerende aktive exits at positionen blev lukket et ANDET
        # sted — manuelt i NT8, eller af tvangslukningen. Saa skal de ryddes.
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

    async def _tidsstyring(self, instrument: str, nu=None) -> None:
        """⚠ `nu` er KUN til test. Loekken kalder uden, saa produktionen
        laeser uret ét sted. Uden den kunne hverken paamindelsen, den normale
        tvangslukning eller overspringelsen proeves — og overspringelsen er
        netop den gren der efterlader et menneske med en aaben position.
        """
        nu = nu or datetime.datetime.now(DK)
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
                # ⚠ OG SAA SKAL TELEFONEN RINGE. At springe tvangslukningen
                # over er det rigtige valg — en markedsordre i CME's pause ville
                # ikke fylde, og "tvangsluk_udfoert" ville staa i journalen om en
                # lukning der ikke skete. Men beslutningen efterlader et MENNESKE
                # med arbejdet, og en haendelse i loggen er ikke en besked til
                # nogen. Uden push ville Iben opdage den aabne position naeste
                # morgen — efter en nat med margin paa en uafdaekket future.
                netto = None
                try:
                    pr = accounts.nt_forbindelse() or {}
                    pos = await asyncio.to_thread(NT.position, instrument,
                                                  pr.get("konto") or "")
                    netto = pos.get("netto")
                except Exception as e:
                    # ⚠ Kan positionen ikke laeses, er svaret UKENDT — ikke
                    # "flad". Netop her maa tavshed ikke blive til et "alt er
                    # fint", for vi har lige undladt at lukke noget.
                    logger.error(f"[ExitOrdrer] positionen kunne ikke laeses "
                                 f"ved oversprunget tvangslukning: {e}")
                if netto:
                    await _push(
                        f"⚠ MES-position paa {netto:+d} er IKKE lukket. "
                        f"Tvangslukningen blev sprunget over "
                        f"({round(forsinkelse)} min efter lukketid). "
                        f"Luk den manuelt i NinjaTrader.",
                        prioritet=5, titel="Trading Dash — aaben position",
                        dedup="exit_tvangsluk_sprunget")
                elif netto is None:
                    await _push(
                        "⚠ Tvangslukningen blev sprunget over, og positionen "
                        "kan ikke laeses. Tjek selv i NinjaTrader om der staar "
                        "noget aabent.",
                        prioritet=5, titel="Trading Dash — position ukendt",
                        dedup="exit_tvangsluk_sprunget")
                await self.journal.log_event(
                    source="manual_exit", event_type="tvangsluk_sprunget_over",
                    symbol=instrument.split()[0],
                    payload={"minutter_efter_lukketid": round(forsinkelse),
                             "netto": netto,
                             "position_ukendt": netto is None,
                             "pushet": bool(netto) or netto is None,
                             "hvorfor": "backenden startede efter lukketid; en "
                                        "markedsordre i CME's pause ville ikke "
                                        "fylde"})
                return
            await tvangsluk(self.tracker, self.journal, instrument=instrument,
                            bogfoer_exit=self.bogfoer_exit)
