"""
vol_serier.py — sammenstilling af vol_cache-serier. L1 og L2 gjort til kode.
═══════════════════════════════════════════════════════════════════════════════════
Dette modul findes FOER den foerste V2-formel skrives, fordi to beslutninger ellers
ville blive truffet i stilhed af den der skriver den — og begge er naesten umulige at
opdage bagefter.

L1 — SAMMENSTIL PAA DATO, ALDRIG PAA POSITION
    VIX, VIX3M og RVX mangler alle tre 2011-09-12. SPY har dagen. Stilles serierne op
    efter raekkenummer, forskydes de tre indeks én dag i forhold til SPY for HELE den
    resterende periode. Femten aars data ville vaere forskudt, hver eneste percentil
    beregnet mod den forkerte dag — og intet ville fejle.

    Det er den farligste enkeltfejl der kan opstaa i V2, fordi den ikke giver
    mistaenkelige tal. Den giver plausible tal der er systematisk forkerte.

    Derfor er der ingen funktion i dette modul der tager en liste og et indeks. Alt
    gaar gennem `dict[date, vaerdi]`, og `sammenstil()` returnerer raekker noeglet paa
    dato. Mangler en serie en dag, staar der `nan` — aldrig naboens vaerdi.

L2 — NYSE-KALENDEREN DEFINERER MOTORENS HANDELSDAGE
    VX har data paa dage hvor NYSE var lukket, fordi CFE holder aabent (MLK,
    Memorial Day, Juneteenth, Carters begravelse). Det er korrekt data om en anden
    boers. Men lag 2 forudsiger dagens RTH-range, og er der ingen RTH-session, findes
    der ingen dag at have en holdning om.

    En CFE-only-dag er derfor IKKE en degraderet dag — den er slet ikke en dag for os.

        NYSE lukket, VX handler         -> DROPPES. Ingen tilstand udstedes.
        NYSE aaben, en serie mangler    -> nan-komponent, DEGRADED, konfidens-nedslag

    Blandes de to sammen, forurenes referencen enten med dage der ikke findes, eller
    ogsaa taelles aegte datahuller som ferie.

    ⚠ Det ophaever ikke B3's fund. Fuldstaendighedsrevisionen skal fortsat rapportere
    "data findes, men kalenderen sagde lukket" som sin egen kategori — det var dét der
    afsloerede CME's 13:15-luk. Revisionen RAPPORTERER uoverensstemmelsen; motoren
    VAELGER NYSE. En diagnose og en driftsregel, ikke det samme.
"""
from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from nyse_kalender import er_handelsdag, handelsdage

CACHE = Path(__file__).resolve().parent / "vol_cache"

# Hvor gammel en komponent maa vaere foer tilstanden er STALE. C2: ved STALE skal
# klodsen NAEGTE at afgive en score frem for at afgive en gammel én.
MAKS_ALDER_DAGE = 5


class Sammenstillingsfejl(Exception):
    """Rejses naar en sammenstilling ikke kan laves paa en maade der er til at stole paa."""


@dataclass
class Raekke:
    """Én handelsdag. `vaerdier` mangler ALDRIG en noegle — der staar nan i stedet.

    `manglende` og `uden_for_daekning` er BEVIDST adskilt, selvom begge giver nan:

      manglende          serien daekker dagen, men har et HUL (fx VIX 2011-09-12)
      uden_for_daekning  serien fandtes slet ikke endnu (fx VIX9D foer 2018-06-22)

    Blandes de, laeser en rapport "VIX9D mangler 4200 dage" som et datakvalitets-
    problem frem for som "instrumentet blev foerst foert af IBKR i 2018". Det er
    samme slags forveksling som L2's — to ting der ser ens ud og betyder noget helt
    forskelligt, og hvor sammenblandingen goer signalet ulaeseligt.
    """
    dag: date
    vaerdier: dict[str, float]
    manglende: list[str] = field(default_factory=list)
    uden_for_daekning: list[str] = field(default_factory=list)

    def har(self, navn: str) -> bool:
        v = self.vaerdier.get(navn)
        return v is not None and not math.isnan(v)


def laes_serie(navn: str, bar: str = "1 day", felt: str = "close",
               mappe: Path | None = None) -> dict[date, float]:
    """Læs én cache-serie som {dato: vaerdi}.

    Returnerer en DICT, ikke en liste. Det er ikke en stilistisk praeference: en liste
    inviterer til at blive indekseret, og positionsindeksering er praecis L1's fejl.
    """
    mappe = mappe or CACHE
    kode = bar.replace(" ", "").replace("day", "dag").replace("mins", "min")
    p = mappe / f"{navn}_{kode}.csv"
    if not p.exists():
        raise Sammenstillingsfejl(f"serien findes ikke: {p}")
    ud: dict[date, float] = {}
    with p.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                d = datetime.fromisoformat(r["timestamp"]).date()
                ud[d] = float(r[felt])
            except (ValueError, KeyError):
                continue
    if not ud:
        raise Sammenstillingsfejl(f"serien er tom: {p}")
    return ud


def sammenstil(serier: dict[str, dict[date, float]],
               start: date | None = None, slut: date | None = None,
               kalender: str = "NYSE") -> list[Raekke]:
    """Stil serier op paa NYSE's handelsdage. Aldrig paa position.

    Dage hvor NYSE var lukket DROPPES, ogsaa selvom en serie har data dér (L2).
    Dage hvor NYSE var aaben men en serie mangler, faar `nan` for netop den serie —
    naboens vaerdi glider ALDRIG ind (L1).
    """
    if kalender != "NYSE":
        raise Sammenstillingsfejl(
            f"kun NYSE-kalenderen er understoettet. Motorens handelsdage ER NYSE's "
            f"(L2) — en anden boers' aabningsdage er ikke dage vi har en holdning om.")
    if not serier:
        return []

    alle = sorted({d for s in serier.values() for d in s})
    s0 = start or alle[0]
    s1 = slut or alle[-1]

    # Hver series egen daekning — saa et hul kan skelnes fra "fandtes ikke endnu".
    daekning = {navn: (min(s), max(s)) for navn, s in serier.items() if s}

    ud: list[Raekke] = []
    for d in handelsdage(s0, s1):
        vaerdier, mangler, udenfor = {}, [], []
        for navn, s in serier.items():
            v = s.get(d)
            if v is not None:
                vaerdier[navn] = v
                continue
            vaerdier[navn] = float("nan")
            f, l = daekning.get(navn, (d, d))
            (udenfor if (d < f or d > l) else mangler).append(navn)
        ud.append(Raekke(dag=d, vaerdier=vaerdier, manglende=mangler,
                         uden_for_daekning=udenfor))
    return ud


def droppede_dage(serier: dict[str, dict[date, float]],
                  start: date | None = None, slut: date | None = None) -> dict[date, list[str]]:
    """Dage med data som IKKE er NYSE-handelsdage — altsaa dage motoren dropper.

    Returneres saa de kan rapporteres frem for at forsvinde. At droppe dem er
    rigtigt; at droppe dem i tavshed er ikke.
    """
    ud: dict[date, list[str]] = {}
    for navn, s in serier.items():
        for d in s:
            if start and d < start:
                continue
            if slut and d > slut:
                continue
            if not er_handelsdag(d):
                ud.setdefault(d, []).append(navn)
    return dict(sorted(ud.items()))


def vurder_status(raekke: Raekke, kraevede: list[str], i_dag: date | None = None,
                  maks_alder: int = MAKS_ALDER_DAGE) -> tuple[str, float]:
    """(status, konfidens) efter C2's regler.

      OK        alle kraevede komponenter til stede
      DEGRADED  mindst én mangler — resten beregnes med nedslag
      STALE     dagen er for gammel til at udtale sig om

    Ved STALE skal kalderen NAEGTE at afgive en score. Stiltiende rapportering paa
    foraeldede data var en af de konkrete fejl i det forrige forsoeg.
    """
    i_dag = i_dag or date.today()
    alder = (i_dag - raekke.dag).days
    if alder > maks_alder:
        return "STALE", 0.0
    mangler = [k for k in kraevede if not raekke.har(k)]
    if not mangler:
        return "OK", 1.0
    if len(mangler) >= len(kraevede):
        return "STALE", 0.0          # intet at regne paa
    # Konfidens falder med andelen der mangler. Lineaert og enkelt — den skal kunne
    # forklares i en morgenbriefing, ikke kalibreres.
    return "DEGRADED", round(1.0 - len(mangler) / len(kraevede), 3)


def tilstoedende(raekker: list[Raekke], navn: str) -> list[tuple[date, float]]:
    """Serien som (dato, vaerdi) UDEN nan — til beregninger der ikke taaler huller.

    Bemaerk at datoen foelger med. Kaster man den vaek og beholder kun vaerdierne,
    er man tilbage ved positionssammenstilling, og L1's fejl er genopstaaet.
    """
    return [(r.dag, r.vaerdier[navn]) for r in raekker if r.har(navn)]


# ═══════════════════════════════════════════════════════════════════════════════
# 1-min-serier — RTH-skaaret ÉT sted
# ═══════════════════════════════════════════════════════════════════════════════
# ⚠ TO FALDGRUBER DER BEGGE ER RAMT I PRAKSIS, OG SOM BEGGE HOERER TIL HER FREM
# FOR I HVER BEREGNING DER TILFAELDIGVIS HUSKER DEM.
#
# 1) TIDSZONEN. vol_cache gemmer UTC (SPY's foerste bar staar 13:30 om sommeren
#    og 14:30 om vinteren, begge 09:30 ET), mens data_harvest gemmer ET. Laeses
#    vol_cache som ET, forskydes hele dagen — det fik sessions_revision til at
#    melde falsk strukturbrud paa baade SPY og IWM.
#
# 2) SESSIONEN. useRTH=True betyder ikke det samme for et CBOE-indeks som for en
#    ETF. VIX har 765 barer om dagen mod SPY's 390, fordi indekset beregnes fra
#    03:15 ET. Stilles serierne op ved siden af hinanden uden skaering, daekker
#    "en dag" for VIX naesten dobbelt saa meget kalendertid som for SPY, og
#    enhver sammenligning er skaev uden at noget fejler.
#
# Skaeringen er derfor ubetinget: ALLE instrumenter skaeres til aktiesessionen.
# Det er en no-op for SPY og IWM (de ER 09:30-16:00) og det rigtige for VIX.

RTH_START = (9, 30)
RTH_SLUT = (16, 0)


@dataclass(frozen=True)
class RTHDag:
    """Én handelsdags RTH-OHLC, udledt af 1-min-barer."""
    dag: date
    aabning: float          # foerste bar i vinduet
    hoej: float
    lav: float
    luk: float              # sidste bar i vinduet

    @property
    def range_(self) -> float:
        return self.hoej - self.lav


def laes_1min_rth(instrument: str, mappe: Path | None = None) -> dict[date, RTHDag]:
    """RTH-OHLC pr. dag for ét instrument, udledt af dets 1-min-serie.

    Tidsstempler laeses som UTC og konverteres til ET; kun barer i
    [09:30, 16:00) taeller med. Halvaabent interval: 16:00-baren hoerer til
    naeste vindue, ikke dette.
    """
    import pytz
    ET = pytz.timezone("America/New_York")
    mappe = mappe or CACHE
    p = mappe / f"{instrument}_1min.csv"
    if not p.exists():
        raise Sammenstillingsfejl(f"serien findes ikke: {p}")

    t0 = RTH_START[0] * 60 + RTH_START[1]
    t1 = RTH_SLUT[0] * 60 + RTH_SLUT[1]

    ud: dict[date, list] = {}
    with p.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                u = datetime.fromisoformat(r["timestamp"])
            except ValueError:
                continue
            if u.tzinfo is None:
                u = u.replace(tzinfo=timezone.utc)
            e = u.astimezone(ET)
            m = e.hour * 60 + e.minute
            if not (t0 <= m < t1):
                continue
            try:
                o, h, l, c = (float(r["open"]), float(r["high"]),
                              float(r["low"]), float(r["close"]))
            except (ValueError, KeyError):
                continue
            d = e.date()
            v = ud.get(d)
            if v is None:
                ud[d] = [e, o, h, l, e, c]     # [tidligst, open, hoej, lav, senest, close]
            else:
                if h > v[2]:
                    v[2] = h
                if l < v[3]:
                    v[3] = l
                if e < v[0]:
                    v[0], v[1] = e, o
                if e > v[4]:
                    v[4], v[5] = e, c
    if not ud:
        raise Sammenstillingsfejl(
            f"{p.name}: ingen barer i {RTH_START[0]:02d}:{RTH_START[1]:02d}-"
            f"{RTH_SLUT[0]:02d}:{RTH_SLUT[1]:02d} ET — forkert vindue eller tidszone?")
    return {d: RTHDag(d, v[1], v[2], v[3], v[5]) for d, v in ud.items()}
