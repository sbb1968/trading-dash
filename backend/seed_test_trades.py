"""
seed_test_trades.py
───────────────────
Indsæt fake trades i den lokale trading_dash.db så vi kan se hvordan
Studio's Oversigt og Journal ser ud med rigtige data.

Kør:
    python seed_test_trades.py         # indsæt fake data
    python seed_test_trades.py --clear # slet de indsatte fake data

Alle fake trades har payload.test_fake_seed=true så vi kan identificere
og rydde dem op uden at røre rigtige trades.
"""
import asyncio
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import aiosqlite
import pytz

from journal import Journal

ET = pytz.timezone("America/New_York")
FAKE_MARKER = "test_fake_seed"   # payload-key der markerer fake trades


async def seed():
    journal = Journal("trading_dash.db")
    await journal.init()

    now_et = datetime.now(ET)

    # Vi simulerer en handelsdag der startede 09:30 ET — find dagens "09:30"
    today_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)

    trades_to_create = [
        # (entry_offset_min, exit_offset_min, source, variant, symbol, side, shares, entry, exit, reason, stop, target)
        # ── Vindere ────────────────────────────────────────
        (15, 45, "Momentum ORB", "all_winner", "CLOV",  "long",  250, 2.50,  2.625, "target",    2.45, 2.60),
        (20, 30, "Konfluens",    "baseline",   "HCAI",  "long",  100, 8.77, 14.69,  "trail",     8.55, None),
        (35, 90, "Momentum ORB", "all_winner", "TLRY",  "long",  400, 1.50,  1.62,  "trail",     1.45, 1.56),
        # ── Tabere ────────────────────────────────────────
        (25, 55, "Konfluens",    "baseline",   "BBIG",  "long",  150, 6.20,  6.05,  "stop",      6.05, None),
        (50, 75, "Momentum ORB", "all_winner", "OCGN",  "long",  300, 2.10,  2.06,  "stop",      2.06, 2.18),
        # ── Manuel handel (vinder) ────────────────────────
        (60, 120, "manual",      None,         "GME",   "long",  50, 22.50, 23.85,  "manual",    None, None),
        # ── Manuel handel (taber) ─────────────────────────
        (100, 150, "manual",     None,         "AMC",   "long",  100, 4.20,  4.05,  "manual",    None, None),
    ]

    # Beregn åben-status: alle trades med exit_offset > current minutes er stadig åbne.
    # Hvis markedet ikke er åbnet endnu i ET (vi er tidlig morgen Dansk tid),
    # simulerer vi at vi er forbi dagens handler så vi ser realistisk data.
    current_offset = (now_et - today_open).total_seconds() / 60
    if current_offset < 0:
        print("  (Markedet er ikke aabnet i ET endnu - simulerer 'efter handelsdag')")
        current_offset = 9999

    created_ids = []
    open_count = 0
    closed_count = 0

    for offset_in, offset_out, source, variant, sym, side, shares, entry_p, exit_p, reason, stop, target in trades_to_create:
        entry_time = today_open + timedelta(minutes=offset_in)

        trade_id = await journal.log_trade_open(
            source=source,
            symbol=sym,
            side=side,
            shares=shares,
            entry_price=entry_p,
            entry_time=entry_time,
            variant=variant,
            entry_reason=f"Fake test trade ({source})",
            current_stop=stop,
            current_target=target,
            current_stage="initial",
            notes="Test-handel — auto-genereret af seed_test_trades.py",
            payload={FAKE_MARKER: True, "seed_run_at": now_et.isoformat()},
        )

        if trade_id is None:
            print(f"  ⚠ Kunne ikke oprette {sym}")
            continue

        created_ids.append(trade_id)

        # Hvis exit-tidspunkt er passé, luk handlen
        if offset_out <= current_offset:
            exit_time = today_open + timedelta(minutes=offset_out)
            if side == "long":
                pnl = (exit_p - entry_p) * shares
            else:
                pnl = (entry_p - exit_p) * shares

            await journal.log_trade_close(
                trade_id=trade_id,
                exit_price=exit_p,
                exit_time=exit_time,
                exit_reason=reason,
                pnl=pnl,
            )
            closed_count += 1
        else:
            open_count += 1

    await journal.close()

    print(f"\n✓ Indsat {len(created_ids)} fake trades:")
    print(f"    {closed_count} lukkede")
    print(f"    {open_count} stadig aabne")
    print(f"\n  Aabn http://127.0.0.1:8000/studio og se Oversigt-fanen")
    print(f"  Koer 'python seed_test_trades.py --clear' for at rydde op")


async def clear():
    journal = Journal("trading_dash.db")
    await journal.init()

    # Slet alle trades hvor payload indeholder vores marker
    # SQLite har ikke nem JSON-search, så vi tjekker LIKE på payload_json
    pattern = f'%"{FAKE_MARKER}": true%'

    async with journal.db.execute(
        "SELECT trade_id, symbol FROM trades WHERE payload_json LIKE ?",
        (pattern,)
    ) as cur:
        rows = await cur.fetchall()

    if not rows:
        print("Ingen fake trades fundet — intet at rydde op.")
        await journal.close()
        return

    print(f"Sletter {len(rows)} fake trades:")
    for trade_id, symbol in rows:
        print(f"  - {symbol} ({trade_id[:8]}...)")

    await journal.db.execute(
        "DELETE FROM trades WHERE payload_json LIKE ?",
        (pattern,)
    )
    await journal.db.commit()
    await journal.close()
    print(f"\n✓ {len(rows)} fake trades slettet")


async def main():
    if "--clear" in sys.argv:
        await clear()
    else:
        await seed()


if __name__ == "__main__":
    asyncio.run(main())