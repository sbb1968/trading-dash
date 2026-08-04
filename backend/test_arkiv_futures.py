"""
test_arkiv_futures.py — arkivet skal beskytte data, ikke bare flytte dem
═══════════════════════════════════════════════════════════════════════════════════
Testene daekker de to spejlvendte fejl:

  · ARKIVET raadner  -> `kopier` opdager det ikke (bevidst), `verificer` skal
  · KILDEN raadner   -> `kopier` maa IKKE skrive skidtet hen over den gode arkivkopi

Den anden er den farlige: den oedelaegger data frem for blot at overse skade, og den
er selvbekraeftende, fordi verifikationen bagefter melder alt i orden.

    python test_arkiv_futures.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

import arkiv_futures as af

FEJL: list[str] = []
HER = Path(__file__).resolve().parent


def paastand(betingelse: bool, hvad: str) -> None:
    if betingelse:
        print(f"  OK    {hvad}")
    else:
        print(f"  FEJL  {hvad}")
        FEJL.append(hvad)


def skriv_csv(sti: Path, n: int, start: datetime, pris: float = 100.0) -> None:
    sti.parent.mkdir(parents=True, exist_ok=True)
    linjer = ["timestamp,open,high,low,close,volume"]
    for i in range(n):
        t = (start + timedelta(minutes=i)).isoformat()
        linjer.append(f"{t},{pris},{pris+1},{pris-1},{pris+0.5},{100+i}")
    sti.write_text("\n".join(linjer) + "\n", encoding="utf-8")


def koer(rod: Path, dest: Path, *argv) -> tuple[int, str]:
    p = subprocess.run([sys.executable, str(HER / "arkiv_futures.py"), *argv,
                        "--dest", str(dest), "--kilde", str(rod)],
                       cwd=rod, capture_output=True, text=True, timeout=300)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


print("\n[1] Uforanderlighed afgoeres af om kontrakten er udloebet")
i_dag = date(2026, 8, 4)
paastand(af.er_uforanderlig("data_harvest/mes_m2k_clean/MES_202409_1min.csv", i_dag),
         "udloebet kontrakt (202409) = UFORANDERLIG")
paastand(not af.er_uforanderlig("data_harvest/mes_m2k_clean/MES_202609_1min.csv", i_dag),
         "front-maaneden (202609) = foranderlig, den vokser stadig")
paastand(not af.er_uforanderlig("data_harvest/mes_m2k_stitched/MES_1min.csv", i_dag),
         "stitched serie = foranderlig, den vokser loebende")
paastand(not af.er_uforanderlig("data_harvest/mes_m2k_clean/MES_202608_1min.csv",
                                date(2026, 8, 25)),
         "kontrakt udloebet for 5 dage siden = stadig foranderlig (hoesten kan mangle)")
paastand(af.er_uforanderlig("data_harvest/mes_m2k_clean/MES_202606_1min.csv", i_dag),
         "kontrakt udloebet for 6 uger siden = uforanderlig")

# ── byg et lille arbejdsmiljoe ────────────────────────────────────────────────
tmp = Path(tempfile.mkdtemp(prefix="arkivtest_"))
rod, dest = tmp / "backend", tmp / "arkiv"
udloebet = rod / "data_harvest/mes_m2k_clean/MES_202409_1min.csv"
voksende = rod / "data_harvest/mes_m2k_stitched/MES_1min.csv"
skriv_csv(udloebet, 200, datetime(2024, 8, 1, 9, 30))
skriv_csv(voksende, 200, datetime(2026, 6, 1, 9, 30))

try:
    print("\n[2] Foerste arkivering")
    kode, ud = koer(rod, dest, "kopier")
    paastand(kode == 0 and "\n2 kopieret" in ud, f"to filer arkiveret (exit {kode})")
    man = af.laes_manifest(dest)
    r_udl = "data_harvest/mes_m2k_clean/MES_202409_1min.csv"
    r_voks = "data_harvest/mes_m2k_stitched/MES_1min.csv"
    paastand(man["filer"][r_udl]["foranderlig"] is False,
             "den udloebne kontrakt er markeret uforanderlig i manifestet")
    paastand(man["filer"][r_voks]["foranderlig"] is True,
             "den stitchede serie er markeret foranderlig")

    print("\n[3] KILDEN raadner — arkivkopien maa IKKE overskrives")
    # Samme antal barer, men én pris aendret. Praecis formen paa bitroed.
    b = bytearray(udloebet.read_bytes())
    b[120] = ord("9") if b[120] != ord("9") else ord("8")
    udloebet.write_bytes(bytes(b))
    arkiv_hash_foer = af.sha256(dest / r_udl)

    # Bitroed aendrer typisk ikke mtime — derfor --fuld, som hasher hver kildefil.
    # Uden den springer hurtigstien filen helt over, og F1-vejen afproeves aldrig.
    kode, ud = koer(rod, dest, "kopier", "--fuld")
    paastand("BLOKERET" in ud, "kopieringen blev blokeret")
    paastand(kode == 1, f"exit-kode 1 saa et script kan opdage det (fik {kode})")
    paastand(af.sha256(dest / r_udl) == arkiv_hash_foer,
             "ARKIVKOPIEN ER UROERT — den gode version overlevede")
    paastand("kraever --accepter-vaekst" not in ud,
             "korruption tilbydes IKKE en override — kun aegte udvidelser faar den")

    print("\n[3b] Uden --fuld springer hurtigstien den raadne kilde helt over")
    # AEGTE bitroed aendrer ikke mtime — disken taber en bit, filsystemet ved intet.
    # Saet tidsstemplet tilbage til det manifestet kender, saa hurtigstien tror
    # filen er uroert. Det er den situation `--fuld` findes for.
    os.utime(udloebet, (man["filer"][r_udl]["kilde_mtime"],
                        man["filer"][r_udl]["kilde_mtime"]))
    kode, ud = koer(rod, dest, "kopier")
    paastand(kode == 0 and "BLOKERET" not in ud,
             "hurtigstien ser hverken korruptionen eller roerer arkivet")
    paastand(af.sha256(dest / r_udl) == arkiv_hash_foer, "arkivkopien stadig uroert")

    print("\n[4] Selv --accepter-vaekst slipper ikke korruption igennem")
    kode, ud = koer(rod, dest, "kopier", "--fuld", "--accepter-vaekst")
    paastand("BLOKERET" in ud and af.sha256(dest / r_udl) == arkiv_hash_foer,
             "stadig blokeret, arkivkopien stadig uroert")

    print("\n[5] En AEGTE udvidelse af den udloebne kontrakt")
    # Gendan kilden og laeg 50 nye barer i enden — en senere hoest der fyldte hul.
    shutil.copy2(dest / r_udl, udloebet)
    skriv_csv(udloebet, 250, datetime(2024, 8, 1, 9, 30))
    kode, ud = koer(rod, dest, "kopier", "--fuld")
    paastand("BLOKERET" in ud and "ren udvidelse" in ud,
             "udvidelsen genkendes som lovlig, men kraever eksplicit accept")
    paastand(af.sha256(dest / r_udl) == arkiv_hash_foer,
             "arkivkopien uroert indtil accepten gives")
    kode, ud = koer(rod, dest, "kopier", "--fuld", "--accepter-vaekst")
    paastand(kode == 0 and "udvider" in ud, f"med --accepter-vaekst gaar den igennem (exit {kode})")
    paastand(len(af._csv_raekker(dest / r_udl)) == 250, "arkivet har nu 250 barer")

    print("\n[6] Den FORANDERLIGE serie opdateres frit")
    skriv_csv(voksende, 300, datetime(2026, 6, 1, 9, 30))
    kode, ud = koer(rod, dest, "kopier")
    paastand(kode == 0 and "BLOKERET" not in ud, "ingen blokering paa en foranderlig fil")
    paastand(len(af._csv_raekker(dest / r_voks)) == 300, "arkivet fulgte med til 300 barer")

    print("\n[7] Gendannelsestest og verifikation")
    kode, ud = koer(rod, dest, "gendan-test")
    paastand(kode == 0 and "BESTAAET" in ud, "gendannelsestesten bestaar")
    kode, ud = koer(rod, dest, "verificer")
    paastand(kode == 0 and "intakt" in ud, "verifikationen melder arkivet intakt")

    print("\n[8] ARKIVET raadner — verificer skal fange det, kopier maa gerne lade vaere")
    b = bytearray((dest / r_udl).read_bytes())
    b[130] = (b[130] + 1) % 256
    (dest / r_udl).write_bytes(bytes(b))
    kode, ud = koer(rod, dest, "verificer")
    paastand(kode == 1 and "KORRUPT" in ud, "verificer opdager bitroed i arkivet")
    kode, ud = koer(rod, dest, "verificer", "--reparer")
    paastand(kode == 0 and "repareret" in ud, "og --reparer henter den gode fra kilden")

    print("\n[9] K1 — et TOMT eller utilgaengeligt arkiv maa ALDRIG melde groent")
    # Arkivet ligger paa en ekstern disk der ikke altid er tilsluttet. Uden dette
    # tjek ville "0 filer, 0 fejl" ende i "Arkivet er intakt" og exit 0 — en kontrol
    # hvis udfald var afgjort af at der ikke var noget at kontrollere.
    tomt = tmp / "tomt_arkiv"
    tomt.mkdir(parents=True, exist_ok=True)
    (tomt / "manifest.json").write_text('{"skema_version":"1.0","filer":{}}',
                                        encoding="utf-8")
    kode, ud = koer(rod, tomt, "verificer")
    paastand(kode == 1, f"tomt manifest -> exit 1 (fik {kode})")
    paastand("UTILGAENGELIGT ELLER TOMT" in ud, "og meldingen siger hvad der er galt")
    paastand("intakt" not in ud, "ordet 'intakt' optraeder IKKE")

    print("\n[9b] Et drev der slet ikke findes")
    kode, ud = koer(rod, Path("Z:/findes_slet_ikke"), "verificer")
    paastand(kode == 1, f"ikke-eksisterende drev -> exit 1 (fik {kode})")
    paastand("findes ikke" in ud and "tilsluttet" in ud,
             "meldingen spoerger om disken er tilsluttet")

    print("\n[9c] Gendannelsestest paa tomt arkiv fejler ogsaa")
    kode, ud = koer(rod, tomt, "gendan-test")
    paastand(kode == 1 and "BESTAAET" not in ud,
             "gendan-test kan ikke bestaa paa et tomt arkiv")

finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n" + "=" * 70)
if FEJL:
    print(f"DUMPET — {len(FEJL)} fejl:")
    for f in FEJL:
        print(f"   · {f}")
    sys.exit(1)
print("ALLE TESTS BESTAAET")
