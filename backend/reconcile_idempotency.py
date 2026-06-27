"""
reconcile_idempotency.py — delt, REN beslutningslogik for idempotent reconcile-close.
═════════════════════════════════════════════════════════════════════════════════════
Lukker Del C-residualen (over-sell paa et forsinket positions-feed). Ingen I/O, ingen
TWS — kun en ren funktion, saa den FARLIGE klassifikation (skal vi gen-placere en close?)
deles 1:1 af alle fire algoers reconcile og ikke kan drifte fra hinanden.

KERNE-INVARIANT: reconcile placerer ALDRIG en ny close naar udfaldet af en tidligere
close er UKENDT. Den durable markoer er journalens `current_stage = "reconcile_closing"`
(overlever genstart); orderRef'en er DETERMINISTISK pr. row, saa den kan rekonstrueres
uden at gemmes.
"""
from __future__ import annotations

RECONCILE_CLOSING = "reconcile_closing"   # journal current_stage-markoer

# Beslutninger i confirmation-stien (en row der allerede er i reconcile_closing):
WAIT    = "wait"       # ordren hviler stadig -> goer intet, tjek igen naeste pas
FLATTEN = "flatten"    # closen er bekraeftet fyldt (eller position flad) -> bogfoer lukket
RETRY   = "retry"      # closen er BEKRAEFTET doed-ufyldt + position stadig aaben -> frisk close
OBSERVE = "observe"    # udfald UKENDT + position stadig aaben (Del C) -> placér ALDRIG, observér

# Gyldige order_state-vaerdier fra conn.get_order_outcome(ref):
#   "active" | "filled" | "terminal_unfilled" | "not_findable"


def reconcile_close_ref(trade_id) -> str:
    """Deterministisk orderRef pr. row -> rekonstruérbar, behoever ikke gemmes."""
    return f"reconcile_close_{trade_id}"


def decide_confirmation(order_state: str, position_open: bool) -> str:
    """REN beslutning for en row i 'reconcile_closing'.

    `order_state`: udfaldet af den tidligere afgivne reconcile-close (fra conn).
    `position_open`: holder IBKR stadig positionen i samme retning (>= antallet)?

    Invariant: kun BEKRAEFTET doed-ufyldt (+ aaben position) udloeser en ny ordre;
    UKENDT udfald giver ALDRIG en ny ordre (det var Del C-over-sell-hullet)."""
    if order_state == "active":
        return WAIT
    if order_state == "filled":
        return FLATTEN
    if order_state == "terminal_unfilled":
        return RETRY if position_open else FLATTEN
    # not_findable -> brug positionen som korroboration
    if not position_open:
        return FLATTEN          # position flad bekraefter at closen fyldte (og aldede ud)
    return OBSERVE              # UKENDT udfald + position stadig aaben -> aldrig gen-placér
