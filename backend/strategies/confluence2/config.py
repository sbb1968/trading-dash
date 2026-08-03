"""
strategies/confluence2/config.py
─────────────────────────────────
Variant-konfigurationer for Konfluens 2 (impuls-strategi).

FORSKEL FRA KONFLUENS 1:
  K1 ventede på 4-af-6 BAGUDSKUENDE bekræftelser (trend, higher-low, VWAP) og
  handlede derfor EFTER en bevægelse var sket. K2 reagerer på selve IMPULSEN
  i realtid: en 1-min candle med volumen-spike + range-ekspansion + stærk
  grøn lukning. To obligatoriske impuls-kriterier + mindst N kontekst-kriterier.

TIMEFRAME: 1-min (bekræftet via analyse — fanger bevægelser 5-min midler væk,
  uden at drukne i støj på de testede data).

EXIT: fire arketyper testet i exit-analysen, alle her som valgbare varianter:
  A) impulse_low   — hold til prisen falder under impuls-candlens low (vidt stop)
  B) trail_hl      — trailing higher-low (viste sig for stram på 1-min — med som ref.)
  C) momentum      — exit på rød volumen-candle eller close < EMA(fast)
  D) target_r      — fast target ved target_r × R, med impuls-low som katastrofe-stop

Target-multiplum (target_r) og stop er JUSTERBARE her, så de kan sweepes i
backtest ligesom K1's varianter. Tallene nedenfor er STARTGÆT fra én dags
analyse — de SKAL kalibreres på en bred backtest før de betyder noget.

INGEN af disse værdier er valideret endnu. Dette er et udgangspunkt for test.
"""

from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class Confluence2VariantConfig:
    name: str

    # ── Entry: impuls-kriterier (obligatoriske) ────────────────
    vol_mult: float = 2.0          # volumen ≥ vol_mult × forrige bars volumen
    vol_ma_mult: float = 1.5       # OG volumen ≥ vol_ma_mult × glidende snit
    vol_ma_len: int = 20           # længde på volumen-snit
    body_mult: float = 1.5         # krop ≥ body_mult × glidende snit-krop
    body_ma_len: int = 10          # længde på krop-snit
    close_pos_min: float = 0.60    # close i øverste (1-close_pos_min) af range

    # ── Entry: kontekst-filtre (mindst context_threshold af dem) ──
    context_threshold: int = 2     # hvor mange af de 4 kontekst-kriterier kræves
    ema_fast_len: int = 9          # EMA til "ikke overudvidet" + momentum-exit
    ema_slow_len: int = 20         # langsommere EMA til trend-kontekst
    rsi_len: int = 14
    rsi_min: float = 50.0          # RSI-momentum-vindue: ikke dødt...
    rsi_max: float = 78.0          # ...men heller ikke udmattet
    overext_atr_mult: float = 2.5  # afvis hvis close > ema_fast + dette × ATR (for sent)
    atr_len: int = 14

    # ── Exit ───────────────────────────────────────────────────
    exit_mode: str = "target_r"    # "impulse_low" | "trail_hl" | "momentum" | "target_r"
    target_r: float = 2.0          # for target_r: target = entry + target_r × R
    catastrophe_stop: bool = True  # impuls-low som hård bund (alle modes undtagen rene impulse_low)
    breakeven_r: float | None = None  # hvis sat: flyt stop til entry når high ≥ entry + breakeven_r × R (kun impulse_low)
    confirm_next_bar: bool = False  # hvis True: fyld kun pending entry hvis næste bars open ≥ impuls-close (følge-igennem)

    # ── Rammer ─────────────────────────────────────────────────
    entry_cutoff_hhmm: tuple[int, int] = (15, 0)   # ingen nye entries efter dette (ET)
    # Rykket 15:45 -> 15:30 den 3/8-2026 (Soeren): ALLE US-strategier skal lukke
    # en halv time foer markedet, saa et genforsoeg naar at ske mens der stadig er
    # likviditet. Var 15:45 for at matche K1/ORB. Bemaerk konsekvensen i
    # impulse_low-modet UDEN trailing: dér er klokken den eneste udgang en vinder
    # har, saa de vindere faar nu 15 minutter mindre. Live-varianten koerer med
    # trailing take-profit slaaet til (T_trail_2_0), saa den rammes mindst.
    force_close_hhmm: tuple[int, int] = (15, 30)   # luk alt — 30 min foer 16:00
    min_warmup_bars: int = 25      # færre 1-min bars end dette → spring over

    # ── Trailing take-profit (tilføjet 1/8-2026) ───────────────
    # Samme mekanik som US-reversion: HH = HØJESTE CLOSE siden entry (starter ved
    # entry-prisen), og vi lukker når en close falder trail_pct under HH.
    #
    # Baggrunden er at exit_mode="impulse_low" IKKE har nogen take-profit. En
    # vinder har derfor kun én udgang: klokken 15:45. I juli 2026 gav det 30
    # session_close-exits med holdetider på 5-6 timer, mens alle 159 stop-exits
    # tabte. Strategien kunne kun tabe på stoppet og kun vinde på klokken.
    #
    # 0.0 = FRA. Alle eksisterende varianter beholder den, så de er bit-for-bit
    # uændrede — kun de nye T_*-varianter nedenfor slår den til.
    trail_pct: float = 0.0

    # ── Stop-eksperimenter (deep-dive 2026-06-12) ──────────────
    # Begge default 0.0 = FRA → A_impulse_low og alle eksisterende varianter er
    # fuldstændig uændrede. Kun de nye A_minR_* / A_atrfloor_*-varianter sætter dem.
    min_r_pct: float = 0.0            # >0: afvis entry hvis (entry − impuls_low)/entry < dette
    stop_atr_floor_mult: float = 0.0  # >0: gulv stoppet ved entry − dette × ATR (udvid tætte stops)


# ── Varianter: de fire exit-arketyper fra analysen ───────────────
# Entry-parametrene er ens på tværs; KUN exit varierer, så vi isolerer
# exit-effekten i backtest (samme tankegang som K1's trail-varianter).
VARIANTS: dict[str, Confluence2VariantConfig] = {
    "A_impulse_low": Confluence2VariantConfig(
        name="A: Impuls-low stop (vidt, hold-til-stop)",
        exit_mode="impulse_low",
        catastrophe_stop=False,    # impuls-low ER stoppet her
    ),
    "A_be1r": Confluence2VariantConfig(
        name="A+BE: Impuls-low + breakeven efter +1R",
        exit_mode="impulse_low",
        catastrophe_stop=False,
        breakeven_r=1.0,
    ),
    "A_confirm": Confluence2VariantConfig(
        name="A+CONF: Impuls-low + bekræftelse på næste bar",
        exit_mode="impulse_low",
        catastrophe_stop=False,
        confirm_next_bar=True,
    ),
    "B_trail_hl": Confluence2VariantConfig(
        name="B: Trailing higher-low (1-min)",
        exit_mode="trail_hl",
    ),
    "C_momentum": Confluence2VariantConfig(
        name="C: Momentum-exit (rød+vol / <EMA fast)",
        exit_mode="momentum",
    ),
    "D_target_2r": Confluence2VariantConfig(
        name="D: Fast +2R target + impuls-low stop",
        exit_mode="target_r",
        target_r=2.0,
    ),
    # Ekstra target-multipla til sweep (target er den vigtigste parameter)
    "D_target_1_5r": Confluence2VariantConfig(
        name="D1.5: +1.5R target + impuls-low stop",
        exit_mode="target_r",
        target_r=1.5,
    ),
    "D_target_3r": Confluence2VariantConfig(
        name="D3: +3R target + impuls-low stop",
        exit_mode="target_r",
        target_r=3.0,
    ),

    # ── Deep-dive 2026-06-12: stop-gulv mod sub-støj-stop (alle = A + ét greb) ──
    # (1) min-R-gate: spring setups over hvor impuls-low ligger for tæt på entry.
    "A_minR_02": Confluence2VariantConfig(
        name="A+minR 0,2%: impuls-low, kræv R ≥ 0,2% af pris",
        exit_mode="impulse_low", catastrophe_stop=False, min_r_pct=0.002,
    ),
    "A_minR_03": Confluence2VariantConfig(
        name="A+minR 0,3%: impuls-low, kræv R ≥ 0,3% af pris",
        exit_mode="impulse_low", catastrophe_stop=False, min_r_pct=0.003,
    ),
    "A_minR_05": Confluence2VariantConfig(
        name="A+minR 0,5%: impuls-low, kræv R ≥ 0,5% af pris",
        exit_mode="impulse_low", catastrophe_stop=False, min_r_pct=0.005,
    ),
    "A_minR_075": Confluence2VariantConfig(
        name="A+minR 0,75%: impuls-low, kræv R ≥ 0,75% af pris",
        exit_mode="impulse_low", catastrophe_stop=False, min_r_pct=0.0075,
    ),
    "A_minR_10": Confluence2VariantConfig(
        name="A+minR 1,0%: impuls-low, kræv R ≥ 1,0% af pris",
        exit_mode="impulse_low", catastrophe_stop=False, min_r_pct=0.010,
    ),

    # (2) ATR-gulv: behold ALLE setups, men udvid et for tæt stop til k × ATR.
    "A_atrfloor_05": Confluence2VariantConfig(
        name="A+ATR-gulv 0,5×: stop = min(impuls-low, entry − 0,5×ATR)",
        exit_mode="impulse_low", catastrophe_stop=False, stop_atr_floor_mult=0.5,
    ),
    "A_atrfloor_10": Confluence2VariantConfig(
        name="A+ATR-gulv 1,0×: stop = min(impuls-low, entry − 1,0×ATR)",
        exit_mode="impulse_low", catastrophe_stop=False, stop_atr_floor_mult=1.0,
    ),
    "A_atrfloor_15": Confluence2VariantConfig(
        name="A+ATR-gulv 1,5×: stop = min(impuls-low, entry − 1,5×ATR)",
        exit_mode="impulse_low", catastrophe_stop=False, stop_atr_floor_mult=1.5,
    ),
    "A_atrfloor_20": Confluence2VariantConfig(
        name="A+ATR-gulv 2,0×: stop = min(impuls-low, entry − 2,0×ATR)",
        exit_mode="impulse_low", catastrophe_stop=False, stop_atr_floor_mult=2.0,
    ),

    # ── Udvidet ATR-gulv-grid (2026-06-15): PF steg monotont til 2,0× (gitterkant),
    # så vi udvider til konvergens for at finde PF-toppen, ikke en kant. ──
    "A_atrfloor_25": Confluence2VariantConfig(
        name="A+ATR-gulv 2,5x", exit_mode="impulse_low", catastrophe_stop=False, stop_atr_floor_mult=2.5,
    ),
    "A_atrfloor_30": Confluence2VariantConfig(
        name="A+ATR-gulv 3,0x", exit_mode="impulse_low", catastrophe_stop=False, stop_atr_floor_mult=3.0,
    ),
    "A_atrfloor_40": Confluence2VariantConfig(
        name="A+ATR-gulv 4,0x", exit_mode="impulse_low", catastrophe_stop=False, stop_atr_floor_mult=4.0,
    ),
    "A_atrfloor_50": Confluence2VariantConfig(
        name="A+ATR-gulv 5,0x", exit_mode="impulse_low", catastrophe_stop=False, stop_atr_floor_mult=5.0,
    ),

    # ── Trailing take-profit oven på live-varianten (Søren 1/8-2026) ──────
    # Identisk med A_atrfloor_20 bortset fra trail_pct. Uden trailing har
    # impulse_low-modet INGEN take-profit: en vinder kan kun komme ud kl. 15:45,
    # og juli 2026 viste konsekvensen — 30 session_close-exits med 5-6 timers
    # holdetid, mens alle 159 stop-exits tabte.
    #
    # Intervallet er valgt ud fra TAL 30/7 (entry 11,53 · top 12,44 · exit 12,37):
    #   0,5 % og 1,0 %  havde udløst ved tilbagefaldet kl. 20:37 (~12,32-12,38)
    #   1,5 %           ligger lige på kanten af samme tilbagefald
    #   2,0 % og 3,0 %  havde ikke udløst — positionen var kørt til lukketid
    # Det er ÉN handel, ikke et bevis. Sweep dem før du stoler på et af tallene.
    "T_trail_0_5": Confluence2VariantConfig(
        name="A+ATR-gulv 2,0x + trailing 0,5%", exit_mode="impulse_low",
        catastrophe_stop=False, stop_atr_floor_mult=2.0, trail_pct=0.5),
    "T_trail_1_0": Confluence2VariantConfig(
        name="A+ATR-gulv 2,0x + trailing 1,0%", exit_mode="impulse_low",
        catastrophe_stop=False, stop_atr_floor_mult=2.0, trail_pct=1.0),
    "T_trail_1_5": Confluence2VariantConfig(
        name="A+ATR-gulv 2,0x + trailing 1,5%", exit_mode="impulse_low",
        catastrophe_stop=False, stop_atr_floor_mult=2.0, trail_pct=1.5),
    "T_trail_2_0": Confluence2VariantConfig(
        name="A+ATR-gulv 2,0x + trailing 2,0%", exit_mode="impulse_low",
        catastrophe_stop=False, stop_atr_floor_mult=2.0, trail_pct=2.0),
    "T_trail_3_0": Confluence2VariantConfig(
        name="A+ATR-gulv 2,0x + trailing 3,0%", exit_mode="impulse_low",
        catastrophe_stop=False, stop_atr_floor_mult=2.0, trail_pct=3.0),
}

# Live-variant: A_atrfloor_20 — impuls-low-stop med ATR-gulv på 2,0× ATR. Gulvet
# udvider et for tæt stop (stop = min(impuls-low, entry − 2,0×ATR)), så normal støj
# ikke skraber os ud før bevægelsen udfolder sig. Valgt efter validering på
# anker-universet (historical_universe april+maj, 100% cache): slår tidligere
# live-variant A_impulse_low på portefølje-PF i BEGGE måneder ved 2¢ slippage
# (maj 2,44/2,57 vs 2,37/2,36 · april 2,07/1,84 vs 1,62/1,49), med bedre maxDD og
# højere win rate (44%/38% vs 38%/30%). Udvidet ATR-gulv-sweep (0,5×→5,0×)
# bekræftede 2,0× som den ROBUSTE værdi, ikke en højere: forbi 2,0× kommer
# PF-gevinsten i stigende grad fra at bære positioner til sessionsluk (exit-mix
# vipper mod hold-til-luk — stop binder kun ~50% ved 3,0×, 27-37% ved 5,0×), og
# aprils OOS-PF topper ~3,0× og FALDER ved 4,0× → uvalideret regime-risiko uden en
# bear/choppy måned. A_impulse_low + øvrige (B/C/D, A_minR_*, A_atrfloor 0,5–5,0×)
# bevares som dokumenterede backtest-referencer.
# SKIFTET 1/8-2026 til T_trail_2_0 — samme variant som ovenfor, men med en
# trailing take-profit på 2,0 %. Alt om stoppet (impuls-low + 2,0× ATR-gulv) og
# hele entry-siden er UÆNDRET; kun exit-siden får en ny udgang.
#
# Hvorfor 2,0 % og ikke tættere: det nye univers (small/mid cap, Volatility 1M
# mellem 5 og 50 %) bevæger sig markant mere end det gamle large cap-univers. En
# månedlig volatilitet på 15 % svarer groft til ~3,3 % daglig range, og en
# trailing på 1 % ville da udløse på almindelig intradag-støj frem for på en
# reel vending. 2,0 % er valgt som det mest konservative sted at begynde — det
# fjerner 5-timers-holdene uden at klippe vinderne på første tilbagefald.
#
# TALLET ER IKKE VALIDERET. Kør backtest_confluence2.py --sweep over T_trail_*
# før du fæster lid til det; 0,5–3,0 % ligger klar som varianter.
LIVE_VARIANT_KEY = "T_trail_2_0"

# ── Strategi-konstanter (ikke variant-specifikke) ─────────────
SESSION_START_HHMM = (9, 30)
SESSION_END_HHMM   = (16, 0)
MINTICK            = 0.01

# ── Universe-filtre — TradingView-screener ───────────────────────
# OMLAGT 1/8-2026 efter Søren og Ibens gennemgang af juli. Selve eksekveringen
# (impuls-kriterier og exit) er UÆNDRET — det var universet der var problemet.
#
# Hvad der ændrede sig og hvorfor:
#   Market cap  5B–1T  ->  300M–10B    fra large cap til small/mid cap. De store
#                                      navne bevæger sig for lidt til impuls-setuppet.
#   Børser      4      ->  2           AMEX og CBOE ude; kun NASDAQ + NYSE.
#   ATR%(1W)    fjernet                erstattet af de to nedenfor.
#   Volatility 1M      NY   5–50%      månedens udsving. Nedre grænse sikrer at der
#                                      ER bevægelse; øvre sorterer det ukontrollable fra.
#   Perf 1W            NY   > 6%       aktien skal allerede være i medvind.
#   type        stock+dr  ->  stock    ingen depotbeviser.
#   sortering   change  ->  Volatility.M   vi vil have de mest volatile øverst,
#                                      ikke dem der tilfældigvis steg mest i dag.
#
# Feltnavnene er verificeret mod TV's API 1/8-2026: 'Volatility.M' og 'Perf.W'
# returnerer tal. 'Perf.1W' returnerer None og ville have gjort filteret tavst
# virkningsløst — samme fælde som 'ATRP|1D' faldt i tidligere.
UNIVERSE_PRICE_MIN    = 5.0
UNIVERSE_PRICE_MAX    = 50.0
UNIVERSE_MIN_VOLUME   = 500_000               # 30-dages gennemsnitsvolumen
UNIVERSE_TOP_N        = 25
UNIVERSE_MKT_CAP_MIN  = 300_000_000           # 300 M — small cap-gulvet
UNIVERSE_MKT_CAP_MAX  = 10_000_000_000        # 10 B — mid cap-loftet
UNIVERSE_VOL_M_MIN    = 5.0                   # Volatility 1M, nedre (%)
UNIVERSE_VOL_M_MAX    = 50.0                  # Volatility 1M, øvre (%)
UNIVERSE_PERF_W_MIN   = 6.0                   # Perf 1W > 6 %
UNIVERSE_TYPES        = ["stock"]             # kun aktier — ingen depotbeviser
UNIVERSE_ORDER_BY     = "Volatility.M"        # sorteres faldende på denne
UNIVERSE_EXCHANGES    = ["NASDAQ", "NYSE"]