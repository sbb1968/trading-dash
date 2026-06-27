# SPEC (UDKAST): Faktorisér reconcile-close til én delt helper

> **Status:** UDKAST fra VS Code Claude — til desktop Claudes gennemgang/finpudsning.
> **Type:** Refaktor i HANDELSSTIEN (reconcile). **Adfærds-bevarende** — ingen ny logik.
> **⚠ DEPLOY-VINDUE:** reconcile kører ved opstart → kun uden for session (weekend/før session).
> **Ikke-hastende:** den funktionelle fix er allerede på plads (`4ee58d7`); dette er hærdning.

---

## 1. Problemet

Den FARLIGE klassifikation er allerede delt og testet (`reconcile_idempotency.decide_confirmation`).
MEN plumbingen omkring den findes stadig som **fire nær-identiske kopier**, én pr. algo:

- `_reconcile_close(...)` — afgiv close + sæt markør + deterministisk orderRef + fyld-verifikation
- `_reconcile_confirm(...)` — bekræftelses-sti (kald decide_confirmation, udfør beslutningen)
- `_reconcile_mark_filled(...)` — bogfør reconcile_flatten (nul-P&L est.)
- `_reconcile_mark_closed(...)` — fantom (IBKR flad, ingen prior close)

Det er PRÆCIS den copy-paste-struktur der lod den oprindelige over-sell ramme én algo ad
gangen (dup-vagten blev fikset i `get_open_orders`, men kunne lige så godt have drevet fra
sig i fire kopier). Hver fremtidig reconcile-ændring skal i dag laves fire steder identisk.

---

## 2. Mål

Én delt, testet helper som alle fire algoer DELEGERER til, så reconcile-close-logikken kun
findes ét sted. Adfærd 100% uændret (accept: `test_k2_close_robusthed.py` forbliver grøn).

---

## 3. Designskitse

Udvid `reconcile_idempotency.py` (eller nyt `reconcile_helpers.py`) med async helpers der
opererer på den UNIFORME grænseflade alle fire algoer allerede har: `algo.conn`,
`algo._journal`, `algo._log`, `algo.name`. Per-algo-forskellene parametriseres:

- **Signatur:** equity-algoer bruger (sym, row, shares, sign); EUREVERSION (sym, qty, row).
  Normalisér internt til (sym, shares, sign, net) — EUREVERSION-wrapperen udleder
  shares=int(abs(qty)), sign=1 if qty>0 else -1.
- **P&L ved bekræftet fyld:** i dag bruger alle fire nul-P&L est. i `_reconcile_mark_filled`
  (fyld-prisen kendes ikke). Hold dét — så multiplier-forskellen (futures) er irrelevant her.
  (Den rigtige `_reconcile_close`-P&L har futures-multiplier; hvis den også deles, send
  `multiplier`/`pnl_fn` ind som parameter.)
- **Log-præfiks:** brug `algo.name`.

Foreslået API (skitse):
```python
async def reconcile_dispatch(algo, row, net, *, place_close, log_tag=None) -> bool:
    """Én row gennem hele idempotens-maskineriet: stage-gate → confirm/place/mark.
    place_close: async (sym,row,shares,sign) der afgiver close (per-algo P&L)."""
```
Hver algo beholder en TYND `_reconcile_close` (den eneste reelle per-algo-forskel: P&L),
men `_reconcile_confirm`/`_reconcile_mark_filled`/gaten flytter ind i helperen.

ALTERNATIV (mindre ambitiøst): behold metoderne, men flyt KUN bekræftelses-beslutningen +
mark_filled til delte funktioner; lad gaten i `_reconcile_orphans_impl` blive. Mindre diff,
men stadig fire gate-kopier. Desktop Claude vælger ambitionsniveau.

---

## 4. Omfang

`reconcile_idempotency.py` (helper) + de fire algoer (`algo_confluence2`, `algo_buythedip`,
`algo_trendjoin`, `algo_europa_reversion`) → delegation. INGEN ændring i entry/exit-logik.

---

## 5. Tests (accept-kriterie)

- `test_k2_close_robusthed.py` ALLE grønne, UÆNDRET (den driver den RIGTIGE K2-reconcile
  gennem helperen) — H1–H4, Sektion I, A–G.
- Tilføj gerne en tynd EUREVERSION-harness (futures/qty-signaturen) så ikke kun K2 dækkes.
- `git diff --stat` skal vise at de fire algo-filer SKRUMPER (logik flyttet til helper).

---

## 6. Risiko

Adfærds-bevarende refaktor; testen er værnet. Eneste reelle risiko er at en per-algo-nuance
(EUREVERSION-multiplier, K2 Position-objekt vs dict) tabes i normaliseringen — derfor skal
EUREVERSION også have en test før deploy. Kun i sikkert vindue.

---

## 7. Hvorfor nu / hvorfor ikke

PRO: fjerner den sidste copy-paste-flade i reconcile → denne fejlklasse kan aldrig ramme én
algo ad gangen igen. CON: rører fire trading-sti-filer for nul funktionel gevinst. Derfor
ikke-hastende — tag den næste gang reconcile alligevel skal røres, eller i et roligt vindue.
```
