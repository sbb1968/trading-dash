"""
test_journal_endpoints.py
─────────────────────────
End-to-end test af /journal/* endpoints.

Strategi:
  1. Skriv et par fake trades til den rigtige trading_dash.db
     (vi rydder op til sidst)
  2. Start ikke backenden — i stedet kalder vi trade_queries direkte
     mod samme DB, hvilket er præcis hvad endpoints gør

Hvis du vil teste det rigtige HTTP-flow, start backenden manuelt
og kør curl-kommandoer (se bunden af filen).
"""
import asyncio
import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import aiosqlite
import pytz

from journal import Journal
import trade_queries

ET = pytz.timezone("America/New_York")

# Brug separat test-DB så vi ikke rører den rigtige
TEST_DB = "test_journal_endpoints.db"


async def insert_fake_trades(journal: Journal) -> list[str]:
    """Skriv 4 fake trades — 2 lukkede + 2 åbne — så vi har noget at læse."""
    today = datetime.now(ET)
    ids = []

    # Trade 1: Lukket vinder (Momentum ORB, long)
    t1 = await journal.log_trade_open(
        source="Momentum ORB", symbol="CLOV", side="long", shares=250,
        entry_price=2.50, entry_time=today - timedelta(hours=3),
        variant="all_winner", entry_reason="ORB breakout @ $2.50",
        current_stop=2.45, current_target=2.60, current_stage="initial",
        payload={"orb_high": 2.49, "rsi_at_entry": 65.2},
    )
    await journal.log_trade_close(
        trade_id=t1, exit_price=2.62,
        exit_time=today - timedelta(hours=2, minutes=45),
        exit_reason="target", pnl=30.0,
    )
    ids.append(t1)

    # Trade 2: Lukket taber (Konfluens, long)
    t2 = await journal.log_trade_open(
        source="Konfluens", symbol="HCAI", side="long", shares=100,
        entry_price=8.77, entry_time=today - timedelta(hours=2),
        variant="baseline", entry_reason="Konfluens 5/6 bricks",
        current_stop=8.55, current_target=None, current_stage="initial",
    )
    await journal.log_trade_close(
        trade_id=t2, exit_price=8.40,
        exit_time=today - timedelta(hours=1, minutes=30),
        exit_reason="stop", pnl=-37.0,
    )
    ids.append(t2)

    # Trade 3: Åben ORB long
    t3 = await journal.log_trade_open(
        source="Momentum ORB", symbol="AMC", side="long", shares=150,
        entry_price=4.20, entry_time=today - timedelta(minutes=30),
        variant="all_winner", entry_reason="ORB breakout @ $4.20",
        current_stop=4.10, current_target=4.36, current_stage="initial",
    )
    ids.append(t3)

    # Trade 4: Åben Konfluens
    t4 = await journal.log_trade_open(
        source="Konfluens", symbol="GME", side="long", shares=200,
        entry_price=15.50, entry_time=today - timedelta(minutes=10),
        variant="baseline", entry_reason="Konfluens 4/6 bricks",
        current_stop=15.20, current_stage="initial",
    )
    ids.append(t4)

    return ids


async def main():
    # Slet evt. gammel test-DB
    if Path(TEST_DB).exists():
        Path(TEST_DB).unlink()

    journal = Journal(TEST_DB)
    await journal.init()

    print("Indsætter fake trades...")
    ids = await insert_fake_trades(journal)
    print(f"  ✓ 4 trades oprettet")

    # ── Test 1: list_trades uden filtre ─────────────────────
    print("\n[1] GET /journal/trades — alle")
    result = await trade_queries.list_trades(journal.db)
    print(f"    ✓ {len(result)} trades returneret (forventet 4)")
    assert len(result) == 4

    # Verificér sortering (nyeste først)
    times = [t["entry_time_et"] for t in result]
    assert times == sorted(times, reverse=True), "Ikke sorteret nyeste først"
    print(f"    ✓ Sorteret nyeste først")

    # Verificér struktur af én trade
    first = result[0]
    expected_keys = {"trade_id", "account_id", "instance_id", "ibkr_account",
                     "source", "variant", "symbol", "side", "shares",
                     "entry_time_utc", "entry_time_et", "entry_price",
                     "exit_time_utc", "exit_price", "pnl", "payload"}
    missing = expected_keys - set(first.keys())
    assert not missing, f"Manglende keys: {missing}"
    print(f"    ✓ Trade har {len(first)} felter, alle forventede til stede")

    # ── Test 2: Filter status=open ──────────────────────────
    print("\n[2] GET /journal/trades?status=open")
    result = await trade_queries.list_trades(journal.db, status="open")
    assert len(result) == 2, f"Forventede 2 åbne, fik {len(result)}"
    assert all(t["exit_price"] is None for t in result)
    print(f"    ✓ 2 åbne trades (AMC, GME)")

    # ── Test 3: Filter status=closed ────────────────────────
    print("\n[3] GET /journal/trades?status=closed")
    result = await trade_queries.list_trades(journal.db, status="closed")
    assert len(result) == 2
    assert all(t["pnl"] is not None for t in result)
    print(f"    ✓ 2 lukkede trades med pnl: {[t['pnl'] for t in result]}")

    # ── Test 4: Filter source=Momentum ORB ──────────────────
    print("\n[4] GET /journal/trades?source=Momentum ORB")
    result = await trade_queries.list_trades(journal.db, source="Momentum ORB")
    assert len(result) == 2
    assert all(t["source"] == "Momentum ORB" for t in result)
    print(f"    ✓ 2 ORB trades")

    # ── Test 5: Filter symbol=HCAI ──────────────────────────
    print("\n[5] GET /journal/trades?symbol=HCAI")
    result = await trade_queries.list_trades(journal.db, symbol="HCAI")
    assert len(result) == 1
    assert result[0]["symbol"] == "HCAI"
    print(f"    ✓ 1 HCAI trade, P&L=${result[0]['pnl']}")

    # ── Test 6: get_trade_by_id ─────────────────────────────
    print("\n[6] GET /journal/trades/{trade_id}")
    t = await trade_queries.get_trade_by_id(journal.db, ids[0])
    assert t is not None
    assert t["trade_id"] == ids[0]
    assert t["symbol"] == "CLOV"
    # Payload skal være dict, ikke string
    assert isinstance(t["payload"], dict)
    assert t["payload"].get("orb_high") == 2.49
    print(f"    ✓ CLOV trade hentet, payload korrekt deserialiseret")

    # ── Test 7: get_trade_by_id med ukendt id ──────────────
    print("\n[7] GET /journal/trades/{ukendt}")
    t = await trade_queries.get_trade_by_id(journal.db, "ikke-rigtig-uuid")
    assert t is None
    print(f"    ✓ Returnerede None")

    # ── Test 8: today_trades_and_summary ───────────────────
    print("\n[8] GET /journal/today")
    today_result = await trade_queries.today_trades_and_summary(journal.db)
    assert "date" in today_result
    assert "trades" in today_result
    assert "summary" in today_result
    print(f"    ✓ Date: {today_result['date']}")
    print(f"    ✓ Trades i dag: {len(today_result['trades'])}")
    s = today_result["summary"]
    print(f"    ✓ Summary: {s['count']} lukkede, {s['wins']}W/{s['losses']}L, "
          f"P&L=${s['total_pnl']}, win_rate={s['win_rate']}%, "
          f"åbne: {s['open_count']}")
    assert s["count"] == 2          # 2 lukkede
    assert s["wins"] == 1            # CLOV
    assert s["losses"] == 1          # HCAI
    assert s["total_pnl"] == -7.0    # 30 - 37
    assert s["open_count"] == 2

    # ── Test 9: open_positions ─────────────────────────────
    print("\n[9] GET /journal/open-positions")
    positions = await trade_queries.open_positions(journal.db)
    assert len(positions) == 2
    symbols = {p["symbol"] for p in positions}
    assert symbols == {"AMC", "GME"}
    print(f"    ✓ Åbne positioner: {sorted(symbols)}")

    # ── Test 10: update_notes ──────────────────────────────
    print("\n[10] PATCH /journal/trades/{trade_id} — opdater notes")
    ok = await trade_queries.update_notes_via_journal(
        journal, ids[0], "Lærepenge: tog target for tidligt"
    )
    assert ok
    t = await trade_queries.get_trade_by_id(journal.db, ids[0])
    assert "Lærepenge" in t["notes"]
    print(f"    ✓ Notes opdateret: {t['notes']}")

    # ── Test 11: Paginering ────────────────────────────────
    print("\n[11] Paginering med limit=2, offset=2")
    first_page = await trade_queries.list_trades(journal.db, limit=2, offset=0)
    second_page = await trade_queries.list_trades(journal.db, limit=2, offset=2)
    assert len(first_page) == 2
    assert len(second_page) == 2
    # Ingen overlap
    first_ids = {t["trade_id"] for t in first_page}
    second_ids = {t["trade_id"] for t in second_page}
    assert not (first_ids & second_ids), "Paginering har overlap"
    print(f"    ✓ 2 sider á 2 trades, ingen overlap")

    await journal.close()
    Path(TEST_DB).unlink()
    print("\n✓ Alle 11 endpoint-tests bestået — test-DB slettet")


if __name__ == "__main__":
    asyncio.run(main())