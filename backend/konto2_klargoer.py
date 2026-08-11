#!/usr/bin/env python3
"""
konto2_klargoer.py — er DENNE maskine klar til at handle på konto 2?
════════════════════════════════════════════════════════════════════════════════
`konto2_opsaetning.md` er en tjekliste et menneske skal læse omhyggeligt. Denne
fil **måler** det samme i stedet. Forskellen er ikke bekvemmelighed: en tjekliste
kan afkrydses af en der tror den er opfyldt, og netop dét er fejlklassen vi har
ramt igen og igen — en kontrol hvis udfald var afgjort af konfigurationen frem
for målt på virkeligheden.

Alt her er **skrivebeskyttet**. Der sendes ingen ordrer, og intet ændres.

    python konto2_klargoer.py

Exit 0 = klar · 1 = noget spærrer

────────────────────────────────────────────────────────────────────────────────
⚠ HVAD DEN IKKE KAN SVARE PÅ

  · om Gatewayen er logget ind som den rigtige BRUGER — det ses først når
    ordreforbindelsen åbnes, og det er V1's opgave (ordre_forbindelse.py)
  · om kontoen har futures-tilladelse — det afgøres af IBKR, ikke af os

Begge dele står i rapporten frem for at blive udeladt i stilhed. En kontrol der
forbliver tavs om det den ikke dækker, læses som om den dækkede alt.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HER = Path(__file__).parent
sys.path.insert(0, str(HER))

FEJL: list[str] = []
ADVARSEL: list[str] = []

# Saettes af --kraev. Uden den er en manglende ordre_forbindelse en oplysning,
# ikke en fejl — se tjek_konfig.
KRAEV_ORDRE_FORBINDELSE = False

# Frontend-commits der ÆNDREDE noget synligt for den der handler. Er exe'en
# ældre end en af dem, mangler brugeren noget hun ikke kan se mangler.
FRONTEND_KRAV = {
    "17f3350": "ordrekvitteringen viser hvilken konto og forbindelse ordren gik til",
    "e65b8a9": "kontoskift uden at redigere account.yaml",
    "f301fa4": "watchlist-kolonner kan til- og fravælges",
}


def kap(t: str) -> None:
    print(f"\n{t}\n" + "─" * 78)


def ok(navn: str, godt: bool, naar_fejl: str = "", info: str = "",
       blokerer: bool = True) -> bool:
    """⚠ `naar_fejl` vises KUN når kontrollen fejler.

    Første udgave printede forklaringen i begge tilfælde, så der stod
    "OK konto er paper — DUQ441063 ligner live". En rapport der modsiger sig
    selv, læses ikke — den skimmes, og så er den værre end ingen rapport.
    """
    hale = info or (naar_fejl if not godt else "")
    print(f"  {'OK  ' if godt else ('FEJL' if blokerer else 'ADV ')} {navn}"
          + (f"  —  {hale}" if hale else ""))
    if not godt:
        (FEJL if blokerer else ADVARSEL).append(f"{navn}: {naar_fejl or info}")
    return godt


def _git(*a: str) -> str:
    try:
        return subprocess.run(["git", *a], cwd=HER.parent, capture_output=True,
                              text=True, timeout=25).stdout.strip()
    except Exception:
        return ""


# ── 1. koden ────────────────────────────────────────────────────────────────
def tjek_kode() -> None:
    kap("1. Koden på denne maskine")

    hoved = _git("rev-parse", "--short", "HEAD")
    ok("git svarer", bool(hoved), info=f"HEAD = {hoved or '?'}")

    for sha, hvad in FRONTEND_KRAV.items():
        # ⚠ merge-base --is-ancestor, ikke `git log | grep`. Sidstnaevnte ville
        # ogsaa matche en commit der blot NAEVNER sha'en i sin tekst.
        r = subprocess.run(["git", "merge-base", "--is-ancestor", sha, "HEAD"],
                           cwd=HER.parent, capture_output=True, timeout=20)
        ok(f"{sha} i historikken", r.returncode == 0,
           naar_fejl=f"MANGLER: {hvad} — koer `git pull`", info=hvad)

    beskidt = _git("status", "--porcelain", "--", "backend", "src")
    ok("ingen ucommittede aendringer i backend/ og src/", not beskidt,
       naar_fejl=f"{len(beskidt.splitlines())} filer aendret lokalt — exe'en kan "
                 f"indeholde noget der ikke staar i git", blokerer=False)


# ── 2. exe'en ───────────────────────────────────────────────────────────────
def tjek_exe() -> None:
    kap("2. Frontend-exe'en")

    # ⚠ DEN EXE DER FAKTISK STARTES, er den i repo-roden. start_trading_dash.bat
    # koerer `%~dp0app.exe`. Byggestien er kun kilden paa en maskine der bygger,
    # og paa Ibens maskine findes den slet ikke. En kontrol der maalte
    # byggestien, ville maale en fil der aldrig aabnes — og paa en maskine der
    # baade bygger OG har en aeldre kopi i roden, ville den maale den forkerte.
    rod = HER.parent / "app.exe"
    bygget = HER.parent / "src-tauri" / "target" / "release" / "app.exe"

    exe = rod if rod.exists() else bygget
    if not ok("app.exe findes", exe.exists(),
              naar_fejl=f"hverken {rod} eller byggestien. Kopiér app.exe til "
                        f"repo-roden — det er den start_trading_dash.bat aabner"):
        return

    exe_tid = datetime.fromtimestamp(exe.stat().st_mtime, tz=timezone.utc)
    print(f"       {exe}")
    print(f"       filens tid {exe_tid.astimezone():%Y-%m-%d %H:%M}")

    # ⚠ TIDEN BEVISER MINDRE END DEN SER UD TIL. Paa en maskine der BYGGER, er
    # mtime byggetidspunktet. Paa en maskine der har faaet exe'en KOPIERET, er
    # den overfoerselstidspunktet — og en kopieret exe ser derfor altid frisk ud,
    # uanset hvad den indeholder. Maalt 11-08: kilden 07:56, kopien 08:11.
    #
    # Tidskontrollen nedenfor fanger stadig det almindelige tilfaelde (en exe der
    # aldrig blev opdateret), men den kan ikke skelne "nybygget" fra "netop
    # kopieret gammel binaer". Det kan summen. Sammenlign den paa tvaers af
    # maskiner — er de ens, er det den samme fil, uanset hvad tiderne siger.
    import hashlib
    h = hashlib.sha256(exe.read_bytes()).hexdigest()
    print(f"       sha256     {h[:32]}")
    print(f"       ⚠ tiden er BYGGE-tid paa en maskine der bygger, og")
    print(f"         OVERFOERSELS-tid paa en der har faaet filen kopieret.")
    print(f"         Kun summen beviser at to maskiner har samme exe.")

    if rod.exists() and bygget.exists():
        b_tid = datetime.fromtimestamp(bygget.stat().st_mtime, tz=timezone.utc)
        ok("roden er ikke aeldre end byggestien", exe_tid >= b_tid,
           naar_fejl=f"⚠ byggestien er NYERE ({b_tid.astimezone():%Y-%m-%d %H:%M}). "
                     f"Du har bygget uden at kopiere — launcheren aabner den gamle",
           blokerer=False)

    # ⚠ DEN FAELDE DER FAKTISK RAMTE OS. Exe'en er et build-artefakt og foelger
    # ikke med `git pull`. En maskine kan have helt ny backend-kode og en
    # frontend fra sidste uge — og saa mangler ordrekvitteringen, som er selve
    # det der viser hvilken konto ordren gik til. Intet fejler; hun ser bare
    # ingen kvittering, og har ingen grund til at savne den.
    for sha, hvad in FRONTEND_KRAV.items():
        ts = _git("show", "-s", "--format=%cI", sha)
        if not ts:
            continue
        c_tid = datetime.fromisoformat(ts)
        ok(f"exe nyere end {sha}", exe_tid >= c_tid,
           naar_fejl=f"⚠ EXE'EN MANGLER: {hvad}. Commit "
                     f"{c_tid.astimezone():%Y-%m-%d %H:%M} er nyere end exe "
                     f"{exe_tid.astimezone():%Y-%m-%d %H:%M}. GENBYG ELLER KOPIÉR.",
           info=hvad)


# ── 3. konfigurationen ──────────────────────────────────────────────────────
def tjek_konfig() -> dict | None:
    kap("3. account.yaml")

    try:
        import accounts
    except Exception as e:
        ok("accounts kan indlaeses", False, naar_fejl=f"{type(e).__name__}: {e}")
        return None

    i = accounts.identity
    print(f"       konto-id {i.account_id} · rolle {i.instance_role} · "
          f"ibkr_account {i.ibkr_account}")

    p = accounts.ordre_forbindelse()

    # ⚠ FRAVAER ER IKKE DET SAMME SOM FEJL. Foerste udgave meldte FEJL paa enhver
    # maskine uden blokken — og fejlteksten instruerede i at tilfoeje den. Paa
    # Soerens workstation, hvor den er slaaet FRA med vilje (11-08, saa Ibens
    # fasteriben2-session ikke kan blive stjaalet), ville en kontrol altsaa
    # sende folk hen for at genskabe netop det problem den skulle beskytte imod.
    #
    # Maskiner der ikke skal skille ordrer og kurser ad, er en gyldig opsaetning.
    # Skal fravaeret vaere en fejl — fordi man ER ved at saette konto 2 op — saa
    # sig det: --kraev.
    if p is None:
        if KRAEV_ORDRE_FORBINDELSE:
            ok("ordre_forbindelse findes", False,
               naar_fejl="--kraev er sat, men blokken mangler. Ordrer ville gaa "
                         "gennem den DELTE forbindelse — se konto2_opsaetning.md §5")
        else:
            print(f"  ADV  ingen ordre_forbindelse — ordrer gaar gennem den DELTE "
                  f"forbindelse til {i.ibkr_account}")
            print(f"       Det er en gyldig opsaetning. Skal maskinen handle paa "
                  f"konto 2, koer med --kraev.")
            print(f"       ⚠ Er den slaaet fra med vilje, saa lad den vaere: kun ÉN "
                  f"Gateway maa vaere logget")
            print(f"         ind som samme TWS-bruger ad gangen "
                  f"(konto2_opsaetning.md §6b).")
        return None

    # ⚠ Den vigtigste kontrol i afsnittet skal ogsaa SIGE at den bestod. Efter
    # --kraev-omskrivningen printede den kun host/port, saa laeseren skulle selv
    # slutte sig til at blokken var fundet. En kontrol der kun er synlig naar den
    # fejler, laeses som fravaerende naar den lykkes.
    ok("ordre_forbindelse findes", True)
    print(f"       {p['host']}:{p['port']} konto {p['konto']} "
          f"bruger {p.get('bruger') or '(ikke angivet)'}")

    konto = (p.get("konto") or "").upper()
    ok("konto angivet", bool(konto), naar_fejl="ordre_forbindelse.konto er tom")
    ok("konto er paper (D-praefiks)",
       konto.startswith("D") or bool(p.get("tillad_live")),
       naar_fejl=f"{konto} ligner live og tillad_live er ikke sat — V2 spaerrer")
    ok("bruger dokumenteret", bool(p.get("bruger")),
       naar_fejl="uden 'bruger' kan fejlbeskeden ved forkert login ikke naevne "
                 "hvem der burde vaere logget ind", blokerer=False)

    # V2 paa konfigurationen — samme kald som selve forbindelsen bruger.
    try:
        import ordre_forbindelse
        ordre_forbindelse.verificer_profil(p)
        ok("V2 slipper profilen igennem", True)
    except Exception as e:
        ok("V2 slipper profilen igennem", False, naar_fejl=str(e))

    return p


# ── 4. kurser ───────────────────────────────────────────────────────────────
def tjek_kurser() -> None:
    kap("4. Kurser — de kommer IKKE fra ordre-Gatewayen")

    import accounts
    i = accounts.identity
    maal = (i.replication_target_url or "").rstrip("/")

    if not ok("replication.target_url sat", bool(maal),
              naar_fejl="uden den kan maskinen ikke faa kurser, og frontenden "
                        "spaerrer en ordre den ikke kan prissaette"):
        return
    if not ok("internal_key sat", bool(i.internal_key),
              naar_fejl="algoserveren afviser opslaget uden noeglen"):
        return

    # ⚠ Et RIGTIGT opslag, ikke bare "adressen staar i filen". Forskellen mellem
    # konfigureret og virksom er hele pointen med denne fil.
    #
    # ⚠ OG DER SPOERGES PAA MES, IKKE SPY. Foerste udgave brugte SPY og lyste
    # roedt kl. 07:50 dansk — ikke fordi noget var galt, men fordi klokken var
    # 01:50 ET og aktiemarkedet lukket. En parathedskontrol der fejler af en
    # grund der intet har med parathed at goere, laerer sin laeser at se bort
    # fra den. Saa er den vaerre end ingen kontrol.
    #
    # MES handler naesten doegnet rundt, saa et manglende svar HER betyder
    # faktisk at feedet er nede. Maalt 11/8 kl. 07:50: MES 7781.5, SPY null.
    def _quote(t: str):
        rq = urllib.request.Request(f"{maal}/quote/{t}",
                                    headers={"X-Internal-Key": i.internal_key})
        with urllib.request.urlopen(rq, timeout=12) as r:
            return json.loads(r.read())

    try:
        d = _quote("MES")
        pris = d.get("price")
        ok("algoserverens feed svarer (MES)", bool(pris),
           naar_fejl=f"MES = {pris}. MES handler naesten doegnet rundt, saa dette "
                     f"er IKKE et spoergsmaal om aabningstid — feedet er nede",
           info=f"MES = {pris} (kilde: {d.get('kilde')})" if pris else "")

        # Aktiekurser til orientering. Er de tomme mens MES svarer, er markedet
        # bare lukket — det spaerrer ikke.
        s = _quote("SPY").get("price")
        print(f"       SPY = {s if s else 'ingen kurs (aktiemarkedet lukket?)'}")
    except urllib.error.HTTPError as e:
        ok("algoserverens feed svarer (MES)", False,
           naar_fejl=f"HTTP {e.code} — forkert internal_key?")
    except Exception as e:
        ok("algoserverens feed svarer (MES)", False,
           naar_fejl=f"{type(e).__name__}: {e} — er Tailscale oppe?")


# ── 5. porte ────────────────────────────────────────────────────────────────
def tjek_porte(p: dict | None) -> None:
    kap("5. Porte")

    import port_tjek
    b = port_tjek.lyttere(8000)
    ok("praecis ÉN backend paa 8000", len(b) == 1,
       naar_fejl=("INGEN lytter — start backenden" if not b else
                  f"⚠ {len(b)} processer paa samme port. De kan koere FORSKELLIG "
                  f"kode, og en ordre kan ramme hvilken som helst af dem"))
    for adr, pid in b:
        print(f"       {adr:24} PID {pid}")

    if p:
        g = port_tjek.lyttere(int(p["port"]))
        ok(f"Gateway paa {p['port']}", len(g) == 1,
           naar_fejl=(f"ingen Gateway — start IB Gateway og log ind som "
                      f"{p.get('bruger') or 'den rigtige bruger'}" if not g else
                      f"{len(g)} lyttere paa ordre-porten"))

    # ⚠ HVAD LIGGER DER PAA DEN DELTE PORT? Rent oplysende, men det er
    # spraengradius: gaar ordre_forbindelse tabt (en fejlindrykning, en glemt
    # blok), falder ordreveje tilbage til 127.0.0.1:7497. Lytter der ingenting,
    # fejler et saadant uheld HOEJLYDT. Lytter der en TWS, gaar ordren derhen —
    # tavst, og til hvilken konto DEN nu styrer.
    delt = port_tjek.lyttere(7497)
    if delt:
        print(f"       ⚠ 7497 (delt/TWS): {len(delt)} lytter(e) — en ordre uden "
              f"ordre_forbindelse ville gaa HERTIL")
        for adr, pid in delt:
            print(f"          {adr:24} PID {pid}")
    else:
        print("       7497 (delt/TWS): tom — et konfigurationsuheld ville "
              "fejle hoejlydt frem for at ramme forbi")


# ── 6. journalen ────────────────────────────────────────────────────────────
def tjek_journal() -> None:
    kap("6. Journalens database")

    db = HER / "trading_dash.db"
    if not ok("trading_dash.db findes", db.exists(),
              naar_fejl="den dannes ved foerste backend-start"):
        return
    try:
        with sqlite3.connect(str(db)) as c:
            kol = {r[1] for r in c.execute("PRAGMA table_info(trades)")}
        # ⚠ Migrationen (19b55c8) tilfoejer 'paper' til en EKSISTERENDE database.
        # Alle tests bygger friske databaser, saa fejlen kunne kun ses her.
        ok("kolonnen 'paper' findes", "paper" in kol,
           naar_fejl="migrationen (19b55c8) er ikke koert — start backenden én gang")
        ok("kolonnen 'ibkr_account' findes", "ibkr_account" in kol,
           naar_fejl="uden den kan en handel ikke stemples med den konto den gik til")
    except Exception as e:
        ok("databasen kan laeses", False, naar_fejl=f"{type(e).__name__}: {e}")


# ── 7. client-id ────────────────────────────────────────────────────────────
def tjek_client_ids() -> None:
    kap("7. Client-id-registret")
    try:
        import ibkr_client_ids as r
        r.kontroller()
        ok("registret er konsistent", True,
           info=f"ordre-forbindelsen bruger {r.ORDRE}, backenden {r.BACKEND}")
    except Exception as e:
        ok("registret er konsistent", False, naar_fejl=str(e))


def main() -> int:
    global KRAEV_ORDRE_FORBINDELSE
    import argparse
    ap = argparse.ArgumentParser(
        description="Er denne maskine klar til at handle paa konto 2?")
    ap.add_argument("--kraev", action="store_true",
                    help="behandl en manglende ordre_forbindelse som en FEJL. Bruges naar man ER ved at saette konto 2 op.")
    KRAEV_ORDRE_FORBINDELSE = ap.parse_args().kraev

    print("=" * 78)
    print("ER DENNE MASKINE KLAR TIL KONTO 2?")
    print("=" * 78)

    tjek_kode()
    tjek_exe()
    p = tjek_konfig()
    tjek_kurser()
    tjek_porte(p)
    tjek_journal()
    tjek_client_ids()

    print("\n" + "=" * 78)
    if ADVARSEL:
        print(f"{len(ADVARSEL)} ADVARSEL — spaerrer ikke:")
        for a in ADVARSEL:
            print(f"   · {a}")
        print()
    if FEJL:
        print(f"⚠ {len(FEJL)} TING SPAERRER:")
        for f in FEJL:
            print(f"   · {f}")
        print("\nHandl ikke foer de er lukket.")
    else:
        print("Alt maalt her er i orden.")

    print("\n⚠ IKKE DAEKKET AF DENNE KONTROL:")
    print("   · om Gatewayen er logget ind som den rigtige BRUGER — det ses")
    print("     foerst naar forbindelsen aabnes (V1 i ordre_forbindelse.py)")
    print("   · om kontoen har futures-tilladelse — det afgoer IBKR")
    print("=" * 78)
    return 1 if FEJL else 0


if __name__ == "__main__":
    sys.exit(main())
