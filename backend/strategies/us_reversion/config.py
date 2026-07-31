"""
strategies/us_reversion/config.py
──────────────────────────────────
Parametre for US-reversion — long-only mean-reversion på MES i den amerikanske
session (09:30–15:00 ET = 15:30–21:00 dansk).

FORSKEL FRA EUROPA-REVERSION (samme familie, andet dyr):
  EUREVERSION handler BEGGE veje og går ind PÅ selve båndbruddet — z≤−2 giver
  long med det samme. Den lever af at udvidelsen i sig selv er overdrevet.

  US-reversion venter i stedet på BEKRÆFTELSE. Båndbruddet på 15m ARMERER kun;
  entry kræver at prisen faktisk er begyndt at vende, målt på 5m. Det er en
  bevidst reaktion på at den amerikanske session TRENDER (jf. regime-analysen:
  PF 0,79 i høj-ER-tercilen mod 6,16 i lav-ER). At købe et bånd-brud blindt her
  er at stå foran toget; vi venter til det er stoppet.

TO TIDSRAMMER — den første strategi i huset der bruger begge samtidigt:
  15m: bånd (z), CMF-kriteriet, og Z-exit-varianten
   5m: de to grønne candles, MACD-kriteriet, stop og trailing

  CMF opdateres derfor kun hvert 15. minut. Flere 5m-triggere inden for samme
  15m-vindue får samme svar på CMF-kriteriet. Det er tilsigtet: kriteriet
  spørger om pengestrømmen på det GROVE billede vender, ikke om støjen på 5m.

VARIANTER frem for én låst config (modsat EUREVERSION): parametrene her er
ukalibrerede startgæt, og de skal sweepes i us_reversion_backtest.py før de
betyder noget. LIVE_VARIANT_KEY afgør hvad live-wrapperen kører.

Placering: C:\\Projects\\trading_dash\\backend\\strategies\\us_reversion\\config.py
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time as dtime


# ═══════════════════════════════════════════════════════════════
#  Session (ET) — faste rammer, ikke variant-styret
# ═══════════════════════════════════════════════════════════════
# Dansk tid er ET+6 i sommerhalvåret. ET er sandhedskilden (som i alle øvrige
# strategier), fordi børsen ikke flytter sig når Danmark skifter tid.
SESSION_START_ET = dtime(9, 30)    # 15:30 dansk — US-åbning, strategien armes
ENTRY_CUTOFF_ET  = dtime(14, 30)   # 20:30 dansk — ingen NYE entries efter dette
FORCE_CLOSE_ET   = dtime(15, 0)    # 21:00 dansk — tvangsluk af alt

# Sidste 5m-slot der regnes som "i sessionen" (spejler EUREVERSIONs
# LAST_SESSION_BAR_ET: force-close skal ramme FØR den sidste bar er væk).
LAST_SESSION_BAR_ET = dtime(14, 55)


# ═══════════════════════════════════════════════════════════════
#  Bars og indikatorer
# ═══════════════════════════════════════════════════════════════
BAR_BAND         = "15 mins"   # bånd/z + CMF + Z-exit
BAR_BAND_MINUTES = 15
BAR_TRIG         = "5 mins"    # entry-trigger + stop + trailing
BAR_TRIG_MINUTES = 5

LOOKBACK  = 30    # bars til MA/std på 15m — som EUREVERSION
CMF_LEN   = 20    # Chaikin Money Flow på 15m
MACD_FAST = 12    # MACD på 5m
MACD_SLOW = 26
MACD_SIG  = 9

# Hvor mange 5m-closes MACD beregnes på. FAST tal, og det er ikke kosmetik:
# en EMA huskes fra sit første element, så MACD på 40 bars og MACD på 320 bars
# giver FORSKELLIGE tal på samme sted. Målt på rigtige MES-data var forskellen
# op til 2,4 point, og de to vinduer var uenige om selve entry-kriteriet
# ("er MACD stigende?") på 4,1 % af barerne. Live og backtest SKAL derfor
# skære til nøjagtig samme længde, ellers handler de to forskellige strategier.
#
# 150 er valgt fordi EMA(26) er konvergeret et godt stykke inden da (~5×26),
# og fordi live-wrapperen altid har mindst så mange bars efter warmup.
MACD_WINDOW = 150

# Mindste antal bars før vi overhovedet evaluerer entry. Skal rumme MACD-vinduet
# PLUS én ekstra bar, så "forrige" MACD kan beregnes på lige så mange bars som
# den aktuelle — ellers sammenlignes 150 bars med 149.
MIN_WARMUP_TRIG = MACD_WINDOW + 1
MIN_WARMUP_BAND = LOOKBACK + CMF_LEN   # z OG CMF skal begge være definerede


# ═══════════════════════════════════════════════════════════════
#  Instrument og sizing
# ═══════════════════════════════════════════════════════════════
INSTRUMENTS   = ["MES"]        # KUN MES — ikke M2K (modsat EUREVERSION)
MULTIPLIER    = {"MES": 5.0}   # $ pr. prispoint (CME micro)
MAX_CONTRACTS = 1              # handler ALTID præcis 1 kontrakt


# ═══════════════════════════════════════════════════════════════
#  Varianter
# ═══════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class UsReversionVariantConfig:
    name: str

    # ── Bånd (15m) ─────────────────────────────────────────────
    # Båndene ligger ved ±entry_z. Long ARMERES når en færdig 15m-close
    # bryder ned gennem det NEDRE bånd (z ≤ −entry_z).
    entry_z: float = 2.0

    # ── Entry-bekræftelse ──────────────────────────────────────
    # (a) 5m: to på hinanden følgende grønne candles hvis samlede stigning,
    #     målt (close₂ − open₁)/open₁, er mindst dette i procent.
    rise_pct: float = 0.08
    # (b) 5m: MACD-linjen (EMA12−EMA26) højere end forrige 5m-bars.
    # (c) 15m: CMF højere end forrige 15m-bars.
    #     Med require_cmf_positive kræves DESUDEN at CMF > 0 — altså at
    #     pengestrømmen ikke bare bliver mindre negativ, men faktisk er positiv.
    require_cmf_positive: bool = False

    # ── Exit ───────────────────────────────────────────────────
    # Stop: entry × (1 − stop_pct/100). Vurderes på 5m-CLOSE.
    stop_pct: float = 0.12
    # Trailing: HH sporer højeste 5m-CLOSE siden entry (start = entry-pris).
    # Exit når close falder trail_pct under HH.
    trail_pct: float = 0.10
    # Ekstra exit: luk når 15m-z når det ØVRE bånd (+entry_z) — altså når
    # reversionen er kørt hele vejen igennem. Additiv: stop, trailing og
    # tvangsluk gælder uændret.
    exit_at_upper_z: bool = False


# Rækkefølgen af exit-tjek er FAST og dokumenteret, så en handel altid får den
# samme exit_reason uanset hvilken kombination der ramte samtidig:
#     1. stop        (kapitalbeskyttelse går forud for alt)
#     2. upper_z     (strategiens egen tese: reversionen er fuldført)
#     3. trail       (fallback-gevinstsikring)
# Tvangsluk (session_end) håndteres af wrapperen og slår alt andet.
EXIT_PRECEDENCE = ("stop", "upper_z", "trail")


VARIANTS: dict[str, UsReversionVariantConfig] = {
    # ── Basis: Sørens specifikation, ord for ord ───────────────
    "base": UsReversionVariantConfig(
        name="Basis: z±2, 0,08% stigning, 0,12% stop, 0,10% trailing",
    ),

    # ── De to varianter Søren bad eksplicit om ─────────────────
    "cmf_positiv": UsReversionVariantConfig(
        name="CMF positiv: kræver CMF > 0, ikke bare stigende",
        require_cmf_positive=True,
    ),
    "exit_upper_z": UsReversionVariantConfig(
        name="Z-exit: luk også når z når +entry_z (reversion fuldført)",
        exit_at_upper_z=True,
    ),

    # ── Sweep: båndets placering ───────────────────────────────
    # Lavere z = flere armeringer, svagere udvidelser. Højere = færre, kraftigere.
    "z1_5": UsReversionVariantConfig(name="Bånd z±1,5 (flere, svagere brud)", entry_z=1.5),
    "z2_5": UsReversionVariantConfig(name="Bånd z±2,5 (færre, kraftigere brud)", entry_z=2.5),
    "z3_0": UsReversionVariantConfig(name="Bånd z±3,0 (kun ekstremer)", entry_z=3.0),

    # ── Sweep: hvor meget bekræftelse kræver vi? ───────────────
    # For lavt = vi køber støj. For højt = vendingen er allerede sket uden os.
    "rise0_04": UsReversionVariantConfig(name="Stigningskrav 0,04% (løsere trigger)", rise_pct=0.04),
    "rise0_12": UsReversionVariantConfig(name="Stigningskrav 0,12% (strammere trigger)", rise_pct=0.12),
    "rise0_20": UsReversionVariantConfig(name="Stigningskrav 0,20% (kun kraftige vendinger)", rise_pct=0.20),

    # ── Sweep: stop ────────────────────────────────────────────
    "stop0_08": UsReversionVariantConfig(name="Stop 0,08% (stramt)", stop_pct=0.08),
    "stop0_20": UsReversionVariantConfig(name="Stop 0,20% (løst)", stop_pct=0.20),
    "stop0_30": UsReversionVariantConfig(name="Stop 0,30% (meget løst)", stop_pct=0.30),

    # ── Sweep: trailing ────────────────────────────────────────
    "trail0_05": UsReversionVariantConfig(name="Trailing 0,05% (tæt — tager hurtig gevinst)", trail_pct=0.05),
    "trail0_20": UsReversionVariantConfig(name="Trailing 0,20% (løs — lader den løbe)", trail_pct=0.20),
    "trail0_40": UsReversionVariantConfig(name="Trailing 0,40% (meget løs)", trail_pct=0.40),

    # ── Kombination af de to eksplicit ønskede ─────────────────
    "cmf_pos_exit_z": UsReversionVariantConfig(
        name="CMF positiv + Z-exit",
        require_cmf_positive=True,
        exit_at_upper_z=True,
    ),

    # ── Båndets placering OVEN PÅ rise0_12 (Søren 31/7-2026) ───
    # Det første sweep pegede på rise_pct=0,12 som den bedste af de tre
    # stigningskrav. Herfra drejes så på båndet alene, med alt andet holdt fast,
    # så effekten af z kan aflæses isoleret. Strategien er long-only og bruger
    # ikke Z-exit i denne gren, så entry_z er reelt KUN det nedre bånd her.
    #
    # Lavere z  = båndet ligger tættere på middel = flere, svagere udvidelser.
    # Højere z  = kun kraftigere udvidelser, men færre handler at bedømme på.
    "r12_z150": UsReversionVariantConfig(
        name="rise 0,12% + bånd z±1,50", rise_pct=0.12, entry_z=1.50),
    "r12_z175": UsReversionVariantConfig(
        name="rise 0,12% + bånd z±1,75", rise_pct=0.12, entry_z=1.75),
    "r12_z225": UsReversionVariantConfig(
        name="rise 0,12% + bånd z±2,25", rise_pct=0.12, entry_z=2.25),
    "r12_z250": UsReversionVariantConfig(
        name="rise 0,12% + bånd z±2,50", rise_pct=0.12, entry_z=2.50),
}

# Hvilken variant live-wrapperen kører. Skift denne ene streng for at ændre
# hvad der handles — backtesten sweeper alle nøgler i VARIANTS.
#
# rise0_12 valgt af Søren 31/7-2026 efter det første sweep (2 år MES, 2 bp):
#     base       n=124  PF 0,84 IS / 1,16 OOS   ← ingen edge
#     rise0_12   n=95   PF 1,00 IS / 1,32 OOS   ← valgt
#     rise0_20   n=32   PF 2,33 IS / 2,73 OOS   ← bedst, men kun 14 OOS-handler
# rise0_20 ser stærkest ud, men på et datagrundlag der er for tyndt til at
# betyde noget. rise0_12 har tre gange så mange handler og peger samme vej:
# et STRAMMERE krav til stigningen er bedre end Sørens oprindelige 0,08%.
# Retningen er sammenhængende i gridet, ikke et enkelt heldigt punkt.
LIVE_VARIANT_KEY = "rise0_12"
