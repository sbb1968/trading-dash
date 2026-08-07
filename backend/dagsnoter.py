"""
dagsnoter.py — hvorfor der ikke blev handlet
════════════════════════════════════════════════════════════════════════════════
Ferie, sygdom, manglende lyst. Fraværet af en handel er også information, men det
har ingen række at hænge på: intet trade_id, ingen ejermaskine. Derfor sin egen
tabel, nøglet på dato, og præcis ÉN note pr. handelsdag — "ferie i uge 30" er
ikke én note, det er fem.

To ting afgør om det bliver rigtigt frem for misvisende:

⚠ KUN NYSE-HANDELSDAGE TÆLLER SOM HULLER. Uden kalenderen ville hver lørdag og
  hver helligdag stå som en uforklaret tom dag, og de tre rigtige ville drukne i
  fyrre falske. nyse_kalender dækker 1993→ med helligdage og observationsregler.

⚠ KUN DATOINTERVALLET FILTRERER — ikke strategi, ikke symbol. Filtrerer man på
  Konfluens 2 alene, og K2 ikke handlede 8/7 mens BuyTheDip gjorde, så er 8/7
  IKKE en dag uden handler. Regnede vi huller ud fra de filtrerede rækker, ville
  brugeren få tilbudt at skrive "ferie" på en dag hun sad og handlede.

En dag tæller som handlet hvis en handel blev ÅBNET den dag. Ikke lukket. Det er
ikke den mest oplagte definition, men det er den eneste der stemmer med hvad man
ser: journal-tabellen filtrerer selv på entry_time_et, så en handel åbnet 30/6 og
lukket 2/7 vises slet ikke når man ser juli. Talte 2/7 som "handlet", ville dagen
mangle i båndet OG være tom i tabellen, og der ville ikke være noget sted at
skrive hvorfor.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone

import pytz

from nyse_kalender import handelsdage

ET = pytz.timezone("America/New_York")

logger = logging.getLogger(__name__)


def _iso(d: date) -> str:
    return d.isoformat()


def idag_et() -> date:
    """Handelsdagen i ET — ikke lokal dato. Kl. 03 dansk tid er det stadig i går
    på børsen, og en dag der endnu ikke er begyndt må ikke stå som et hul."""
    return datetime.now(ET).date()


# ── Læsning ──────────────────────────────────────────────────────────────────

async def hent_noter(db, date_from: str, date_to: str) -> dict[str, str]:
    """Alle dagsnoter i intervallet, som {dato: note}."""
    cur = await db.execute(
        "SELECT dato, note FROM dagsnoter WHERE dato >= ? AND dato <= ? ORDER BY dato",
        (date_from, date_to))
    return {r[0]: r[1] for r in await cur.fetchall()}


async def handlede_dage(db, date_from: str, date_to: str) -> set[str]:
    """Datoer i intervallet hvor mindst én handel blev åbnet.

    INGEN source/symbol-filtrering — se modulets docstring."""
    cur = await db.execute(
        "SELECT DISTINCT substr(entry_time_et, 1, 10) FROM trades "
        "WHERE entry_time_et >= ? AND entry_time_et <= ?",
        (date_from, date_to + "T23:59:59"))
    return {r[0] for r in await cur.fetchall() if r[0]}


async def byg_baand(db, date_from: str, date_to: str) -> dict:
    """Alt Journal-båndet skal bruge for ét datointerval.

    Returnerer huller (handelsdage uden handler, med evt. note) og forældreløse
    noter (dage der HAR handler men alligevel bærer en note — det sker når man
    skriver ferien ind på forhånd og så kommer hjem og handler alligevel).
    En sådan note må ikke bare forsvinde tavst; så ville den være umulig at rette.
    """
    try:
        d0, d1 = date.fromisoformat(date_from), date.fromisoformat(date_to)
    except ValueError as e:
        raise ValueError(f"ugyldig dato: {e}") from e
    if d1 < d0:
        raise ValueError(f"date_to ({date_to}) ligger før date_from ({date_from})")

    aabne = handelsdage(d0, d1)
    handlet = await handlede_dage(db, date_from, date_to)
    noter = await hent_noter(db, date_from, date_to)
    idag = idag_et()

    huller = []
    for d in aabne:
        iso = _iso(d)
        if iso in handlet:
            continue
        huller.append({
            "dato":     iso,
            "note":     noter.get(iso, ""),
            # Fremtid og i dag er ikke huller man har overset — de er bare ikke
            # kommet endnu. Uden skelnen ville hver dag man kigger fremad ligne
            # et problem, og advarslen ville miste sin betydning.
            "fremtid":  d > idag,
            "i_dag":    d == idag,
        })

    forpasset = [{"dato": k, "note": v} for k, v in sorted(noter.items())
                 if k in handlet]

    return {
        "date_from": date_from,
        "date_to": date_to,
        "handelsdage": len(aabne),
        "huller": huller,
        "noter_paa_handlede_dage": forpasset,
    }


# ── Skrivning ────────────────────────────────────────────────────────────────

async def saet_note(db, dato: str, note: str) -> str:
    """Gem eller slet noten for én dag. Returnerer 'gemt' eller 'slettet'.

    Tom tekst sletter — det er den eneste måde at fortryde en note på, og det
    svarer til hvad man forventer af et felt man kan rydde."""
    try:
        d = date.fromisoformat(dato)
    except ValueError as e:
        raise ValueError(f"ugyldig dato {dato!r}: {e}") from e

    # ⚠ En note på en lørdag ville aldrig kunne SES igen — båndet viser kun
    # NYSE-handelsdage, så den ville ligge i databasen uden nogen vej tilbage.
    # Afvis den ved indgangen frem for at gemme noget usynligt.
    if not handelsdage(d, d):
        raise ValueError(f"{dato} er ikke en NYSE-handelsdag (weekend eller helligdag)")

    nu = datetime.now(timezone.utc).isoformat()
    tekst = (note or "").strip()

    if not tekst:
        await db.execute("DELETE FROM dagsnoter WHERE dato = ?", (dato,))
        await db.commit()
        return "slettet"

    await db.execute(
        "INSERT INTO dagsnoter (dato, note, oprettet, aendret) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(dato) DO UPDATE SET note = excluded.note, aendret = excluded.aendret",
        (dato, tekst, nu, nu))
    await db.commit()
    return "gemt"
