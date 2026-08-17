#!/usr/bin/env python3
"""
v6_moenstersoegning.py — find kontroller hvis fejl behandles som en beståelse
════════════════════════════════════════════════════════════════════════════════
Arbejdsordrens T3/V6. Fejlklassen er fundet **ni gange** i dette projekt nu, i
skiftende forklædning:

  1. Regime-motorens V1/V4 — "≥3 af 4 etiketter" bestod på hvid støj
  2. `qualifyContractsAsync` + `if q:` — truthy liste også ved fejl (15 af 58 steder)
  3. F1-testen — `"2 kopieret" in output` matchede glad `"102 kopieret"`
  4. Arkivverifikation — "0 fejl fundet" med disken frakoblet
  5. V-test 3's oprindelige kriterium — målte diskretisering, ikke motoren
  6. Signal-evalueringens nævner — 11 barer mod 1 gav ALT et løft omkring 5
  7. Reconcile-timeout — "fortsætter til handel"
  8. Journal-500 — NaN tog hele vinduet
  9. Reconcile-jobbets første prøve — testede en kopi, ikke koden

Den stående regel: **formulér det input der ville få kontrollen til at fejle, og
kør det. Kan du ikke formulere sådan et input, er det ikke en kontrol.**

⚠ DETTE VÆRKTØJ FINDER KANDIDATER, IKKE FEJL. Et `except: pass` kan være helt
rigtigt (journalisering må aldrig vælte handelsflowet). Værktøjet rangerer efter
hvor meget der står på spil, og et menneske dømmer. En scanner der selv afgjorde
hvad der var en fejl, ville være… en kontrol hvis fejl behandles som en beståelse.

    python v6_moenstersoegning.py
    python v6_moenstersoegning.py --alle      # ogsaa lav-risiko-fund
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROD = Path(__file__).resolve().parent

# ⚠ Filer hvor en slugt fejl koster PENGE. Rangeringen er ikke kosmetik — en
# swallow i en logger er ligegyldig, den samme i ordreflowet er ikke.
KRITISK = ("algo_", "strategy_base", "risk", "ibkr", "order", "reconcile",
           "journal", "scheduler", "paper_trading", "main.py")

SPRING_OVER = ("venv", "__pycache__", "node_modules", "test_", "_test",
               "backtest", "analyse", "probe", "diag_")


def vaegt(sti: Path) -> int:
    n = sti.name.lower()
    if any(k in n for k in KRITISK):
        return 2
    return 1


class Finder(ast.NodeVisitor):
    """Går AST'et igennem — ikke tekst. En regex på `except` rammer også
    kommentarer og strenge, og så bruger man en time på falske fund."""

    def __init__(self, sti: Path, kilde: str):
        self.sti = sti
        self.linjer = kilde.splitlines()
        self.fund: list[tuple[int, str, str]] = []

    # ── except-blokke ────────────────────────────────────────────────────────
    def visit_ExceptHandler(self, node: ast.ExceptHandler):
        krop = node.body
        # bare `pass`
        if len(krop) == 1 and isinstance(krop[0], ast.Pass):
            self._meld(node.lineno, "except: pass",
                       "fejlen sluges helt — ingen log, ingen adfærdsændring")
        # `return True` / `return <sandhed>` i except
        for s in krop:
            if isinstance(s, ast.Return) and isinstance(s.value, ast.Constant) \
                    and s.value.value is True:
                self._meld(s.lineno, "except: return True",
                           "FEJL RAPPORTERES SOM SUCCES")
            if isinstance(s, ast.Return) and s.value is None:
                self._meld(s.lineno, "except: return (None)",
                           "afbryder stille — kalderen kan ikke se forskel på "
                           "'intet at gøre' og 'det gik galt'")
        # kun logning, ingen raise/return/flag
        if krop and all(self._er_ren_logning(s) for s in krop):
            self._meld(node.lineno, "except: kun log",
                       "logges og fortsættes — samme udfald som succes")
        self.generic_visit(node)

    def _er_ren_logning(self, s: ast.stmt) -> bool:
        if not isinstance(s, ast.Expr) or not isinstance(s.value, ast.Call):
            return False
        f = s.value.func
        navn = getattr(f, "attr", None) or getattr(f, "id", "")
        return navn in ("debug", "info", "warning", "error", "exception", "print")

    # ── timeouts der fortsætter ──────────────────────────────────────────────
    # ⚠ HÅNDTERINGEN LIGGER TIT UDEN FOR except-BLOKKEN. Første udgave kiggede
    # kun i selve handleren og meldte derfor alle seks strategier — den kode
    # der netop var rettet, hvor genforsøgsløkken sætter et flag og spærringen
    # sker BAGEFTER. En scanner der sender folk hen til allerede rettet kode,
    # er værre end ingen scanner. Vinduet er nu hele den omsluttende funktion.
    #
    # ⚠ Det er GROVT, og det står her frem for at blive kaldt præcist: en
    # funktion der spærrer ét sted og sluger et andet, går fri. Værktøjet
    # peger; mennesket dømmer.
    def visit_FunctionDef(self, node):
        self._fn_kilde = ast.unparse(node)
        self.generic_visit(node)
        self._fn_kilde = ""

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Try(self, node: ast.Try):
        for h in node.handlers:
            t = ast.unparse(h.type) if h.type else ""
            if "TimeoutError" in t:
                i_handler = any(
                    isinstance(s, (ast.Raise, ast.Return)) or
                    (isinstance(s, ast.Expr) and "spaer" in ast.unparse(s).lower())
                    for s in h.body)
                fn = getattr(self, "_fn_kilde", "")
                i_funktion = any(k in fn for k in
                                 ("spaer_entries", "raise ", "return False"))
                if not i_handler and not i_funktion:
                    self._meld(h.lineno, "timeout → fortsæt",
                               "en kontrol der løb tør for tid, behandles som "
                               "bestået (samme som reconcile 13-08)")
        self.generic_visit(node)

    def _meld(self, linje: int, slags: str, hvorfor: str):
        kode = self.linjer[linje - 1].strip() if linje <= len(self.linjer) else ""
        self.fund.append((linje, slags, f"{hvorfor}   |   {kode[:70]}"))


def scan(alle: bool = False):
    filer = [p for p in ROD.rglob("*.py")
             if not any(s in str(p) for s in SPRING_OVER)]
    resultat = []
    for p in sorted(filer):
        try:
            kilde = p.read_text(encoding="utf-8")
            traeet = ast.parse(kilde)
        except Exception:
            continue
        f = Finder(p, kilde)
        f.visit(traeet)
        for linje, slags, hvorfor in f.fund:
            resultat.append((vaegt(p), p.name, linje, slags, hvorfor))

    # rangér: kritiske filer først, derefter de groveste mønstre
    RANG = {"except: return True": 0, "timeout → fortsæt": 1,
            "except: pass": 2, "except: kun log": 3, "except: return (None)": 4}
    resultat.sort(key=lambda r: (-r[0], RANG.get(r[3], 9), r[1], r[2]))

    print(f"V6 — kontroller hvis fejl kan blive til en beståelse\n"
          f"{len(filer)} filer scannet · {len(resultat)} kandidater\n")
    print("⚠ KANDIDATER, IKKE FEJL. Et slugt kald kan være helt rigtigt.")
    print("   Spørgsmålet ved hver: HVAD ville en bruger se, hvis den her fejler?\n")

    grupper: dict[str, list] = {}
    for r in resultat:
        grupper.setdefault(r[3], []).append(r)

    for slags in sorted(grupper, key=lambda s: RANG.get(s, 9)):
        raek = grupper[slags]
        kritiske = [r for r in raek if r[0] == 2]
        print(f"── {slags}  ({len(raek)} i alt, {len(kritiske)} i kritiske filer) ──")
        vis = raek if alle else kritiske
        for _, fil, linje, _, hvorfor in vis[:25]:
            print(f"   {fil}:{linje}")
            print(f"      {hvorfor}")
        if not alle and len(raek) > len(kritiske):
            print(f"   … {len(raek)-len(kritiske)} uden for kritiske filer "
                  f"(vis med --alle)")
        print()
    return resultat


if __name__ == "__main__":
    scan(alle="--alle" in sys.argv)
