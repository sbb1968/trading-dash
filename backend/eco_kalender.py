#!/usr/bin/env python3
r"""
eco_kalender.py — planlagte oekonomiske events der kan flytte MES
════════════════════════════════════════════════════════════════════════════════
Implementerer `spec_eco_calendar_studio.md` (18-08-2026) med rev. A og B.

    python eco_kalender.py --hoest            # dagens uge -> databasen + eksport
    python eco_kalender.py --uge aug9.2026    # historik fra uge-HTML
    python eco_kalender.py --importer-cache   # engangs: kalender_cache/events.json
    python eco_kalender.py --vis 2026-08-14
    python eco_kalender.py --status

⚠ PROTOTYPEN FANDTES IKKE. Specens §1 beder om at genbruge Coworks
`kalender_konfig.py`, `klassificer()`, `kontroller_feed()`, `test_kalender.py` og
fiksturen. Ingen af dem ligger i repoet — kun i en zip jeg ikke har faaet. Det
her er derfor bygget paa vores EGNE maalte aekvivalenter (`oekonomisk_kalender.py`
og `kalender_tier.py`), som daekker samme grund og hvis maalinger staar nedenfor.
Er der noget i prototypen der ikke er genskabt her, er det fordi jeg ikke har
kunnet se den — ikke fordi den er fravalgt.

────────────────────────────────────────────────────────────────────────────────
DE BINDENDE MAALINGER (specens §1.1, uafhaengigt maalt her 18-08)
────────────────────────────────────────────────────────────────────────────────
A. `impact` DUER IKKE SOM FILTER. Maalt paa ugen 9.-15. august: 56 af 76 events
   er "Low" hos ForexFactory — herunder 10-y Bond Auction og samtlige Fed-talere.
   Vores tier-tabel er uenig med FF om 12 af 29 USD-events. KLASSIFIKATION SKER
   PAA TITEL. En kildes vigtighedsmarkering maa aldrig smide noget ud.

B. Feedet kan kun indevaerende uge (`nextweek`/`lastweek`/`thismonth` -> 404).
   Men HTML-siden `calendar?week=aug9.2026` kan VILKAARLIGE uger, ogsaa bagud,
   OG den har en Faktisk-kolonne feedet ikke har. Rev. A1 har ret.

────────────────────────────────────────────────────────────────────────────────
⚠ TIDEN LAESES AF `dateline` (UNIX-sekunder), ALDRIG af de viste klokkeslaet.
Siden gengiver tider i BROWSERENS zone. En aflaesning af det viste ville arve en
forskydning man ikke kan se, og den ville flytte sig to gange om aaret fordi EU
og USA skifter sommertid paa forskellige datoer.

⚠ TO SIKKERHEDSKRAV DER IKKE ER PYNT
  · En FEJLET hoest og en STILLE DAG maa aldrig se ens ud. `status()` svarer
    `stale: True` med tidsstempel frem for en tom liste med 200 OK.
  · Én ubrugelig raekke maa ikke tage hele svaret med sig. Se `_til_udv()`.
Begge er projektets tilbagevendende fejlklasse — en kontrol hvis fejl behandles
som en bestaaelse.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
import sqlite3
import sys
import urllib.request
from pathlib import Path
from typing import Protocol

import pytz

import nyse_kalender
from kalender_tier import (TIER1_TITLER, TIER2_TITLER, TIER2_PRAEFIKS,
                           TITEL_SYNONYMER)

HER = Path(__file__).resolve().parent
DB = HER / "trading_dash.db"
EKSPORT_DIR = HER / "eco_export"
EKSPORT_FIL = EKSPORT_DIR / "eco_events.csv"
DROPLOG = EKSPORT_DIR / "droppede_titler.csv"

ET = pytz.timezone("America/New_York")
DK = pytz.timezone("Europe/Copenhagen")

# ── Konstantblok ────────────────────────────────────────────────────────────
# Day trading. En kalender der raekker et aar frem, bliver en planlaegningsflade
# ingen laeser. Basen beskaeres ikke — kun visning og default-svar.
VINDUE_BAGUD_DAGE = 45
VINDUE_FREM_DAGE = 45

# Jobbet koerer dagligt. 30 timer giver ét overspringet doegn plus margin foer
# siden raaber op; to overspringede doegn er en fejl der SKAL ses.
MAX_HOEST_ALDER_TIMER = 30

# Oevevinduet i Trading Practice, dansk vaegur.
OEVEVINDUE_START = dt.time(8, 0)
OEVEVINDUE_SLUT = dt.time(15, 0)

# MES er et S&P-produkt. Ikke-USD hoestes med (rev. B1: hoest bredt) men faar
# tier 0 og naar derfor aldrig visningen.
VIGTIG_VALUTA = "USD"

# Sundhedsgraenser. ⚠ GULVE, IKKE KALIBREREDE VAERDIER. Den ene uge vi har maalt
# havde 76 events; 20 ligger langt under enhver rigtig uge, ogsaa en helligdagsuge.
# Saettes de for taet paa det normale, raaber kontrollen op paa en stille uge.
MIN_EVENTS_PR_UGE = 20
MAX_FEED_ALDER_DAGE = 2

FEED = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
UGE_HTML = "https://www.forexfactory.com/calendar?week={uge}"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"


class KildeFejl(RuntimeError):
    """En kilde der ikke kan levere. ⚠ Rejses — returneres ALDRIG som tom liste."""


# ── Kilde-kontrakten ────────────────────────────────────────────────────────
class EcoKilde(Protocol):
    navn: str
    daekker: str                    # "naer" | "fremad" | "historik"

    def hent(self, fra: dt.date, til: dt.date) -> list[dict]:
        """Rejser ved fejl. Returnerer ALDRIG tom liste som 'ingen events'."""

    def sundhedstjek(self, raa: list[dict]) -> None:
        """Rejser hvis svaret er tomt, afkortet eller foraeldet."""


# ── Tid ─────────────────────────────────────────────────────────────────────
def _et(unix: int) -> dt.datetime:
    return dt.datetime.fromtimestamp(int(unix), tz=dt.timezone.utc).astimezone(ET)


def _dk(unix: int) -> dt.datetime:
    return dt.datetime.fromtimestamp(int(unix), tz=dt.timezone.utc).astimezone(DK)


# ── Titel-normalisering ─────────────────────────────────────────────────────
_MAANED_PAREN = re.compile(r"\((jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\)")


def normaliser_titel(t: str) -> str:
    """Noegle til fletning. Fjerner FORMAT-variation, ikke BETYDNING.

    ⚠ SPECENS §3.3 SIGER "fjern m/m, q/q, y/y". DET MAA IKKE GOERES.
    "CPI m/m" og "CPI y/y" udgives samme dag paa samme klokkeslaet — maalt i
    cachen 12-08 ligger begge 08:30 ET. Fjernes perioden, faar de identisk
    noegle, og fletningen ville smide den ene vaek. Lydloest, og netop paa den
    vigtigste dag i maaneden.

    Perioden KOLLAPSES derfor i stedet: "(MoM)" og "MoM" bliver til "m/m", saa
    "CPI (MoM)" flettes med "CPI m/m" uden at "CPI y/y" rives med.
    """
    s = t.lower().strip()
    s = _MAANED_PAREN.sub(" ", s)
    s = s.replace("(", " ").replace(")", " ")
    s = re.sub(r"\b(mom|m/m)\b", "m/m", s)
    s = re.sub(r"\b(yoy|y/y)\b", "y/y", s)
    s = re.sub(r"\b(qoq|q/q)\b", "q/q", s)
    s = re.sub(r"[^a-z0-9/]+", " ", s)
    return " ".join(s.split())


def kanonisk_titel(raa: str) -> str:
    """Anden kildes navn -> vores kanoniske (ForexFactorys).

    ⚠ EKSAKT OPSLAG, INGEN FUZZY MATCHING. Enhver tolerant strengsammenligning
    slaar "Core CPI" sammen med "CPI" foer eller siden, og den fejl er usynlig
    indtil den koster noget. En ukendt titel beholder sit eget navn og bliver
    tier 0 frem for at blive gaettet ind i tier 1.
    """
    return TITEL_SYNONYMER.get((raa or "").strip(), (raa or "").strip())


# ── Klassifikation ──────────────────────────────────────────────────────────
def klassificer(navn: str, land: str = VIGTIG_VALUTA) -> tuple[int, str]:
    """(tier, begrundelse). Tier 0 = hoestes og gemmes, men vises ikke.

    ⚠ LANDET AFGOER FOERST. Tier-tabellerne er skrevet for USD, men titlerne
    er ikke enestaaende: Storbritannien har ogsaa "Unemployment Rate", Canada har
    "Housing Starts", euroomraadet har "Trade Balance". Uden landefiltret fik
    britisk arbejdsloeshed tier 1 og stod oeverst paa en side der handler om MES.
    Maalt paa den foerste hoest 18-08: seks ikke-USD-events sneg sig ind i
    visningen, heraf ét som tier 1.

    Det er samme fejlklasse som resten — en kontrol der ligner en beslutning,
    men som aldrig blev spurgt om det den skulle. Tabellen svarer paa "flytter
    den her titel MES?", og det spoergsmaal giver kun mening for USD.

    ⚠ De hoestes stadig og gemmes stadig (rev. B1). De faar bare tier 0 og
    havner i droploggen, saa en fremtidig maaling kan forfremme dem.
    """
    n = (navn or "").strip()
    if (land or "").strip().upper() != VIGTIG_VALUTA:
        return 0, f"tier 0: ikke {VIGTIG_VALUTA} ({land})"
    if n in TIER1_TITLER:
        return 1, "tier 1: staar i TIER1_TITLER"
    if n in TIER2_TITLER:
        return 2, "tier 2: staar i TIER2_TITLER"
    for p in TIER2_PRAEFIKS:
        if n.startswith(p):
            return 2, f"tier 2: praefiks {p!r}"
    return 0, "tier 0: ikke paa tier-listerne"


def i_oevevindue(unix: int, har_klokkeslet: bool) -> bool:
    """08:00-15:00 dansk paa en NYSE-handelsdag.

    ⚠ `nyse_kalender.er_handelsdag`, ikke `weekday() < 5`. Modulet findes
    allerede og kender helligdage; et event paa en NYSE-helligdag er ikke et
    oevevindue-event, og reglen skal staa ét sted i kodebasen.

    ⚠ Et event UDEN klokkeslaet kan pr. definition ikke ligge i vinduet. Et
    gaettet klokkeslaet paa CPI er vaerre end intet.
    """
    if not har_klokkeslet:
        return False
    t = _dk(unix)
    if not nyse_kalender.er_handelsdag(t.date()):
        return False
    return OEVEVINDUE_START <= t.time() < OEVEVINDUE_SLUT


def _raekke(navn, valuta, unix, kilde, kilde_vigtighed=None, forecast=None,
            previous=None, actual=None, har_klokkeslet=True) -> dict:
    titel = kanonisk_titel(navn)
    t_et, t_dk = _et(unix), _dk(unix)
    tier, begrund = klassificer(titel, valuta)
    return {
        "ts_utc": dt.datetime.fromtimestamp(int(unix), tz=dt.timezone.utc)
                    .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "unix": int(unix),
        "titel": titel,
        "raa_titel": (navn or "").strip(),
        "land": valuta,
        "ts_dk": t_dk.strftime("%Y-%m-%dT%H:%M:%S"),
        "dato_dk": t_dk.strftime("%Y-%m-%d"),
        "klokke_dk": t_dk.strftime("%H:%M") if har_klokkeslet else None,
        "tid_et": t_et.strftime("%H:%M") if har_klokkeslet else None,
        "tier": tier,
        "begrundelse": begrund,
        "kilde": kilde,
        "kilde_vigtighed": kilde_vigtighed,
        "forecast": forecast or None,
        "previous": previous or None,
        "actual": actual or None,
        "har_klokkeslet": 1 if har_klokkeslet else 0,
        "i_oevevindue": 1 if i_oevevindue(unix, har_klokkeslet) else 0,
    }


# ── Kilde 1: ForexFactory JSON-feed (dagligt) ───────────────────────────────
class ForexFactoryFeed:
    """Indevaerende uge. Let og ren, men uden Faktisk-kolonne.

    ⚠ RATE-LIMITER. Fire kald paa et par minutter gav HTTP 429 (maalt 18-08).
    Derfor ét kald om dagen; ellers er kalenderen nede praecis den morgen den
    betyder noget.
    """
    navn = "forexfactory_feed"
    daekker = "naer"

    def hent(self, fra: dt.date, til: dt.date) -> list[dict]:
        try:
            r = urllib.request.Request(FEED, headers={"User-Agent": UA})
            raa = json.load(urllib.request.urlopen(r, timeout=30))
        except Exception as e:
            raise KildeFejl(f"{self.navn}: kunne ikke hente ({e})") from e
        if not isinstance(raa, list):
            raise KildeFejl(f"{self.navn}: svaret er ikke en liste ({type(raa).__name__})")
        self.sundhedstjek(raa)
        return [x for x in self.omform(raa)
                if fra <= dt.date.fromisoformat(x["dato_dk"]) <= til]

    def omform(self, raa: list[dict]) -> list[dict]:
        ud = []
        for x in raa:
            t = dt.datetime.fromisoformat(x["date"])
            # FF laegger heldags-events paa 12:00am ET. Et USD-nyhedstal falder
            # aldrig midnat, saa 00:00 laeses som "ingen tid" frem for at blive
            # vist som et praecist klokkeslaet der ikke findes.
            har_tid = t.astimezone(ET).strftime("%H:%M") != "00:00"
            ud.append(_raekke(x.get("title"), x.get("country"), t.timestamp(),
                              self.navn, x.get("impact"), x.get("forecast"),
                              x.get("previous"), None, har_tid))
        return ud

    def sundhedstjek(self, raa: list[dict]) -> None:
        if not raa:
            raise KildeFejl(f"{self.navn}: TOMT svar — ikke det samme som 'ingen events'")
        mangler = [k for k in ("title", "country", "date") if k not in raa[0]]
        if mangler:
            raise KildeFejl(f"{self.navn}: felter mangler i svaret: {mangler}")
        if len(raa) < MIN_EVENTS_PR_UGE:
            raise KildeFejl(f"{self.navn}: AFKORTET — {len(raa)} events paa en uge "
                            f"(gulv {MIN_EVENTS_PR_UGE})")
        try:
            nyest = max(dt.datetime.fromisoformat(x["date"]) for x in raa)
        except Exception as e:
            raise KildeFejl(f"{self.navn}: kunne ikke laese datoer ({e})") from e
        alder = (dt.datetime.now(dt.timezone.utc) - nyest).days
        if alder > MAX_FEED_ALDER_DAGE:
            raise KildeFejl(f"{self.navn}: FORAELDET — nyeste event er {alder} dage gammelt")


# ── Kilde 2: ForexFactory uge-HTML (historik + Faktisk) ─────────────────────
class ForexFactoryUge:
    """Vilkaarlige uger, ogsaa bagud, MED Faktisk-kolonne.

    ⚠ HTML-SKRABNING KNAEKKER STILLE, hvor et feed knaekker hoejlydt (rev. A1).
    Skifter et klassenavn, returnerer en naiv parser nul raekker — og en tom uge
    ligner en rolig uge. Derfor rejser `sundhedstjek` OGSAA hvis strukturen ikke
    findes, ikke kun hvis den er tom.
    """
    navn = "forexfactory_uge"
    daekker = "historik"

    def __init__(self, uge: str | None = None, html: str | None = None):
        self.uge = uge
        self._html = html

    def hent(self, fra: dt.date, til: dt.date) -> list[dict]:
        html = self._html
        if html is None:
            try:
                r = urllib.request.Request(UGE_HTML.format(uge=self.uge),
                                           headers={"User-Agent": UA})
                html = urllib.request.urlopen(r, timeout=45).read().decode("utf-8", "replace")
            except Exception as e:
                raise KildeFejl(f"{self.navn}: kunne ikke hente {self.uge} ({e})") from e
        dage = self._skaer_ud(html)
        raa = [e for d in dage for e in d.get("events", [])]
        self.sundhedstjek(raa)
        return [x for x in self.omform(raa)
                if fra <= dt.date.fromisoformat(x["dato_dk"]) <= til]

    @staticmethod
    def _skaer_ud(html: str) -> list[dict]:
        """⚠ KLAMMEMATCHNING, IKKE REGEX. Det omgivende er et JS-objekt med
        unoterede noegler; et regex-'fix' af dem braekker strenge der selv
        indeholder ':' — og der er mange (URL'er, klokkeslaet). `days`-arrayet
        ER derimod gyldig JSON, saa kun det skaeres ud."""
        try:
            i = html.index("calendarComponentStates[1]")
            i = html.index("days:", i)
            j = html.index("[", i)
        except ValueError as e:
            raise KildeFejl("forexfactory_uge: STRUKTUREN FINDES IKKE i siden — "
                            "layoutet er sandsynligvis aendret. (En tom uge og en "
                            "aendret side maa ikke ligne hinanden.)") from e
        dyb, k, inde, esc = 0, j, False, False
        while k < len(html):
            c = html[k]
            if inde:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    inde = False
            else:
                if c == '"':
                    inde = True
                elif c == "[":
                    dyb += 1
                elif c == "]":
                    dyb -= 1
                    if dyb == 0:
                        break
            k += 1
        try:
            return json.loads(html[j:k + 1])
        except Exception as e:
            raise KildeFejl(f"forexfactory_uge: days-arrayet kunne ikke laeses ({e})") from e

    def omform(self, raa: list[dict]) -> list[dict]:
        ud = []
        for e in raa:
            etiket = (e.get("timeLabel") or "").strip().lower()
            har_tid = etiket not in ("all day", "tentative", "")
            if not e.get("dateline"):
                continue
            ud.append(_raekke(e.get("name"), e.get("currency"), e.get("dateline"),
                              self.navn,
                              e.get("impactTitle") or e.get("impactClass"),
                              e.get("forecast"), e.get("previous"), e.get("actual"),
                              har_tid))
        return ud

    def sundhedstjek(self, raa: list[dict]) -> None:
        if not raa:
            raise KildeFejl(f"{self.navn}: TOMT svar — ikke det samme som 'ingen events'")
        # ⚠ Kolonnekontrollen er det der skiller en LAYOUTAENDRING fra en stille
        # uge. Uden den er en parser der pludselig kun finder tomme objekter
        # umulig at skelne fra en uge uden nyheder.
        for kol in ("name", "currency", "dateline"):
            if not any(x.get(kol) for x in raa):
                raise KildeFejl(f"{self.navn}: kolonnen {kol!r} findes ikke i nogen "
                                f"raekke — layoutet er aendret")
        if len(raa) < MIN_EVENTS_PR_UGE:
            raise KildeFejl(f"{self.navn}: AFKORTET — {len(raa)} events paa en uge "
                            f"(gulv {MIN_EVENTS_PR_UGE})")


# Daglig drift bruger KUN feedet (rate-limiteren). Uge-HTML er en eksplicit
# kommando, ikke et job — den henter 391 KB pr. kald.
KILDER: list[EcoKilde] = [ForexFactoryFeed()]


# ── Skema ───────────────────────────────────────────────────────────────────
# ⚠ IDENTISK MED BLOKKEN I db_schema.sql mellem markoererne ECO_SKEMA.
# `test_eco_kalender.py` sammenligner de to, saa de ikke kan drive fra hinanden
# i stilhed. Aendres den ene, skal den anden med.
SKEMA = """
CREATE TABLE IF NOT EXISTS eco_events (
    ts_utc                TEXT NOT NULL,
    titel                 TEXT NOT NULL,
    land                  TEXT NOT NULL,
    ts_dk                 TEXT NOT NULL,
    dato_dk               TEXT NOT NULL,
    klokke_dk             TEXT,
    tier                  INTEGER NOT NULL,
    begrundelse           TEXT NOT NULL,
    kilde                 TEXT NOT NULL,
    kilde_vigtighed       TEXT,
    forecast              TEXT,
    previous              TEXT,
    actual                TEXT,
    forecast_ved_release  TEXT,
    har_klokkeslet        INTEGER NOT NULL,
    i_oevevindue          INTEGER NOT NULL,
    foerst_set            TEXT NOT NULL,
    sidst_bekraeftet      TEXT NOT NULL,
    PRIMARY KEY (ts_utc, titel, land)
);
CREATE INDEX IF NOT EXISTS idx_eco_dato ON eco_events(dato_dk);
CREATE INDEX IF NOT EXISTS idx_eco_tier ON eco_events(tier);

CREATE TABLE IF NOT EXISTS eco_hoest (
    ts_utc   TEXT    NOT NULL,
    kilde    TEXT    NOT NULL,
    ok       INTEGER NOT NULL,
    antal    INTEGER NOT NULL,
    besked   TEXT
);
CREATE INDEX IF NOT EXISTS idx_eco_hoest_kilde ON eco_hoest(kilde, ts_utc);
"""


def forbind(db: Path | str | None = None) -> sqlite3.Connection:
    con = sqlite3.connect(str(db or DB))
    con.row_factory = sqlite3.Row
    con.executescript(SKEMA)
    con.commit()
    return con


def forbind_laes(db: Path | str | None = None) -> sqlite3.Connection:
    """Skrivebeskyttet forbindelse til laeseruter.

    ⚠ IKKE `forbind()`. Den koerer executescript(SKEMA) ved hvert kald, og et
    HTTP-endpoint skal ikke tage en skrivelaas paa handelsdatabasen for at svare
    paa "hvad sker der i dag". Tabellerne oprettes af journal.init() ved opstart
    (db_schema.sql) og af hoest-jobbet — ikke af en laesning.
    """
    con = sqlite3.connect(f"file:{db or DB}?mode=ro", uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    return con


def _nu() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Sammenfletning ──────────────────────────────────────────────────────────
def flet(raekker: list[dict]) -> tuple[list[dict], dict]:
    """Fold raekker fra flere kilder sammen. (flettede, rapport).

    Noeglen er (dato_dk, normaliseret kanonisk titel, land) — specens §3.3.
    Rapporten er rev. B4's to tal.

    ⚠ ALDRIG STILTIENDE OVERSKRIVNING. Uenighed om klokkeslaet LOGGES; to kilder
    der er uenige om hvornaar CPI lander, er selv et signal om fejlkonfiguration.
    """
    ud: dict[tuple, dict] = {}
    uenige_tid, dubletter = [], 0
    for r in raekker:
        n = (r["dato_dk"], normaliser_titel(r["titel"]), r["land"])
        if n not in ud:
            ud[n] = dict(r)
            continue
        dubletter += 1
        haves = ud[n]
        if haves["ts_utc"] != r["ts_utc"]:
            uenige_tid.append({
                "dato": r["dato_dk"], "titel": r["titel"],
                "kilder": sorted({haves["kilde"], r["kilde"]}),
                "tider": sorted({haves["ts_utc"], r["ts_utc"]}),
            })
            # Finest oploesning vinder: den med klokkeslaet slaar den uden.
            if r["har_klokkeslet"] and not haves["har_klokkeslet"]:
                haves.update({k: r[k] for k in ("ts_utc", "ts_dk", "klokke_dk",
                                                "tid_et", "har_klokkeslet",
                                                "i_oevevindue")})
        for felt in ("forecast", "previous", "actual", "kilde_vigtighed"):
            if haves.get(felt) is None and r.get(felt) is not None:
                haves[felt] = r[felt]
        if r["kilde"] not in haves["kilde"].split("+"):
            haves["kilde"] = "+".join(sorted(set(haves["kilde"].split("+")) | {r["kilde"]}))
    return list(ud.values()), {"dubletter_flettet": dubletter,
                               "klokkeslaets_uenigheder": uenige_tid}


# ── Skrivning ───────────────────────────────────────────────────────────────
def gem(con: sqlite3.Connection, raekker: list[dict]) -> tuple[int, int]:
    """(nye, opdaterede). Idempotent: to koersler giver identisk base.

    ⚠ HENTETID SKRIVES KUN I foerst_set/sidst_bekraeftet, aldrig i et datafelt.
    Ellers ville to koersler af den samme uge give forskellige raekker, og
    "ingen aendring" kunne ikke skelnes fra "alt aendret".

    ⚠ forecast_ved_release FRYSES ved foerste actual og overskrives ALDRIG.
    Forecast revideres helt frem til udgivelsen; en historik man ikke gemte, kan
    ikke rekonstrueres bagefter (rev. A3).
    """
    nu, ny, opd = _nu(), 0, 0
    for r in raekker:
        n = (r["ts_utc"], r["titel"], r["land"])
        haves = con.execute(
            "SELECT * FROM eco_events WHERE ts_utc=? AND titel=? AND land=?", n).fetchone()
        if haves is None:
            fvr = r["forecast"] if r.get("actual") else None
            con.execute(
                "INSERT INTO eco_events (ts_utc,titel,land,ts_dk,dato_dk,klokke_dk,tier,"
                "begrundelse,kilde,kilde_vigtighed,forecast,previous,actual,"
                "forecast_ved_release,har_klokkeslet,i_oevevindue,foerst_set,sidst_bekraeftet)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (r["ts_utc"], r["titel"], r["land"], r["ts_dk"], r["dato_dk"],
                 r["klokke_dk"], r["tier"], r["begrundelse"], r["kilde"],
                 r["kilde_vigtighed"], r["forecast"], r["previous"], r["actual"],
                 fvr, r["har_klokkeslet"], r["i_oevevindue"], nu, nu))
            ny += 1
            continue
        # Kun ikke-tomme vaerdier opdaterer. `actual` kommer FOERST efter
        # offentliggoerelsen, saa en opdatering af en kendt raekke er normal.
        fvr = haves["forecast_ved_release"]
        if fvr is None and r.get("actual") and haves["forecast"]:
            fvr = haves["forecast"]      # fryses her, i det oejeblik der kom et tal
        aendret = False
        felter = {}
        for k in ("klokke_dk", "kilde_vigtighed", "forecast", "previous", "actual",
                  "tier", "begrundelse"):
            v = r.get(k)
            if v is not None and haves[k] != v:
                felter[k] = v
                aendret = True
        if fvr != haves["forecast_ved_release"]:
            felter["forecast_ved_release"] = fvr
            aendret = True
        felter["sidst_bekraeftet"] = nu
        con.execute(f"UPDATE eco_events SET {','.join(k + '=?' for k in felter)} "
                    "WHERE ts_utc=? AND titel=? AND land=?",
                    (*felter.values(), *n))
        opd += 1 if aendret else 0
    con.commit()
    return ny, opd


def log_droppede(raekker: list[dict]) -> int:
    """Tier 0 -> droppede_titler.csv, med kilde.

    ⚠ DET ER EN MENU, IKKE ET REVISIONSSPOR (rev. B2). Opdager vi om tre
    maaneder at noget flytter MES, slaar vi op her, flytter titlen op i
    kalender_tier.py — og har data BAGUD med det samme, fordi den blev hoestet
    hele tiden. Hoestede vi smalt, skulle vi vente 45 dage paa at vinduet fyldte
    sig.
    """
    nul = [r for r in raekker if r["tier"] == 0]
    if not nul:
        return 0
    EKSPORT_DIR.mkdir(parents=True, exist_ok=True)
    ny = not DROPLOG.exists()
    with DROPLOG.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if ny:
            w.writerow(["set", "dato_dk", "klokke_dk", "land", "titel", "raa_titel",
                        "kilde", "kilde_vigtighed"])
        for r in nul:
            w.writerow([_nu(), r["dato_dk"], r["klokke_dk"] or "", r["land"],
                        r["titel"], r.get("raa_titel", r["titel"]), r["kilde"],
                        r["kilde_vigtighed"] or ""])
    return len(nul)


def droplog_optaelling() -> list[tuple[str, str, int]]:
    """(titel, land, antal), hyppigste foerst. Uden opslag er loggen et baand
    ingen kan laese (rev. B2)."""
    if not DROPLOG.exists():
        return []
    tael: dict[tuple[str, str], int] = {}
    with DROPLOG.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            k = (r["titel"], r["land"])
            tael[k] = tael.get(k, 0) + 1
    return sorted(((t, l, n) for (t, l), n in tael.items()), key=lambda x: -x[2])


def eksporter(con: sqlite3.Connection) -> Path:
    """Fladfil til Trading Practice.

    ⚠ FLADFIL, IKKE HTTP — og det foelger af `trading_practice/SPEC.md` §1, ikke
    af smag. §1 skiller de to systemer ad med spraengradius som foerste
    begrundelse: Trading Dash sender rigtige ordrer, og et nedbrud kl. 15:19
    betyder at strategierne ikke starter kl. 15:20. Kaldte oevebanen
    `http://algoserver/eco/naeste` under en drill, ville en fejl i Trading Dash
    kunne vaelte oeveredskabet. §1.2 tillader praecis én kobling: laes en fil
    skrivebeskyttet, én gang, ved labelling. Det her er den samme figur.
    """
    EKSPORT_DIR.mkdir(parents=True, exist_ok=True)
    r = con.execute("SELECT dato_dk, klokke_dk, titel, tier, har_klokkeslet "
                    "FROM eco_events WHERE tier > 0 ORDER BY ts_utc").fetchall()
    with EKSPORT_FIL.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["dato_dk", "klokke_dk", "titel", "tier", "har_klokkeslet"])
        for x in r:
            w.writerow([x["dato_dk"], x["klokke_dk"] or "", x["titel"], x["tier"],
                        x["har_klokkeslet"]])
    return EKSPORT_FIL


# ── Hoest ───────────────────────────────────────────────────────────────────
def hoest(kilder: list[EcoKilde] | None = None, db: Path | str | None = None,
          fra: dt.date | None = None, til: dt.date | None = None) -> dict:
    """Hent fra alle kilder, flet, gem, eksportér. Rapport som dict.

    ⚠ EN FEJLET KILDE STOPPER IKKE DE OEVRIGE, men den skrives til eco_hoest med
    ok=0. Tavshed maa ikke ligne succes.
    """
    i_dag = dt.date.today()
    fra = fra or i_dag - dt.timedelta(days=VINDUE_BAGUD_DAGE)
    til = til or i_dag + dt.timedelta(days=VINDUE_FREM_DAGE)
    kilder = kilder if kilder is not None else KILDER
    con = forbind(db)
    alle, fejl = [], []
    for k in kilder:
        try:
            r = k.hent(fra, til)
            con.execute("INSERT INTO eco_hoest (ts_utc,kilde,ok,antal,besked) VALUES (?,?,?,?,?)",
                        (_nu(), k.navn, 1, len(r), None))
            alle += r
        except Exception as e:
            con.execute("INSERT INTO eco_hoest (ts_utc,kilde,ok,antal,besked) VALUES (?,?,?,?,?)",
                        (_nu(), k.navn, 0, 0, str(e)[:500]))
            fejl.append({"kilde": k.navn, "fejl": str(e)})
    con.commit()
    flettede, rapport = flet(alle)
    ny, opd = gem(con, flettede)
    droppet = log_droppede(flettede)
    sti = eksporter(con)
    con.close()
    return {"hentet": len(alle), "efter_fletning": len(flettede),
            "nye": ny, "opdaterede": opd, "tier0_logget": droppet,
            "eksport": str(sti), "fejlede_kilder": fejl, **rapport}


# ── Forespoergsler ──────────────────────────────────────────────────────────
def _minutter_til(ts_utc: str) -> int:
    t = dt.datetime.strptime(ts_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    return round((t - dt.datetime.now(dt.timezone.utc)).total_seconds() / 60)


def _til_udv(r: sqlite3.Row) -> dict:
    """Én raekke til API-form.

    ⚠ SPRAENGRADIUS (journal-500-lektien). Ét ubrugeligt event maa ikke tage
    hele vinduet med sig. Kan raekken ikke laeses, erstattes den af en MAERKET
    pladsholder — og resten leveres. En pladsholder man kan se, er bedre end et
    500-svar hvor alle events forsvinder paa grund af ét.
    """
    try:
        return {
            "ts_utc": r["ts_utc"], "titel": r["titel"], "land": r["land"],
            "dato_dk": r["dato_dk"],
            "klokke_dk": r["klokke_dk"] if r["har_klokkeslet"] else None,
            "tier": int(r["tier"]), "begrundelse": r["begrundelse"],
            "kilde": r["kilde"], "kilde_vigtighed": r["kilde_vigtighed"],
            "forecast": r["forecast"], "previous": r["previous"],
            "actual": r["actual"],
            "har_klokkeslet": bool(r["har_klokkeslet"]),
            "i_oevevindue": bool(r["i_oevevindue"]),
            "minutter_til": _minutter_til(r["ts_utc"]),
        }
    except Exception as e:
        return {"pladsholder": True, "fejl": f"{type(e).__name__}: {e}",
                "titel": "(ubrugelig raekke)", "tier": 0, "land": "?",
                "ts_utc": None, "dato_dk": None, "klokke_dk": None,
                "forecast": None, "previous": None, "actual": None,
                "minutter_til": None,
                "har_klokkeslet": False, "i_oevevindue": False}


def hent_events(con, fra: str | None = None, til: str | None = None,
                max_tier: int = 2) -> list[dict]:
    i_dag = dt.date.today()
    fra = fra or (i_dag - dt.timedelta(days=VINDUE_BAGUD_DAGE)).isoformat()
    til = til or (i_dag + dt.timedelta(days=VINDUE_FREM_DAGE)).isoformat()
    r = con.execute(
        "SELECT * FROM eco_events WHERE dato_dk BETWEEN ? AND ? "
        "AND tier > 0 AND tier <= ? ORDER BY ts_utc, titel", (fra, til, max_tier)).fetchall()
    return [_til_udv(x) for x in r]


def hent_dag(con, dato: str) -> list[dict]:
    r = con.execute("SELECT * FROM eco_events WHERE dato_dk=? AND tier > 0 "
                    "ORDER BY har_klokkeslet DESC, ts_utc, titel", (dato,)).fetchall()
    return [_til_udv(x) for x in r]


def naeste(con, max_tier: int = 1) -> dict | None:
    nu = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    r = con.execute("SELECT * FROM eco_events WHERE ts_utc > ? AND tier > 0 AND tier <= ? "
                    "AND har_klokkeslet = 1 ORDER BY ts_utc LIMIT 1", (nu, max_tier)).fetchone()
    return _til_udv(r) if r else None


def status(con) -> dict:
    """⚠ `stale` ER SVARETS VIGTIGSTE FELT. Er hoesten ikke lykkedes inden for
    MAX_HOEST_ALDER_TIMER, skal siden SIGE det — ikke vise en rolig dag."""
    kendte = {x.navn for x in KILDER} | {
        r["kilde"] for r in con.execute("SELECT DISTINCT kilde FROM eco_hoest")}
    kilder, nyeste = [], None
    for k in kendte:
        ok = con.execute("SELECT ts_utc, antal FROM eco_hoest WHERE kilde=? AND ok=1 "
                         "ORDER BY ts_utc DESC LIMIT 1", (k,)).fetchone()
        sen = con.execute("SELECT ts_utc, ok, besked FROM eco_hoest WHERE kilde=? "
                          "ORDER BY ts_utc DESC LIMIT 1", (k,)).fetchone()
        kilder.append({
            "kilde": k,
            "sidste_ok": ok["ts_utc"] if ok else None,
            "sidste_antal": ok["antal"] if ok else 0,
            "seneste_forsoeg": sen["ts_utc"] if sen else None,
            "seneste_ok": bool(sen["ok"]) if sen else None,
            "seneste_besked": sen["besked"] if sen else None,
        })
        if ok and (nyeste is None or ok["ts_utc"] > nyeste):
            nyeste = ok["ts_utc"]
    alder = None
    if nyeste:
        t = dt.datetime.strptime(nyeste, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
        alder = (dt.datetime.now(dt.timezone.utc) - t).total_seconds() / 3600
    i_dag = dt.date.today().isoformat()
    return {
        "stale": alder is None or alder > MAX_HOEST_ALDER_TIMER,
        "sidste_hoest": nyeste,
        "alder_timer": round(alder, 1) if alder is not None else None,
        "max_alder_timer": MAX_HOEST_ALDER_TIMER,
        "kilder": sorted(kilder, key=lambda x: x["kilde"]),
        "antal_events": con.execute("SELECT COUNT(*) c FROM eco_events").fetchone()["c"],
        "antal_viste": con.execute(
            "SELECT COUNT(*) c FROM eco_events WHERE tier>0").fetchone()["c"],
        "daekning": {
            "foerste": con.execute("SELECT MIN(dato_dk) d FROM eco_events").fetchone()["d"],
            "sidste": con.execute("SELECT MAX(dato_dk) d FROM eco_events").fetchone()["d"],
            "fremad_fra_i_dag": con.execute(
                "SELECT COUNT(*) c FROM eco_events WHERE dato_dk >= ? AND tier>0",
                (i_dag,)).fetchone()["c"],
        },
        "droppede_titler": [{"titel": t, "land": l, "antal": n}
                            for t, l, n in droplog_optaelling()[:25]],
    }


def omklassificer(con: sqlite3.Connection) -> dict:
    """Genberegn tier + begrundelse for ALLE gemte raekker.

    ⚠ UDEN DEN ER REV. B2's LOEFTE USANDT. B2 siger at man kan slaa op i
    droploggen, flytte en titel op i kalender_tier.py "og have data bagud med det
    samme". Data ligger ganske rigtigt der — men `tier` er skrevet ind i hver
    raekke, saa en forfremmelse ville kun gaelde fremtidige hoester. Historikken
    ville staa som tier 0 og aldrig blive vist.

    Koeres derfor hver gang tier-tabellerne aendres. Idempotent, og roerer intet
    andet end tier/begrundelse.
    """
    aendret, op, ned = 0, [], []
    for x in con.execute("SELECT ts_utc,titel,land,tier FROM eco_events").fetchall():
        ny, begrund = klassificer(x["titel"], x["land"])
        if ny == x["tier"]:
            continue
        con.execute("UPDATE eco_events SET tier=?, begrundelse=? "
                    "WHERE ts_utc=? AND titel=? AND land=?",
                    (ny, begrund, x["ts_utc"], x["titel"], x["land"]))
        aendret += 1
        (op if ny and (not x["tier"] or ny < x["tier"]) else ned).append(
            f"{x['land']} {x['titel']}: {x['tier']} -> {ny}")
    con.commit()
    return {"aendret": aendret, "forfremmet": sorted(set(op)), "nedrykket": sorted(set(ned))}


# ── Engangs-import af den gamle JSON-cache ──────────────────────────────────
def importer_cache(con, sti: Path | None = None) -> tuple[int, int]:
    """`kalender_cache/events.json` -> databasen.

    ⚠ DE HER RAEKKER KAN IKKE HENTES IGEN FRA FEEDET (thisweek only), og de
    baerer Faktisk-vaerdier fra 12. og 14. august. De blev committet netop
    derfor. At smide dem vaek ville koste maalinger vi ikke kan lave om.
    """
    sti = sti or (HER / "kalender_cache" / "events.json")
    if not sti.exists():
        return 0, 0
    raa = json.loads(sti.read_text(encoding="utf-8"))
    r = [_raekke(x["navn"], x["valuta"], x["dateline"], "kalender_cache",
                 x.get("impact"), x.get("forecast"), x.get("previous"),
                 x.get("actual"), True) for x in raa]
    flettede, _ = flet(r)
    log_droppede(flettede)
    return gem(con, flettede)


# ── CLI ─────────────────────────────────────────────────────────────────────
def _vis(con, dato: str) -> None:
    e = hent_dag(con, dato)
    if not e:
        print(f"  (ingen tier 1/2-events {dato})")
        return
    print(f"\n{dt.date.fromisoformat(dato).strftime('%A')} {dato}   ({len(e)} events)")
    print(f"  {'DK':7}{'T':3}{'land':6}{'begivenhed':40}{'prog.':>9}{'forrige':>9}{'faktisk':>9}")
    print("  " + "-" * 83)
    for x in e:
        print(f"  {(x['klokke_dk'] or '-- : --'):7}{x['tier']:<3}{x['land']:6}"
              f"{x['titel'][:38]:40}{(x['forecast'] or '-'):>9}"
              f"{(x['previous'] or '-'):>9}{(x['actual'] or '-'):>9}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Oekonomisk kalender for MES")
    ap.add_argument("--hoest", action="store_true", help="daglig hoest fra registrerede kilder")
    ap.add_argument("--uge", help="historik fra uge-HTML, fx aug9.2026")
    ap.add_argument("--fra-fil", help="parse en allerede hentet uge-HTML")
    ap.add_argument("--importer-cache", action="store_true",
                    help="engangs: kalender_cache/events.json")
    ap.add_argument("--vis", help="vis en dato (YYYY-MM-DD, dansk)")
    ap.add_argument("--omklassificer", action="store_true",
                    help="genberegn tier for alle gemte raekker (efter aendring i kalender_tier.py)")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()

    if a.hoest:
        r = hoest()
        print(json.dumps(r, ensure_ascii=False, indent=1))
        # ⚠ Exit-kode. Fejler alle kilder, skal jobbet FEJLE — ikke melde 0 og
        # lade en uges tavshed passere ubemaerket.
        sys.exit(1 if r["fejlede_kilder"] and r["hentet"] == 0 else 0)
    if a.uge or a.fra_fil:
        html = (Path(a.fra_fil).read_text(encoding="utf-8", errors="replace")
                if a.fra_fil else None)
        con = forbind()
        r = ForexFactoryUge(a.uge, html).hent(dt.date(1990, 1, 1), dt.date(2100, 1, 1))
        f, rap = flet(r)
        ny, opd = gem(con, f)
        log_droppede(f)
        eksporter(con)
        print(f"uge {a.uge or a.fra_fil}: {len(r)} hentet, {ny} nye, {opd} opdateret · {rap}")
        con.close()
    if a.importer_cache:
        con = forbind()
        ny, opd = importer_cache(con)
        eksporter(con)
        print(f"cache-import: {ny} nye, {opd} opdateret")
        con.close()
    if a.omklassificer:
        con = forbind()
        r = omklassificer(con)
        eksporter(con)
        print(json.dumps(r, ensure_ascii=False, indent=1))
        con.close()
    if a.vis:
        con = forbind()
        _vis(con, a.vis)
        con.close()
    if a.status:
        con = forbind()
        print(json.dumps(status(con), ensure_ascii=False, indent=1))
        con.close()
    if not any((a.hoest, a.uge, a.fra_fil, a.importer_cache, a.omklassificer,
                a.vis, a.status)):
        ap.print_help()
