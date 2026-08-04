"""
nyse_kalender.py — NYSE handelsdage, helligdage og halve dage, 1993 →
═══════════════════════════════════════════════════════════════════════════════════
Bygget til punkt B3 i regime-specen (fuldstaendighedsrevision) og til V0.2's krav om
at HALVE HANDELSDAGE identificeres og flages i stedet for stiltiende at indgaa: en
halv dag producerer en lille range der ligner et lavvolatilitetsregime men ikke er det.

Ingen ekstern afhaengighed — `pandas_market_calendars` er ikke installeret, og
`pandas.tseries.holiday.USFederalHolidayCalendar` er FORKERT for NYSE (boersen holder
Langfredag lukket, men handler paa Columbus Day og Veterans Day).

⚠ KALENDEREN ER EN HYPOTESE, IKKE EN FACIT.
Reglerne nedenfor er rekonstrueret, ikke hentet fra NYSE. Derfor er den rigtige brug
at lade DATA kontrollere KALENDEREN og ikke kun omvendt: `sessions_revision.py`
rapporterer baade sessioner kalenderen forventede men data mangler, OG sessioner data
har men kalenderen kaldte lukkede — den sidste kategori er en kalenderfejl, ikke et
datahul, og den skal frem i lyset frem for at blive absorberet.

Brug:
    from nyse_kalender import handelsdage, er_halv_dag, forventede_rth_minutter
    dage = handelsdage(date(2024, 1, 1), date(2024, 12, 31))     # 252 dage
    forventede_rth_minutter(date(2024, 11, 29))                  # 210 (dagen efter Thanksgiving)
"""
from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache

# RTH = 09:30–16:00 ET. En halv dag lukker 13:00 ET paa NYSE.
FULD_SESSION_MINUTTER = 390
HALV_SESSION_MINUTTER = 210

# CME's equity-index-futures (ES, MES, RTY, M2K) foelger NYSE's LUKKEDAGE, men ikke
# dens tidlige lukketid: de handler et kvarter laengere, til 13:15 ET. Verificeret
# empirisk 2026-08-04 paa MES og M2K 1-min — praecis 225 barer i 09:30-16:00-vinduet
# paa alle seks halve dage i baade 2024 og 2025, hvor NYSE-antagelsen gav 210.
# Uden dette skel ville et kvarters normal handel blive rapporteret som en defekt.
HALV_SESSION_MINUTTER_FUTURES = 225

MARKEDER = ("aktier", "futures")

# Kalenderen er kun verificeret fra 1993 (SPY's foerste handelsdag). Foer da kan
# reglerne afvige — spoerg ikke om svar den ikke har.
GYLDIG_FRA = date(1993, 1, 1)

# ── Ad-hoc lukninger (ikke regelbaserede) ─────────────────────────────────────
# Kilde: NYSE's historiske lukkedage. Kun 1993 → i dag.
EKSTRAORDINAERT_LUKKET: frozenset[date] = frozenset({
    date(1994, 4, 27),                        # Nixons begravelse
    date(2001, 9, 11), date(2001, 9, 12),     # 11. september
    date(2001, 9, 13), date(2001, 9, 14),
    date(2004, 6, 11),                        # Reagans begravelse
    date(2007, 1, 2),                         # Fords begravelse
    date(2012, 10, 29), date(2012, 10, 30),   # orkanen Sandy
    date(2018, 12, 5),                        # G.H.W. Bushs begravelse
    date(2025, 1, 9),                         # Carters begravelse
})

# ── Halve dage der ikke foelger de tre regler nedenfor ────────────────────────
# Dagen efter juledag, tilbage da NYSE stadig lukkede tidligt paa den. Praksis
# ophoerte efter 2003 (2008-12-26 var en hel dag).
EKSTRA_HALVE_DAGE: frozenset[date] = frozenset({
    date(1997, 12, 26), date(1999, 12, 31), date(2003, 12, 26),
})


def _paaskesoendag(aar: int) -> date:
    """Anonym gregoriansk algoritme. Langfredag = paaskesoendag - 2 dage."""
    a = aar % 19
    b, c = divmod(aar, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    lo = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * lo) // 451
    maaned, dag = divmod(h + lo - 7 * m + 114, 31)
    return date(aar, maaned, dag + 1)


def _n_te_ugedag(aar: int, maaned: int, ugedag: int, n: int) -> date:
    """n'te forekomst af ugedag (0=man) i maaneden. n=-1 = sidste."""
    if n > 0:
        d = date(aar, maaned, 1)
        d += timedelta(days=(ugedag - d.weekday()) % 7)
        return d + timedelta(days=7 * (n - 1))
    naeste = date(aar + (maaned == 12), (maaned % 12) + 1, 1)
    d = naeste - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - ugedag) % 7)


def _naermeste_hverdag(d: date) -> date | None:
    """NYSE's observationsregel: loerdag -> fredag foer, soendag -> mandag efter.

    Undtagelse for nytaarsdag: falder den paa en loerdag, observeres den IKKE
    (fredag 31. december er en almindelig handelsdag). Haandteres af kalderen.
    """
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


@lru_cache(maxsize=256)
def helligdage(aar: int) -> frozenset[date]:
    """NYSE-lukkedage i et kalenderaar (uden weekender, med ad-hoc-lukninger)."""
    ud: set[date] = set()

    # Nytaar. Loerdag -> ingen observation; soendag -> mandag 2. januar.
    nytaar = date(aar, 1, 1)
    if nytaar.weekday() == 6:
        ud.add(date(aar, 1, 2))
    elif nytaar.weekday() != 5:
        ud.add(nytaar)

    if aar >= 1998:                                    # MLK-dagen indfoert 1998
        ud.add(_n_te_ugedag(aar, 1, 0, 3))
    ud.add(_n_te_ugedag(aar, 2, 0, 3))                 # Washingtons foedselsdag
    ud.add(_paaskesoendag(aar) - timedelta(days=2))    # Langfredag
    ud.add(_n_te_ugedag(aar, 5, 0, -1))                # Memorial Day

    if aar >= 2022:                                    # Juneteenth indfoert 2022
        j = _naermeste_hverdag(date(aar, 6, 19))
        if j:
            ud.add(j)

    uafh = _naermeste_hverdag(date(aar, 7, 4))          # 4. juli
    if uafh:
        ud.add(uafh)
    ud.add(_n_te_ugedag(aar, 9, 0, 1))                 # Labor Day
    ud.add(_n_te_ugedag(aar, 11, 3, 4))                # Thanksgiving

    jul = _naermeste_hverdag(date(aar, 12, 25))         # juledag
    if jul:
        ud.add(jul)

    ud |= {d for d in EKSTRAORDINAERT_LUKKET if d.year == aar}
    return frozenset(d for d in ud if d.weekday() < 5)


def er_handelsdag(d: date) -> bool:
    return d.weekday() < 5 and d not in helligdage(d.year)


@lru_cache(maxsize=256)
def halve_dage(aar: int) -> frozenset[date]:
    """Dage med tidlig lukning kl. 13:00 ET (210 RTH-minutter i stedet for 390)."""
    ud: set[date] = set()

    # 1) Dagen efter Thanksgiving — altid.
    ud.add(_n_te_ugedag(aar, 11, 3, 4) + timedelta(days=1))

    # 2) 3. juli, naar den er en handelsdag og 4. juli er en hverdag.
    #    Falder 4. juli i weekenden, er 3. juli enten selve helligdagen (loerdag-
    #    tilfaeldet) eller en almindelig hel dag.
    if date(aar, 7, 4).weekday() < 5:
        ud.add(date(aar, 7, 3))

    # 3) Juleaftensdag, naar den falder man-tors. Falder den paa en fredag, er den
    #    i stedet selve den observerede juledag (25. er da en loerdag) og altsaa lukket.
    juleaften = date(aar, 12, 24)
    if juleaften.weekday() <= 3:
        ud.add(juleaften)

    ud |= {d for d in EKSTRA_HALVE_DAGE if d.year == aar}
    # En halv dag skal per definition vaere en handelsdag.
    return frozenset(d for d in ud if er_handelsdag(d))


def er_halv_dag(d: date) -> bool:
    return d in halve_dage(d.year)


def forventede_rth_minutter(d: date, marked: str = "aktier") -> int:
    """Antal 1-min RTH-barer en komplet session paa denne dato boer have. 0 = lukket.

    `marked="futures"` for CME's equity-index-kontrakter, der lukker 13:15 og ikke
    13:00 paa halve dage. Lukkedagene er de samme for begge markeder.
    """
    if marked not in MARKEDER:
        raise ValueError(f"ukendt marked {marked!r}; vaelg blandt {MARKEDER}")
    if not er_handelsdag(d):
        return 0
    if not er_halv_dag(d):
        return FULD_SESSION_MINUTTER
    return (HALV_SESSION_MINUTTER_FUTURES if marked == "futures"
            else HALV_SESSION_MINUTTER)


def handelsdage(start: date, slut: date) -> list[date]:
    """Alle NYSE-handelsdage i [start, slut], inklusive begge endepunkter."""
    if start < GYLDIG_FRA:
        raise ValueError(
            f"nyse_kalender er kun verificeret fra {GYLDIG_FRA}; bad om {start}. "
            "Udvid EKSTRAORDINAERT_LUKKET og verificér reglerne foer du saenker graensen.")
    ud, d = [], start
    while d <= slut:
        if er_handelsdag(d):
            ud.append(d)
        d += timedelta(days=1)
    return ud
