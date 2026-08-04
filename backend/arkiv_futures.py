r"""
arkiv_futures.py — D1.2: arkivér det uerstattelige futures-intradag, med bevis
═══════════════════════════════════════════════════════════════════════════════════
IBKR purger kontrakt-DEFINITIONEN ca. 24 maaneder efter udloeb. Derefter kan man ikke
engang spoerge om barerne — kontrakten findes ikke laengere, og `qualifyContractsAsync`
svarer med en tom skal (conId=0). `data_harvest/mes_m2k_clean/` og
`mes_m2k_stitched/` kan derfor IKKE hentes igen. De er den eneste del af
datagrundlaget der ikke kan reproduceres, og skal behandles som et primaert aktiv
paa niveau med kildekoden.

⚠ EN SIKKERHEDSKOPI INGEN HAR PROEVET AT GENDANNE ER EN FORMODNING, IKKE EN KOPI.
Derfor er `gendan-test` en foersteklasses kommando og ikke en note i en manual: den
kopierer TILBAGE fra arkivet til en midlertidig mappe og verificerer at hver enkelt
fil hasher til det manifestet lovede. Koer den efter hver `kopier`.

Placering besluttet 2026-08-04: ekstern disk H: (Elements). Stien staar i
ARKIV_ROD nedenfor og i vol_harvest_plan.md, saa den ikke kun findes i nogens hoved.

KOMMANDOER
    python arkiv_futures.py status                  # hvad er arkiveret, hvad er nyt
    python arkiv_futures.py kopier                  # kopiér + skriv manifest
    python arkiv_futures.py verificer               # rehash arkivet mod manifestet
    python arkiv_futures.py verificer --reparer     # ... og hent beskadigede fra kilden
    python arkiv_futures.py gendan-test             # gendan til temp og verificér
    python arkiv_futures.py kopier --dest E:\andet  # anden disk

Alle kommandoer er sikre at gentage. `kopier` springer filer over hvis stoerrelse,
tidsstempel OG hash er uaendret; en aendret fil kopieres igen, og den gamle udgave
lander i en versionsmappe frem for at blive overskrevet.

⚠ ARBEJDSDELINGEN MELLEM `kopier` OG `verificer`
`kopier` sammenligner KILDEN med manifestet — den ser ikke paa arkivfilerne selv.
Bliver en arkiveret fil beskadiget paa disken, melder `kopier` derfor "uaendret" og
gaar videre. Det er en bevidst afvejning (den maa ikke hashe 280 MB ved hver koersel),
men det betyder at bitroeddet KUN opdages af `verificer`, og kun repareres af
`verificer --reparer`. Koer den med jaevne mellemrum — kvartalsjobbet goer det.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# ── Konfiguration ─────────────────────────────────────────────────────────────
ARKIV_ROD = Path(r"H:\trading_dash_arkiv")     # ekstern disk (Elements)
MANIFEST_NAVN = "manifest.json"
BLOK = 1 << 20                                  # 1 MiB ad gangen — filerne er store

# Mapper der arkiveres, relativt til backend/. Tilfoej nye her; kommandoerne
# behoever ingen aendringer.
KILDER = [
    "data_harvest/mes_m2k_clean",
    "data_harvest/mes_m2k_stitched",
]
MOENSTRE = ("*.csv", "*.json", "*.md")


def sha256(sti: Path) -> str:
    h = hashlib.sha256()
    with sti.open("rb") as f:
        for blok in iter(lambda: f.read(BLOK), b""):
            h.update(blok)
    return h.hexdigest()


def nu() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def kilde_filer(rod: Path) -> list[Path]:
    ud: list[Path] = []
    for k in KILDER:
        d = rod / k
        if not d.is_dir():
            continue
        for m in MOENSTRE:
            ud += sorted(d.glob(m))
    return sorted(set(ud))


def rel(sti: Path, rod: Path) -> str:
    return sti.relative_to(rod).as_posix()


def laes_manifest(dest: Path) -> dict:
    p = dest / MANIFEST_NAVN
    if not p.exists():
        return {"skema_version": "1.0", "oprettet": nu(), "opdateret": None, "filer": {}}
    return json.loads(p.read_text(encoding="utf-8"))


def skriv_manifest(dest: Path, man: dict) -> None:
    man["opdateret"] = nu()
    man["antal_filer"] = len(man["filer"])
    man["bytes_i_alt"] = sum(f["bytes"] for f in man["filer"].values())
    (dest / MANIFEST_NAVN).write_text(
        json.dumps(man, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    # Manifestets egen hash, saa en beskadiget manifestfil ogsaa opdages.
    (dest / (MANIFEST_NAVN + ".sha256")).write_text(
        sha256(dest / MANIFEST_NAVN) + "\n", encoding="utf-8")


def mb(n: int) -> str:
    return f"{n/1024/1024:.1f} MB"


def tjek_dest(dest: Path, skal_findes: bool) -> Path | None:
    """Er destinationen der overhovedet? En ekstern disk kan vaere frakoblet."""
    drev = Path(dest.anchor)
    if not drev.exists():
        print(f"FEJL: drevet {drev} findes ikke. Er den eksterne disk tilsluttet?")
        return None
    if skal_findes and not (dest / MANIFEST_NAVN).exists():
        print(f"FEJL: intet manifest i {dest}. Koer 'kopier' foerst.")
        return None
    return dest


# ═══════════════════════════════════════════════════════════════════════════════
# status
# ═══════════════════════════════════════════════════════════════════════════════
def cmd_status(args, rod: Path) -> int:
    dest = Path(args.dest)
    filer = kilde_filer(rod)
    if not filer:
        print(f"Ingen kildefiler fundet under {rod} — tjek KILDER.")
        return 2
    i_alt = sum(f.stat().st_size for f in filer)
    print(f"Kilde: {len(filer)} filer, {mb(i_alt)}")
    for k in KILDER:
        n = [f for f in filer if k in rel(f, rod)]
        print(f"   {k}: {len(n)} filer, {mb(sum(x.stat().st_size for x in n))}")

    if not Path(dest.anchor).exists():
        print(f"\nArkiv: drevet {dest.anchor} er ikke tilsluttet.")
        return 0
    man = laes_manifest(dest)
    if not man["filer"]:
        print(f"\nArkiv {dest}: TOMT — intet er sikkerhedskopieret endnu.")
        return 0
    print(f"\nArkiv {dest}: {len(man['filer'])} filer, "
          f"{mb(man.get('bytes_i_alt', 0))}, opdateret {man.get('opdateret')}")
    print(f"   sidste gendannelsestest: {man.get('sidste_gendan_test') or 'ALDRIG'}")

    nye = [f for f in filer if rel(f, rod) not in man["filer"]]
    aendrede = [f for f in filer
                if rel(f, rod) in man["filer"]
                and man["filer"][rel(f, rod)]["bytes"] != f.stat().st_size]
    if nye:
        print(f"   {len(nye)} filer er IKKE arkiveret: "
              f"{', '.join(rel(f, rod) for f in nye[:5])}{' …' if len(nye) > 5 else ''}")
    if aendrede:
        print(f"   {len(aendrede)} filer har aendret stoerrelse siden arkiveringen")
    if not nye and not aendrede:
        print("   arkivet er ajour med kilden")
    return 0


# ═══════════════════════════════════════════════════════════════════════════════
# kopier
# ═══════════════════════════════════════════════════════════════════════════════
def cmd_kopier(args, rod: Path) -> int:
    dest = tjek_dest(Path(args.dest), skal_findes=False)
    if dest is None:
        return 1
    dest.mkdir(parents=True, exist_ok=True)
    man = laes_manifest(dest)
    filer = kilde_filer(rod)
    if not filer:
        print("Ingen kildefiler — intet at arkivere.")
        return 2

    kopieret = sprunget = erstattet = 0
    for src in filer:
        r = rel(src, rod)
        maal = dest / r
        st = src.stat()
        tidligere = man["filer"].get(r)

        if (tidligere and maal.exists()
                and tidligere["bytes"] == st.st_size
                and tidligere.get("kilde_mtime") == int(st.st_mtime)):
            sprunget += 1
            continue

        h = sha256(src)
        if tidligere and tidligere["sha256"] == h and maal.exists():
            # Samme indhold, kun tidsstemplet flyttede sig. Opdatér manifestet, kopiér ikke.
            tidligere["kilde_mtime"] = int(st.st_mtime)
            sprunget += 1
            continue

        maal.parent.mkdir(parents=True, exist_ok=True)
        if maal.exists():
            # Overskriv ALDRIG en tidligere arkiveret udgave — laeg den til side.
            gammel = dest / "_tidligere" / datetime.now().strftime("%Y%m%d") / r
            gammel.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(maal), str(gammel))
            erstattet += 1
        shutil.copy2(src, maal)

        # Verificér straks — en kopi der ikke er laest tilbage er ikke verificeret.
        h2 = sha256(maal)
        if h2 != h:
            print(f"FEJL: {r} hasher forskelligt efter kopiering. Afbryder.")
            return 1
        man["filer"][r] = {"bytes": st.st_size, "sha256": h,
                           "kilde_mtime": int(st.st_mtime), "arkiveret": nu()}
        kopieret += 1
        print(f"   + {r}  ({mb(st.st_size)})")

    skriv_manifest(dest, man)
    print(f"\n{kopieret} kopieret, {sprunget} uaendret, {erstattet} erstattet "
          f"(gammel udgave gemt under _tidligere/).")
    print(f"Manifest: {dest/MANIFEST_NAVN}")
    if kopieret:
        print("\nKoer nu:  python arkiv_futures.py gendan-test")
        print("En kopi der ikke er gendannet er en formodning, ikke en sikkerhedskopi.")
    return 0


# ═══════════════════════════════════════════════════════════════════════════════
# verificer
# ═══════════════════════════════════════════════════════════════════════════════
def cmd_verificer(args, rod: Path) -> int:
    dest = tjek_dest(Path(args.dest), skal_findes=True)
    if dest is None:
        return 1
    man = laes_manifest(dest)

    # Manifestet selv foerst.
    sig = dest / (MANIFEST_NAVN + ".sha256")
    if sig.exists():
        ventet = sig.read_text(encoding="utf-8").strip()
        faktisk = sha256(dest / MANIFEST_NAVN)
        # Manifestet er lige blevet laest, ikke skrevet — hashen skal passe.
        if ventet != faktisk:
            print("ADVARSEL: manifestet hasher ikke som ved skrivningen. "
                  "Enten er det redigeret, eller filen er beskadiget.")

    fejl, mangler = [], []
    for r, m in sorted(man["filer"].items()):
        p = dest / r
        if not p.exists():
            mangler.append(r)
            continue
        if sha256(p) != m["sha256"]:
            fejl.append(r)
    print(f"{len(man['filer'])} filer i manifestet · {len(mangler)} mangler · "
          f"{len(fejl)} hasher forkert")
    for r in mangler[:10]:
        print(f"   MANGLER  {r}")
    for r in fejl[:10]:
        print(f"   KORRUPT  {r}")
    if not (mangler or fejl):
        print("Arkivet er intakt.")
        return 0

    if not args.reparer:
        print("\nKoer 'verificer --reparer' for at hente de beskadigede filer fra kilden igen.")
        print("BEMAERK: 'kopier' reparerer dem IKKE — den sammenligner kilden med "
              "manifestet, ikke arkivet med sig selv, og vil melde 'uaendret'.")
        return 1

    # ── reparation ────────────────────────────────────────────────────────────
    # Kun fra en kilde der stadig hasher til det manifestet lovede. Er kilden ogsaa
    # aendret, ved vi ikke hvilken af de to der er den rigtige — og saa er det
    # forkert at overskrive noget som helst.
    reddet, tabt = 0, []
    for r in mangler + fejl:
        m = man["filer"][r]
        src = rod / r
        if not src.exists():
            tabt.append((r, "kilden findes ikke laengere — arkivet VAR den sidste kopi"))
            continue
        if sha256(src) != m["sha256"]:
            tabt.append((r, "kilden hasher ogsaa anderledes end manifestet"))
            continue
        maal = dest / r
        maal.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, maal)
        if sha256(maal) != m["sha256"]:
            tabt.append((r, "kopien hasher stadig forkert — disken kan vaere daarlig"))
            continue
        reddet += 1
        print(f"   repareret  {r}")
    print(f"\n{reddet} repareret, {len(tabt)} kunne ikke.")
    for r, hvorfor in tabt:
        print(f"   TABT  {r}: {hvorfor}")
    return 1 if tabt else 0


# ═══════════════════════════════════════════════════════════════════════════════
# gendan-test
# ═══════════════════════════════════════════════════════════════════════════════
def cmd_gendan_test(args, rod: Path) -> int:
    """Gendan FRA arkivet til en midlertidig mappe og verificér hver fil.

    Det er forskellen paa at have kopieret og at kunne gendanne. Testen roerer
    aldrig kilden — den skriver kun i en temp-mappe der ryddes bagefter.
    """
    dest = tjek_dest(Path(args.dest), skal_findes=True)
    if dest is None:
        return 1
    man = laes_manifest(dest)
    if not man["filer"]:
        print("Arkivet er tomt — intet at gendanne.")
        return 1

    poster = sorted(man["filer"].items())
    if args.stikproeve and args.stikproeve < len(poster):
        # Deterministisk stikproeve: jaevnt fordelt, ikke tilfaeldig, saa to koersler
        # tester det samme og et resultat kan gentages.
        skridt = len(poster) / args.stikproeve
        poster = [poster[int(i * skridt)] for i in range(args.stikproeve)]

    tmp = Path(tempfile.mkdtemp(prefix="arkiv_gendan_"))
    print(f"Gendanner {len(poster)} filer til {tmp} …")
    fejl = []
    try:
        for r, m in poster:
            kilde = dest / r
            maal = tmp / r
            maal.parent.mkdir(parents=True, exist_ok=True)
            if not kilde.exists():
                fejl.append((r, "findes ikke i arkivet"))
                continue
            shutil.copy2(kilde, maal)
            h = sha256(maal)
            if h != m["sha256"]:
                fejl.append((r, f"hash {h[:12]} != manifest {m['sha256'][:12]}"))
                continue
            if maal.stat().st_size != m["bytes"]:
                fejl.append((r, "stoerrelse afviger"))
                continue
            # Kan den overhovedet laeses som det den er? En CSV med korrekt hash men
            # afhugget hoved er stadig ubrugelig.
            if r.endswith(".csv"):
                with maal.open(encoding="utf-8") as f:
                    hoved = f.readline().strip()
                if not hoved or "," not in hoved:
                    fejl.append((r, f"ulaeselig CSV-header: {hoved[:40]!r}"))
            print(f"   OK  {r}  ({mb(m['bytes'])})")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if fejl:
        print(f"\nGENDANNELSESTEST DUMPET — {len(fejl)} filer:")
        for r, hvorfor in fejl[:15]:
            print(f"   {r}: {hvorfor}")
        return 1

    daekning = ("alle filer" if len(poster) == len(man["filer"])
                else f"stikproeve paa {len(poster)} af {len(man['filer'])} filer")
    man["sidste_gendan_test"] = nu()
    man["sidste_gendan_test_daekning"] = daekning
    skriv_manifest(dest, man)
    print(f"\nGENDANNELSESTEST BESTAAET ({daekning}).")
    print("Arkivet er en sikkerhedskopi, ikke en formodning.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Arkivér futures-intradag der ikke kan hentes igen")
    ap.add_argument("kommando", choices=["status", "kopier", "verificer", "gendan-test"])
    ap.add_argument("--dest", default=str(ARKIV_ROD), help=f"arkivrod (default {ARKIV_ROD})")
    ap.add_argument("--stikproeve", type=int, default=0,
                    help="gendan-test: test kun N filer (0 = alle)")
    ap.add_argument("--reparer", action="store_true",
                    help="verificer: hent beskadigede filer fra kilden igen")
    args = ap.parse_args()

    rod = Path(__file__).resolve().parent
    return {"status": cmd_status, "kopier": cmd_kopier,
            "verificer": cmd_verificer, "gendan-test": cmd_gendan_test}[args.kommando](args, rod)


if __name__ == "__main__":
    sys.exit(main())
