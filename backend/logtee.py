#!/usr/bin/env python3
"""
logtee.py — skriv stdin baade til skaermen OG til en fil
════════════════════════════════════════════════════════════════════════════════
Windows' cmd har ingen `tee`. Bruges af start_backend.bat --synlig, saa backendens
udskrift kan LAESES i vinduet uden at logfilen gaar tabt.

    python -u -m uvicorn main:app ... 2>&1 | python -u logtee.py "sti\\til\\fil.log"

⚠ HVORFOR IKKE PowerShells Tee-Object. Den ville virke, men den traekker en hel
PowerShell-proces ind i roeret paa en server der skal koere hele dagen, og den
aendrer hvordan Ctrl+C forplanter sig. Det her er fem linjer og ingen ny
afhaengighed — Python er der allerede.

⚠ LINJEVIS OG UBUFRET. Skrives der i blokke, staar vinduet tomt i lange perioder
og faar backenden til at se doed ud — praecis det problem der gjorde tee'en
noedvendig.

⚠ EXITKODEN EFTER ET ROER TILHOERER SIDSTE LED, altsaa denne fil — ikke uvicorn.
Derfor viser start_backend.bat ikke laengere et exitnummer i sin stop-banner; den
peger paa de sidste linjer i stedet. Et forkert tal er vaerre end intet tal.
"""
import sys


def main() -> int:
    if len(sys.argv) < 2:
        print("brug: logtee.py <logfil>", file=sys.stderr)
        return 2
    ind = sys.stdin.buffer
    ud = sys.stdout.buffer
    try:
        with open(sys.argv[1], "ab", buffering=0) as f:
            while True:
                linje = ind.readline()
                if not linje:
                    break
                ud.write(linje)
                ud.flush()
                f.write(linje)
    except KeyboardInterrupt:
        # Ctrl+C rammer hele roeret. Ikke en fejl — brugeren stoppede serveren.
        pass
    except OSError as e:
        print(f"[logtee] kunne ikke skrive til {sys.argv[1]}: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
