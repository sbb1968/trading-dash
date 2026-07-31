"""
test_indicators_cmf.py — CMF i live-motoren skal matche pandas-udgaven eksakt.

indicators.cmf() (liste-baseret, live) og store_bevaegelser_lib.cmf() (pandas,
analyse/backtest) beregner det samme tal. Divergerer de, ville en strategi
handle paa ét CMF og blive backtestet paa et andet — praecis den slags
stille uenighed rule.py-moenstret findes for at forhindre.

Koeres med:  python test_indicators_cmf.py

Placering: C:\\Projects\\trading_dash\\backend\\test_indicators_cmf.py
"""

import sys

_ok = True


def check(name: str, passed: bool, got=None) -> None:
    global _ok
    _ok = _ok and bool(passed)
    print(f"  {'PASS' if passed else 'FAIL'}  {name}" + ("" if passed else f"  {got!r}"))


def _bars(seed: int, n: int) -> list[dict]:
    """Deterministiske syntetiske bars — ingen RNG, saa testen er reproducerbar."""
    out = []
    px = 100.0
    for i in range(n):
        # Simpel deterministisk vandring med varierende range og volumen.
        px += ((seed * 7 + i * 13) % 11 - 5) * 0.1
        rng = 0.2 + ((seed + i * 3) % 7) * 0.05
        low = px - rng / 2
        high = px + rng / 2
        # close placeres skiftevis hoejt/lavt i range -> mult skifter fortegn
        frac = ((seed + i * 5) % 10) / 9.0
        close = low + frac * (high - low)
        out.append({
            "open": px, "high": high, "low": low, "close": close,
            "volume": 100.0 + ((seed * 3 + i * 17) % 400),
        })
    return out


def section_A():
    print("\nSektion A - live CMF matcher pandas-udgaven")
    import pandas as pd
    from indicators import cmf as cmf_live
    from store_bevaegelser_lib import cmf as cmf_pd

    for seed in (1, 7, 42):
        for length in (10, 20, 34):
            bars = _bars(seed, length + 15)
            live = cmf_live(bars, length)
            pdv = cmf_pd(
                pd.Series([b["high"] for b in bars]),
                pd.Series([b["low"] for b in bars]),
                pd.Series([b["close"] for b in bars]),
                pd.Series([b["volume"] for b in bars]),
                length,
            ).iloc[-1]
            close = live is not None and abs(live - float(pdv)) < 1e-12
            check(f"A seed={seed} len={length}: {live!r} vs {float(pdv)!r}", close,
                  (live, float(pdv)))


def section_B():
    print("\nSektion B - randtilfaelde")
    from indicators import cmf as cmf_live

    check("B1 for faa bars -> None", cmf_live(_bars(1, 5), 20) is None)

    # Nul-range bars: mult udefineret -> 0 (Pine-konvention), ikke division-med-nul
    flat = [{"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0,
             "volume": 50.0} for _ in range(20)]
    check("B2 nul-range bars -> 0.0 (ikke crash)", cmf_live(flat, 20) == 0.0,
          cmf_live(flat, 20))

    # Ingen volumen i vinduet -> forholdet er udefineret, IKKE 0
    novol = [{"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5,
              "volume": 0.0} for _ in range(20)]
    check("B3 nul volumen -> None (udefineret, ikke 0)", cmf_live(novol, 20) is None,
          cmf_live(novol, 20))

    # Alle closes i toppen af range -> ren akkumulation -> CMF = +1
    top = [{"open": 100.0, "high": 101.0, "low": 99.0, "close": 101.0,
            "volume": 10.0} for _ in range(20)]
    check("B4 close = high hele vejen -> +1.0", abs(cmf_live(top, 20) - 1.0) < 1e-12,
          cmf_live(top, 20))

    # Alle closes i bunden -> ren distribution -> CMF = -1
    bot = [{"open": 100.0, "high": 101.0, "low": 99.0, "close": 99.0,
            "volume": 10.0} for _ in range(20)]
    check("B5 close = low hele vejen -> -1.0", abs(cmf_live(bot, 20) + 1.0) < 1e-12,
          cmf_live(bot, 20))


def section_C():
    print("\nSektion C - compute_all eksponerer cmf_20")
    from indicators import compute_all
    row = compute_all(_bars(3, 40))
    check("C1 cmf_20 findes og er et tal", isinstance(row.get("cmf_20"), float),
          row.get("cmf_20"))
    check("C2 tom bars-liste -> cmf_20 = None", compute_all([])["cmf_20"] is None)


if __name__ == "__main__":
    print("Test: CMF (live vs pandas)")
    section_A()
    section_B()
    section_C()
    print("\nRESULTAT:", "ALLE OK" if _ok else "FEJL")
    sys.exit(0 if _ok else 1)
