"""
test_position_ledger.py
────────────────────────
Grundig enhedstest af position_ledger.py. Bygger en midlertidig test-database
og verificerer hver funktion mod kendte scenarier. KOERER IKKE mod den rigtige
journal — opretter sin egen temp-db.

Koer i backend-mappen EFTER position_ledger.py er oprettet:
    python test_position_ledger.py

Alle tests skal printe PASS. Foerste FAIL stopper med besked om hvad der gik galt.
"""

import os
import sqlite3
import tempfile

import position_ledger as pl


def _make_db(rows):
    """Opret temp-db med trades-tabel og indsaet rows.
    rows: liste af (source, symbol, side, shares, entry_price, exit_time_utc)."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    c = sqlite3.connect(path)
    c.execute(
        "CREATE TABLE trades (source TEXT, symbol TEXT, side TEXT, "
        "shares INTEGER, entry_price REAL, exit_time_utc TEXT)"
    )
    c.executemany("INSERT INTO trades VALUES (?,?,?,?,?,?)", rows)
    c.commit()
    c.close()
    return path


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        raise SystemExit(1)


# ── Test 1: to long-strategier i samme ticker holdes adskilt ──
def test_same_ticker_long():
    db = _make_db([
        ("Momentum ORB", "VCIG", "long", 200, 5.80, None),
        ("Konfluens",    "VCIG", "long", 100, 5.85, None),
        ("Momentum ORB", "ABSI", "long",  50, 6.00, None),
    ])
    pos = pl.positions_by_source(db)
    check("ORB ejer 200 VCIG",
          pos.get(("VCIG", "Momentum ORB"), {}).get("shares") == 200, pos)
    check("Konfluens ejer 100 VCIG",
          pos.get(("VCIG", "Konfluens"), {}).get("shares") == 100, pos)
    check("ORB avg_entry VCIG = 5.80",
          pos.get(("VCIG", "Momentum ORB"), {}).get("avg_entry") == 5.80, pos)
    check("ABSI kun ORB, 50 stk",
          pos.get(("ABSI", "Momentum ORB"), {}).get("shares") == 50, pos)
    os.unlink(db)


# ── Test 2: lukkede handler ignoreres ──
def test_closed_ignored():
    db = _make_db([
        ("Konfluens", "KSS", "long", 80, 20.0, "2026-06-02T14:00:00Z"),  # lukket
        ("Konfluens", "VCIG", "long", 50, 5.0, None),                     # aaben
    ])
    pos = pl.positions_by_source(db)
    check("Lukket KSS ikke med", ("KSS", "Konfluens") not in pos, pos)
    check("Aaben VCIG med", ("VCIG", "Konfluens") in pos, pos)
    os.unlink(db)


# ── Test 3: netto pr. symbol summerer paa tvaers af sources ──
def test_account_net():
    db = _make_db([
        ("Momentum ORB", "VCIG", "long", 200, 5.80, None),
        ("Konfluens",    "VCIG", "long", 100, 5.85, None),
        ("Momentum ORB", "ABSI", "long",  50, 6.00, None),
    ])
    net = pl.account_net_by_symbol(db)
    check("VCIG netto = 300", net.get("VCIG") == 300, net)
    check("ABSI netto = 50", net.get("ABSI") == 50, net)
    os.unlink(db)


# ── Test 4: short giver negativt netto ──
def test_short_negative():
    db = _make_db([
        ("StratA", "TLRY", "long",  300, 2.40, None),
        ("StratB", "TLRY", "short", 100, 2.45, None),
    ])
    net = pl.account_net_by_symbol(db)
    check("TLRY netto = 300-100 = 200", net.get("TLRY") == 200, net)
    os.unlink(db)


# ── Test 5: afstemning — alle match ──
def test_reconcile_match():
    db = _make_db([
        ("Momentum ORB", "VCIG", "long", 200, 5.80, None),
        ("Konfluens",    "VCIG", "long", 100, 5.85, None),
        ("Momentum ORB", "ABSI", "long",  50, 6.00, None),
    ])
    ibkr = [
        {"ticker": "VCIG", "position": 300, "avg_cost": 5.82},
        {"ticker": "ABSI", "position": 50,  "avg_cost": 6.00},
    ]
    r = pl.reconcile_against_ibkr(db, ibkr)
    check("VCIG+ABSI matcher", sorted(r["match"]) == ["ABSI", "VCIG"], r)
    check("ingen divergens", r["divergence"] == [], r)
    check("ingen ibkr_only", r["ibkr_only"] == [], r)
    check("ingen journal_only", r["journal_only"] == [], r)
    os.unlink(db)


# ── Test 6: afstemning — forældreløs (IBKR holder, journal kender ikke) ──
def test_reconcile_orphan():
    db = _make_db([
        ("Momentum ORB", "VCIG", "long", 200, 5.80, None),
    ])
    ibkr = [
        {"ticker": "VCIG", "position": 200, "avg_cost": 5.80},
        {"ticker": "TSLA", "position": 10,  "avg_cost": 200.0},  # foraeldreloes
    ]
    r = pl.reconcile_against_ibkr(db, ibkr)
    check("VCIG matcher", r["match"] == ["VCIG"], r)
    check("TSLA er ibkr_only",
          r["ibkr_only"] == [{"symbol": "TSLA", "ibkr": 10}], r)
    os.unlink(db)


# ── Test 7: afstemning — journal-position IBKR ikke holder ──
def test_reconcile_journal_only():
    db = _make_db([
        ("Konfluens", "ABSI", "long", 50, 6.0, None),
    ])
    ibkr = []  # IBKR holder intet
    r = pl.reconcile_against_ibkr(db, ibkr)
    check("ABSI er journal_only",
          r["journal_only"] == [{"symbol": "ABSI", "journal": 50}], r)
    os.unlink(db)


# ── Test 8: afstemning — antal stemmer ikke (divergens) ──
def test_reconcile_divergence():
    db = _make_db([
        ("Momentum ORB", "VCIG", "long", 200, 5.80, None),
    ])
    ibkr = [{"ticker": "VCIG", "position": 150, "avg_cost": 5.80}]  # IBKR har 150, journal 200
    r = pl.reconcile_against_ibkr(db, ibkr)
    check("VCIG er divergens",
          r["divergence"] == [{"symbol": "VCIG", "journal": 200, "ibkr": 150}], r)
    os.unlink(db)


# ── Test 9: tom journal giver tomme resultater (ingen crash) ──
def test_empty():
    db = _make_db([])
    check("tom positions_by_source", pl.positions_by_source(db) == {}, "")
    check("tom account_net", pl.account_net_by_symbol(db) == {}, "")
    r = pl.reconcile_against_ibkr(db, [])
    check("tom afstemning",
          r == {"match": [], "divergence": [], "ibkr_only": [], "journal_only": []}, r)
    os.unlink(db)


# ── Test 10: manglende db-fil crasher ikke ──
def test_missing_db():
    check("manglende db → tom", pl.positions_by_source("/findes/ikke.db") == {}, "")


if __name__ == "__main__":
    print("Test af position_ledger.py\n")
    test_same_ticker_long()
    test_closed_ignored()
    test_account_net()
    test_short_negative()
    test_reconcile_match()
    test_reconcile_orphan()
    test_reconcile_journal_only()
    test_reconcile_divergence()
    test_empty()
    test_missing_db()
    print("\nALLE TESTS BESTAAET ✓")
