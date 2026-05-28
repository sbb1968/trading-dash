"""
net_stability_test.py - maaler stabiliteten af netforbindelsen over tid.

Baggrund: baade Konfluens (workstation) og ORB (algoserver) oplevede
datafeed-timeouts og genforbindinger 2026-05-28. Symptomet var IKKE
manglende forbindelse, men USTABIL forbindelse - den virker, fejler,
kommer sig. Dette script maaler om netforbindelsen er stabil nok til
paalidelig handel, ved at teste over flere minutter frem for et enkelt
oejebliksbillede.

Tester tre ting gentagne gange:
  1. DNS + TCP-forbindelse til IBKR's servere (det TWS taler med)
  2. TCP-forbindelse til en generel baseline (Cloudflare) for at skelne
     "IBKR-problem" fra "generelt net-problem"
  3. Lokal TWS-port (7497) hvis TWS koerer - er den lokale forbindelse OK?

Maaler for hver: lykkedes forbindelsen, og hvor lang tid tog den (latency).
Opsummerer til sidst: succesrate, gennemsnit/min/max latency, og en dom.

Koer (helst naar TWS er lukket, saa testen ikke konkurrerer om forbindelsen,
ELLER efter handelsdagen):
    python net_stability_test.py
    python net_stability_test.py --minutes 10   # laengere test

Scriptet bruger kun standardbiblioteket - ingen pip-installation noedvendig.
"""
import socket
import time
import argparse
import statistics
from datetime import datetime

# IBKR's gateway-servere som TWS forbinder til. Vi tester et par stykker.
# (Disse er IBKR's offentlige gateway-hostnavne; portene er standard TWS/IBKR.)
IBKR_HOSTS = [
    ("gw1.ibllc.com", 4000),
    ("gw2.ibllc.com", 4000),
    ("cdc1.ibllc.com", 4000),
]
# Baseline - hvis disse ogsaa fejler, er det generelt net, ikke IBKR.
BASELINE_HOSTS = [
    ("1.1.1.1", 443),       # Cloudflare
    ("8.8.8.8", 443),       # Google DNS
]
# Lokal TWS-port (kun relevant hvis TWS koerer paa denne maskine).
LOCAL_TWS = ("127.0.0.1", 7497)

TIMEOUT_SEC = 5.0


def probe(host, port, timeout=TIMEOUT_SEC):
    """Forsoeg en TCP-forbindelse. Returner (success, latency_ms eller None)."""
    start = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            latency = (time.perf_counter() - start) * 1000
            return True, latency
    except Exception:
        return False, None


def run_round(targets, label):
    """Test alle targets en gang, returner liste af (host, success, latency)."""
    results = []
    for host, port in targets:
        ok, lat = probe(host, port)
        results.append((host, ok, lat))
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=5.0,
                    help="hvor laenge testen koerer (default 5 min)")
    ap.add_argument("--interval", type=float, default=10.0,
                    help="sekunder mellem hver runde (default 10)")
    args = ap.parse_args()

    total_rounds = int((args.minutes * 60) / args.interval)
    print("=" * 64)
    print("  NETVAERKS-STABILITETSTEST")
    print("=" * 64)
    print(f"  Varighed: {args.minutes} min   Interval: {args.interval} sek")
    print(f"  Runder:   {total_rounds}")
    print(f"  Start:    {datetime.now().strftime('%H:%M:%S')}")
    print("=" * 64)
    print("  Tip: koer denne naar TWS er LUKKET, saa de ikke konkurrerer.")
    print("=" * 64)

    # Saml resultater pr. kategori
    ibkr_lat, ibkr_fail = [], 0
    base_lat, base_fail = [], 0
    tws_lat,  tws_fail  = [], 0
    ibkr_total = base_total = tws_total = 0

    for r in range(total_rounds):
        ts = datetime.now().strftime("%H:%M:%S")

        # IBKR-servere: tael som ?T forsoeg pr. runde (bedste af de tre)
        ibkr_results = run_round(IBKR_HOSTS, "IBKR")
        ibkr_oks = [lat for _, ok, lat in ibkr_results if ok]
        ibkr_total += 1
        if ibkr_oks:
            ibkr_lat.append(min(ibkr_oks))
            ibkr_status = f"OK ({min(ibkr_oks):.0f}ms)"
        else:
            ibkr_fail += 1
            ibkr_status = "FEJL"

        # Baseline
        base_results = run_round(BASELINE_HOSTS, "BASE")
        base_oks = [lat for _, ok, lat in base_results if ok]
        base_total += 1
        if base_oks:
            base_lat.append(min(base_oks))
            base_status = f"OK ({min(base_oks):.0f}ms)"
        else:
            base_fail += 1
            base_status = "FEJL"

        # Lokal TWS (kun hvis den svarer)
        tws_ok, tws_l = probe(*LOCAL_TWS)
        tws_total += 1
        if tws_ok:
            tws_lat.append(tws_l)
            tws_status = f"OK ({tws_l:.0f}ms)"
        else:
            tws_fail += 1
            tws_status = "lukket/fejl"

        print(f"[{ts}] runde {r+1:>3}/{total_rounds}  "
              f"IBKR: {ibkr_status:<12} Baseline: {base_status:<12} "
              f"TWS-lokal: {tws_status}")

        if r < total_rounds - 1:
            time.sleep(args.interval)

    # -- Opsummering --
    def summarize(name, lats, fails, total):
        rate = 100 * (total - fails) / total if total else 0
        print(f"\n  {name}:")
        print(f"    Succesrate: {rate:.0f}%  ({total-fails}/{total} lykkedes, {fails} fejlede)")
        if lats:
            print(f"    Latency:    snit {statistics.mean(lats):.0f}ms  "
                  f"min {min(lats):.0f}ms  max {max(lats):.0f}ms")
            if len(lats) > 1:
                print(f"    Udsving:    stdafv {statistics.pstdev(lats):.0f}ms "
                      f"(hoejt = ustabilt)")

    print("\n" + "=" * 64)
    print("  RESULTAT")
    print("=" * 64)
    summarize("IBKR-servere", ibkr_lat, ibkr_fail, ibkr_total)
    summarize("Baseline (Cloudflare/Google)", base_lat, base_fail, base_total)
    summarize("Lokal TWS (port 7497)", tws_lat, tws_fail, tws_total)

    print("\n" + "=" * 64)
    print("  FORTOLKNING")
    print("=" * 64)
    ibkr_rate = 100 * (ibkr_total - ibkr_fail) / ibkr_total if ibkr_total else 0
    base_rate = 100 * (base_total - base_fail) / base_total if base_total else 0

    if ibkr_rate >= 99 and base_rate >= 99:
        print("  Forbindelsen ser STABIL ud (>=99% paa baade IBKR og baseline).")
        print("  Hvis I alligevel oplever datafeed-hikke, ligger problemet")
        print("  sandsynligvis i TWS/IBKR-laget, ikke i netforbindelsen selv.")
    elif base_rate >= 99 and ibkr_rate < 99:
        print("  Baseline er stabil, men IBKR-servere fejler/svinger.")
        print("  -> Peger paa et IBKR-specifikt problem (deres servere, eller")
        print("     ruten til dem), ikke jeres generelle internet.")
    elif base_rate < 99:
        print("  BASELINE fejler ogsaa -> det er den GENERELLE netforbindelse")
        print("  der er ustabil. Det rammer alt, inkl. IBKR. Dette underbygger")
        print("  at Ibens netforbindelse b_r forbedres.")
    else:
        print("  Blandet billede - se tallene ovenfor.")

    if ibkr_lat and statistics.pstdev(ibkr_lat) > 100:
        print("\n  BEMAERK: stor latency-variation mod IBKR (jitter). Selv hvis")
        print("  succesraten er hoej, kan store udsving give timeouts under")
        print("  realtime-handel. Det passer med de hikke I har set.")

    print("=" * 64)
    print(f"  Slut: {datetime.now().strftime('%H:%M:%S')}")
    print("=" * 64)


if __name__ == "__main__":
    main()
