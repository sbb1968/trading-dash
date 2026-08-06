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


# ═══════════════════════════════════════════════════════════════
#  Retning
# ═══════════════════════════════════════════════════════════════
# Strategien var oprindelig long-only: brud NED gennem nedre bånd armerede, og
# entry krævede at prisen begyndte at vende OP. Short er den nøjagtige spejling
# omkring gennemsnittet — brud OP gennem øvre bånd, entry når prisen begynder at
# vende NED.
#
# Retningen er en PARAMETER, ikke en kopi af koden. Duplikerede man de otte
# funktioner, ville de to sider divergere ved første ændring af én af dem, og
# forskellen ville være usynlig indtil en handel opførte sig forkert. Med
# `retning` kan de per konstruktion ikke komme ud af trit.
LONG  = 1
SHORT = -1


def is_break(z: float, entry_z: float, retning: int = LONG) -> bool:
    """Har en færdig 15m-close brudt UD gennem båndet i handelbar retning? → armér.

    long:  z ≤ −entry_z  (brud ned gennem nedre bånd — vi vil købe reversalen op)
    short: z ≥ +entry_z  (brud op gennem øvre bånd — vi vil sælge reversalen ned)
    """
    return z <= -entry_z if retning == LONG else z >= entry_z


def is_back_inside(z: float, entry_z: float, retning: int = LONG) -> bool:
    """
    Er prisen tilbage inde i båndet? → afarmér.

    Armeringen holder altså kun så længe udvidelsen består. Lukker 15m tilbage
    inde i båndet uden at vi nåede en entry, er begivenheden forbi — en reversal
    to timer senere er en ANDEN begivenhed og skal have sit eget brud.
    """
    return z > -entry_z if retning == LONG else z < entry_z


def is_break_below(z: float, entry_z: float) -> bool:
    """Long-brud. Bevares fordi backtesten og de eksisterende tests kalder den."""
    return is_break(z, entry_z, LONG)


# ═══════════════════════════════════════════════════════════════
#  Entry-bekræftelse
# ═══════════════════════════════════════════════════════════════

def two_bar_move_pct(bars5: list[dict], retning: int = LONG) -> Optional[float]:
    """
    Den samlede bevægelse i procent over de seneste TO 5m-bars — men kun hvis
    begge peger den vej reversalen skal gå. Ellers None.

    long:  begge grønne (close > open), stigning fra b1.open til b2.close
    short: begge røde   (close < open), fald    fra b1.open til b2.close

    Returneres ALTID som et positivt tal, så kravet (`rise_pct`) kan
    sammenlignes ens for begge retninger. En short der falder 0,5 % giver 0,5 —
    ikke −0,5. Ellers ville `>= rise_pct` betyde noget forskelligt på de to sider.

    Måles fra den FØRSTE bars open til den ANDEN bars close, altså hele
    bevægelsen over de ti minutter — ikke summen af de to kroppe hver for sig.
    En kort modsatrettet pause imellem ville ellers kunne skjule sig.
    """
    if len(bars5) < 2:
        return None
    b1, b2 = bars5[-2], bars5[-1]
    if retning == LONG:
        peger_rigtigt = b2["close"] > b2["open"] and b1["close"] > b1["open"]
    else:
        peger_rigtigt = b2["close"] < b2["open"] and b1["close"] < b1["open"]
    if not peger_rigtigt:
        return None
    if b1["open"] <= 0:
        return None
    return (b2["close"] - b1["open"]) / b1["open"] * 100.0 * retning


def two_green_rise_pct(bars5: list[dict]) -> Optional[float]:
    """Long-varianten. Bevares fordi backtesten og de eksisterende tests kalder den."""
    return two_bar_move_pct(bars5, LONG)


def check_entry(
    bars5:     list[dict],
    macd_now:  Optional[float],
    macd_prev: Optional[float],
    cmf_now:   Optional[float],
    cmf_prev:  Optional[float],
    cfg:       UsReversionVariantConfig,
    retning:   int = LONG,
) -> tuple[bool, dict]:
    """
    Er alle tre bekræftelses-kriterier opfyldt? Forudsætter at kalderen
    allerede har konstateret at siden er ARMERET.

    Spejlet for short — samme tre krav, modsat vej:
        long   to grønne 5m · MACD stigende · CMF stigende
        short  to røde   5m · MACD faldende · CMF faldende

    require_cmf_positive spejles til require_cmf_negativ for short: pengestrømmen
    skal ikke bare blive mindre positiv, men faktisk være negativ. Det er den
    samme påstand set fra den anden side.

    Returnerer (ok, detaljer). `detaljer` rummer hvert delkriterium hver for
    sig, så en afvisning kan forklares i loggen og gemmes i forensikken —
    "hvorfor tog den ikke den handel?" skal kunne besvares bagefter.

    macd_*: MACD-LINJEN (EMA12−EMA26) på 5m, nu og forrige bar.
    cmf_*:  CMF på 15m, nu og forrige bar. Bemærk den grovere tidsramme —
            flere 5m-triggere i samme 15m-vindue får derfor samme svar her.
    """
    rise = two_bar_move_pct(bars5, retning)

    ok_rise = rise is not None and rise >= cfg.rise_pct
    # `retning *` vender sammenligningen: for short skal MACD og CMF FALDE.
    ok_macd = (macd_now is not None and macd_prev is not None
               and retning * (macd_now - macd_prev) > 0)
    ok_cmf = (cmf_now is not None and cmf_prev is not None
              and retning * (cmf_now - cmf_prev) > 0)
    if ok_cmf and cfg.require_cmf_positive:
        ok_cmf = (cmf_now > 0) if retning == LONG else (cmf_now < 0)

    detaljer = {
        "retning":    "long" if retning == LONG else "short",
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

def stop_price(entry_price: float, cfg: UsReversionVariantConfig,
               retning: int = LONG) -> float:
    """Stop-niveau i pris. long: entry × (1 − stop_pct/100). short: × (1 + …)."""
    return entry_price * (1.0 - retning * cfg.stop_pct / 100.0)


def trail_price(ekstrem_close: float, cfg: UsReversionVariantConfig,
                retning: int = LONG) -> float:
    """Trailing-niveau i pris, målt fra det gunstigste close siden entry.

    long:  højeste close × (1 − trail_pct/100)
    short: laveste close × (1 + trail_pct/100)
    """
    return ekstrem_close * (1.0 - retning * cfg.trail_pct / 100.0)


def check_exit(
    entry_price:   float,
    ekstrem_close: float,
    last_close:    float,
    z:             Optional[float],
    cfg:           UsReversionVariantConfig,
    retning:       int = LONG,
) -> Optional[str]:
    """
    Exit-årsag for en åben position, eller None. Vurderes på 5m-CLOSE.

    Rækkefølgen er fast (config.EXIT_PRECEDENCE) så en handel altid får samme
    årsag uanset hvad der ramte samtidig:
      'stop'    — prisen er gået stop_pct MOD os
      'upper_z' — reversionen er kørt hele vejen igennem til det MODSATTE bånd
                  (kun med exit_at_upper_z)
      'trail'   — prisen har givet trail_pct tilbage fra det gunstigste close

    'upper_z' beholder sit navn for begge retninger, så exit_reason kan
    sammenlignes på tværs af historikken. For en short betyder det det NEDRE
    bånd — altså stadig "reversionen er fuldført", som navnet står for.

    z sendes som None når der endnu ikke findes en gyldig 15m-z; niveauet
    springes da over frem for at gætte.

    Tvangsluk ved sessions-slut ligger IKKE her — det er wrapperens ansvar og
    slår alt andet.
    """
    # retning * (pris − niveau) ≤ 0 betyder "prisen er nået til eller forbi
    # niveauet i ugunstig retning" — for begge sider.
    if retning * (last_close - stop_price(entry_price, cfg, retning)) <= 0:
        return "stop"
    if cfg.exit_at_upper_z and z is not None and retning * (z - retning * cfg.entry_z) >= 0:
        return "upper_z"
    if retning * (last_close - trail_price(ekstrem_close, cfg, retning)) <= 0:
        return "trail"
    return None


def update_ekstrem(ekstrem_close: float, last_close: float,
                   retning: int = LONG) -> float:
    """
    Opdatér det GUNSTIGSTE close siden entry — højeste for long, laveste for short.

    Referencen starter ved ENTRY-prisen, ikke ved den første close efter entry.
    Går prisen imod os med det samme, måles trailingen derfor fra entry — ellers
    ville et øjeblikkeligt dyk nulstille referencen til et ugunstigere niveau og
    gøre trailing-stoppet meningsløst løst.
    """
    if retning == LONG:
        return last_close if last_close > ekstrem_close else ekstrem_close
    return last_close if last_close < ekstrem_close else ekstrem_close


def update_hh(hh_close: float, last_close: float) -> float:
    """Long-varianten. Bevares fordi backtesten og de eksisterende tests kalder den."""
    return update_ekstrem(hh_close, last_close, LONG)
