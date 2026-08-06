"""
test_us_reversion_short.py — er short en EKSAKT spejling af long?
═══════════════════════════════════════════════════════════════════════════════════
US-reversion var long-only: brud NED gennem nedre baand armerede, entry naar prisen
begyndte at vende OP. Short er den noejagtige spejling omkring gennemsnittet.

"Det virker ogsaa for short" er ikke godt nok. Paastanden der skal afproeves er
STRENGERE: spejler man input omkring gennemsnittet, skal short give praecis samme
svar som long gav. Ellers er der en asymmetri — og en asymmetri ingen har valgt, er
en fejl uanset hvor rimelig den ser ud.

Derfor koerer de fleste tests her som PAR: samme situation, spejlet, samme facit.

    python test_us_reversion_short.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from strategies.us_reversion import rule as r
from strategies.us_reversion.config import VARIANTS, UsReversionVariantConfig

BASE = VARIANTS["base"]
FEJL: list[str] = []


def kraev(b, hvad):
    print(f"  {'OK  ' if b else 'FEJL'} {hvad}")
    if not b:
        FEJL.append(hvad)


def bar(o, c):
    return {"open": o, "close": c}


print("\n1. Armering — spejlet om gennemsnittet")
for z in (2.5, 2.0, 1.9, 0.0):
    kraev(r.is_break(-z, 2.0, r.LONG) == r.is_break(z, 2.0, r.SHORT),
          f"z=∓{z}: long og short armerer symmetrisk "
          f"({r.is_break(-z, 2.0, r.LONG)})")

print("\n2. Afarmering — ogsaa symmetrisk")
for z in (2.5, 1.9, 0.0):
    kraev(r.is_back_inside(-z, 2.0, r.LONG) == r.is_back_inside(z, 2.0, r.SHORT),
          f"z=∓{z}: afarmering symmetrisk ({r.is_back_inside(-z, 2.0, r.LONG)})")

print("\n3. Long armerer IKKE paa et brud op, og omvendt")
kraev(r.is_break(2.5, 2.0, r.LONG) is False, "long armerer ikke ved oevre baand")
kraev(r.is_break(-2.5, 2.0, r.SHORT) is False, "short armerer ikke ved nedre baand")

print("\n4. To-bars-bevaegelsen: begge skal pege samme vej")
op  = [bar(100.0, 100.2), bar(100.2, 100.5)]     # to groenne, +0,5 %
ned = [bar(100.0,  99.8), bar( 99.8,  99.5)]     # to roede,   -0,5 %
kraev(abs(r.two_bar_move_pct(op,  r.LONG)  - 0.5) < 1e-9,
      f"long: to groenne -> +0,5 % ({r.two_bar_move_pct(op, r.LONG):.4f})")
kraev(abs(r.two_bar_move_pct(ned, r.SHORT) - 0.5) < 1e-9,
      f"short: to roede -> +0,5 % som POSITIVT tal "
      f"({r.two_bar_move_pct(ned, r.SHORT):.4f})")
kraev(r.two_bar_move_pct(ned, r.LONG) is None, "long afviser to roede")
kraev(r.two_bar_move_pct(op,  r.SHORT) is None, "short afviser to groenne")
blandet = [bar(100.0, 100.2), bar(100.2, 100.0)]
kraev(r.two_bar_move_pct(blandet, r.LONG) is None
      and r.two_bar_move_pct(blandet, r.SHORT) is None,
      "én af hver -> ingen af siderne accepterer")

print("\n5. Bevaegelsen returneres POSITIVT for begge — ellers betyder kravet noget forskelligt")
kraev(r.two_bar_move_pct(ned, r.SHORT) > 0,
      "short-fald er positivt, saa >= rise_pct er samme krav paa begge sider")

print("\n6. Bekraeftelse: MACD og CMF skal FALDE for short")
ok_l, d_l = r.check_entry(bars5=op,  macd_now=2.0, macd_prev=1.0,
                          cmf_now=0.3, cmf_prev=0.1, cfg=BASE, retning=r.LONG)
ok_s, d_s = r.check_entry(bars5=ned, macd_now=1.0, macd_prev=2.0,
                          cmf_now=0.1, cmf_prev=0.3, cfg=BASE, retning=r.SHORT)
kraev(ok_l is True, "long accepterer stigende MACD+CMF")
kraev(ok_s is True, "short accepterer FALDENDE MACD+CMF")
kraev(d_s["retning"] == "short", "detaljerne siger hvilken retning der blev vurderet")

ok, _ = r.check_entry(bars5=ned, macd_now=2.0, macd_prev=1.0,
                      cmf_now=0.1, cmf_prev=0.3, cfg=BASE, retning=r.SHORT)
kraev(ok is False, "short afviser STIGENDE MACD (samme fejl som long m. faldende)")

print("\n7. require_cmf_positive spejles til 'negativ' for short")
streng = UsReversionVariantConfig(name="streng", require_cmf_positive=True)
ok_l, _ = r.check_entry(bars5=op, macd_now=2.0, macd_prev=1.0,
                        cmf_now=-0.05, cmf_prev=-0.2, cfg=streng, retning=r.LONG)
kraev(ok_l is False, "long: CMF stiger men er stadig negativ -> afvist")
ok_s, _ = r.check_entry(bars5=ned, macd_now=1.0, macd_prev=2.0,
                        cmf_now=0.05, cmf_prev=0.2, cfg=streng, retning=r.SHORT)
kraev(ok_s is False, "short: CMF falder men er stadig positiv -> afvist (spejlet)")
ok_s2, _ = r.check_entry(bars5=ned, macd_now=1.0, macd_prev=2.0,
                         cmf_now=-0.05, cmf_prev=0.2, cfg=streng, retning=r.SHORT)
kraev(ok_s2 is True, "short: CMF falder OG er negativ -> accepteret")

print("\n8. Stop ligger paa hver sin side af entry — lige langt vaek")
sl = r.stop_price(100.0, BASE, r.LONG)
ss = r.stop_price(100.0, BASE, r.SHORT)
kraev(sl < 100.0 < ss, f"long {sl:.4f} < entry < short {ss:.4f}")
kraev(abs((100.0 - sl) - (ss - 100.0)) < 1e-9, "praecis samme afstand fra entry")

print("\n9. Trailing maales fra det GUNSTIGSTE close")
kraev(r.update_ekstrem(100.0, 101.0, r.LONG) == 101.0, "long husker hoejeste")
kraev(r.update_ekstrem(100.0,  99.0, r.LONG) == 100.0, "long ignorerer lavere")
kraev(r.update_ekstrem(100.0,  99.0, r.SHORT) == 99.0, "short husker LAVESTE")
kraev(r.update_ekstrem(100.0, 101.0, r.SHORT) == 100.0, "short ignorerer hoejere")

print("\n10. Exit — samme situation spejlet giver samme aarsag")
z_ex = UsReversionVariantConfig(name="z", exit_at_upper_z=True)
par = [
    ("stop",    (100.0,  100.0,  99.80,  0.0), (100.0, 100.0, 100.20,  0.0)),
    ("trail",   (100.0,  101.0, 100.85,  0.0), (100.0,  99.0,  99.15,  0.0)),
    ("upper_z", (100.0,  100.0, 100.00,  2.5), (100.0, 100.0, 100.00, -2.5)),
    (None,      (100.0,  100.0, 100.00,  0.0), (100.0, 100.0, 100.00,  0.0)),
]
for forventet, (e, x, c, z), (e2, x2, c2, z2) in par:
    a = r.check_exit(e,  x,  c,  z,  z_ex, r.LONG)
    b = r.check_exit(e2, x2, c2, z2, z_ex, r.SHORT)
    kraev(a == forventet and b == forventet,
          f"{str(forventet):8s}: long={a} short={b}")

print("\n11. 'upper_z' for en short betyder det NEDRE baand")
# Navnet beholdes for begge, saa exit_reason kan sammenlignes paa tvaers af
# historikken — det staar for "reversionen er fuldfoert", ikke for et verdenshjoerne.
kraev(r.check_exit(100.0, 100.0, 100.0, -2.5, z_ex, r.SHORT) == "upper_z",
      "short exit'er naar z naar -entry_z")
kraev(r.check_exit(100.0, 100.0, 100.0, 2.5, z_ex, r.SHORT) is None,
      "short exit'er IKKE naar z naar +entry_z (det er dens egen side)")

print("\n12. Gamle long-navne virker uaendret (backtesten kalder dem)")
kraev(r.is_break_below(-2.5, 2.0) is True, "is_break_below")
kraev(r.two_green_rise_pct(op) == r.two_bar_move_pct(op, r.LONG), "two_green_rise_pct")
kraev(r.update_hh(100.0, 101.0) == 101.0, "update_hh")
kraev(r.check_exit(100.0, 100.0, 99.8, 0.0, BASE) == "stop",
      "check_exit uden retning = long, som foer")

print("\n" + "=" * 70)
if FEJL:
    print(f"{len(FEJL)} FEJL:")
    for f in FEJL:
        print("  -", f)
    sys.exit(1)
print("Alt groent.")
