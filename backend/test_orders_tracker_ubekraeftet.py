"""
test_orders_tracker_ubekraeftet.py — et gæt må ikke stå som faktum
════════════════════════════════════════════════════════════════════════════════
`record_placed` skriver `status: "Submitted"` **ved placeringen**. Det er ikke en
måling — det er en antagelse om hvad der lige er sket. Live-afstemningen retter
den, men kun hvis den når ordren.

⚠ Målt 11-08 på Sørens workstation: to MES-ordrer fra 10-08 stod som
**"2 åbne · Afsendt · 0 fyldt"**. De blev fyldt og lukket samme aften — men de lå
på DUQ441063, og maskinen spurgte DUN748991 om dem. Den delte forbindelse svarer
ikke "nej"; den svarer **ingenting**, og gættet blev stående som fakta.

To ting manglede:

  1. **Kontoen blev slet ikke registreret.** Uden den kan en senere aflæsning
     ikke vide om den overhovedet *kan* kende ordren.
  2. **Intet skelnede gæt fra måling.** `bekraeftet` gør nu det.

"Vi ved det ikke" er et dårligere svar end sandheden — men et bedre svar end en
påstand.
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import orders_tracker as ot


class Conn:
    def __init__(self, konto="DUN748991", trades=()):
        self.connected = True
        self.account = konto
        self._trades = list(trades)
        conn = self

        class _IB:
            @staticmethod
            def trades():
                return conn._trades
        self.ib = _IB()


class _Trade:
    def __init__(self, oid, status="Filled", filled=1, avg=7778.75):
        self.order = type("o", (), {"orderId": oid})()
        self.orderStatus = type("s", (), {
            "status": status, "filled": filled, "remaining": 0,
            "avgFillPrice": avg})()


def _tracker_med(entries: list[dict]) -> ot.OrdersTracker:
    f = pathlib.Path(tempfile.mkdtemp()) / "orders_log.json"
    f.write_text(json.dumps(entries), encoding="utf-8")
    ot.ORDERS_LOG = f
    ot._tracker = None
    return ot.get_tracker()


def _entry(oid, minutter_siden, konto=None, bekraeftet=False, status="Submitted"):
    e = {
        "order_id": oid, "source": "manual_watchlist", "ticker": "MES",
        "action": "BUY", "shares": 1, "order_type": "MKT", "limit_price": None,
        "placed_at": (datetime.now() - timedelta(minutes=minutter_siden)).isoformat(),
        "status": status, "filled": 0, "remaining": 1, "avg_fill": 0,
        "bekraeftet": bekraeftet,
    }
    if konto:
        e["ibkr_account"] = konto
    return e


def _hent(t, conn):
    return asyncio.run(t.get_all_orders(conn, period_hours=72,
                                        sources={"manual_watchlist", "manual"}))


def test_ubekraeftet_fra_tidligere_koersel_er_UKENDT():
    """⚠ Kernen. Præcis de to MES-ordrer fra i går."""
    t = _tracker_med([_entry(4, 1200), _entry(7, 1195)])
    r = _hent(t, Conn())
    assert all(o["status"] == "UNKNOWN" for o in r), [o["status"] for o in r]
    assert all(o["status_group"] == "unknown" for o in r)
    assert all(o["note"] for o in r), "der skal staa HVORFOR"
    assert not [o for o in r if o["status_group"] == "open"], \
        "de taeller stadig som aabne"


def test_noten_naevner_kontoen_naar_den_kendes():
    t = _tracker_med([_entry(4, 1200, konto="DUQ441063")])
    r = _hent(t, Conn(konto="DUN748991"))
    n = r[0]["note"] or ""
    assert "DUQ441063" in n and "DUN748991" in n, n


def test_bekraeftet_status_staar_ved_magt():
    """Vagten må ikke gøre ALT ukendt.

    En ordre der ÉN gang er set live, beholder sin status efter genstart — det
    er en måling, ikke et gæt.
    """
    t = _tracker_med([_entry(4, 1200, bekraeftet=True, status="Filled")])
    r = _hent(t, Conn())
    assert r[0]["status"] == "Filled", r[0]
    assert r[0]["note"] is None
    assert r[0]["status_group"] == "filled"


def test_live_aflaesning_bekraefter_og_persisterer():
    """Når afstemningen NÅR ordren, bliver gættet til en måling."""
    t = _tracker_med([_entry(4, 1)])
    r = _hent(t, Conn(trades=[_Trade(4, "Filled", 1, 7776.0)]))
    assert r[0]["status"] == "Filled", r[0]
    assert r[0]["bekraeftet"] is True
    assert r[0]["avg_fill"] == 7776.0
    gemt = json.loads(ot.ORDERS_LOG.read_text(encoding="utf-8"))
    assert gemt[0]["bekraeftet"] is True, "bekraeftelsen overlevede ikke til disk"


def test_ordre_fra_DENNE_koersel_degraderes_ikke():
    """En ordre lagt for et øjeblik siden er ikke 'ubekræftet fra en tidligere
    session' — den er bare ny. Ellers ville hver eneste friske ordre blinke
    'ukendt' i sekunderne før første afstemning."""
    t = _tracker_med([_entry(9, 0)])
    r = _hent(t, Conn())
    assert r[0]["status"] == "Submitted", r[0]
    assert r[0]["status_group"] == "open"


if __name__ == "__main__":
    for navn, fn in sorted(globals().items()):
        if navn.startswith("test_"):
            fn()
            print(f"  OK  {navn}")
    print("\nAlle bestod.")
