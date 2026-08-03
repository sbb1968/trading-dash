"""
test_relstyrke_universe.py — universet Relativ Styrke rangerer i
─────────────────────────────────────────────────────────────────
Relativ Styrke er projektets eneste spor med en PRAEREGISTRERET dom skrevet foer
resultaterne. Universet er derfor ikke en fri parameter man tuner — det skal
matche den population sporet bestod sin test paa.

Frem til 3/8-2026 gjorde det ikke det. Live hentede via fetch_tv_top_gainers og
afveg paa to maader der begge betyder noget for en TVAERSNITLIG strategi:

  1. Spredningen var 4x backtestens (early_rs std 5,85 % mod 1,92 %).
  2. Live forfiltrerede paa MOMENTUM (dagens stoerste stigninger) — samme akse
     som early_rs selv maaler, hvilket afkorter rangordningen.

Testen laaser at det ikke sker igen. Den rammer ikke netvaerket.

Kør i backend-mappen:  python test_relstyrke_universe.py
"""

import ast
import inspect
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

import algo_relstyrke as R


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        raise SystemExit(1)


def scan_kwargs():
    """De kwargs _scan_universe FAKTISK sender til den delte screener.

    Laeses med ast, ikke med tekstsoegning — ellers ville en kommentar der naevner
    'perf_w_min' faa testen til at tro at parameteren sendes.
    """
    src = inspect.getsource(R.RelStyrkeLive._scan_universe)
    tree = ast.parse(src.strip())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and \
                getattr(node.func, "id", "") == "build_volatility_universe_rows":
            return {k.arg for k in node.keywords}
    return set()


print("\nSektion A — ingen forfiltrering paa samme akse som scoren")
kw = scan_kwargs()
check("A0 _scan_universe kalder den delte volatilitets-screener", bool(kw), kw)

# DEN VIGTIGSTE LAAS. early_rs ER rangeringen; forfiltrerer man paa ugens afkast,
# skaerer man i det tvaersnit scoren skal maale, og selection alpha forsvinder.
# K2 og BuyTheDip bruger perf_w_min med god grund — her ville det vaere skadeligt.
check("A1 perf_w_min sendes IKKE (ville forfiltrere paa momentum)",
      "perf_w_min" not in kw, kw)
check("A2 REQUIRE_ALL_GREEN er slaaet fra (samme grund)",
      R.REQUIRE_ALL_GREEN is False, R.REQUIRE_ALL_GREEN)

# Rangering paa volatilitet, ikke paa dagsaendring. "change" ville vaere
# top-gainer-adfaerden vi netop kom vaek fra.
check("A3 order_by sendes eksplicit", "order_by" in kw, kw)
check("A4 der rangeres paa Volatility.M — IKKE paa 'change'",
      R.UNIVERSE_ORDER_BY == "Volatility.M", R.UNIVERSE_ORDER_BY)

print("\nSektion B — populationen matcher den validerede")
check("B1 market cap-gulv = small cap (300 mio.)",
      R.UNIVERSE_MKT_CAP_MIN == 300_000_000, R.UNIVERSE_MKT_CAP_MIN)
check("B2 market cap-loft = mid cap (10 mia.)",
      R.UNIVERSE_MKT_CAP_MAX == 10_000_000_000, R.UNIVERSE_MKT_CAP_MAX)
check("B3 market cap sendes med i scanet",
      {"mkt_cap_min", "mkt_cap_max"} <= kw, kw)
check("B4 prisbaand $5-50 (som rekonstruktionen)",
      (R.UNIVERSE_PRICE_MIN, R.UNIVERSE_PRICE_MAX) == (5.0, 50.0),
      (R.UNIVERSE_PRICE_MIN, R.UNIVERSE_PRICE_MAX))
check("B5 kun 'stock' — ingen depotbeviser",
      R.UNIVERSE_TYPES == ["stock"], R.UNIVERSE_TYPES)
check("B6 boerser = NASDAQ + NYSE (ingen AMEX/CBOE)",
      R.UNIVERSE_EXCHANGES == ["NASDAQ", "NYSE"], R.UNIVERSE_EXCHANGES)
check("B7 likviditetsgulv bevaret (500k)",
      R.UNIVERSE_MIN_VOLUME == 500_000, R.UNIVERSE_MIN_VOLUME)

print("\nSektion C — den gamle kilde er faktisk vaek")
src = inspect.getsource(R.RelStyrkeLive._scan_universe)
check("C1 fetch_tv_top_gainers kaldes ikke laengere",
      "fetch_tv_top_gainers(" not in src)
check("C2 tvaersnittet er stadig top-25", R.UNIVERSE_TOP_N == 25, R.UNIVERSE_TOP_N)

print("\nSektion D — beslutnings-parametre er URØRT (kun universet er aendret)")
# Hvis nogen senere ogsaa begynder at skrue paa T/K/score, er det ikke laengere
# "fjern en utestet afvigelse" — saa er det et nyt sweep, og praeregistreringen
# forbyder det udtrykkeligt.
check("D1 score = early_rs", R.SCORE == "early_rs", R.SCORE)
check("D2 TOP_K = 3", R.TOP_K == 3, R.TOP_K)
check("D3 beslutning fyrer 09:46 (efter 09:45-baren lukker)",
      R.DECISION_FIRE_ET.strftime("%H:%M") == "09:46", R.DECISION_FIRE_ET)

print("\nALLE TESTS BESTÅET ✓")
