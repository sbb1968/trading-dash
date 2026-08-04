"""
sessions_revision.py — B2 og B3 i regime-specen
═══════════════════════════════════════════════════════════════════════════════════
To kontroller der hoerer sammen, fordi de begge handler om at en tidsserie kan se
fuldstaendig ud uden at vaere det.

B2 — KONSTANT BARANTAL PR. SESSION (`tjek_konstant_barantal`)
    Sessionsdefinitioner aendrer sig over fjorten aar. VIX er allerede fanget i at
    have tre forskellige gennem historikken. Bygger man en tid-paa-dagen-profil hen
    over et saadant brud, midler man to forskellige markeder sammen og faar en profil
    der ikke beskriver nogen af dem.

    Reglen: enhver tid-paa-dagen-profil bygges paa et vindue hvis barantal pr. session
    er KONSTANT hen over hele den historik der indgaar — og koden verificerer det og
    fejler HOEJLYDT hvis antallet skifter. Bruddene skal fanges af en assert, ikke af
    at nogen tilfaeldigvis kigger paa bartaellinger igen.

    Bemaerk hvad der er en fejl og hvad der ikke er:
      · flere barer end sessionen kan rumme -> STRUKTURFEJL (dubletter/tidszone)
      · aarsmedianen skifter                -> STRUKTURFEJL (sessionsdefinition aendret)
      · enkelte manglende minutter          -> datahul, rapporteres, tolereres
      · faerre barer paa en halv handelsdag -> forventet, kalenderen kender dem

    ⚠ RAEKKEVIDDE. Kontrollen ser kun paa barer INDE i vinduet. Aendrer en session sig
    udelukkende udenfor — aabner fx en time tidligere, mens 09:30-16:00 er uroert —
    er antallet i vinduet stadig konstant, og kontrollen tier. Det er efter hensigten:
    profilen bruger kun vinduet, saa et brud udenfor kan ikke forurene den. Men det
    betyder ogsaa at kontrollen ikke er en generel sessionsdetektor, og at vinduet
    skal vaelges saa det ligger inden for ALLE historikkens sessionsdefinitioner
    (09:30-16:00 goer for VIX' tre).

B3 — FULDSTAENDIGHEDSREVISION (`fuldstaendighedsrevision`)
    Syv vellykkede stikproever viser at der findes data i syv aar. De viser ikke at
    der findes data DERIMELLEM. Udviklingsperiodens effektive start er dér hvor
    sammenhaengen faktisk begynder — ikke dér hvor den tidligste vellykkede stikproeve
    ligger.

    Revisionen taeller sessioner fundet mod forventet ud fra NYSE-kalenderen, lister
    de manglende datoer, og udleder `effektiv_start`: dagen efter det seneste lange
    hul. Den rapporterer OGSAA sessioner hvor data findes men kalenderen sagde lukket
    — det er en kalenderfejl og ikke et datahul, og de to maa ikke blandes sammen.

Brug som bibliotek:
    from sessions_revision import fuldstaendighedsrevision, tjek_konstant_barantal
    rap = fuldstaendighedsrevision(tider)
    tjek_konstant_barantal(tider, vindue=(time(9,30), time(16,0)))   # rejser ved brud

Brug som afslutning paa en harvest (C4, punkt 2):
    python sessions_revision.py --mappe vol_cache --moenster "*_1min.csv"
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path

from nyse_kalender import (er_halv_dag, er_handelsdag, forventede_rth_minutter,
                           handelsdage)

# ── Konstanter ────────────────────────────────────────────────────────────────
RTH_VINDUE = (time(9, 30), time(16, 0))
# Et "langt hul" er saa mange sammenhaengende manglende sessioner at serien ikke
# laengere kan kaldes sammenhaengende foer det. Fem = en hel handelsuge.
LANGT_HUL_SESSIONER = 5
# Under denne daekning i et rullende kvartal regnes serien ikke som sammenhaengende.
MIN_DAEKNING_KVARTAL = 0.90
KVARTAL_SESSIONER = 63
# Andel af minutterne i en session der må mangle foer sessionen kaldes ufuldstaendig.
MAKS_MANGLENDE_ANDEL = 0.02


class SessionsBrud(Exception):
    """Rejses naar barantallet pr. session ikke er konstant. Se B2."""


@dataclass
class Fuldstaendighed:
    """Resultatet af B3-revisionen for én serie."""
    navn: str
    foerste: date | None = None
    sidste: date | None = None
    sessioner_fundet: int = 0
    sessioner_forventet: int = 0
    manglende: list[date] = field(default_factory=list)
    uventede: list[date] = field(default_factory=list)     # data, men kalenderen sagde lukket
    ufuldstaendige: list[tuple[date, int, int]] = field(default_factory=list)
    huller: list[tuple[date, date, int]] = field(default_factory=list)   # (fra, til, antal)
    effektiv_start: date | None = None
    effektiv_start_grund: str = ""

    @property
    def daekning(self) -> float:
        if not self.sessioner_forventet:
            return 0.0
        return self.sessioner_fundet / self.sessioner_forventet

    def som_dict(self) -> dict:
        return {
            "navn": self.navn,
            "foerste": self.foerste.isoformat() if self.foerste else None,
            "sidste": self.sidste.isoformat() if self.sidste else None,
            "sessioner_fundet": self.sessioner_fundet,
            "sessioner_forventet": self.sessioner_forventet,
            "daekning": round(self.daekning, 4),
            "antal_manglende": len(self.manglende),
            "manglende": [d.isoformat() for d in self.manglende],
            "uventede": [d.isoformat() for d in self.uventede],
            "ufuldstaendige": [[d.isoformat(), n, f] for d, n, f in self.ufuldstaendige],
            "huller": [[a.isoformat(), b.isoformat(), n] for a, b, n in self.huller],
            "effektiv_start": self.effektiv_start.isoformat() if self.effektiv_start else None,
            "effektiv_start_grund": self.effektiv_start_grund,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# Indlaesning
# ═══════════════════════════════════════════════════════════════════════════════
def laes_tider(sti: Path, kolonne: str = "timestamp") -> list[datetime]:
    """Laes tidsstempler fra en CSV. Naive stempler antages allerede at vaere ET."""
    ud: list[datetime] = []
    with sti.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            raa = r.get(kolonne) or r.get("date") or r.get("Datetime") or ""
            try:
                dt = datetime.fromisoformat(str(raa).strip())
            except ValueError:
                continue
            ud.append(dt.replace(tzinfo=None) if dt.tzinfo else dt)
    ud.sort()
    return ud


def _i_vindue(t: datetime, vindue: tuple[time, time]) -> bool:
    """Halvaabent [start, slut): 16:00-baren hoerer til naeste vindue, ikke dette."""
    return vindue[0] <= t.time() < vindue[1]


def _pr_session(tider, vindue) -> dict[date, int]:
    n: dict[date, int] = defaultdict(int)
    for t in tider:
        if vindue is None or _i_vindue(t, vindue):
            n[t.date()] += 1
    return dict(n)


def forventede_barer(d: date, vindue: tuple[time, time],
                     marked: str = "aktier", fast: int | None = None) -> int:
    """Hvor mange 1-min-barer en KOMPLET session paa denne dato boer give i vinduet.

    En halv dag lukker 13:00 ET (13:15 for CME's equity-index-futures), saa et
    09:30-16:00-vindue rummer da kun 210 hhv. 225 barer. Kalenderen kender de dage
    — det er derfor de ikke maa taelle som et brud.
    """
    sess = forventede_rth_minutter(d, marked)
    if sess == 0:
        return 0
    # DAGSSERIER: én bar pr. handelsdag, uanset hvor mange minutter sessionen har.
    # Uden dette ville hver eneste dagsbar blive flaget som "ufuldstaendig" (1 mod
    # 390 forventede minutter) — en kontrol der raaber paa alt, siger intet.
    if fast is not None:
        return fast
    if vindue is None:
        return sess
    # Vinduets overlap med selve sessionen (09:30 -> 16:00 eller 13:00).
    luk_min = (9 * 60 + 30) + sess
    start_min = max(vindue[0].hour * 60 + vindue[0].minute, 9 * 60 + 30)
    slut_min = min(vindue[1].hour * 60 + vindue[1].minute, luk_min)
    return max(0, slut_min - start_min)


# ═══════════════════════════════════════════════════════════════════════════════
# B2 — konstant barantal pr. session
# ═══════════════════════════════════════════════════════════════════════════════
def tjek_konstant_barantal(tider, vindue: tuple[time, time] = RTH_VINDUE,
                           navn: str = "serie", rejs: bool = True,
                           marked: str = "aktier",
                           forventede_pr_session: int | None = None) -> dict:
    """Verificér at barantallet pr. session er konstant hen over hele historikken.

    Rejser `SessionsBrud` ved strukturbrud. Returnerer en rapport-dict uanset
    (saet `rejs=False` hvis du hellere vil inspicere end at fejle).

    ⚠ Kald denne FOER du bygger en tid-paa-dagen-profil. Det er hele pointen med B2:
    bruddet skal fanges af koden, ikke af at nogen kigger paa bartaellinger igen.
    """
    n_pr_dag = _pr_session(tider, vindue)
    if not n_pr_dag:
        rap = {"navn": navn, "ok": False, "brud": ["ingen barer i vinduet"], "aar": {}}
        if rejs:
            raise SessionsBrud(f"{navn}: ingen barer i vinduet {vindue[0]}-{vindue[1]}")
        return rap

    brud: list[str] = []
    # Halve dage har lovligt et andet antal — de skal ikke med i konstansvurderingen.
    hele = {d: n for d, n in n_pr_dag.items() if not er_halv_dag(d) and er_handelsdag(d)}

    # (1) FLERE barer end sessionen kan rumme. Det kan markedet ikke producere, saa
    #     det er altid en defekt: dubletter fra en genoptaget harvest, to kontrakter
    #     blandet i samme serie, eller stempler i en anden tidszone end antaget.
    for_mange = []
    for d, n in sorted(n_pr_dag.items()):
        f = forventede_barer(d, vindue, marked, forventede_pr_session)
        if f and n > f:
            for_mange.append(f"{d}: {n}>{f}")
    if for_mange:
        brud.append(f"{len(for_mange)} sessioner har flere barer end sessionen kan rumme "
                    f"({', '.join(for_mange[:5])}"
                    f"{' …' if len(for_mange) > 5 else ''}) — dubletter, blandede "
                    f"kontrakter eller tidszonefejl, ikke markedet")
    # (2) Aarsmedianen skal vaere den samme hele vejen. Skifter den, er
    #     sessionsdefinitionen aendret undervejs.
    pr_aar: dict[int, list[int]] = defaultdict(list)
    for d, n in hele.items():
        pr_aar[d.year].append(n)
    medianer = {a: int(statistics.median(v)) for a, v in sorted(pr_aar.items()) if v}
    if len(set(medianer.values())) > 1:
        skift = []
        aar_sorteret = sorted(medianer)
        for i in range(1, len(aar_sorteret)):
            f, t = aar_sorteret[i - 1], aar_sorteret[i]
            if medianer[f] != medianer[t]:
                skift.append(f"{f}:{medianer[f]} -> {t}:{medianer[t]}")
        brud.append("aarsmedianen for barer pr. session skifter (" + "; ".join(skift) +
                    ") — sessionsdefinitionen er ikke konstant over historikken")

    rap = {
        "navn": navn,
        "ok": not brud,
        "brud": brud,
        "vindue": f"{vindue[0]}-{vindue[1]}" if vindue else "hele dagen",
        "sessioner": len(n_pr_dag),
        "aarsmedian": medianer,
        "median": int(statistics.median(list(hele.values()))) if hele else 0,
    }
    if brud and rejs:
        raise SessionsBrud(f"{navn}: " + " | ".join(brud))
    return rap


# ═══════════════════════════════════════════════════════════════════════════════
# B3 — fuldstaendighedsrevision
# ═══════════════════════════════════════════════════════════════════════════════
def _find_effektiv_start(fundne: list[date], forventede: list[date]) -> tuple[date, str]:
    """Dagen hvor serien BEGYNDER at vaere sammenhaengende — ikke dens foerste bar.

    Gaar bagud fra i dag og stopper ved det seneste lange hul, eller ved det seneste
    rullende kvartal hvor daekningen faldt under graensen. Alt foer det er stikproever,
    ikke historik, og maa ikke indgaa i en udviklingsperiode.
    """
    if not forventede:
        return (fundne[0] if fundne else None), "ingen forventede sessioner"
    har = set(fundne)
    flag = [d in har for d in forventede]

    seneste_brud_idx = -1
    grund = "sammenhaengende fra foerste session"

    # (a) langt hul
    loeb = 0
    for i, ok in enumerate(flag):
        if ok:
            loeb = 0
        else:
            loeb += 1
            if loeb >= LANGT_HUL_SESSIONER and i > seneste_brud_idx:
                seneste_brud_idx = i
                grund = (f"seneste hul paa >= {LANGT_HUL_SESSIONER} sammenhaengende "
                         f"sessioner slutter {forventede[i]}")

    # (b) rullende kvartal under daekningsgraensen
    if len(flag) >= KVARTAL_SESSIONER:
        haves = sum(flag[:KVARTAL_SESSIONER])
        for slut in range(KVARTAL_SESSIONER - 1, len(flag)):
            if slut >= KVARTAL_SESSIONER:
                haves += flag[slut] - flag[slut - KVARTAL_SESSIONER]
            if haves / KVARTAL_SESSIONER < MIN_DAEKNING_KVARTAL and slut > seneste_brud_idx:
                seneste_brud_idx = slut
                grund = (f"seneste rullende kvartal under {MIN_DAEKNING_KVARTAL:.0%} "
                         f"daekning slutter {forventede[slut]}")

    for i in range(seneste_brud_idx + 1, len(forventede)):
        if flag[i]:
            return forventede[i], grund
    return (fundne[-1] if fundne else None), grund + " (intet sammenhaengende stykke efter)"


def fuldstaendighedsrevision(tider, navn: str = "serie",
                             vindue: tuple[time, time] | None = RTH_VINDUE,
                             start: date | None = None,
                             slut: date | None = None,
                             marked: str = "aktier",
                             forventede_pr_session: int | None = None) -> Fuldstaendighed:
    """Taell sessioner fundet mod forventet, list de manglende, og find effektiv start."""
    rap = Fuldstaendighed(navn=navn)
    n_pr_dag = _pr_session(tider, vindue)
    if not n_pr_dag:
        return rap

    datoer = sorted(n_pr_dag)
    rap.foerste, rap.sidste = datoer[0], datoer[-1]
    s0 = start or rap.foerste
    s1 = slut or rap.sidste

    forventede = handelsdage(s0, s1)
    har = set(datoer)
    rap.sessioner_forventet = len(forventede)
    rap.sessioner_fundet = sum(1 for d in forventede if d in har)
    rap.manglende = [d for d in forventede if d not in har]
    # Data hvor kalenderen sagde lukket. Det er en KALENDERFEJL, ikke et datahul —
    # og den skal frem i lyset frem for at blive absorberet i taellingen.
    rap.uventede = [d for d in datoer if s0 <= d <= s1 and not er_handelsdag(d)]

    if vindue is not None:
        for d in forventede:
            if d not in har:
                continue
            f = forventede_barer(d, vindue, marked, forventede_pr_session)
            if f and n_pr_dag[d] < f * (1.0 - MAKS_MANGLENDE_ANDEL):
                rap.ufuldstaendige.append((d, n_pr_dag[d], f))

    # Sammenhaengende huller, med datoer — ikke kun et antal.
    loeb: list[date] = []
    for d in forventede:
        if d in har:
            if loeb:
                rap.huller.append((loeb[0], loeb[-1], len(loeb)))
                loeb = []
        else:
            loeb.append(d)
    if loeb:
        rap.huller.append((loeb[0], loeb[-1], len(loeb)))
    rap.huller.sort(key=lambda h: -h[2])

    rap.effektiv_start, rap.effektiv_start_grund = _find_effektiv_start(
        [d for d in datoer if s0 <= d <= s1], forventede)
    return rap


# ═══════════════════════════════════════════════════════════════════════════════
# Rapport
# ═══════════════════════════════════════════════════════════════════════════════
def skriv_rapport(rapporter: list[Fuldstaendighed], b2: list[dict], sti: Path) -> None:
    L = ["# Fuldstaendighedsrevision (B3) og sessionskonstans (B2)\n\n",
         f"Koert: {datetime.now().isoformat(timespec='seconds')}\n\n",
         "Sessioner fundet mod forventet ud fra NYSE-kalenderen. **Effektiv start** er "
         "dér hvor sammenhaengen faktisk begynder — ikke dér hvor den foerste bar "
         "ligger. Det er den dato en udviklingsperiode maa regnes fra.\n\n",
         "| serie | foerste bar | sidste bar | fundet/forventet | daekning | "
         "**effektiv start** | huller |\n|---|---|---|---|---|---|---|\n"]
    for r in rapporter:
        L.append(f"| {r.navn} | {r.foerste} | {r.sidste} | "
                 f"{r.sessioner_fundet}/{r.sessioner_forventet} | {r.daekning:.1%} | "
                 f"**{r.effektiv_start}** | {len(r.huller)} |\n")

    L.append("\n## Huller pr. serie\n")
    for r in rapporter:
        L.append(f"\n### {r.navn}\n")
        L.append(f"\nEffektiv start: **{r.effektiv_start}** — {r.effektiv_start_grund}\n")
        if not r.huller:
            L.append("\nIngen manglende sessioner.\n")
        else:
            L.append(f"\n{len(r.manglende)} manglende sessioner i "
                     f"{len(r.huller)} huller. De ti stoerste:\n\n")
            L.append("| fra | til | sessioner |\n|---|---|---|\n")
            for a, b, n in r.huller[:10]:
                L.append(f"| {a} | {b} | {n} |\n")
        if r.uventede:
            L.append(f"\n**{len(r.uventede)} sessioner med data paa dage kalenderen kaldte "
                     f"lukkede** — det er en kalenderfejl og ikke et datahul: "
                     f"{', '.join(str(d) for d in r.uventede[:15])}"
                     f"{' …' if len(r.uventede) > 15 else ''}\n")
        if r.ufuldstaendige:
            L.append(f"\n{len(r.ufuldstaendige)} ufuldstaendige sessioner "
                     f"(under {(1-MAKS_MANGLENDE_ANDEL):.0%} af forventede minutter). "
                     f"De fem vaerste:\n\n| dato | barer | forventet |\n|---|---|---|\n")
            for d, n, f in sorted(r.ufuldstaendige, key=lambda x: x[1])[:5]:
                L.append(f"| {d} | {n} | {f} |\n")

    L.append("\n## B2 — barantal pr. session konstant?\n\n")
    L.append("| serie | vindue | sessioner | median | status |\n|---|---|---|---|---|\n")
    for r in b2:
        status = ("ikke relevant (dagsserie)" if r.get("sprunget")
                  else ("OK" if r["ok"] else "**BRUD**"))
        L.append(f"| {r['navn']} | {r.get('vindue','')} | {r.get('sessioner',0)} | "
                 f"{r.get('median',0)} | {status} |\n")
    for r in b2:
        if not r["ok"]:
            L.append(f"\n**{r['navn']}:**\n")
            for b in r["brud"]:
                L.append(f"\n- {b}\n")
    sti.write_text("".join(L), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="B2+B3: sessionskonstans og fuldstaendighed mod NYSE-kalenderen")
    ap.add_argument("--mappe", default="vol_cache", help="mappe med CSV-serier")
    ap.add_argument("--moenster", default="*_1min.csv", help="filnavn-moenster")
    ap.add_argument("--vindue", default="09:30-16:00",
                    help="RTH-vindue, eller 'alle' for hele doegnet (futures)")
    ap.add_argument("--ud", default=None, help="rapportsti (.md); JSON skrives ved siden af")
    ap.add_argument("--marked", default="aktier", choices=["aktier", "futures"],
                    help="futures = CME equity-index (halve dage lukker 13:15, ikke 13:00)")
    ap.add_argument("--barer-pr-session", dest="barer_pr_session", type=int, default=None,
                    help="fast antal barer pr. handelsdag (brug 1 til DAGSSERIER)")
    ap.add_argument("--streng", action="store_true",
                    help="afslut med fejlkode hvis B2 finder brud")
    args = ap.parse_args()

    if args.vindue.lower() in ("alle", "all", "ingen"):
        vindue = None
    else:
        a, b = args.vindue.split("-")
        vindue = (time.fromisoformat(a), time.fromisoformat(b))

    mappe = Path(args.mappe)
    filer = sorted(mappe.glob(args.moenster))
    if not filer:
        print(f"Ingen filer matcher {mappe}/{args.moenster}")
        return 2

    rapporter, b2 = [], []
    for f in filer:
        tider = laes_tider(f)
        navn = f.stem
        print(f"[{navn}] {len(tider)} barer …")
        r = fuldstaendighedsrevision(tider, navn=navn, vindue=vindue,
                                     marked=args.marked,
                                     forventede_pr_session=args.barer_pr_session)
        rapporter.append(r)
        if args.barer_pr_session == 1:
            # DAGSSERIER: B2 handler om tid-paa-dagen-profiler, og dem findes der
            # ikke paa dagsbarer. Vinduet 09:30-16:00 fanger nul barer (dagsbarer
            # stemples ved midnat), saa kontrollen ville melde BRUD hvor den slet
            # ikke gaelder. En kontrol der raaber hvor den ikke er relevant,
            # drukner den der er.
            b = {"navn": navn, "ok": True, "brud": [], "sprunget": True,
                 "vindue": "—", "sessioner": r.sessioner_fundet, "median": 1}
        else:
            b = tjek_konstant_barantal(tider, vindue=vindue or RTH_VINDUE, navn=navn,
                                       rejs=False, marked=args.marked,
                                       forventede_pr_session=args.barer_pr_session)
        b2.append(b)
        print(f"   {r.sessioner_fundet}/{r.sessioner_forventet} sessioner "
              f"({r.daekning:.1%}) · effektiv start {r.effektiv_start} · "
              f"B2 {'ikke relevant' if b.get('sprunget') else ('OK' if b['ok'] else 'BRUD')}")
        for x in b["brud"]:
            print(f"   !! {x}")

    ud = Path(args.ud) if args.ud else mappe / "sessions_revision.md"
    skriv_rapport(rapporter, b2, ud)
    ud.with_suffix(".json").write_text(json.dumps(
        {"fuldstaendighed": [r.som_dict() for r in rapporter], "b2": b2},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSkrevet: {ud}\n         {ud.with_suffix('.json')}")

    if args.streng and any(not b["ok"] for b in b2):
        print("\nB2 fandt strukturbrud — se rapporten.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
