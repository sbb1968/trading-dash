"""
test_manual_trade_endpoints.py
──────────────────────────────
End-to-end test af /journal/manual-trade og .../close.

Vi kører backenden in-process med TestClient og mocker IBKR-forbindelsen
så vi kan teste hele flowet uden TWS.

Test-DB: trading_dash.db (vi rydder op til sidst — kun de trades vi
opretter).
"""
import asyncio
import os
import sys
import unittest.mock as mock
from pathlib import Path

# Backenden importerer mange ting ved load — vi skal sikre at vi kører
# fra backend-mappen så Python finder alt
sys.path.insert(0, str(Path(__file__).parent))


# ─────────────────────────────────────────────────────────────────
# Fake IBKRConnection — simulerer paper-fills uden TWS
# ─────────────────────────────────────────────────────────────────

class FakeIBKR:
    """
    Mock af IBKRConnection. Returnerer succesfulde MKT-fills med
    en bestemt pris pr. ticker så vi kan teste P&L-beregninger.
    """

    def __init__(self, fill_prices: dict[str, float], connected: bool = True):
        """
        fill_prices: {"CLOV": 2.50, ...} — næste fill-pris pr. ticker.
        Hver gang place_paper_order kaldes, bruges den aktuelle pris.
        """
        self.fill_prices = fill_prices
        self.connected = connected
        self.orders_placed = []   # liste af (ticker, action, qty)
        self._next_order_id = 1000

    async def place_paper_order(self, ticker, action, quantity, order_type="MKT", limit_price=0):
        self.orders_placed.append((ticker, action, quantity))
        order_id = self._next_order_id
        self._next_order_id += 1

        fill_price = self.fill_prices.get(ticker, 0)
        if fill_price <= 0:
            return None    # simulér IBKR-fejl

        return {
            "ticker":   ticker,
            "action":   action,
            "quantity": quantity,
            "order_id": order_id,
            "status":   "Filled",
            "filled":   quantity,
            "avg_fill": fill_price,
        }


# ─────────────────────────────────────────────────────────────────
# Main test
# ─────────────────────────────────────────────────────────────────

async def run_tests():
    # Importér main først — det initialiserer FastAPI app, journal osv.
    import main
    from fastapi.testclient import TestClient
    import trade_queries
    import journal as journal_module

    # Initialisér journal (det sker normalt i startup-event)
    if main.journal._db is None:
        await main.journal.init()

    # ── Saml trade_ids vi opretter, så vi kan rydde op til sidst ──
    created_trade_ids = []

    fake = FakeIBKR(fill_prices={
        "CLOV": 2.50,
        "AMC":  4.20,
        "GME":  15.50,
    })

    # Patch strategy_manager.get_ibkr så endpoints bruger vores fake
    with mock.patch.object(main.strategy_manager, "get_ibkr", return_value=fake):
        client = TestClient(main.app)

        # ── Test 1: Åbn manuel long-position ─────────────────
        print("\n[1] POST /journal/manual-trade — open long CLOV")
        r = client.post("/journal/manual-trade", json={
            "symbol": "CLOV",
            "side":   "long",
            "shares": 100,
            "notes":  "Test-handel #1",
        })
        assert r.status_code == 200, f"Forventede 200, fik {r.status_code}: {r.text}"
        data = r.json()
        assert data["ok"] is True
        assert data["symbol"] == "CLOV"
        assert data["side"] == "long"
        assert data["shares"] == 100
        assert data["entry_price"] == 2.50
        clov_trade_id = data["trade_id"]
        created_trade_ids.append(clov_trade_id)
        print(f"    ✓ trade_id: {clov_trade_id}")
        print(f"    ✓ entry_price: ${data['entry_price']}")
        print(f"    ✓ IBKR-ordre placeret: {fake.orders_placed[-1]}")
        assert fake.orders_placed[-1] == ("CLOV", "BUY", 100)

        # ── Test 2: Åbn manuel short-position ────────────────
        print("\n[2] POST /journal/manual-trade — open short AMC")
        r = client.post("/journal/manual-trade", json={
            "symbol": "AMC",
            "side":   "short",
            "shares": 50,
        })
        assert r.status_code == 200
        data = r.json()
        amc_trade_id = data["trade_id"]
        created_trade_ids.append(amc_trade_id)
        assert fake.orders_placed[-1] == ("AMC", "SELL", 50)
        print(f"    ✓ Short åbnet med SELL: {fake.orders_placed[-1]}")

        # ── Test 3: Verificér trades i DB ────────────────────
        print("\n[3] GET /journal/open-positions — verificér 2 åbne")
        r = client.get("/journal/open-positions")
        assert r.status_code == 200
        positions = r.json()["positions"]
        # Filtrer kun vores test-trades (der kan være andre i DB)
        our_open = [p for p in positions if p["trade_id"] in created_trade_ids]
        assert len(our_open) == 2
        print(f"    ✓ Begge åbne positioner fundet i DB")

        # ── Test 4: Luk long CLOV med profit ─────────────────
        # Skift fill-prisen så vi får +5% profit
        fake.fill_prices["CLOV"] = 2.625
        print("\n[4] POST .../close — luk CLOV med profit (2.50 → 2.625)")
        r = client.post(f"/journal/manual-trade/{clov_trade_id}/close", json={
            "notes": "Tog 5% profit",
        })
        assert r.status_code == 200, f"Forventede 200, fik {r.status_code}: {r.text}"
        data = r.json()
        assert data["exit_price"] == 2.625
        # P&L = (2.625 - 2.50) * 100 = 12.50
        assert data["pnl"] == 12.50, f"Forventet pnl 12.50, fik {data['pnl']}"
        assert data["pnl_pct"] == 5.0
        print(f"    ✓ Exit @ ${data['exit_price']}, P&L=${data['pnl']} ({data['pnl_pct']}%)")
        assert fake.orders_placed[-1] == ("CLOV", "SELL", 100)

        # ── Test 5: Luk short AMC med profit ─────────────────
        # Short tjener når prisen falder
        fake.fill_prices["AMC"] = 4.00
        print("\n[5] POST .../close — luk short AMC med profit (4.20 → 4.00)")
        r = client.post(f"/journal/manual-trade/{amc_trade_id}/close", json={})
        assert r.status_code == 200
        data = r.json()
        assert data["exit_price"] == 4.00
        # Short P&L = (entry - exit) * shares = (4.20 - 4.00) * 50 = 10.00
        assert data["pnl"] == 10.0
        # Short pnl_pct = (4.20 - 4.00) / 4.20 * 100 = 4.7619...
        assert abs(data["pnl_pct"] - 4.76) < 0.01
        print(f"    ✓ Short-close med BUY: {fake.orders_placed[-1]}")
        print(f"    ✓ P&L=${data['pnl']} ({data['pnl_pct']}%)")
        assert fake.orders_placed[-1] == ("AMC", "BUY", 50)

        # ── Test 6: Forsøg at lukke allerede lukket trade ────
        print("\n[6] POST .../close — allerede lukket trade")
        r = client.post(f"/journal/manual-trade/{clov_trade_id}/close", json={})
        assert r.status_code == 400, f"Forventede 400, fik {r.status_code}"
        assert "allerede lukket" in r.json()["detail"].lower()
        print(f"    ✓ {r.json()['detail'][:80]}")

        # ── Test 7: Forsøg at lukke en algo-trade ────────────
        # Vi laver en fake algo-trade direkte i DB'en
        print("\n[7] POST .../close — algo-trade kan ikke lukkes manuelt")
        from datetime import datetime
        import pytz
        ET = pytz.timezone("America/New_York")
        algo_trade_id = await main.journal.log_trade_open(
            source="Momentum ORB",   # IKKE manual
            symbol="GME",
            side="long",
            shares=100,
            entry_price=15.50,
            entry_time=datetime.now(ET),
            variant="all_winner",
            entry_reason="Algo test",
        )
        created_trade_ids.append(algo_trade_id)

        r = client.post(f"/journal/manual-trade/{algo_trade_id}/close", json={})
        assert r.status_code == 400
        assert "ikke en manuel" in r.json()["detail"].lower()
        print(f"    ✓ Algo-trade afvist: {r.json()['detail'][:80]}")

        # ── Test 8: Ukendt symbol returnerer 500 ─────────────
        print("\n[8] POST /journal/manual-trade — ukendt symbol → IBKR fail")
        r = client.post("/journal/manual-trade", json={
            "symbol": "NOSUCH",
            "side":   "long",
            "shares": 100,
        })
        # Vores FakeIBKR returnerer None for tickers den ikke kender
        assert r.status_code == 500
        print(f"    ✓ Returnerede 500: {r.json()['detail'][:80]}")

        # ── Test 9: Notes merge ved close ────────────────────
        print("\n[9] Notes merge ved close")
        # Verificér at CLOV-trade har både entry- og close-notes
        clov = await trade_queries.get_trade_by_id(main.journal.db, clov_trade_id)
        notes = clov.get("notes") or ""
        assert "Test-handel #1" in notes
        assert "Tog 5% profit" in notes
        assert "[CLOSE]" in notes
        print(f"    ✓ Notes merged: {notes!r}")

        # ── Test 10: Lukket trade fra summary ────────────────
        print("\n[10] GET /journal/today — lukket-summary inkluderer vores trades")
        r = client.get("/journal/today")
        today_data = r.json()
        our_closed = [
            t for t in today_data["trades"]
            if t["trade_id"] in created_trade_ids and t["exit_time_utc"]
        ]
        # 2 lukkede manuelle (CLOV, AMC); algo GME er stadig åben
        assert len(our_closed) == 2
        our_pnl = sum(t["pnl"] for t in our_closed if t["pnl"])
        assert our_pnl == 22.50    # 12.50 + 10.00
        print(f"    ✓ 2 af vores trades er lukket, samlet P&L: ${our_pnl}")

    # ── Cleanup: slet test-trades ────────────────────────────
    print("\n[Cleanup] Sletter test-trades fra DB")
    for tid in created_trade_ids:
        await main.journal.db.execute(
            "DELETE FROM trades WHERE trade_id = ?", (tid,)
        )
    await main.journal.db.commit()
    print(f"    ✓ {len(created_trade_ids)} test-trades slettet")

    await main.journal.close()

    print("\n✓ Alle 10 manual-trade endpoint-tests bestået")


if __name__ == "__main__":
    asyncio.run(run_tests())