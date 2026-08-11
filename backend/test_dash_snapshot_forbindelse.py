"""
test_dash_snapshot_forbindelse.py — Portfolio-vinduet skal vise den konto der handles på
════════════════════════════════════════════════════════════════════════════════
Fundet 11-08-2026 på Sørens workstation:

    ⚠ Fejl ved hentning: name 'via_ordre' is not defined

`17f3350` tilføjede feltet `forbindelse` til `/account/dash-snapshot` med
variablen `via_ordre` — men tildelte den aldrig, og hentede fortsat kun fra den
**delte** forbindelse.

⚠ FEJLEN VAR SKJULT AF ET TIDLIGT RETUR. Funktionen svarer "IBKR ikke forbundet"
når den delte forbindelse mangler, og på en maskine med ordre-Gateway mangler den
næsten altid. NameError-linjen blev derfor aldrig nået — indtil en delt
forbindelse kom op.

⚠ OG KONSEKVENSEN VAR STØRRE END FEJLBESKEDEN. På Ibens workstation ville
Portfolio-vinduet have svaret "IBKR ikke forbundet" mens hendes MES-position lå
lige der, fordi hun ingen delt forbindelse har. Det er **trin 8a i aftenens
test** — kontrollen af at handlen landede på den rigtige konto. Den vigtigste
enkeltkontrol i hele flytningen ville have set ud som en fejlet handel.

Testen dækker begge grene. En rettelse der kun er set virke i det ene tilfælde,
er ikke set virke.
"""
from __future__ import annotations

import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import main
import ordre_forbindelse


class FalskConn:
    """Minimal IBKR-forbindelse. Tæller om nogen beder om markedsdata."""

    def __init__(self, konto: str):
        self.connected = True
        self.account = konto
        self.snapshot_kald: list[str] = []

    def get_account_summary(self):
        return {"net_liquidation": 1000.0, "cash_balance": 500.0,
                "unrealized_pnl": 0.0, "realized_pnl": 0.0}

    async def get_positions_live(self):
        return [{"ticker": "MES", "position": 1, "avg_cost": 38890.0,
                 "multiplier": "5", "sec_type": "FUT"}]

    async def get_snapshot(self, ticker):
        self.snapshot_kald.append(ticker)
        return {"last": 7778.5}


def _koer(via_ordre: bool, konto: str):
    """Kør endpointet med den ene eller den anden forbindelse."""
    delt = FalskConn("DUN748991")
    ordre = FalskConn(konto)

    g_konf, g_hent, g_get, g_quote = (
        ordre_forbindelse.konfigureret, ordre_forbindelse.hent,
        main.strategy_manager.get_ibkr, main.quote)
    try:
        ordre_forbindelse.konfigureret = lambda: via_ordre
        main.ordre_forbindelse.konfigureret = lambda: via_ordre

        async def _hent(*a, **k):
            return ordre
        main.ordre_forbindelse.hent = _hent
        main.strategy_manager.get_ibkr = lambda: delt

        async def _quote(t):
            return {"ticker": t, "price": 7779.25, "kilde": "algoserver"}
        main.quote = _quote

        svar = asyncio.run(main.account_dash_snapshot())
        return svar, delt, ordre
    finally:
        ordre_forbindelse.konfigureret = g_konf
        main.ordre_forbindelse.konfigureret = g_konf
        main.ordre_forbindelse.hent = g_hent
        main.strategy_manager.get_ibkr = g_get
        main.quote = g_quote


def test_delt_forbindelse_virker():
    """Maskiner uden ordre-Gateway — den almindelige opsætning."""
    svar, delt, ordre = _koer(via_ordre=False, konto="DUQ441063")
    assert svar["ok"] is True, svar.get("error")
    assert svar["forbindelse"] == "delt", svar["forbindelse"]
    assert svar["ibkr_account"] == "DUN748991"
    assert delt.snapshot_kald, "den delte forbindelse skal levere kurserne her"


def test_ordreforbindelse_viser_SIN_konto():
    """⚠ Kernen. Vinduet skal vise den konto der handles på."""
    svar, delt, ordre = _koer(via_ordre=True, konto="DUQ441063")
    assert svar["ok"] is True, svar.get("error")
    assert svar["forbindelse"] == "ordre", svar["forbindelse"]
    assert svar["ibkr_account"] == "DUQ441063", (
        f"vinduet viser {svar['ibkr_account']} — det er den DELTE forbindelses "
        f"konto, ikke den der handles på")


def test_ordreforbindelsen_spoerges_ALDRIG_om_markedsdata():
    """⚠ Det var netop dét der udløste sessionskonflikten.

    En Gateway beder ikke om data af sig selv, og så længe kun den ene læser,
    opstår konflikten ikke. Prisen skal komme fra quote() — algoserveren, eller
    TradingView for crypto.
    """
    svar, delt, ordre = _koer(via_ordre=True, konto="DUQ441063")
    assert ordre.snapshot_kald == [], (
        f"ordreforbindelsen blev bedt om markedsdata for {ordre.snapshot_kald} "
        f"— det er præcis den anmodning der stjæler sessionen")
    p = svar["positions"][0]
    assert p["last_price"] == 7779.25, "prisen kom ikke fra quote()"


def test_spaerret_ordreforbindelse_falder_IKKE_tilbage():
    """En spærret vagt må ikke give den delte forbindelses tal.

    Vinduet ville da vise en ANDEN kontos positioner under overskriften
    "din konto" — og det ser ud som en succes.
    """
    g_konf, g_hent = ordre_forbindelse.konfigureret, main.ordre_forbindelse.hent
    try:
        main.ordre_forbindelse.konfigureret = lambda: True

        async def _fejl(*a, **k):
            raise ordre_forbindelse.OrdreForbindelseFejl("⚠ FORKERT KONTO")
        main.ordre_forbindelse.hent = _fejl
        main.strategy_manager.get_ibkr = lambda: FalskConn("DUN748991")

        svar = asyncio.run(main.account_dash_snapshot())
        assert svar["ok"] is False, svar
        assert "spærret" in svar["error"].lower(), svar["error"]
        assert "DUN748991" not in str(svar), "den delte kontos tal slap ud"
    finally:
        main.ordre_forbindelse.konfigureret = g_konf
        main.ordre_forbindelse.hent = g_hent


if __name__ == "__main__":
    for navn, fn in sorted(globals().items()):
        if navn.startswith("test_"):
            fn()
            print(f"  OK  {navn}")
    print("\nAlle bestod.")
