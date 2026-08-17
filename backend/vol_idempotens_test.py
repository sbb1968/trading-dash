#!/usr/bin/env python3
r"""
vol_idempotens_test.py — T7: er V1-harvesten idempotent?
════════════════════════════════════════════════════════════════════════════════
Arbejdsordrens T7, det sidste V1-lukkekrav. Bestå → V1 kan erklæres lukket i
`spec_volatilitet.md`.

HVAD IDEMPOTENS BETYDER HER, præcist — og det er ikke "filerne er identiske":

  · rækker der fandtes før, skal være UÆNDREDE
  · der må ikke komme DUBLETTER af data der allerede var hentet
  · nye rækker er kun tilladt STRENGT EFTER den forrige sidste bar
    (kører man to gange hen over et minutskifte, ER der lovligt en bar mere)

⚠ EN REN FIL-HASH VILLE VÆRE FORKERT I BEGGE RETNINGER. Den ville dumpe en
korrekt harvest der nåede at hente én ny bar, og den ville bestå en harvest der
skrev de samme målinger igen under nye nøgler — hvis bare den gjorde det
deterministisk. Det sidste er præcis den fejl det forrige projekt havde: nøglet
på KØRSELSDATO fik historikken til at vokse uden at der kom ny viden.

TO DELE, og den anden er den vigtige (Revision G):

  DEL A  kræver TWS. Kør harvesten igen og sammenlign mod et øjebliksbillede.
  DEL B  er offline. Fodrer sammenligneren et input der SKAL få den til at
         fejle — to filer der kun adskiller sig ved et hentetidspunkt.
         Består kontrollen begge, måler den ingenting.

KØRSEL (Sørens workstation, TWS åbent på 7497):

    cd C:\Projects\trading_dash\backend
    venv\Scripts\python vol_idempotens_test.py --snapshot     # 1. tag billede
    venv\Scripts\python vol_harvest.py --hvad dagligt         # 2. kør harvesten
    venv\Scripts\python vol_idempotens_test.py --sammenlign   # 3. døm

Del B kører automatisk i trin 3 og kræver hverken TWS eller data.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import tempfile
from pathlib import Path

HER = Path(__file__).resolve().parent
CACHE = HER / "vol_cache"
BILLEDE = HER / "vol_cache" / "_idempotens_billede.json"

# ⚠ Manifestet SKAL ændre sig ved hver kørsel (opdateret, hentede_svar,
# nye_barer). Det er bogføring om KØRSLEN, ikke data. At kræve det uændret
# ville dumpe en korrekt harvest.
UNDTAG = {"_idempotens_billede.json", "manifest.json"}


def datafiler() -> list[Path]:
    return sorted(p for p in CACHE.glob("*.csv") if p.name not in UNDTAG)


def laes(p: Path) -> list[list[str]]:
    with p.open(encoding="utf-8", newline="") as f:
        return [r for r in csv.reader(f) if r]


BLOK = 1000


def _blokhash(raekker: list[list[str]]) -> str:
    """⚠ STABIL PÅ TVÆRS AF PROCESSER. Første udgave brugte Pythons indbyggede
    hash(), og den er SALTET pr. proces for strenge (PYTHONHASHSEED). Billedet
    blev taget i én proces og sammenligningen kørt i en anden, så ALLE ti filer
    blev meldt som ændrede — også tre der var bit-for-bit urørte. Værktøjet
    skrev "V1 kan IKKE erklæres lukket" med fuld overbevisning, og det var
    forkert.

    ⚠ OG KONTROLFIKSTURET KUNNE IKKE SE DET, fordi det kørte i SAMME proces som
    den kontrol det prøvede. Et fikstur skal krydse de samme grænser som den
    rigtige måling — ellers er der en hel klasse fejl det pr. konstruktion er
    blindt for. Det er tiende gang samme sygdom dukker op i dette projekt, og
    første gang i mit eget kontrolfikstur.
    """
    h = hashlib.blake2b(digest_size=16)
    for r in raekker:
        h.update(("".join(r) + "").encode("utf-8"))
    return h.hexdigest()


def billede_af(p: Path, graense: int | None = None) -> dict:
    """Øjebliksbillede. `graense` = hash kun de første N rækker.

    ⚠ GRÆNSEN ER IKKE PYNT. Blokkene er 1.000 rækker, så en TILFØJET række
    lander i den sidste blok og ændrer dens hash — og uden grænsen ville
    enhver lovlig daglig opdatering blive meldt som "eksisterende data ændrede
    sig". Kontrolfiksturet i DEL B fangede netop det, før nogen havde kørt en
    harvest. Ved sammenligning hashes den NYE fil kun til den GAMLE længde.
    """
    r = laes(p)
    krop = r[1:] if r and not r[0][0][:1].isdigit() else r
    fuld = len(krop)
    if graense is not None:
        krop = krop[:graense]
    return {"raekker": fuld,
            "foerste": krop[0][0] if krop else None,
            "sidste": (r[1:] if r and not r[0][0][:1].isdigit() else r)[-1][0] if fuld else None,
            # ⚠ Hele indholdet gemmes ikke — VIX_1min er 114 MB. En hash pr.
            # 1.000 rækker lokaliserer en ændring uden at bære filen rundt.
            "blokke": [_blokhash(krop[i:i+BLOK])
                       for i in range(0, len(krop), BLOK)]}


def tag_billede() -> int:
    filer = datafiler()
    if not filer:
        print(f"⚠ Ingen CSV'er i {CACHE} — er du i backend/?")
        return 1
    b = {p.name: billede_af(p) for p in filer}
    BILLEDE.write_text(json.dumps(b), encoding="utf-8")
    print(f"Øjebliksbillede af {len(filer)} filer gemt.\n")
    for navn, v in b.items():
        print(f"  {navn:18} {v['raekker']:>9,} rækker   {str(v['sidste'])[:16]}")
    print(f"\nKør nu:  venv\\Scripts\\python vol_harvest.py --hvad dagligt")
    print(f"Derefter: venv\\Scripts\\python vol_idempotens_test.py --sammenlign")
    return 0


# ── Selve kontrollen ────────────────────────────────────────────────────────
def doem_fil(foer: dict, p: Path) -> list[str]:
    """Sammenlign en fil mod sit gamle billede. Tom liste = idempotent."""
    # ⚠ Den nye fil hashes KUN til den gamle længde — se billede_af.
    efter = billede_af(p, graense=foer["raekker"])
    efter["raekker"] = billede_af(p)["raekker"]
    return doem(foer, efter)


def doem(foer: dict, efter: dict) -> list[str]:
    """Returnér liste af overtrædelser. Tom liste = idempotent."""
    fejl = []
    if efter["raekker"] < foer["raekker"]:
        fejl.append(f"filen SKRUMPEDE ({foer['raekker']} → {efter['raekker']})")
        return fejl
    if foer["foerste"] != efter["foerste"]:
        fejl.append(f"første bar flyttede sig ({foer['foerste']} → {efter['foerste']})")
    # ⚠ KERNEN: alt der fandtes før, skal stå uændret. Blokkene dækker de
    # gamle rækker; en ændret måling ét sted inde i serien ændrer sin blok.
    gamle = foer["blokke"]
    if efter["blokke"][:len(gamle)] != gamle:
        n = next(i for i, (a, b) in enumerate(zip(gamle, efter["blokke"])) if a != b)
        fejl.append(f"eksisterende data ÆNDREDE SIG (blok {n}, "
                    f"omkring række {n*1000:,})")
    # nye rækker er kun lovlige efter den forrige sidste bar
    if efter["raekker"] > foer["raekker"]:
        if foer["sidste"] and efter["sidste"] and efter["sidste"] <= foer["sidste"]:
            fejl.append(f"{efter['raekker']-foer['raekker']} nye rækker, men "
                        f"sidste bar rykkede ikke frem — det er DUBLETTER")
    return fejl


def sammenlign() -> int:
    if not BILLEDE.exists():
        print("⚠ Intet øjebliksbillede. Kør --snapshot først.")
        return 1
    foer = json.loads(BILLEDE.read_text(encoding="utf-8"))
    fejl_i_alt = 0

    print("DEL A — harvesten kørt to gange\n")
    bedoemt = 0
    for p in datafiler():
        if p.name not in foer:
            print(f"  ?    {p.name}: ny fil siden billedet (ikke bedømt)")
            continue
        bedoemt += 1
        efter = billede_af(p)
        f = doem_fil(foer[p.name], p)
        nye = efter["raekker"] - foer[p.name]["raekker"]
        haale = f"+{nye} nye" if nye else "uændret"
        print(f"  {'OK  ' if not f else 'FEJL'} {p.name:18} {haale:>12}   "
              f"{str(efter['sidste'])[:16]}")
        for x in f:
            print(f"         ⚠ {x}")
        fejl_i_alt += len(f)

    # ⚠ NUL BEDØMTE FILER ER IKKE NUL OVERTRÆDELSER. Med et tomt øjebliksbillede
    # sammenlignede løkken ingenting, fandt ingen fejl — og værktøjet skrev
    # "IDEMPOTENS BEVIST". Det er præcis den fejlklasse hele projektet er
    # organiseret omkring at undgå, denne gang i kontrollen selv.
    if bedoemt == 0:
        print("  ⚠ INGEN filer blev bedømt — øjebliksbilledet er tomt eller")
        print("     hører til andre filer. Der er intet bevist.")
        fejl_i_alt += 1
    else:
        print(f"\n  {bedoemt} filer bedømt.")

    # ── DEL B: kan kontrollen overhovedet fejle? ────────────────────────────
    print("\nDEL B — kontrolfikstur: et input der SKAL dumpe\n")
    print("  ⚠ En idempotens-kontrol der aldrig er set fejle, er ikke en kontrol.")
    print("     Her fodres den med det Revision G nævner: en harvest der skriver")
    print("     HENTETIDSPUNKT ind i datafilen. Samme måling, ny værdi.\n")

    tmp = Path(tempfile.mkdtemp())
    raekker = [["timestamp", "close", "hentet"]] + \
              [[f"2026-08-{d:02d}", "100.5", "2026-08-17T09:00:00"] for d in range(1, 12)]
    a = tmp / "a.csv"
    a.write_text("\n".join(",".join(r) for r in raekker), encoding="utf-8")
    b_raekker = [raekker[0]] + [[r[0], r[1], "2026-08-17T11:30:00"] for r in raekker[1:]]
    b = tmp / "b.csv"
    b.write_text("\n".join(",".join(r) for r in b_raekker), encoding="utf-8")

    fikstur = doem_fil(billede_af(a), b)
    if fikstur:
        print(f"  OK   fiksturet DUMPER som det skal: {fikstur[0]}")
    else:
        print("  FEJL fiksturet bestod — kontrollen kan ikke se en ændret måling.")
        print("       Så betyder DEL A's grønne resultat ingenting.")
        fejl_i_alt += 1

    # og den anden vej: en ægte tilvækst må IKKE dumpe
    c_raekker = raekker + [["2026-08-12", "101.0", "2026-08-17T09:00:00"]]
    c = tmp / "c.csv"
    c.write_text("\n".join(",".join(r) for r in c_raekker), encoding="utf-8")
    tilvaekst = doem_fil(billede_af(a), c)
    if not tilvaekst:
        print("  OK   …og en ÆGTE ny bar dumper ikke (ellers ville kontrollen")
        print("       kalde enhver korrekt daglig opdatering en fejl)")
    else:
        print(f"  FEJL en ægte ny bar blev afvist: {tilvaekst[0]}")
        fejl_i_alt += 1

    # ── DEL C: krydser fiksturet de samme grænser som målingen? ────────────
    # ⚠ DEN FEJL DEL B IKKE KUNNE SE. Del B kører i ÉN proces; den rigtige
    # måling tager billedet i én og sammenligner i en anden. Med Pythons
    # saltede hash() var alt derfor "ændret" — også tre filer der var
    # bit-for-bit urørte — og Del B bestod alligevel.
    #
    # Et fikstur der ikke krydser de samme grænser som målingen, er pr.
    # konstruktion blindt for en hel klasse fejl.
    print("\nDEL C — samme fil, ny proces: giver billedet det samme?\n")
    import subprocess
    prog = ("import sys; sys.argv=['x']; import pathlib; "
            "import vol_idempotens_test as v; "
            "print(v.billede_af(pathlib.Path(sys.argv[0] if False else r'%s'))['blokke'][0])" % a)
    ude = subprocess.run([sys.executable, "-c", prog], capture_output=True,
                         text=True, cwd=str(HER))
    her = billede_af(a)["blokke"][0]
    der = ude.stdout.strip()
    if der == her:
        print(f"  OK   samme blokhash i to processer ({str(her)[:20]}…)")
    else:
        print("  FEJL hashen ændrer sig mellem processer:")
        print(f"       her: {str(her)[:24]}   anden proces: {der[:24]}")
        print("       → DEL A ville melde ALT som ændret, også urørte filer.")
        fejl_i_alt += 1

    print("\n" + "═" * 70)
    if fejl_i_alt:
        print(f"⚠ {fejl_i_alt} OVERTRÆDELSE(R) — V1 kan IKKE erklæres lukket.")
    else:
        print("IDEMPOTENS BEVIST. V1's sidste lukkekrav er opfyldt.")
        print("→ erklær V1 lukket i spec_volatilitet.md")
    return 1 if fejl_i_alt else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", action="store_true", help="tag øjebliksbillede FØR harvesten")
    ap.add_argument("--sammenlign", action="store_true", help="døm EFTER harvesten")
    a = ap.parse_args()
    if a.snapshot:
        sys.exit(tag_billede())
    if a.sammenlign:
        sys.exit(sammenlign())
    ap.print_help()
