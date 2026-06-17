"""
test_strategy_isolation.py
──────────────────────────
GUARD: strategier skal være TOTALT isolerede (kun globalt tabsmax deles).

Håndhæver isolations-kontrakten statisk (AST) på alle strategi-wrappers (algo_*.py).
Fejler hvis en strategi kobler sig på en anden — de to vektorer der reelt kan bide:

  A) Søskende-import: `from strategies.<anden>` ud over neutrale delte moduler.
  B) Krydskilde-læsning: et string-literal (ikke docstring) der nævner en ANDEN
     strategis source-navn (fx SQL'en `... source='Konfluens 2' ...` der koblede
     BuyTheDip til K2 før commit 1aa5cae).

Plus: enhver ny algo_*.py SKAL registreres her (tvinger en bevidst beslutning om
dens isolation). Ren statisk analyse — ingen import af strategierne, ingen TWS.

Kør:  python test_strategy_isolation.py
"""

import ast
import glob
import os
import re

BACKEND = os.path.dirname(os.path.abspath(__file__))


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        raise SystemExit(1)


# ── Registry: hver strategi-wrapper → (egen source, egen strategies-pakke|None) ──
# Tilføj nye strategier HER. Mangler en algo_*.py → testen fejler (med vilje).
STRATEGIES = {
    "algo_confluence.py":       ("Konfluens",        "strategies.confluence"),
    "algo_confluence2.py":      ("Konfluens 2",      "strategies.confluence2"),
    "algo_buythedip.py":        ("BuyTheDip",        None),   # selvstændig, ingen egen pakke
    "algo_europa_reversion.py": ("Europa-reversion", "strategies.europa_reversion"),
    "algo_momentum.py":         ("Momentum ORB",     "strategies.momentum_orb"),
}
ALL_SOURCES = [src for src, _ in STRATEGIES.values()]

# Neutrale delte moduler en strategi GERNE må importere (ikke en søskende-strategis logik).
NEUTRAL_SHARED = ("strategies.base",)
# Delt, TILSTANDSLØS screener-primitiv. Whitelisted indtil Trin 2 flytter den til et
# neutralt modul (så bor den under K1's pakke; det er infra, ikke K1-logik).
WHITELISTED_SHARED = ("strategies.confluence.tv_scanner",)

# Filer der matcher algo_*.py men IKKE er aktive strategier.
IGNORE_FILES = {"algo_momentum_backup.py"}


def _name_regex(name):
    """Match `name` som helt source-navn — undgå at ramme et længere søskende-navn
    der starter med `name ` (fx 'Konfluens' må ikke matche i 'Konfluens 2')."""
    suffixes = [n[len(name):].lstrip() for n in ALL_SOURCES
                if n != name and n.startswith(name + " ")]
    pat = re.escape(name)
    if suffixes:
        pat += r"(?!\s+(?:%s))" % "|".join(re.escape(s) for s in suffixes)
    return re.compile(pat)


def _docstring_ids(tree):
    """id() af de Constant-noder der er docstrings (module/class/func) — prosa må
    gerne nævne andre strategier ved navn; det er KODE-strenge vi vogter."""
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                ids.add(id(body[0].value))
    return ids


def _imported_modules(tree):
    """(modulnavn, lineno) for alle import/from-import (også lazy inde i funktioner)."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            out.append((node.module, node.lineno))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                out.append((alias.name, node.lineno))
    return out


def _code_str_literals(tree):
    """(strengværdi, lineno) for alle str-Constants UNDTAGEN docstrings."""
    ds = _docstring_ids(tree)
    out = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in ds):
            out.append((node.value, getattr(node, "lineno", 0)))
    return out


def _import_allowed(module, own_pkg):
    if not module.startswith("strategies."):
        return True   # ikke en strategi-pakke → uden for guardens scope
    if own_pkg and (module == own_pkg or module.startswith(own_pkg + ".")):
        return True   # egen pakke
    if module in NEUTRAL_SHARED or module.startswith("strategies.base."):
        return True
    if module in WHITELISTED_SHARED:
        return True
    return False


def main():
    print("Test: strategi-isolation (guard mod inter-strategi kobling)")

    # ── 0) Enhver aktiv algo_*.py skal være registreret ──
    found = {os.path.basename(p) for p in glob.glob(os.path.join(BACKEND, "algo_*.py"))}
    found -= IGNORE_FILES
    unregistered = sorted(found - set(STRATEGIES))
    check("Alle algo_*.py er registreret i isolations-guarden (ellers tilføj dem)",
          not unregistered, f"uregistreret: {unregistered}")

    other_regex = {src: _name_regex(src) for src in ALL_SOURCES}

    for fname, (own_src, own_pkg) in STRATEGIES.items():
        path = os.path.join(BACKEND, fname)
        if not os.path.exists(path):
            check(f"{fname} findes", False, "fil mangler — opdater registry")
            continue
        tree = ast.parse(open(path, encoding="utf-8").read(), filename=fname)

        # ── A) Søskende-import ──
        bad_imports = [(m, ln) for (m, ln) in _imported_modules(tree)
                       if not _import_allowed(m, own_pkg)]
        check(f"{fname}: ingen søskende-strategi-import",
              not bad_imports,
              "; ".join(f"{m} (linje {ln})" for m, ln in bad_imports))

        # ── B) Krydskilde-læsning (anden strategis source i et kode-literal) ──
        violations = []
        for value, ln in _code_str_literals(tree):
            for src in ALL_SOURCES:
                if src == own_src:
                    continue
                if other_regex[src].search(value):
                    snippet = value if len(value) <= 60 else value[:57] + "..."
                    violations.append(f"'{src}' i \"{snippet}\" (linje {ln})")
        check(f"{fname}: ingen reference til en anden strategis source",
              not violations, " | ".join(violations))

    print("\nALLE TESTS BESTÅET ✓")


if __name__ == "__main__":
    main()
