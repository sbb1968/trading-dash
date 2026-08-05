"""
test_manuel_forensik.py — efterlader en manuel handel de samme spor som en algos?
═══════════════════════════════════════════════════════════════════════════════════
Paastanden der skal afproeves er ikke "koden koerer" men "sporet er det samme". Derfor
sammenligner testen FELT FOR FELT mod en rigtig algo-handel fra databasen, frem for
mod en liste jeg selv har skrevet ned.

Koerer mod en midlertidig database og en falsk IBKR — ingen TWS noedvendig.

    python test_manuel_forensik.py
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytz

import manuel_forensik
from journal import Journal

ET = pytz.timezone("America/New_York")
FEJL: list[str] = []


def paastand(betingelse: bool, hvad: str) -> None:
    if betingelse:
        print(f"  OK    {hvad}")
    else:
        print(f"  FEJL  {hvad}")
        FEJL.append(hvad)


# ── Falsk IBKR: leverer bars uden TWS ────────────────────────────────────────
class FalskeBars:
    """Nok bars til at indikatorerne kan beregnes (RSI/MACD/BB kraever ~26+)."""

    def __init__(self, n=80, start=None):
        self.n = n
        self.start = start or (datetime.now(timezone.utc) - timedelta(minutes=n))

    def som_df(self):
        import pandas as pd
        idx, rows = [], []
        for i in range(self.n):
            p = 100.0 + (i % 7) * 0.5          # lidt bevaegelse, ellers er std 0
            idx.append(self.start + timedelta(minutes=i))
            # STORT begyndelsesbogstav — praecis som fetch_trade_bars returnerer.
            # Denne double brugte smaat og var dermed enig med en fejl i _hent_bars
            # i stedet for med virkeligheden. Den kunne aldrig fange noget.
            # En test-double skal bygges efter PRODUCENTEN, ikke efter forbrugerens
            # antagelse om producenten.
            rows.append({"Open": p, "High": p + 0.4, "Low": p - 0.4,
                         "Close": p + 0.1, "Volume": 1000 + i})
        return pd.DataFrame(rows, index=pd.DatetimeIndex(idx))


async def _falsk_fetch(conn, symbol, source, entry, exit_, **kw):
    return FalskeBars().som_df()


async def koer():
    tmp = Path(tempfile.mkdtemp(prefix="manforensik_"))
    j = Journal(str(tmp / "t.db"))
    await j.init()
    ibkr = SimpleNamespace(connected=True)

    # Erstat bar-hentningen — testen handler om forensikken, ikke om IBKR.
    import trade_chart
    trade_chart.fetch_trade_bars = _falsk_fetch

    try:
        print("\n[1] Entry skriver en raekke i `trades` — det gjorde den ikke foer")
        tid = await manuel_forensik.registrer_entry(
            j, ibkr, symbol="AAPL", side="LONG", shares=100, fill_pris=150.25,
            ordre_id=4711, ordre_status="Filled", et_tz=ET)
        paastand(tid is not None, f"trade_id returneret: {str(tid)[:8]}…")

        async with j.db.execute(
            "SELECT source, symbol, side, shares, entry_price, entry_reason, "
            "       exit_time_utc, capital_used, payload_json "
            "FROM trades WHERE trade_id = ?", (tid,)) as c:
            r = await c.fetchone()
        paastand(r is not None, "raekken findes i `trades`")
        paastand(r[0] == "manual", f"source = {r[0]}")
        paastand(r[1] == "AAPL" and r[3] == 100 and abs(r[4] - 150.25) < 1e-9,
                 "symbol, shares og entry_price er korrekte")
        paastand(r[6] is None, "exit_time_utc er NULL — positionen staar aaben")
        paastand(abs(r[7] - 15025.0) < 1e-6, f"capital_used beregnet: {r[7]}")

        print("\n[2] Entry-forensikken har de SAMME indikatorer som algoerne")
        async with j.db.execute(
            "SELECT payload_json FROM events WHERE event_type='trade_forensics' "
            "AND source='manual' ORDER BY id DESC LIMIT 1") as c:
            e = await c.fetchone()
        paastand(e is not None, "trade_forensics-event skrevet")
        snap = json.loads(e[0])
        paastand(snap.get("phase") == "entry", f"phase = {snap.get('phase')}")

        ind = snap.get("indicators") or {}
        forventet = {"rsi_14", "macd", "macd_signal", "macd_hist", "ema_9", "ema_20",
                     "bb_upper", "bb_middle", "bb_lower", "bb_width_pct",
                     "bb_position_pct", "vwap", "vwap_distance_pct", "cmf_20"}
        mangler = forventet - set(ind)
        paastand(not mangler, f"alle 14 indikatorer med (mangler: {mangler or 'ingen'})")
        beregnet = [k for k in forventet if ind.get(k) is not None]
        paastand(len(beregnet) >= 10,
                 f"{len(beregnet)} af {len(forventet)} har en VAERDI, ikke kun en noegle")

        print("\n[3] Eventet baerer trade_id — det goer algoernes IKKE")
        paastand(snap.get("trade_id") == tid,
                 "forensikken kan joines deterministisk til handlen")
        paastand(snap.get("manuel") is True, "og er maerket som manuel")

        print("\n[4] Exit lukker raekken, beregner P&L og gemmer chart_bars")
        tid2 = await manuel_forensik.registrer_exit(
            j, ibkr, symbol="AAPL", shares=100, fill_pris=152.75,
            ordre_id=4712, ordre_status="Filled", et_tz=ET)
        paastand(tid2 == tid, "exit fandt den aabne entry (FIFO)")

        async with j.db.execute(
            "SELECT exit_price, exit_reason, pnl, pnl_pct, duration_sec, payload_json "
            "FROM trades WHERE trade_id = ?", (tid,)) as c:
            r = await c.fetchone()
        paastand(abs(r[0] - 152.75) < 1e-9, "exit_price gemt")
        paastand(r[1] == "manuel_salg", f"exit_reason = {r[1]}")
        paastand(abs(r[2] - 250.0) < 1e-6, f"P&L = {r[2]} (100 × 2,50)")
        paastand(r[3] is not None and abs(r[3] - 1.6639) < 0.01, f"pnl_pct = {r[3]}")
        paastand(r[4] is not None, "duration_sec beregnet")

        pay = json.loads(r[5])
        paastand("chart_bars" in pay and len(pay["chart_bars"]) > 0,
                 f"chart_bars gemt: {len(pay.get('chart_bars', []))} bars")
        paastand(len(pay["chart_bars"][0]) == 6,
                 "hver bar er [ts, o, h, l, c, v] — samme form som algoernes")
        paastand("chart_bars_kilde" in pay,
                 "og det STAAR at de er hentet ved exit, ikke evalueret undervejs")
        paastand(pay.get("ibkr_order_id") == 4711 and pay.get("ibkr_order_id_exit") == 4712,
                 "entry- og exit-payload er MERGET, begge ordre-id'er bevaret")

        print("\n[5] Sammenligning med en RIGTIG algo-handel, felt for felt")
        # Ikke min egen liste — de kolonner en algo faktisk udfylder.
        algo_db = Path("trading_dash.db")
        if algo_db.exists():
            import sqlite3
            c2 = sqlite3.connect(algo_db)
            kol = [x[1] for x in c2.execute("PRAGMA table_info(trades)")]
            rr = c2.execute(
                "SELECT * FROM trades WHERE source='Konfluens 2' "
                "AND exit_time_utc IS NOT NULL LIMIT 1").fetchone()
            c2.close()
            if rr:
                algo = {k: v for k, v in zip(kol, rr)}
                algo_udfyldt = {k for k, v in algo.items() if v is not None}
                # ⚠ Laes den MIDLERTIDIGE databases EGEN kolonneraekkefoelge.
                # Foerste udgave zippede produktionens kolonnenavne mod en frisk
                # databases raekke — men produktionen har faaet kolonner tilfoejet
                # med ALTER TABLE, saa raekkefoelgen er en anden. Resultatet var en
                # forskudt sammenligning der meldte `payload_json` som tomt, selvom
                # test [4] lige havde bevist at den var fyldt.
                # Det er NOEJAGTIG samme fejl som L1 i regime-arbejdet: sammenstilling
                # paa position frem for paa navn. Den er nem at lave og svaer at se.
                async with j.db.execute("PRAGMA table_info(trades)") as c:
                    man_kol = [x[1] for x in await c.fetchall()]
                async with j.db.execute(
                        "SELECT * FROM trades WHERE trade_id = ?", (tid,)) as c:
                    mr = await c.fetchone()
                man = {k: v for k, v in zip(man_kol, mr)}
                man_udfyldt = {k for k, v in man.items() if v is not None}
                # Felter algoen har, som manuel ikke har.
                kun_algo = algo_udfyldt - man_udfyldt
                print(f"        algo udfylder {len(algo_udfyldt)} kolonner, "
                      f"manuel {len(man_udfyldt)}")
                print(f"        kun hos algoen: {sorted(kun_algo) or 'ingen'}")
                # Stop/target/stage findes ikke for manuel — det er en aegte forskel,
                # ikke en logningsmangel. Alt ANDET skal vaere med.
                tilladt = {"current_stop", "current_target", "current_stage",
                           "trail_stop", "variant", "notes", "current_price"}
                uventet = kun_algo - tilladt
                paastand(not uventet,
                         f"ingen UVENTEDE huller mod algoen (uventet: {uventet or 'ingen'})")
            else:
                print("        (ingen lukket Konfluens 2-handel at sammenligne med)")
        else:
            print("        (trading_dash.db ikke fundet — springer sammenligning over)")

        print("\n[6] Salg UDEN aaben entry skjules ikke")
        tid3 = await manuel_forensik.registrer_exit(
            j, ibkr, symbol="TSLA", shares=50, fill_pris=200.0,
            ordre_id=4713, ordre_status="Filled", et_tz=ET)
        paastand(tid3 is None, "returnerer None — der var intet at lukke")
        async with j.db.execute(
            "SELECT count(*) FROM events WHERE event_type='exit_uden_aaben_entry'") as c:
            n = (await c.fetchone())[0]
        paastand(n == 1, "men det er REGISTRERET som event, ikke tabt i stilhed")

        print("\n[7] Antals-uoverensstemmelse rapporteres frem for at blive glattet ud")
        t4 = await manuel_forensik.registrer_entry(
            j, ibkr, symbol="MSFT", side="LONG", shares=100, fill_pris=400.0,
            ordre_id=5000, ordre_status="Filled", et_tz=ET)
        await manuel_forensik.registrer_exit(
            j, ibkr, symbol="MSFT", shares=40, fill_pris=410.0,
            ordre_id=5001, ordre_status="Filled", et_tz=ET)
        async with j.db.execute(
            "SELECT pnl, payload_json FROM trades WHERE trade_id = ?", (t4,)) as c:
            r = await c.fetchone()
        pay = json.loads(r[1])
        paastand("antal_uoverensstemmelse" in pay,
                 "delvist salg flagget i payloadet")
        paastand(abs(r[0] - 400.0) < 1e-6,
                 f"P&L beregnet paa de 40 der faktisk blev solgt: {r[0]}")

        print("\n[8] FUTURES: MES afregnes i $ PR. PRISPOINT, ikke pr. aktie")
        # Ibens scenarie: 2 MES, 6,50 point gevinst. MES er $5/point -> 65 USD.
        import trade_chart as tc
        # Maalt mod IBKR 2026-08-05: med useRTH=True laa 0 af 148 AAPL-bars i et
        # pre-market-handelsvindue; med False laa 60 der. MES: 141 bars, korrekt
        # vindue. Derfor useRTH=False for manuelle handler uanset instrument.
        paastand(tc.params_for("manual", "MES")["use_rth"] is False,
                 "MES-charten henter UDEN RTH-filter")
        paastand(tc.params_for("manual", "AAPL")["use_rth"] is False,
                 "og det goer AKTIER ogsaa — watchlist tillader pre-market")
        paastand(tc.params_for("Konfluens 2", "AAPL")["use_rth"] is True,
                 "men algoerne beholder deres eget RTH-filter — kun manual aendres")

        m = await manuel_forensik.registrer_entry(
            j, ibkr, symbol="MES", side="long", shares=2, fill_pris=7645.25,
            ordre_id=9001, ordre_status="Filled", et_tz=ET)
        await manuel_forensik.registrer_exit(
            j, ibkr, symbol="MES", shares=2, fill_pris=7651.75,
            ordre_id=9002, ordre_status="Filled", et_tz=ET)
        async with j.db.execute(
                "SELECT pnl FROM trades WHERE trade_id = ?", (m,)) as c:
            pnl_mes = (await c.fetchone())[0]
        paastand(abs(pnl_mes - 65.0) < 1e-6,
                 f"P&L = {pnl_mes} = 6,50 point × 2 kontrakter × multiplier 5")
        paastand(abs(pnl_mes - 13.0) > 1,
                 "og IKKE 13 — det var fejlen før multiplikatoren kom med")

        print("\n[9] FIFO naar der er to aabne paa samme ticker")
        a = await manuel_forensik.registrer_entry(
            j, ibkr, symbol="NVDA", side="LONG", shares=10, fill_pris=100.0,
            ordre_id=6000, ordre_status="Filled", et_tz=ET)
        await asyncio.sleep(1.1)      # sikrer forskellig entry_time_utc
        b = await manuel_forensik.registrer_entry(
            j, ibkr, symbol="NVDA", side="LONG", shares=10, fill_pris=110.0,
            ordre_id=6001, ordre_status="Filled", et_tz=ET)
        lukket = await manuel_forensik.registrer_exit(
            j, ibkr, symbol="NVDA", shares=10, fill_pris=120.0,
            ordre_id=6002, ordre_status="Filled", et_tz=ET)
        paastand(lukket == a, "den AELDSTE blev lukket foerst (FIFO), ikke den nyeste")

    finally:
        await j.close()
        shutil.rmtree(tmp, ignore_errors=True)


asyncio.run(koer())


# ═══════════════════════════════════════════════════════════════════════════════
# Bar-kolonner: fundet paa en RIGTIG handel, ikke af en test
# ═══════════════════════════════════════════════════════════════════════════════
# fetch_trade_bars returnerer "Open"/"High"/"Low"/"Close". _hent_bars laeste dem med
# smaat, fik None, og _byg_bar gjorde None til 0.0. Alle 44 bars blev nul-bars.
# Indikatorerne regnede paa dem UDEN at klage — rsi_14=100, ema_9=0, macd=0, baand
# None — og chart_bars blev tom. Ingen fejl nogen steder. Det opdagede vi kun ved at
# laese forensikken paa en faktisk MES-handel.
#
# Testen holder BEGGE ender: rigtige kolonner skal give rigtige tal, og forkerte
# kolonner skal give INGEN bars (ikke nul-bars, der er den tavse fejl).
print("\n--- Bar-kolonner (fundet paa en rigtig MES-handel) ---")

import pandas as pd
from datetime import datetime as _dtm
_NU = _dtm.now(timezone.utc)
from datetime import timedelta as _td


class _FalskIBKR:
    def __init__(self, df):
        self.df = df


async def _bars_med(df):
    """Koer _hent_bars med en fastlagt DataFrame i stedet for IBKR."""
    import trade_chart
    aegte = trade_chart.fetch_trade_bars

    async def falsk(*a, **k):
        return df
    trade_chart.fetch_trade_bars = falsk
    try:
        return await manuel_forensik._hent_bars(
            _FalskIBKR(df), "MES", _NU, _NU + _td(minutes=1))
    finally:
        trade_chart.fetch_trade_bars = aegte


def _df(kolonner):
    idx = pd.to_datetime(["2026-08-05 03:05:00", "2026-08-05 03:06:00"], utc=True)
    vaerdier = [[7795.0, 7796.5, 7794.75, 7796.5, 137.0],
                [7796.5, 7797.75, 7796.5, 7797.75, 271.0]]
    return pd.DataFrame(vaerdier, index=idx, columns=kolonner)


rigtige = asyncio.run(_bars_med(_df(["Open", "High", "Low", "Close", "Volume"])))
paastand(len(rigtige) == 2, f"rigtige kolonner -> 2 bars (fik {len(rigtige)})")
paastand(all(b.close > 0 for b in rigtige),
         f"ingen nul-bars — closes: {[b.close for b in rigtige]}")
paastand(rigtige[-1].close == 7797.75,
         f"prisen er den RIGTIGE, ikke 0.0 (fik {rigtige[-1].close})")
paastand(rigtige[-1].volume == 271.0, "volumen kom ogsaa med")

smaat = asyncio.run(_bars_med(_df(["open", "high", "low", "close", "volume"])))
paastand(smaat == [],
         f"forkerte kolonner -> INGEN bars, ikke nul-bars (fik {len(smaat)})")

uden_volumen = _df(["Open", "High", "Low", "Close", "Volume"]).drop(columns=["Volume"])
kun_pris = asyncio.run(_bars_med(uden_volumen))
paastand(len(kun_pris) == 2 and kun_pris[0].volume == 0.0,
         "manglende VOLUMEN maa gerne blive 0 — kun priser er kritiske")

print("\n" + "=" * 70)
if FEJL:
    print(f"DUMPET — {len(FEJL)} fejl:")
    for f in FEJL:
        print(f"   · {f}")
    sys.exit(1)
print("ALLE TESTS BESTAAET")
