"""
strategies/us_reversion/rule.py
────────────────────────────────
Den rene beslutningslogik for US-reversion — ENESTE sandhedskilde.

Bruges identisk af live-wrapperen (algo_us_reversion.py) og backtesten
(us_reversion_backtest.py), så de aldrig kan divergere. Ingen IBKR, ingen
state, ingen I/O — kun matematik på lister og eksplicit medsendte værdier.

HVORFOR MACD/CMF SENDES IND FREM FOR AT BLIVE BEREGNET HER:
  strategies/-pakken er importren — den rører aldrig backend-roden, hvor den
  liste-baserede indicators.py bor. Reglen tager derfor de færdige tal som
  argumenter. Begge kaldere (live-wrapper og backtest) ligger i backend-roden
  og bruger SAMME indicators.py, så der er stadig kun én implementering af
  MACD og CMF i spil.

TILSTAND holdes af kalderen (armering, HH), ikke her. Funktionerne er rene, så
de kan testes uden at bygge en hel strategi op.
"""

from __future__ import annotations

from statistics import pstdev
from typing import Optional

from strategies.us_reversion.config import UsReversionVariantConfig


# ═══════════════════════════════════════════════════════════════
#  Bånd (15m)
# ═══════════════════════════════════════════════════════════════

def compute_z(closes: list[float]) -> Optional[tuple[float, float]]:
    """
    (z, std) over de seneste closes — eller None hvis std ≤ 0 eller seneste
    close ≤ 0 (intet brugbart signal).

    Population-std (pstdev), præcis som Europa-reversion og dens validerede
    backtest. De to strategier skal måle "udvidelse" ens, ellers kan de ikke
    sammenlignes bagefter.
    """
    if len(closes) < 2:
        return None
    ma = sum(closes) / len(closes)
    sd = pstdev(closes)
    if sd <= 0 or closes[-1] <= 0:
        return None
    return (closes[-1] - ma) / sd, sd


def bands(closes: list[float], entry_z: float) -> Optional[tuple[float, float, float]]:
    """
    (gennemsnit, nedre bånd, øvre bånd) i PRIS — til visning og forensik.

    Det øvre bånd udløser ingen entry (strategien er long-only), men tegnes i
    charten og gemmes i forensikken, så en handel kan aflæses i sin kontekst
    bagefter. Med exit_at_upper_z bruges det dog også som exit-niveau.
    """
    if len(closes) < 2:
        return None
    ma = sum(closes) / len(closes)
    sd = pstdev(closes)
    if sd <= 0:
        return None
    return ma, ma - entry_z * sd, ma + entry_z * sd


def is_break_below(z: float, entry_z: float) -> bool:
    """Har en færdig 15m-close brudt NED gennem det nedre bånd? → armér."""
    return z <= -entry_z


def is_back_inside(z: float, entry_z: float) -> bool:
    """
    Er prisen tilbage inde i båndet? → afarmér.

    Armeringen holder altså kun så længe udvidelsen består. Lukker 15m tilbage
    over det nedre bånd uden at vi nåede en entry, er begivenheden forbi — en
    reversal to timer senere er en ANDEN begivenhed og skal have sit eget brud.
    """
    return z > -entry_z


# ═══════════════════════════════════════════════════════════════
#  Entry-bekræftelse
# ═══════════════════════════════════════════════════════════════

def two_green_rise_pct(bars5: list[dict]) -> Optional[float]:
    """
    Den samlede stigning i procent over de seneste TO 5m-bars — men kun hvis
    begge er grønne (close > open). Ellers None.

    Måles fra den FØRSTE bars open til den ANDEN bars close, altså hele
    bevægelsen over de ti minutter — ikke summen af de to kroppe hver for sig.
    En kort rød pause imellem to grønne ville ellers kunne skjule sig.
    """
    if len(bars5) < 2:
        return None
    b1, b2 = bars5[-2], bars5[-1]
    if not (b2["close"] > b2["open"] and b1["close"] > b1["open"]):
        return None
    if b1["open"] <= 0:
        return None
    return (b2["close"] - b1["open"]) / b1["open"] * 100.0


def check_entry(
    bars5:     list[dict],
    macd_now:  Optional[float],
    macd_prev: Optional[float],
    cmf_now:   Optional[float],
    cmf_prev:  Optional[float],
    cfg:       UsReversionVariantConfig,
) -> tuple[bool, dict]:
    """
    Er alle tre bekræftelses-kriterier opfyldt? Forudsætter at kalderen
    allerede har konstateret at long er ARMERET.

    Returnerer (ok, detaljer). `detaljer` rummer hvert delkriterium hver for
    sig, så en afvisning kan forklares i loggen og gemmes i forensikken —
    "hvorfor tog den ikke den handel?" skal kunne besvares bagefter.

    macd_*: MACD-LINJEN (EMA12−EMA26) på 5m, nu og forrige bar.
    cmf_*:  CMF på 15m, nu og forrige bar. Bemærk den grovere tidsramme —
            flere 5m-triggere i samme 15m-vindue får derfor samme svar her.
    """
    rise = two_green_rise_pct(bars5)

    ok_rise = rise is not None and rise >= cfg.rise_pct
    ok_macd = (macd_now is not None and macd_prev is not None
               and macd_now > macd_prev)
    ok_cmf = (cmf_now is not None and cmf_prev is not None
              and cmf_now > cmf_prev)
    if ok_cmf and cfg.require_cmf_positive:
        ok_cmf = cmf_now > 0

    detaljer = {
        "rise_pct":   round(rise, 4) if rise is not None else None,
        "rise_krav":  cfg.rise_pct,
        "ok_rise":    ok_rise,
        "macd":       macd_now,
        "macd_prev":  macd_prev,
        "ok_macd":    ok_macd,
        "cmf":        cmf_now,
        "cmf_prev":   cmf_prev,
        "cmf_positiv_kraevet": cfg.require_cmf_positive,
        "ok_cmf":     ok_cmf,
    }
    return (ok_rise and ok_macd and ok_cmf), detaljer


# ═══════════════════════════════════════════════════════════════
#  Exit
# ═══════════════════════════════════════════════════════════════

def stop_price(entry_price: float, cfg: UsReversionVariantConfig) -> float:
    """Stop-niveau i pris: entry × (1 − stop_pct/100)."""
    return entry_price * (1.0 - cfg.stop_pct / 100.0)


def trail_price(hh_close: float, cfg: UsReversionVariantConfig) -> float:
    """Trailing-niveau i pris: højeste close siden entry × (1 − trail_pct/100)."""
    return hh_close * (1.0 - cfg.trail_pct / 100.0)


def check_exit(
    entry_price: float,
    hh_close:    float,
    last_close:  float,
    z:           Optional[float],
    cfg:         UsReversionVariantConfig,
) -> Optional[str]:
    """
    Exit-årsag for en åben LONG, eller None. Vurderes på 5m-CLOSE.

    Rækkefølgen er fast (config.EXIT_PRECEDENCE) så en handel altid får samme
    årsag uanset hvad der ramte samtidig:
      'stop'    — close ≤ entry × (1 − stop_pct)
      'upper_z' — 15m-z har nået +entry_z (kun med exit_at_upper_z)
      'trail'   — close ≤ HH × (1 − trail_pct)

    z sendes som None når der endnu ikke findes en gyldig 15m-z; upper_z
    springes da over frem for at gætte.

    Tvangsluk ved sessions-slut ligger IKKE her — det er wrapperens ansvar og
    slår alt andet.
    """
    if last_close <= stop_price(entry_price, cfg):
        return "stop"
    if cfg.exit_at_upper_z and z is not None and z >= cfg.entry_z:
        return "upper_z"
    if last_close <= trail_price(hh_close, cfg):
        return "trail"
    return None


def update_hh(hh_close: float, last_close: float) -> float:
    """
    Opdatér højeste close siden entry.

    HH starter ved ENTRY-prisen, ikke ved den første close efter entry. Falder
    prisen med det samme, måles trailingen derfor fra entry — ellers ville et
    øjeblikkeligt dyk nulstille referencen til et lavere niveau og gøre
    trailing-stoppet meningsløst løst.
    """
    return last_close if last_close > hh_close else hh_close
