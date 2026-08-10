"""
test_konto_laesninger.py — kontoen er en KONTROL, aldrig en kilde
════════════════════════════════════════════════════════════════════════════════
IBKR har ingen forestilling om strategier. Kontoen holder 38 VELO-aktier; der
findes ikke "Konfluens 2's 19" hos brokeren. I det øjeblik kode læser noget fra
kontoen og tilskriver svaret til én strategi, er adskillelsen væk — ikke fordi
nogen glemte at kode den, men fordi informationen allerede er kastet væk i det
lag der svarer.

`_ibkr_still_holds` var ét sted. Det kostede fem uønskede shorts.

⚠ HVORFOR DETTE ER ET REGISTER OG IKKE ET FORBUD. Reglen i specen — *en strategi
spørger aldrig IBKR om positioner* — kan ikke håndhæves i dag: der findes endnu
ikke det interne, tilskrevne regnskab den forudsætter. Et blankt forbud ville
fejle med det samme på lovlig kode, og en test der er rød fra fødslen bliver
slået fra.

Registret gør noget andet og opnåeligt: det **fryser fladen**. Hvert eksisterende
kaldested står her med sin klassifikation, og enhver NY konto-læsning fejler
testen indtil nogen har taget stilling til den. Samme greb som
`ibkr_client_ids` — og det var netop dét der afslørede at 46 og 47 var delt.

    python test_konto_laesninger.py
"""
from __future__ import annotations

import ast
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

ROD = pathlib.Path(__file__).parent
FEJL: list[str] = []


def kraev(b, hvad):
    print(f"  {'OK  ' if b else 'FEJL'} {hvad}")
    if not b:
        FEJL.append(hvad)


# Konto-læsende metoder på IBKRConnection. Alt herunder returnerer noget der
# gælder KONTOEN, ikke den strategi der spurgte.
KONTO_LAESNINGER = (
    "get_positions", "get_positions_live", "get_positions_reliable",
    "get_account_summary", "get_open_orders", "get_order_outcome",
    "executions_i_dag",
)

# ⚠ ordre_status står bevidst IKKE på listen. Den spørger om ÉN ordre, og en
# ordre ER tilskrivbar — vi ved hvem der sendte den. Det er hele forskellen
# mellem den gamle og den nye vagt.

# Filer der må læse kontoen frit: selve forbindelsen, afstemning, oprydnings-
# værktøjer og alt uden for handelsstien.
FRI = {
    "ibkr_connect.py", "afstem_konto.py", "manual_reconcile.py", "main.py",
    "strategy_manager.py", "risk_manager.py", "fleet_report.py",
    "ryd_ejerloese_shorts.py", "mes_flatten.py", "cogt_flatten.py",
    "cogt_fix_dupe_orders.py", "diag_cogt_orders.py", "reconcile_idempotency.py",
}

# ── REGISTRET ───────────────────────────────────────────────────────────────
# (fil, metode) -> klassifikation. Se rapporten for fejlkæderne.
#
#   kontrol    læses for at SAMMENLIGNE, ikke for at afgøre noget om egen andel
#   tilskrevet resultatet filtreres på noget der ER strategiens eget (orderRef)
#   ⚠ familie-A  resultatet tilskrives strategien uden at kunne være det
REGISTER: dict[tuple[str, str], str] = {}
for _f in ("algo_confluence2.py", "algo_buythedip.py", "algo_trendjoin.py",
           "algo_relstyrke.py", "algo_europa_reversion.py", "algo_us_reversion.py"):
    # Opstarts-reconcile: kontoen sammenholdes med EGNE journalrækker (source=name).
    # Kontrol, ikke kilde — men den lukker på et sammenfald, se rapportens B2.
    REGISTER[(_f, "get_positions_reliable")] = "kontrol"
    # Dup-vagt: filtrerer på orderRef, som ER strategiens eget.
    REGISTER[(_f, "get_open_orders")] = "tilskrevet"
    # Ordreudfald slås op på ref — tilskrivbart.
    REGISTER[(_f, "get_order_outcome")] = "tilskrevet"

# NLV til risikogrænser. Konto-niveau med vilje: grænsen ER en andel af kontoen.
REGISTER[("strategy_base.py", "get_account_summary")] = "kontrol"
# ⚠ Den sidste rest af familie A. _ibkr_still_holds bruges ikke længere som
# beslutningsgrundlag (se _lukkeordre_ufyldt), men funktionen står endnu og
# læser stadig kontoens netto.
REGISTER[("strategy_base.py", "get_positions_reliable")] = "familie-A (udfaset)"
# Universvalg og førflyvning: informativ, ikke beslutning om egen andel.
REGISTER[("algo_relstyrke.py", "get_account_summary")] = "kontrol"
REGISTER[("algo_trendjoin.py", "get_account_summary")] = "kontrol"


def kaldsteder() -> dict[tuple[str, str], list[int]]:
    """Alle konto-læsninger i handelsstien: {(fil, metode): [linjenumre]}.

    ⚠ PARSER, IKKE REGEX. Første udgave søgte efter `.get_positions(` som tekst og
    fandt et træf i en DOCSTRING i position_ledger.py — en beskrivelse af et kald,
    ikke et kald. Et mønster der matcher bredere end den påstand det bærer, er
    samme fejlklasse som resten: kontrollen svarede på noget andet end den sagde.
    ast ser kun rigtige kald.
    """
    ud: dict[tuple[str, str], list[int]] = {}
    for p in sorted(ROD.rglob("*.py")):
        rel = p.relative_to(ROD).as_posix()
        if rel.startswith(("venv/", "_archive/")) or rel.startswith("test_"):
            continue
        if rel in FRI:
            continue
        try:
            traeet = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(traeet):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            if isinstance(f, ast.Attribute) and f.attr in KONTO_LAESNINGER:
                ud.setdefault((rel, f.attr), []).append(node.lineno)
    return ud


print("\n1. Hvor læses kontoen i handelsstien?")
fundet = kaldsteder()
print(f"     {len(fundet)} (fil, metode)-par · "
      f"{sum(len(v) for v in fundet.values())} kaldesteder")

print("\n2. ⚠ Alle er registreret — en NY konto-læsning kræver stillingtagen")
ukendte = [f"{f}:{','.join(map(str, l))} -> .{m}()"
           for (f, m), l in sorted(fundet.items()) if (f, m) not in REGISTER]
kraev(not ukendte,
      "ingen uregistrerede konto-læsninger"
      + ("" if not ukendte else ":\n        " + "\n        ".join(ukendte)))

print("\n3. Registret beskriver kun ting der findes")
foraeldede = [f"{f} -> .{m}()" for (f, m) in sorted(REGISTER) if (f, m) not in fundet]
kraev(not foraeldede,
      "ingen forældede poster i registret"
      + ("" if not foraeldede else ": " + "; ".join(foraeldede)))

print("\n4. Klassifikationen")
from collections import Counter
tael = Counter(REGISTER[k] for k in fundet if k in REGISTER)
for klasse, n in sorted(tael.items()):
    print(f"     {klasse:22} {n}")
familie_a = [f"{f}.{m}()" for (f, m) in fundet if REGISTER.get((f, m), "").startswith("familie-A")]
print(f"     tilbage i familie A: {familie_a or 'ingen'}")

print("\n5. ⚠ Falsifikation — kan vagten sige nej?")
_gemt = dict(REGISTER)
REGISTER.pop(("algo_confluence2.py", "get_positions_reliable"))
ukendte2 = [k for k in fundet if k not in REGISTER]
kraev(len(ukendte2) == 1,
      f"fjernes én post fra registret, fanges kaldestedet ({ukendte2})")
REGISTER.clear(); REGISTER.update(_gemt)
kraev(not [k for k in fundet if k not in REGISTER],
      "og registret er rent igen bagefter")

# Og den anden vej: en indsat overtrædelse i en fil der ikke maa laese kontoen.
proev = ROD / "_falsifikation_konto.py"
proev.write_text("async def f(conn):\n    return await conn.get_positions_live()\n",
                 encoding="utf-8")
try:
    fundet2 = kaldsteder()
    kraev(("_falsifikation_konto.py", "get_positions_live") in fundet2,
          "en NY konto-læsning i en ny fil bliver fundet")
    kraev(("_falsifikation_konto.py", "get_positions_live") not in REGISTER,
          "og den er ikke i registret — altså ville testen fejle på den")
finally:
    proev.unlink()

kraev(("_falsifikation_konto.py", "get_positions_live") not in kaldsteder(),
      "og filen er ryddet op igen")

print("\n" + "=" * 74)
if FEJL:
    print(f"{len(FEJL)} FEJL:")
    for f in FEJL:
        print("  -", f)
    sys.exit(1)
print(f"Alt groent. {sum(len(v) for v in fundet.values())} konto-laesninger, alle registreret.")
