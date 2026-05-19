"""
test_trades_schema.py — Verificér at trades-tabel og helpers virker
Kør:
    cd C:\\Projects\\trading-dash\\backend
    venv\\Scripts\\activate
    python test_trades_schema.py
"""
import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path

import aiosqlite
import pytz

from journal import Journal

ET = pytz.timezone("America/New_York")


async def main():
    test_db = "test_trades_schema.db"

    # Slet evt. gammel test-fil
    if Path(test_db).exists():
        Path(test_db).unlink()

    j = Journal(test_db)
    await j.init()

    # ── Test 1: Skema ───────────────────────────────────────
    print("\n[1] Skema-verifikation")
    async with aiosqlite.connect(test_db) as db:
        async with db.execute("PRAGMA table_info(trades)") as cur:
            cols = await cur.fetchall()
    assert len(cols) == 27, f"Forventede 27 kolonner, fik {len(cols)}"
    print(f"    ✓ trades-tabel har {len(cols)} kolonner")

    # ── Test 2: log_trade_open ──────────────────────────────
    print("\n[2] log_trade_open — opret ny åben trade")
    entry_time = datetime.now(ET)
    trade_id = await j.log_trade_open(
        source="Momentum ORB",
        symbol="CLOV",
        side="long",
        shares=250,
        entry_price=2.50,
        entry_time=entry_time,
        variant="all_winner",
        entry_reason="ORB breakout @ $2.50",
        current_stop=2.45,
        current_target=2.60,
        current_stage="initial",
        payload={"orb_high": 2.49, "orb_low": 2.40, "rsi_at_entry": 65.2},
    )
    assert trade_id is not None, "trade_id må ikke være None"
    print(f"    ✓ trade_id: {trade_id}")

    # Verificér at row blev oprettet med korrekte felter
    async with aiosqlite.connect(test_db) as db:
        async with db.execute(
            "SELECT account_id, instance_id, ibkr_account, source, variant, "
            "symbol, side, shares, entry_price, current_stop, current_stage, "
            "exit_time_utc, capital_used "
            "FROM trades WHERE trade_id = ?",
            (trade_id,)
        ) as cur:
            row = await cur.fetchone()

    assert row is not None
    (account_id, instance_id, ibkr, source, variant,
     symbol, side, shares, entry_price, stop, stage,
     exit_time, capital_used) = row

    print(f"    ✓ account_id={account_id!r}, instance_id={instance_id!r}, ibkr={ibkr!r}")
    print(f"    ✓ source={source!r}, variant={variant!r}, symbol={symbol!r}")
    print(f"    ✓ side={side!r}, shares={shares}, entry_price={entry_price}")
    print(f"    ✓ stop={stop}, stage={stage!r}, capital_used={capital_used}")
    print(f"    ✓ exit_time_utc={exit_time!r} (skal være None for åben)")
    assert exit_time is None
    assert capital_used == 2.50 * 250

    # ── Test 3: update_trade_state ──────────────────────────
    print("\n[3] update_trade_state — ratchet stop og skift stage")
    ok = await j.update_trade_state(
        trade_id=trade_id,
        current_stop=2.50,        # BE-stop
        current_target=None,      # uændret
        current_stage="breakeven",
    )
    assert ok
    async with aiosqlite.connect(test_db) as db:
        async with db.execute(
            "SELECT current_stop, current_target, current_stage FROM trades WHERE trade_id = ?",
            (trade_id,)
        ) as cur:
            row = await cur.fetchone()
    new_stop, new_target, new_stage = row
    print(f"    ✓ stop ratcheted: 2.45 → {new_stop}")
    print(f"    ✓ target bevaret: {new_target}")
    print(f"    ✓ stage: initial → {new_stage}")
    assert new_stop == 2.50
    assert new_target == 2.60   # ikke ændret
    assert new_stage == "breakeven"

    # ── Test 4: log_trade_close ─────────────────────────────
    print("\n[4] log_trade_close — luk handlen med profit")
    exit_time = entry_time + timedelta(minutes=15)
    exit_price = 2.62
    expected_pnl = (exit_price - 2.50) * 250  # 30.00

    ok = await j.log_trade_close(
        trade_id=trade_id,
        exit_price=exit_price,
        exit_time=exit_time,
        exit_reason="target",
        pnl=expected_pnl,
        payload={"exit_trigger_bar_high": 2.63, "vix_at_exit": 18.5},
    )
    assert ok

    async with aiosqlite.connect(test_db) as db:
        async with db.execute(
            "SELECT exit_price, exit_reason, pnl, pnl_pct, duration_sec, payload_json "
            "FROM trades WHERE trade_id = ?",
            (trade_id,)
        ) as cur:
            row = await cur.fetchone()
    ep, er, pnl, pnl_pct, dur, payload_str = row
    payload = json.loads(payload_str)

    print(f"    ✓ exit_price={ep}, exit_reason={er!r}")
    print(f"    ✓ pnl=${pnl:.2f}, pnl_pct={pnl_pct:.4f}%")
    print(f"    ✓ duration_sec={dur} ({dur/60:.1f} min)")
    print(f"    ✓ payload merged: {sorted(payload.keys())}")
    assert ep == exit_price
    assert er == "target"
    assert pnl == 30.0
    assert abs(pnl_pct - 4.8) < 0.01    # (2.62 - 2.50) / 2.50 * 100 = 4.8%
    assert dur == 900                    # 15 minutter = 900 sek
    # Entry-payload og exit-payload skal begge være der
    assert "orb_high" in payload         # fra entry
    assert "exit_trigger_bar_high" in payload  # fra exit

    # ── Test 5: update_trade_state efter close = no-op ─────
    print("\n[5] update_trade_state efter close — skal IKKE påvirke lukket trade")
    await j.update_trade_state(
        trade_id=trade_id,
        current_stop=99.99,    # hvis dette anvendes er det en bug
    )
    async with aiosqlite.connect(test_db) as db:
        async with db.execute(
            "SELECT current_stop FROM trades WHERE trade_id = ?",
            (trade_id,)
        ) as cur:
            row = await cur.fetchone()
    assert row[0] == 2.50, f"current_stop blev ændret efter close! {row[0]}"
    print(f"    ✓ current_stop forblev {row[0]} (ikke 99.99) — fryser efter close virker")

    # ── Test 6: log_trade_close med ukendt trade_id ────────
    print("\n[6] log_trade_close med ukendt trade_id — skal returnere False")
    ok = await j.log_trade_close(
        trade_id="ikke-en-rigtig-uuid",
        exit_price=1.0,
        exit_time=datetime.now(ET),
        exit_reason="error",
        pnl=0.0,
    )
    assert ok is False
    print(f"    ✓ Returnerede False som forventet")

    # ── Test 7: short-trade pnl_pct spejlvendes ────────────
    print("\n[7] Short-trade — pnl_pct skal spejlvendes")
    short_id = await j.log_trade_open(
        source="Momentum ORB",
        symbol="AMC",
        side="short",
        shares=100,
        entry_price=4.50,
        entry_time=datetime.now(ET),
    )
    # Short: vi tjente penge fordi prisen faldt
    await j.log_trade_close(
        trade_id=short_id,
        exit_price=4.30,
        exit_time=datetime.now(ET),
        exit_reason="target",
        pnl=20.0,    # (4.50 - 4.30) * 100 = 20
    )
    async with aiosqlite.connect(test_db) as db:
        async with db.execute(
            "SELECT pnl_pct FROM trades WHERE trade_id = ?", (short_id,)
        ) as cur:
            row = await cur.fetchone()
    short_pnl_pct = row[0]
    # (4.50 - 4.30) / 4.50 * 100 = 4.4444%
    assert abs(short_pnl_pct - 4.4444) < 0.01, f"Short pnl_pct fejl: {short_pnl_pct}"
    print(f"    ✓ Short pnl_pct = {short_pnl_pct:.4f}% (positiv = gevinst)")

    # ── Test 8: count_trades ───────────────────────────────
    print("\n[8] count_trades")
    total = await j.count_trades()
    assert total == 2, f"Forventede 2 trades, fik {total}"
    print(f"    ✓ count_trades = {total}")

    # ── Test 9: update_trade_notes ─────────────────────────
    print("\n[9] update_trade_notes — også på lukket trade")
    ok = await j.update_trade_notes(
        trade_id=trade_id,
        notes="Perfekt ORB-breakout, fulgte planen til punkt og prikke",
    )
    assert ok
    async with aiosqlite.connect(test_db) as db:
        async with db.execute(
            "SELECT notes FROM trades WHERE trade_id = ?", (trade_id,)
        ) as cur:
            row = await cur.fetchone()
    assert "perfekt" in row[0].lower()
    print(f"    ✓ Notes opdateret: {row[0][:50]}...")

    await j.close()
    Path(test_db).unlink()
    print("\n✓ Alle 9 tests bestået — test-DB slettet")


if __name__ == "__main__":
    asyncio.run(main())