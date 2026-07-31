"""
test_us_reversion_rule.py — adfaerds-laas paa US-reversions rene regel.

Daekker rule.py alene (ingen IBKR, ingen wrapper): baand/armering, de tre
entry-bekraeftelser hver for sig, exit-raekkefoelgen og HH-sporingen.

Koeres med:  python test_us_reversion_rule.py

Placering: C:\\Projects\\trading_dash\\backend\\test_us_reversion_rule.py
"""

import sys

from strategies.us_reversion import rule
from strategies.us_reversion.config import VARIANTS, UsReversionVariantConfig

_ok = True


def check(name: str, passed: bool, got=None) -> None:
    global _ok
    _ok = _ok and bool(passed)
    print(f"  {'PASS' if passed else 'FAIL'}  {name}" + ("" if passed else f"  {got!r}"))


BASE = VARIANTS["base"]


def bar(o, c, h=None, l=None, v=100.0):
    """5m-bar. high/low udfyldes automatisk saa de omslutter open/close."""
    return {"open": o, "close": c,
            "high": h if h is not None else max(o, c),
            "low":  l if l is not None else min(o, c),
            "volume": v}


# ═══════════════════════════════════════════════════════════════
def section_A():
    print("\nSektion A - baand og armering (15m)")

    # Konstruer en serie hvor sidste close ligger praecis 2 std under snittet.
    closes = [100.0] * 29 + [100.0]
    check("A1 nul spredning -> None (intet signal)", rule.compute_z(closes) is None)

    closes = [100.0, 102.0, 98.0, 101.0, 99.0] * 6
    zs = rule.compute_z(closes)
    check("A2 gyldig serie -> (z, std)", zs is not None and len(zs) == 2, zs)

    # Baand-niveauer skal ligge symmetrisk om snittet.
    b = rule.bands(closes, 2.0)
    ma, lo, hi = b
    check("A3 nedre baand under snit", lo < ma)
    check("A4 oevre baand over snit", hi > ma)
    check("A5 baandene er symmetriske", abs((ma - lo) - (hi - ma)) < 1e-12)

    # Armering
    check("A6 z = -2.0 armerer (graensen taeller med)", rule.is_break_below(-2.0, 2.0))
    check("A7 z = -2.5 armerer", rule.is_break_below(-2.5, 2.0))
    check("A8 z = -1.9 armerer IKKE", not rule.is_break_below(-1.9, 2.0))

    # Afarmering: tilbage inde i baandet
    check("A9 z = -1.9 er tilbage inde -> afarmer", rule.is_back_inside(-1.9, 2.0))
    check("A10 z = -2.0 er IKKE tilbage inde", not rule.is_back_inside(-2.0, 2.0))
    check("A11 armering og afarmering er gensidigt udelukkende",
          all(rule.is_break_below(z, 2.0) != rule.is_back_inside(z, 2.0)
              for z in (-3.0, -2.0, -1.99, 0.0, 2.0)))


# ═══════════════════════════════════════════════════════════════
def section_B():
    print("\nSektion B - kriterium (a): to groenne 5m-candles")

    # To groenne, samlet +0.20% (100.00 -> 100.20)
    bars = [bar(99.0, 99.5), bar(100.00, 100.10), bar(100.10, 100.20)]
    r = rule.two_green_rise_pct(bars)
    check("B1 to groenne -> maalt fra bar1.open til bar2.close",
          r is not None and abs(r - 0.20) < 1e-9, r)

    # Sidste bar roed -> ingen trigger
    bars = [bar(100.00, 100.10), bar(100.10, 100.00)]
    check("B2 sidste bar roed -> None", rule.two_green_rise_pct(bars) is None)

    # Foerste bar roed -> ingen trigger
    bars = [bar(100.10, 100.00), bar(100.00, 100.30)]
    check("B3 foerste bar roed -> None", rule.two_green_rise_pct(bars) is None)

    check("B4 kun én bar -> None", rule.two_green_rise_pct([bar(100.0, 100.1)]) is None)

    # Doji (close == open) er ikke groen
    bars = [bar(100.0, 100.0), bar(100.0, 100.3)]
    check("B5 doji taeller ikke som groen -> None", rule.two_green_rise_pct(bars) is None)


# ═══════════════════════════════════════════════════════════════
def section_C():
    print("\nSektion C - check_entry: alle tre kriterier")

    # Opfylder (a): 100.00 -> 100.20 = +0.20% >= 0.08%
    god = [bar(100.00, 100.10), bar(100.10, 100.20)]

    ok, d = rule.check_entry(god, macd_now=1.0, macd_prev=0.5,
                             cmf_now=0.10, cmf_prev=0.05, cfg=BASE)
    check("C1 alle tre opfyldt -> entry", ok, d)

    # (a) fejler: for lille stigning
    lille = [bar(100.000, 100.010), bar(100.010, 100.020)]   # +0.02%
    ok, d = rule.check_entry(lille, 1.0, 0.5, 0.10, 0.05, BASE)
    check("C2 for lille stigning -> ingen entry", not ok and not d["ok_rise"], d)

    # (b) fejler: MACD falder
    ok, d = rule.check_entry(god, macd_now=0.4, macd_prev=0.5,
                             cmf_now=0.10, cmf_prev=0.05, cfg=BASE)
    check("C3 MACD falder -> ingen entry", not ok and not d["ok_macd"], d)

    # (b) uafgjort: MACD uaendret taeller IKKE som stigende
    ok, d = rule.check_entry(god, 0.5, 0.5, 0.10, 0.05, BASE)
    check("C4 MACD uaendret -> ingen entry", not ok and not d["ok_macd"], d)

    # (c) fejler: CMF falder
    ok, d = rule.check_entry(god, 1.0, 0.5, cmf_now=0.04, cmf_prev=0.05, cfg=BASE)
    check("C5 CMF falder -> ingen entry", not ok and not d["ok_cmf"], d)

    # Manglende data (warmup) -> ingen entry, ingen crash
    ok, d = rule.check_entry(god, None, None, None, None, BASE)
    check("C6 manglende indikatorer -> ingen entry", not ok, d)

    # Detaljerne skal forklare afvisningen
    check("C7 detaljer rummer hvert delkriterium",
          {"ok_rise", "ok_macd", "ok_cmf", "rise_pct"} <= set(d))


def section_D():
    print("\nSektion D - varianten 'CMF positiv'")
    god = [bar(100.00, 100.10), bar(100.10, 100.20)]
    POS = VARIANTS["cmf_positiv"]

    # Stigende MEN negativ CMF: basis accepterer, varianten afviser.
    ok_base, _ = rule.check_entry(god, 1.0, 0.5, -0.02, -0.05, BASE)
    ok_pos, d = rule.check_entry(god, 1.0, 0.5, -0.02, -0.05, POS)
    check("D1 basis accepterer stigende men negativ CMF", ok_base)
    check("D2 varianten AFVISER stigende men negativ CMF", not ok_pos, d)

    # Stigende OG positiv: begge accepterer.
    ok_pos, _ = rule.check_entry(god, 1.0, 0.5, 0.06, 0.02, POS)
    check("D3 varianten accepterer stigende og positiv CMF", ok_pos)


# ═══════════════════════════════════════════════════════════════
def section_E():
    print("\nSektion E - exit og raekkefoelge")
    entry = 100.0

    # Ingen exit: prisen staar stille lige over stop og trailing
    check("E1 ingen betingelse ramt -> None",
          rule.check_exit(entry, hh_close=100.0, last_close=100.0, z=0.0, cfg=BASE) is None)

    # Stop: 0.12% under entry = 99.88
    check("E2 close paa stop-niveau -> 'stop'",
          rule.check_exit(entry, 100.0, 99.88, 0.0, BASE) == "stop")
    check("E3 close lige over stop -> ikke stop",
          rule.check_exit(entry, 100.0, 99.90, 0.0, BASE) != "stop")

    # Trailing: HH 101.0, 0.10% under = 100.899
    check("E4 close under trailing-niveau -> 'trail'",
          rule.check_exit(entry, 101.0, 100.80, 0.0, BASE) == "trail")
    check("E5 close over trailing-niveau -> None",
          rule.check_exit(entry, 101.0, 100.95, 0.0, BASE) is None)

    # upper_z er FRA i basis
    check("E6 basis ignorerer hoej z",
          rule.check_exit(entry, 100.0, 100.0, z=5.0, cfg=BASE) is None)

    # upper_z i sin variant
    UZ = VARIANTS["exit_upper_z"]
    check("E7 varianten lukker ved z >= +entry_z",
          rule.check_exit(entry, 100.0, 100.0, z=2.0, cfg=UZ) == "upper_z")
    check("E8 varianten lukker IKKE ved z under taersklen",
          rule.check_exit(entry, 100.0, 100.0, z=1.9, cfg=UZ) is None)
    check("E9 z = None (ingen 15m endnu) -> springes over, intet gaet",
          rule.check_exit(entry, 100.0, 100.0, z=None, cfg=UZ) is None)

    # RAEKKEFOELGE: stop slaar upper_z og trail naar alle rammer samtidig
    check("E10 stop gaar forud for trail",
          rule.check_exit(entry, 101.0, 99.80, 0.0, BASE) == "stop")
    check("E11 stop gaar forud for upper_z",
          rule.check_exit(entry, 100.0, 99.80, z=5.0, cfg=UZ) == "stop")
    # upper_z gaar forud for trail
    check("E12 upper_z gaar forud for trail",
          rule.check_exit(entry, 101.0, 100.80, z=5.0, cfg=UZ) == "upper_z")


def section_F():
    print("\nSektion F - HH-sporing")
    check("F1 hoejere close loefter HH", rule.update_hh(100.0, 100.5) == 100.5)
    check("F2 lavere close aendrer ikke HH", rule.update_hh(100.5, 100.0) == 100.5)
    check("F3 lige close aendrer ikke HH", rule.update_hh(100.5, 100.5) == 100.5)

    # HH starter ved entry: et oejeblikkeligt dyk maa ikke saenke referencen
    hh = 100.0                      # = entry
    for c in (99.95, 99.90, 99.92):
        hh = rule.update_hh(hh, c)
    check("F4 dyk lige efter entry saenker ikke HH", hh == 100.0, hh)


def section_G():
    print("\nSektion G - alle varianter er velformede")
    for key, cfg in VARIANTS.items():
        assert isinstance(cfg, UsReversionVariantConfig)
        ok = (cfg.entry_z > 0 and cfg.rise_pct > 0
              and cfg.stop_pct > 0 and cfg.trail_pct > 0 and cfg.name)
        check(f"G {key}: positive parametre + navn", ok, cfg)


if __name__ == "__main__":
    print("Test: US-reversion (ren regel)")
    section_A()
    section_B()
    section_C()
    section_D()
    section_E()
    section_F()
    section_G()
    print("\nRESULTAT:", "ALLE OK" if _ok else "FEJL")
    sys.exit(0 if _ok else 1)
