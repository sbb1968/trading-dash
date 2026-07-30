#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
regime_guard.py — design-mode-vagt for regime-motor v2 (spec fase 0.4).
════════════════════════════════════════════════════════════════════════

Kontaminations-regel 2: fase 0-4 designes UDELUKKENDE paa data til og med
DESIGN_END. Alt derefter er forseglet indtil fase 5, hvor motoren er frosset.

Hvorfor en kode-vagt og ikke bare disciplin: holdout-kontaminering sker ikke
ved at nogen beslutter at snyde. Den sker ved at en loader henter "alt hvad
der ligger i mappen", og ingen opdager at de sidste fem uger var med. Vagten
goer det til en haard fejl i stedet for en stille skaevhed.

Brug:
    from regime_guard import guard, DESIGN_END

    guard.assert_open("data_harvest/ES_1day.csv")   # filsti-niveau (valgfrit)
    dage = guard.filter_dates(alle_dage, "ES_1day") # dato-niveau (det vigtige)

    # Fase 5 — efter frysning, eksplicit og logget:
    guard.open_holdout("fase 5: motoren er frosset, commit <hash>")

Vagten er bevidst STOEJENDE: hver gang den skaerer data fra, taelles det, og
`guard.rapport()` viser hvad der blev holdt ude. Et tavst filter ville
efterlade samme tvivl som slet ingen vagt.
"""
from __future__ import annotations

from collections import Counter
from datetime import date


# ═══════════════════════════════════════════════════════════════════
# KONSTANT — spec'ens config-blok. Aendres ikke uden logning.
# ═══════════════════════════════════════════════════════════════════
DESIGN_END = date(2026, 4, 30)


class _Guard:
    """Holder mode + statistik over hvad der er skaaret fra."""

    def __init__(self) -> None:
        self._mode = "design"          # "design" | "holdout_open"
        self._grund = ""
        self._afskaaret: Counter = Counter()   # kilde -> antal frasorterede datoer
        self._set: Counter = Counter()         # kilde -> antal datoer set i alt

    # ---------------------------------------------------------------
    @property
    def mode(self) -> str:
        return self._mode

    @property
    def er_design(self) -> bool:
        return self._mode == "design"

    def open_holdout(self, grund: str) -> None:
        """Aabn holdout (fase 5). Kraever en begrundelse — den ryger i rapporten."""
        if not grund or len(grund.strip()) < 10:
            raise ValueError("open_holdout kraever en reel begrundelse (>=10 tegn)")
        self._mode = "holdout_open"
        self._grund = grund.strip()

    # ---------------------------------------------------------------
    def tillader(self, d: date) -> bool:
        """Maa denne dato indgaa i den aktuelle mode?"""
        return self._mode != "design" or d <= DESIGN_END

    def assert_dato(self, d: date, kilde: str) -> None:
        """Haard fejl hvis datoen ligger efter snittet i design-mode.

        Bruges hvor en enkelt dato skal vaere gyldig (fx den dag en etiket
        beregnes for). Til bulk-filtrering: brug filter_dates.
        """
        self._set[kilde] += 1
        if not self.tillader(d):
            self._afskaaert_inc(kilde)
            raise AssertionError(
                f"DESIGN-MODE: {kilde} forsoegte at bruge {d}, som ligger efter "
                f"DESIGN_END={DESIGN_END}. Holdout er forseglet indtil fase 5."
            )

    def filter_dates(self, dates, kilde: str) -> list[date]:
        """Behold kun datoer der maa bruges. Taeller hvad der blev skaaret fra."""
        ud, n_af = [], 0
        for d in dates:
            self._set[kilde] += 1
            if self.tillader(d):
                ud.append(d)
            else:
                n_af += 1
        if n_af:
            self._afskaaret[kilde] += n_af
        return ud

    def _afskaaert_inc(self, kilde: str) -> None:
        self._afskaaret[kilde] += 1

    # ---------------------------------------------------------------
    def rapport(self) -> str:
        """Menneskelig opsummering — hoerer med i hver fase-rapport."""
        linjer = [f"Design-mode-vagt: mode={self._mode}, DESIGN_END={DESIGN_END}"]
        if self._mode != "design":
            linjer.append(f"  HOLDOUT AABEN — begrundelse: {self._grund}")
        if not self._afskaaret:
            linjer.append("  Ingen datoer skaaret fra (alle kilder ligger inden for snittet).")
        else:
            linjer.append("  Frasorteret efter DESIGN_END:")
            for k in sorted(self._afskaaret):
                linjer.append(f"    {k:<28} {self._afskaaret[k]:>6} af {self._set[k]:>6} datoer")
        return "\n".join(linjer)


# Én delt instans — importér denne, lav ikke nye.
guard = _Guard()
