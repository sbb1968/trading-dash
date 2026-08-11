"""
ibkr_client_ids.py — ét register over IBKR client-id'er
════════════════════════════════════════════════════════════════════════════════
To API-forbindelser med samme clientId mod samme TWS giver en konflikt der ligner
alt muligt andet: en høst der mister forbindelsen, en genstart der ikke vil tage,
en dag hvor data mangler uden grund. Den fejler ikke højlydt — den svarer forkert
og sender fejlsøgningen et andet sted hen.

⚠ HVAD DER VAR GALT. Backenden trak `clientId = random.randint(10, 99)` ved HVER
forbindelse, mens femten faste id'er lå i netop det interval:

    10 11 12 15 16 28 29 46 47 48 50 51 52 55 76

Det er 15 af 90 mulige værdier — omkring **17 % kollisionsrisiko pr.
forbindelse**. Backenden genforbinder gentagne gange gennem en handelsdag, så
sandsynligheden for at det sker på et eller andet tidspunkt nærmer sig sikkerhed.
Det har efter alt at dømme allerede kostet os kørsler vi tilskrev noget andet.

⚠ OG TO SCRIPTS DELTE ID MED HINANDEN. Registret afslørede det, ikke en fejlsøgning:
    46  nkd_density_check + nkd_harvest_15min
    47  asian_harvest_1min + nikkei_harvest_1min + regime_data_depth_probe

────────────────────────────────────────────────────────────────────────────────
REGLERNE

  1-99      scripts og høst-jobs, ét id pr. proces
  200-209   RESERVERET til backenden. Ligger langt uden for scripts' interval,
            så et nyt script ikke kan snuble ind i det ved et uheld.

Ingen tilfældige trækninger. Nyt script → tilføj en linje her først.

`kontroller()` køres ved import og kaster på dubletter. Registret håndhæver altså
sig selv frem for at være en liste nogen skal huske at læse.
"""
from __future__ import annotations


class KlientIdFejl(Exception):
    pass


# ── Backendens reserverede blok ─────────────────────────────────────────────
# Backenden har én delt IBKR-forbindelse ad gangen (strategy_manager.ibkr_conn).
# Blokken har plads til flere, fordi paper og live på sigt skal kunne køre
# samtidig mod hver sin Gateway — se ibkr_session_rapport.md.
BACKEND = 200
# Den lokale ORDRE-forbindelse (konto 2). Koerer paa SAMME maskine som backenden,
# saa en kollision mellem de to ville vaere reel. Se ordre_forbindelse.py.
ORDRE = 201
BACKEND_BLOK = range(200, 210)

# ── Scripts og høst-jobs ────────────────────────────────────────────────────
# Ét id pr. fil. Køres to af dem samtidig med samme id, kolliderer de.
SCRIPTS: dict[str, int] = {
    "test_feed.py":                   4,
    "ibkr_session_probe.py":          5,
    "flatten_alt.py":                 8,
    "download_daily_ibkr.py":        10,
    "fetch_universe.py":             11,
    "download_intraday_ibkr.py":     12,
    "backtest_confluence2.py":       15,
    "build_historical_universe.py":  16,
    "catalyst_harvest.py":           28,
    "velocity_universe_harvest.py":  29,
    "nkd_density_check.py":          45,   # var 46 — delte med nkd_harvest_15min
    "nkd_harvest_15min.py":          46,
    "asian_harvest_1min.py":         47,
    "harvest_futures_1min.py":       48,
    "nikkei_harvest_1min.py":        49,   # var 47 — delte med asian_harvest
    "halt_resumption_harvest.py":    51,
    "vol_data_probe.py":             52,
    "regime_data_depth_probe.py":    53,   # var 47 — delte med asian_harvest
    "diagnose_feed2.py":             54,   # var random.randint(50, 99)
    "harvest_trendjoin_5min.py":     13,
    "probe_futures_depth.py":        26,
    "scalping_universe_tjek.py":     19,
    "relstyrke_shadow_eval.py":      14,
    "test_vol_kontroller.py":        99,
    "trendjoin_shadow_eval.py":      17,
    "vol_futures_retention_test.py": 65,
    "vol_intradag_dybde_verifikation.py": 64,
    "vol_kvartalsjob.py":            18,
    "vol_harvest.py":                79,
    "regime_univers_backfill.py":    76,
}


def kontroller() -> None:
    """Kaster på dubletter og på id'er der lander i backendens blok.

    ⚠ Køres ved import. En liste der kun er rigtig når nogen husker at læse den,
    er ikke et register — det var netop sådan 46 og 47 blev delt.
    """
    set_op: dict[int, list[str]] = {}
    for fil, kid in SCRIPTS.items():
        set_op.setdefault(kid, []).append(fil)

    dubletter = {k: v for k, v in set_op.items() if len(v) > 1}
    if dubletter:
        raise KlientIdFejl(
            "samme client-id er tildelt flere filer: "
            + "; ".join(f"{k} -> {', '.join(sorted(v))}" for k, v in sorted(dubletter.items())))

    i_backend = {k: v for k, v in set_op.items() if k in BACKEND_BLOK}
    if i_backend:
        raise KlientIdFejl(
            f"scripts må ikke bruge backendens reserverede blok {BACKEND_BLOK.start}-"
            f"{BACKEND_BLOK.stop - 1}: {i_backend}")


def for_script(filnavn: str) -> int:
    """Id'et for et script. Kaster hvis det ikke står i registret — et nyt script
    skal tilføjes her FØR det forbinder, ikke bagefter når det kolliderer."""
    if filnavn not in SCRIPTS:
        raise KlientIdFejl(
            f"{filnavn} står ikke i SCRIPTS. Tilføj en linje i ibkr_client_ids.py "
            f"med et ledigt id — brug ikke et tilfældigt tal.")
    return SCRIPTS[filnavn]


def ledige(antal: int = 5) -> list[int]:
    """Foreslå ledige id'er i script-intervallet. Til når et nyt script skal med."""
    brugte = set(SCRIPTS.values())
    return [i for i in range(1, 100) if i not in brugte][:antal]


kontroller()
