r"""
vol_kvartalsjob.py — D1.3: staaende kvartalsjob mod IBKR's purge-graense
═══════════════════════════════════════════════════════════════════════════════════
IBKR purger kontraktdefinitionen ca. 24 maaneder efter udloeb, og graensen FLYTTER
SIG FREMAD hele tiden: hvert kvartal doer endnu en kontrakt permanent. Vi taber
altsaa ikke adgang til fortiden én gang — vi taber loebende, kvartal for kvartal, saa
laenge vi ikke goer noget.

Vendt om er det en mulighed. Hoster vi hver kontrakts fulde 1-min-historik mens den
STADIG kan kvalificeres, vokser arkivet med et aar hvert aar. Toaarsgraensen binder
kun saa laenge man ikke arkiverer. Om tre aar har vi fem aars futures-intradag og kan
udvikle lag 3 direkte paa MES og M2K i stedet for omvejen om SPY og IWM — og
proxy-metoden bliver da en valideret reserveloesning frem for en noedvendighed.

Jobbet goer fire ting, i den raekkefoelge:
  1. Kortlaegger hvilke kontrakter der STADIG kan kvalificeres, og skriver det i en
     log. Graensen er kun kendt som "mellem 23 og 25,5 maaneder"; strammer IBKR den,
     opdager vi det her frem for ved at mangle data.
  2. Peger paa de kontrakter der er udloebet men endnu ikke arkiveret — med god
     margin til graensen (sigt efter inden for TOLV maaneder, ikke tyve).
  3. Verificerer arkivet (bitroed opdages kun her) og reparerer hvad der kan reddes.
  4. Skriver en post i vol_arkiv_log.md.

Selve hoestningen koeres af `harvest_futures_1min.py` — jobbet udskriver de praecise
kommandoer frem for at starte dem selv, saa en lang hentning aldrig gaar i gang
uovervaaget midt i en handelsdag.

KOERSEL (kvartalsvis, efter hver udloeb — TWS/Gateway skal vaere aabent):
    python vol_kvartalsjob.py
    python vol_kvartalsjob.py --symbols MES,M2K,ES,RTY --client-id 76
    python vol_kvartalsjob.py --uden-ibkr      # kun arkivdelen, ingen TWS noedvendig
"""
from __future__ import annotations

import asyncio

# Python 3.14: skal staa FOER ib_async importeres
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import argparse
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

HOST, PORT, CLIENT_ID = "127.0.0.1", 7497, 76
SYMBOLER = ["MES", "M2K", "ES", "RTY"]
EXCHANGE = {"MES": "CME", "M2K": "CME", "ES": "CME", "RTY": "CME"}
KVARTALSMAANEDER = (3, 6, 9, 12)

# Hvor langt tilbage vi overhovedet proever at kvalificere. 30 maaneder daekker
# graensen (~24) med luft nok til at se hvor den ligger.
PROBE_MAANEDER_TILBAGE = 30
# Arkivér en udloebet kontrakt inden for tolv maaneder — ikke tyve. Marginen er
# hele pointen: graensen kan strammes uden varsel.
MAALSAETNING_MAANEDER = 12

# KONTROLFIKSTUR I BEGGE RETNINGER (E2). Permanent, ikke noget vi tilfoejer naar vi
# kommer i tvivl. Kvalificerer den kendt-negative, er kortlaegningen vaerdiloes og
# hele resultatet kasseres — for saa svarer IBKR ja til alt.
KENDT_NEGATIV_YM = "201503"          # elleve aar gammel; kan ikke leve
ARKIV_LOG = "vol_arkiv_log.md"


def maaneder_mellem(a: date, b: date) -> float:
    return (b.year - a.year) * 12 + (b.month - a.month) + (b.day - a.day) / 30.44


def kvartalsmaaneder(fra: date, til: date) -> list[str]:
    ud = []
    y = fra.year
    while y <= til.year:
        for m in KVARTALSMAANEDER:
            d = date(y, m, 20)
            if fra <= d <= til:
                ud.append(f"{y}{m:02d}")
        y += 1
    return sorted(ud)


async def kan_kvalificeres(ib, symbol: str, ym: str) -> tuple[bool, str]:
    """Findes kontraktdefinitionen stadig hos IBKR?

    ⚠ `qualifyContractsAsync` returnerer en TRUTHY liste ogsaa naar kvalificeringen
    mislykkedes — med conId=0. Derfor bruges reqContractDetails, og conId tjekkes
    eksplicit. Se ibkr_kvalificer.py for hele historien.
    """
    from ib_async import Future
    try:
        base = Future(symbol=symbol, exchange=EXCHANGE.get(symbol, "CME"), currency="USD",
                      lastTradeDateOrContractMonth=ym, includeExpired=True)
        det = await asyncio.wait_for(ib.reqContractDetailsAsync(base), timeout=15)
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:60]}"
    for cd in (det or []):
        if getattr(cd.contract, "conId", 0):
            return True, str(cd.contract.conId)
    return False, "ingen kontraktdefinition"


async def kortlaeg(args, emit, ib=None) -> dict:
    """Kortlaeg hvilke kontrakter der stadig kan kvalificeres.

    `ib` kan injiceres. Det er ikke bekvemmelighed: uden den kan KONTROLFIKSTURETS
    kasseringsvej ikke afproeves uden en levende TWS — og en kontrol hvis fejlvej
    aldrig er koert, er ikke en kontrol (Revision G). Se test_vol_kontroller.py.
    """
    i_dag = date.today()
    fra = i_dag - timedelta(days=int(PROBE_MAANEDER_TILBAGE * 30.44))
    ym_liste = kvartalsmaaneder(fra, i_dag + timedelta(days=200))
    symboler = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    egen_forbindelse = ib is None
    if egen_forbindelse:
        from ib_async import IB
        ib = IB()
        emit(f"Forbinder TWS {HOST}:{args.port} (clientId={args.client_id}) …")
        try:
            await ib.connectAsync(HOST, args.port, clientId=args.client_id, timeout=20)
        except Exception as e:
            emit(f"KUNNE IKKE FORBINDE: {e}")
            return {}
        emit("Forbundet.\n")

    resultat: dict[str, dict[str, bool]] = {}
    kontrol_ok = True
    try:
        # Kontrolfikstur foerst: svarer IBKR ja til en kontrakt der umuligt kan leve,
        # er alt hvad der foelger stoej.
        lever, hvorfor = await kan_kvalificeres(ib, symboler[0], KENDT_NEGATIV_YM)
        if lever:
            emit(f"KONTROLFIKSTUR DUMPET: {symboler[0]} {KENDT_NEGATIV_YM} kvalificerede "
                 f"(conId {hvorfor}). IBKR svarer ja til alt — kortlaegningen kasseres.")
            kontrol_ok = False
        else:
            emit(f"Kontrolfikstur OK: {symboler[0]} {KENDT_NEGATIV_YM} kvalificerer ikke "
                 f"({hvorfor}) — som en elleve aar gammel kontrakt skal.\n")

        if kontrol_ok:
            for sym in symboler:
                resultat[sym] = {}
                emit(f"[{sym}]")
                for ym in ym_liste:
                    lever, hvorfor = await kan_kvalificeres(ib, sym, ym)
                    resultat[sym][ym] = lever
                    udl = date(int(ym[:4]), int(ym[4:]), 20)
                    alder = maaneder_mellem(udl, i_dag)
                    naar = f"udloeb om {-alder:.1f} mdr" if alder < 0 else f"{alder:.1f} mdr siden"
                    emit(f"   {ym}  {'LEVER' if lever else 'purget'}   ({naar})")
                emit("")
    finally:
        if egen_forbindelse:
            try:
                ib.disconnect()
            except Exception:
                pass
    return resultat if kontrol_ok else {}


def graense_af(resultat: dict) -> tuple[str | None, str | None]:
    """Indsnaevr purge-graensen: aeldste kontrakt der lever, yngste der er purget."""
    aeldste_levende, yngste_purget = None, None
    for sym, pr in resultat.items():
        for ym in sorted(pr):
            if pr[ym]:
                if aeldste_levende is None or ym < aeldste_levende:
                    aeldste_levende = ym
            else:
                if yngste_purget is None or ym > yngste_purget:
                    yngste_purget = ym
    return aeldste_levende, yngste_purget


def mangler_arkivering(resultat: dict, rod: Path) -> list[tuple[str, str, float]]:
    """Udloebne kontrakter der stadig lever, men som vi ikke har hoestet."""
    i_dag = date.today()
    clean = rod / "data_harvest" / "mes_m2k_clean"
    ud = []
    for sym, pr in sorted(resultat.items()):
        for ym, lever in sorted(pr.items()):
            if not lever:
                continue
            udl = date(int(ym[:4]), int(ym[4:]), 20)
            if udl > i_dag:                     # endnu ikke udloebet — hoestes senere
                continue
            if (clean / f"{sym}_{ym}_1min.csv").exists():
                continue
            ud.append((sym, ym, maaneder_mellem(udl, i_dag)))
    ud.sort(key=lambda t: -t[2])                # mest udsatte foerst
    return ud


def skriv_log(rod: Path, resultat: dict, mangler: list, arkiv_status: str,
              kortlagt: bool = True) -> Path:
    sti = rod / "vol_probe_output" / ARKIV_LOG
    sti.parent.mkdir(exist_ok=True)
    aeldste, yngste = graense_af(resultat)
    L = []
    if not sti.exists():
        L.append("# Arkivlog — kvartalsvis kortlaegning af IBKR's purge-graense\n\n"
                 "Hver post viser hvilke futures-kontrakter der stadig kunne "
                 "kvalificeres paa koerselsdagen. Graensen flytter sig fremad, og "
                 "strammer IBKR den, skal det kunne ses her frem for at blive opdaget "
                 "som manglende data.\n")
    L.append(f"\n---\n\n## {date.today()}\n\n")
    if not kortlagt:
        # At springe et trin over og at faa et ubrugeligt svar er IKKE det samme.
        # Skrives de ens, laeser en sprunget kortlaegning senere som en fejlet én.
        L.append("Kortlaegningen blev **ikke koert** (`--uden-ibkr`). Denne post siger "
                 "intet om purge-graensen og intet om hvad der mangler at blive "
                 "hoestet — kun om arkivets tilstand.\n")
    elif not resultat:
        L.append("Kortlaegningen blev **kasseret** — kontrolfiksturet dumpede eller "
                 "TWS svarede ikke. Ingen konklusion om graensen kan drages af denne "
                 "koersel.\n")
    else:
        L.append(f"Aeldste kontrakt der stadig lever: **{aeldste}** · "
                 f"yngste der er purget: **{yngste}**\n\n")
        # Graensen er et INTERVAL, ikke et tal. Den aeldste levende kontrakts alder er
        # en NEDRE graense (saa laenge lever de mindst), den yngste purgedes alder en
        # OEVRE. At skrive ét tal ville give en praecision maalingen ikke har — og et
        # for lavt tal ville faa redningsvinduet til at se kortere ud end det er.
        if aeldste:
            ned = maaneder_mellem(date(int(aeldste[:4]), int(aeldste[4:]), 20), date.today())
            if yngste:
                op = maaneder_mellem(date(int(yngste[:4]), int(yngste[4:]), 20), date.today())
                L.append(f"Purge-graensen ligger dermed mellem **{ned:.1f} og {op:.1f} "
                         f"maaneder** efter udloeb: {aeldste} lever ved {ned:.1f} mdr, "
                         f"mens {yngste} er vaek ved {op:.1f} mdr.\n\n")
            else:
                L.append(f"Ingen purget kontrakt fundet i vinduet, saa graensen er kun "
                         f"kendt som **over {ned:.1f} maaneder** efter udloeb.\n\n")
        L.append("| symbol | " + " | ".join(sorted(next(iter(resultat.values())))) + " |\n")
        L.append("|---" * (1 + len(next(iter(resultat.values())))) + "|\n")
        for sym, pr in sorted(resultat.items()):
            L.append(f"| {sym} | " + " | ".join("lever" if pr[ym] else "—"
                                                for ym in sorted(pr)) + " |\n")
    if mangler:
        L.append(f"\n**{len(mangler)} udloebne kontrakter lever endnu, men er ikke hoestet:**\n\n")
        for sym, ym, alder in mangler:
            haster = " ⚠ HASTER" if alder > MAALSAETNING_MAANEDER else ""
            L.append(f"- {sym} {ym} — udloebet for {alder:.1f} mdr siden{haster}\n")
    elif kortlagt and resultat:
        L.append("\nAlle udloebne kontrakter der stadig lever, er hoestet.\n")
    else:
        L.append("\nHvad der mangler at blive hoestet, er **ikke undersoegt** i denne "
                 "koersel.\n")
    L.append(f"\nArkivstatus: {arkiv_status}\n")
    with sti.open("a", encoding="utf-8") as f:
        f.write("".join(L))
    return sti


def koer_arkiv(rod: Path, dest: str | None, emit) -> str:
    """Verificér arkivet og reparér hvad der kan reddes. Bitroed ses kun her."""
    # --fuld paa kopier: hash hver kildefil frem for at stole paa mtime. Bitroed i
    # KILDEN aendrer ikke mtime, saa hurtigstien ville springe den over — og en
    # raadden kilde er praecis hvad der ikke maa naa arkivet.
    cmd = [sys.executable, str(rod / "arkiv_futures.py"), "verificer", "--reparer"]
    if dest:
        cmd += ["--dest", dest]
    try:
        p = subprocess.run(cmd, cwd=rod, capture_output=True, text=True, timeout=1800)
    except Exception as e:
        emit(f"Arkivverifikation kunne ikke koeres: {e}")
        return f"kunne ikke koeres ({e})"
    emit(p.stdout.strip() or p.stderr.strip())
    sidste = [l for l in (p.stdout or "").splitlines() if l.strip()]
    return (sidste[-1] if sidste else "intet output") + f"  (exit {p.returncode})"


def main() -> int:
    ap = argparse.ArgumentParser(description="D1.3: kvartalsjob mod IBKR's purge-graense")
    ap.add_argument("--symbols", default=",".join(SYMBOLER))
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--client-id", dest="client_id", type=int, default=CLIENT_ID)
    ap.add_argument("--dest", default=None, help="arkivrod (default: arkiv_futures.ARKIV_ROD)")
    ap.add_argument("--uden-ibkr", action="store_true",
                    help="spring kortlaegningen over; koer kun arkivdelen")
    args = ap.parse_args()

    rod = Path(__file__).resolve().parent
    def emit(s=""):
        # Konsollen paa Windows er cp1252 og kan ikke skrive rammetegn eller ⚠.
        # Rapportfilerne skrives i UTF-8 og beholder dem.
        try:
            print(s, flush=True)
        except UnicodeEncodeError:
            enc = sys.stdout.encoding or "ascii"
            print(s.encode(enc, errors="replace").decode(enc), flush=True)

    resultat = {}
    if not args.uden_ibkr:
        emit("=" * 70)
        emit("1) Hvilke kontrakter kan stadig kvalificeres?")
        emit("=" * 70)
        resultat = asyncio.run(kortlaeg(args, emit))

    emit("=" * 70)
    emit("2) Hvad mangler at blive arkiveret?")
    emit("=" * 70)
    mangler = mangler_arkivering(resultat, rod) if resultat else []
    if args.uden_ibkr:
        emit("IKKE UNDERSOEGT — kortlaegningen blev sprunget over (--uden-ibkr).")
    elif not resultat:
        emit("IKKE UNDERSOEGT — kortlaegningen blev kasseret eller kunne ikke koeres.")
    elif not mangler:
        emit("Intet. Alle udloebne kontrakter der stadig lever, er hoestet.")
    else:
        for sym, ym, alder in mangler:
            emit(f"   {sym} {ym} - udloebet for {alder:.1f} mdr siden"
                 + ("   !! over maalsaetningen paa "
                    f"{MAALSAETNING_MAANEDER} mdr" if alder > MAALSAETNING_MAANEDER else ""))
        emit("\nKoer disse, aeldste kontrakt foerst (kronologisk stigende, jf. E3):")
        for sym, ym, _ in sorted(mangler, key=lambda t: (t[1], t[0])):
            y, m = int(ym[:4]), int(ym[4:])
            start = (date(y, m, 20) - timedelta(days=100)).isoformat()
            emit(f"   python harvest_futures_1min.py --symbols {sym} "
                 f"--start {start} --end {date(y, m, 20).isoformat()}")

    emit("\n" + "=" * 70)
    emit("3) Arkivets tilstand")
    emit("=" * 70)
    arkiv_status = koer_arkiv(rod, args.dest, emit)

    sti = skriv_log(rod, resultat, mangler, arkiv_status,
                    kortlagt=not args.uden_ibkr)
    emit(f"\nLogpost skrevet: {sti}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
