"""
test_manuel_konto_isolation.py — en fyldning maa kun lukke sin EGEN kontos handel
═══════════════════════════════════════════════════════════════════════════════════
⚠ FEJLEN DENNE TEST HOLDER LUKKET (maalt 11-09-2026, Ibens workstation).

`find_aaben()` slog op paa `source` og `symbol` alene:

    WHERE source = ? AND symbol = ? AND exit_time_utc IS NULL
    ORDER BY entry_time_utc ASC LIMIT 1

Ingen konto. FIFO gik altsaa paa TVAERS af konti.

Udfaldet: et salg af 1 MES paa DUN748991 blev bogfoert som exit paa en aaben
MES-handel fra dagen foer paa DUQ441063 — en anden konto, hos en anden
broker-forbindelse. To raekker blev forkerte i ÉN skrivning:

    DUQ441063  fremstod lukket med +146,25  (positionen var ikke roert)
    DUN748991  fremstod aaben               (den var netop solgt)

Regnestykket var rigtigt. Handlen var det ikke. Det er samme familie som de
ejerloese positioner: journalen og brokeren er ude af fase, og ingen af delene
siger fra.

⚠ SALGSVAGTEN KAN IKKE FANGE DET, og skal ikke. Den spoerger BROKEREN om
positionen findes, og paa DUN748991 gjorde den. Fejlen laa alene i hvilken
journalraekke fyldningen blev knyttet til — efter at ordren med rette var
godkendt.

    python test_manuel_konto_isolation.py
"""
from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytz

import journal as journal_modul
import manuel_forensik
from journal import Journal

ET = pytz.timezone("America/New_York")

KONTO_A = "DUQ441063"      # Ibens produktionskonto
KONTO_B = "DUN748991"      # en ANDEN konto paa samme maskine

FEJL: list[str] = []


def paastand(betingelse: bool, hvad: str) -> None:
    print(f"  {'OK  ' if betingelse else 'FEJL'} {hvad}")
    if not betingelse:
        FEJL.append(hvad)


class FalskeBars:
    def __init__(self, n=40):
        self.n = n
        self.start = datetime.now(timezone.utc) - timedelta(minutes=n)

    def som_df(self):
        import pandas as pd
        idx, rows = [], []
        for i in range(self.n):
            p = 7600.0 + (i % 5) * 0.25
            idx.append(self.start + timedelta(minutes=i))
            rows.append({"Open": p, "High": p + 0.5, "Low": p - 0.5,
                         "Close": p + 0.25, "Volume": 100 + i})
        return pd.DataFrame(rows, index=pd.DatetimeIndex(idx))


async def _falsk_fetch(conn, symbol, source, entry, exit_, **kw):
    return FalskeBars().som_df()


async def koer() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="kontoisolation_"))
    j = Journal(str(tmp / "t.db"))
    await j.init()

    import trade_chart
    trade_chart.fetch_trade_bars = _falsk_fetch

    # Hvilken konto journalen staempler med — skiftes undervejs, saa de to
    # handler havner paa hver sin konto ligesom paa den rigtige maskine.
    konto_nu = {"id": KONTO_A}
    journal_modul.saet_konto_kilde(lambda: (konto_nu["id"], True))

    try:
        # ── Konto A aabner en MES-handel (i gaar) ─────────────────────────
        konto_nu["id"] = KONTO_A
        ibkr_a = SimpleNamespace(connected=True, account=KONTO_A)
        tid_a = await manuel_forensik.registrer_entry(
            j, ibkr_a, symbol="MES", side="LONG", shares=1, fill_pris=7600.50,
            ordre_id=173, ordre_status="Filled", et_tz=ET)
        paastand(tid_a is not None, f"konto A aabnede MES @ 7600,50 ({str(tid_a)[:8]}…)")

        # ── Konto B aabner sin EGEN MES-handel (i dag) ────────────────────
        konto_nu["id"] = KONTO_B
        ibkr_b = SimpleNamespace(connected=True, account=KONTO_B)
        tid_b = await manuel_forensik.registrer_entry(
            j, ibkr_b, symbol="MES", side="LONG", shares=1, fill_pris=7627.75,
            ordre_id=6, ordre_status="Filled", et_tz=ET)
        paastand(tid_b is not None, f"konto B aabnede MES @ 7627,75 ({str(tid_b)[:8]}…)")
        paastand(tid_a != tid_b, "de to handler er forskellige raekker")

        # ── Konto B saelger. Den AELDSTE aabne MES er konto A's ───────────
        # Uden kontofilteret vaelger FIFO netop den — og det var fejlen.
        print("\n  konto B saelger 1 MES @ 7629,75")
        tid_luk = await manuel_forensik.registrer_exit(
            j, ibkr_b, symbol="MES", shares=1, fill_pris=7629.75,
            ordre_id=9, ordre_status="Filled", et_tz=ET)

        paastand(tid_luk == tid_b,
                 "salget lukkede konto B's egen handel" +
                 ("" if tid_luk == tid_b else f" — lukkede {str(tid_luk)[:8]}… i stedet"))
        paastand(tid_luk != tid_a,
                 "salget roerte IKKE konto A's handel")

        # ── Raekkerne skal staa som virkeligheden ─────────────────────────
        async with j.db.execute(
            "SELECT exit_time_utc, exit_price, pnl FROM trades WHERE trade_id=?",
            (tid_a,)) as c:
            a = await c.fetchone()
        paastand(a[0] is None,
                 f"konto A's handel staar stadig AABEN (exit={a[0]})")
        paastand(a[2] is None, f"konto A fik ingen P&L paategnet (pnl={a[2]})")

        async with j.db.execute(
            "SELECT exit_time_utc, exit_price, pnl FROM trades WHERE trade_id=?",
            (tid_b,)) as c:
            b = await c.fetchone()
        paastand(b[0] is not None, "konto B's handel er lukket")
        paastand(b[1] is not None and abs(b[1] - 7629.75) < 1e-9,
                 f"exit_price = {b[1]}")
        # (7629,75 - 7627,75) x 1 x 5 = 10,00 — MES-multiplikatoren er $5/point.
        paastand(b[2] is not None and abs(b[2] - 10.0) < 1e-6,
                 f"P&L regnet paa KONTO B's entry: {b[2]} (forventet 10,00)")

        # ── Og kontoen skal komme fra FORBINDELSEN, ikke konfigurationen ──
        # Paa en maskine med ordre-Gateway er den delte forbindelse en anden
        # konto. Slaar vi op paa "maskinens konto", rammer vi forkert.
        print("\n  opslag med eksplicit konto")
        fundet_a = await manuel_forensik.find_aaben(j, "MES", konto=KONTO_A)
        paastand(fundet_a is not None and fundet_a["trade_id"] == tid_a,
                 "find_aaben(konto=A) finder A's aabne handel")
        fundet_b = await manuel_forensik.find_aaben(j, "MES", konto=KONTO_B)
        paastand(fundet_b is None,
                 "find_aaben(konto=B) finder ingenting — B's er lukket")
        fundet_c = await manuel_forensik.find_aaben(j, "MES", konto="DU0000000")
        paastand(fundet_c is None,
                 "en tredje konto faar INGEN af dem")

    finally:
        await j.close()
        journal_modul.saet_konto_kilde(None)
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    print("  ── manuel forensik: kontoisolation ──")
    asyncio.run(koer())
    print()
    if FEJL:
        print(f"  DUMPET — {len(FEJL)} fejl:")
        for f in FEJL:
            print(f"     · {f}")
        return 1
    print("  ALLE BESTAAET")
    return 0


if __name__ == "__main__":
    sys.exit(main())
