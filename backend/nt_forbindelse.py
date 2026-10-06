"""
nt_forbindelse.py — den SKRIVENDE forbindelse til NinjaTrader, med vagter
════════════════════════════════════════════════════════════════════════════════
Pendant til `ordre_forbindelse.py`, men mod NinjaTrader 8's ATI i stedet for
IBKR. Samme opgave, samme disciplin, helt andet maskineri.

    LÆS    ATI-socket 36973 — NT8 pusher kontotilstand af sig selv
    SKRIV  OIF-filer i `Documents\\NinjaTrader 8\\incoming\\`
    KURSER kommer stadig fra IBKR/algoserveren. ATI leverer ingen.

────────────────────────────────────────────────────────────────────────────────
⚠ HVORFOR DET HER MODUL OVERHOVEDET FINDES

IBKR's API binder en forbindelse til en konto: man logger på, og ordrer arver
kontoen. **ATI har ingen forbindelse.** Hver ordre er en selvstændig tekstfil,
og kontoen er felt nummer to:

    PLACE;DEMO8580770;MES 09-26;BUY;1;LIMIT;4602.50;;DAY;;id;;
          └── feltet ──┘

Lades feltet tomt, bruger NT8 sin egen **Default account** — den der tilfældigvis
står i platformens dropdown. Konfigurationsfilen kan altså sige én ting og
ordren gå et andet sted hen, uden at noget fejler.

Derfor de fire vagter. De er skrevet NU, ikke når de får brug for sig selv:
Søren har siden 16-09 en **live-konto (2080414)** hos Payward Europe. Den har
ikke vist sig i ATI-strømmen endnu, men rapportens eget forbehold gælder —
fravær er ikke bevis.

  V1  KONTOEN SKRIVES EKSPLICIT i hver eneste kommando. Aldrig tom, aldrig
      overladt til Default account.
  V2  PAPER/SIM-BEKRÆFTELSE på konfigurationen, FØR der røres noget. Kontoen
      skal stå på listen over kendte simulationskonti, medmindre `tillad_live`
      er sat udtrykkeligt.
  V3  KONTOEN SKAL FINDES I VIRKELIGHEDEN. ATI-strømmen fortæller hvilke konti
      NT8 faktisk er forbundet til lige nu. Står den konfigurerede ikke dér,
      sendes der intet — samme regel som `ordre_forbindelse`s V1, der læser
      `managedAccounts()` efter connect.
  V4  EN LIVE-KONTO I STRØMMEN RÅBER OP. Dukker der en konto op som hverken er
      kendt sim eller udtrykkeligt tilladt, er blast radius ændret siden vi
      målte, og det skal siges — ikke opdages senere.

────────────────────────────────────────────────────────────────────────────────
⚠ TO TING VI HAR MÅLT, SOM ER LETTE AT TAGE FEJL AF

**Filnavnet skal begynde med `oif`.** `TDPROBE.oif.txt` gav *"Unknown OIF file
type"* — ordet *type* var nøglen; NT8 klassificerer på navnet, ikke indholdet.
Med et forkert navn læses filen aldrig, uanset hvor korrekt den er.

**ATI-strømmen er et ØJEBLIKSBILLEDE, ikke en opregning.** Målt 26-09: en ordre
der beviseligt fandtes stod ikke i det snapshot der blev læst lige før den blev
annulleret. At noget ikke ses i ét oplæg beviser derfor ingenting — kun en
`OrderStatus|<id> <tilstand>` med terminal tilstand gør.
"""
from __future__ import annotations

import logging
import pathlib
import re
import socket
import time
from typing import Optional

import accounts

logger = logging.getLogger(__name__)

# ── Hvor NT8 bor ───────────────────────────────────────────────────────────
NT8_ROD = pathlib.Path.home() / "Documents" / "NinjaTrader 8"
INCOMING = NT8_ROD / "incoming"
LOGMAPPE = NT8_ROD / "log"
ATI_HOST, ATI_PORT = "127.0.0.1", 36973

# ⚠ KENDTE SIMULATIONSKONTI. Alt andet kræver tillad_live: true.
# Sim101      NT8's egen simulator — ruter ingen steder
# DEMO8580770 Tradovates demokonto — rigtig infrastruktur, legetøjspenge
# ⚠ V2 spaerrer alt der ikke staar her. DEMO8635291 er Ibens egen
# Tradovate-demokonto; uden den ville hendes foerste klik blive afvist.
SIM_KONTI = {"SIM101", "DEMO8580770", "DEMO8635291"}

# Hvor længe vi lytter når strømmen skal aflæses. NT8 pusher af sig selv, så
# der skal ikke sendes noget — men den sender ikke øjeblikkeligt.
LYT_SEK = 6.0


class NtForbindelseFejl(Exception):
    """Rejses når en vagt spærrer. Aldrig fanget og logget videre — en spærret
    ordrevej skal stoppe kaldet, ikke farve det."""


class NtTilstandUkendt(Exception):
    """ATI kunne ikke aflæses.

    ⚠ Det er IKKE det samme som "ingen konti". Et tomt svar fra en socket der
    ikke svarede, må aldrig kunne læses som at NT8 ikke har nogen konti — så
    ville V3 bestå ved at fejle. Samme fejlklasse som afstemningen der sagde
    "nul bogførte" fordi den ikke kunne læse tabellen.
    """


# ═══════════════════════════════════════════════════════════════════════════
# Instrumentnavnet — NT8's format, udledt af IBKR's kontrakt
# ═══════════════════════════════════════════════════════════════════════════
# ⚠ MAANEDEN MAA IKKE HARDKODES. Watchlisten siger "MES"; NT8 kraever
# "MES 12-26" (symbol + MM-YY). Skrev vi maaneden i en konstant, ville
# ordrevejen knaekke ved hver rulning — og den knaekker STILLE: loggen siger
# "holds unknown instrument", ordren oprettes ikke, og intet andet sker.
#
# MESU6 udloeb 18-09-2026. En konstant skrevet i august ville have virket i
# seks uger og derefter vaeret tavst forkert.
#
# Derfor udledes maaneden af den kontrakt IBKR allerede har kvalificeret.
# `qualify_future` vaelger den MEST HANDLEDE kontrakt, ikke bare den naermeste
# ikke-udloebne — saa vi faar samme kontrakt som kurserne kommer fra.
_MAANEDSKODE = {"F": 1, "G": 2, "H": 3, "J": 4, "K": 5, "M": 6,
                "N": 7, "Q": 8, "U": 9, "V": 10, "X": 11, "Z": 12}


def nt_instrument(symbol: str, kontrakt=None) -> str:
    """"MES" + IBKR-kontrakten -> "MES 12-26".

    `kontrakt` er et ib_async-Contract. Uden den kastes der — vi GAETTER ikke
    en maaned.
    """
    sym = (symbol or "").upper().strip()
    if not sym:
        raise NtForbindelseFejl("nt_instrument: tomt symbol")
    if kontrakt is None:
        raise NtForbindelseFejl(
            f"nt_instrument({sym}): ingen kvalificeret IBKR-kontrakt. "
            f"Maaneden GAETTES ikke — uden kontrakt sendes der ingen ordre.")

    # Foerste valg: udloebsdatoen, som er entydig.
    raa = str(getattr(kontrakt, "lastTradeDateOrContractMonth", "") or "")
    if len(raa) >= 6 and raa[:6].isdigit():
        aar, maaned = int(raa[:4]), int(raa[4:6])
        return f"{sym} {maaned:02d}-{aar % 100:02d}"

    # Fald tilbage paa localSymbol: MESZ6 -> Z = december, 6 = 2026.
    lokal = str(getattr(kontrakt, "localSymbol", "") or "").upper()
    m = re.fullmatch(r"([A-Z0-9]{2,4})([FGHJKMNQUVXZ])(\d)", lokal)
    if m:
        maaned = _MAANEDSKODE[m.group(2)]
        # ⚠ ÉT ciffer for aaret. IBKR skriver 6 for 2026; vi antager det
        # indevaerende aarti. Det holder til 2029 og skal saa genbesoeges —
        # derfor staar det her frem for at vaere en tavs antagelse.
        import datetime as _dt
        aarti = (_dt.date.today().year // 10) * 10
        aar = aarti + int(m.group(3))
        return f"{sym} {maaned:02d}-{aar % 100:02d}"

    raise NtForbindelseFejl(
        f"nt_instrument({sym}): kunne hverken laese udloeb "
        f"({raa!r}) eller localSymbol ({lokal!r}) fra kontrakten")


# ═══════════════════════════════════════════════════════════════════════════
# Konfiguration
# ═══════════════════════════════════════════════════════════════════════════
def konfigureret() -> bool:
    """Har denne maskine overhovedet en NT8-ordrevej?"""
    return accounts.nt_forbindelse() is not None


def verificer_profil(profil: dict) -> None:
    """V2 på konfigurationen — FØR der røres noget.

    ⚠ Rækkefølgen er ikke ligegyldig. Opdages en live-konto først efter at en
    OIF-fil er skrevet, er ordren allerede på vej. Det billige tjek tages først.
    """
    konto = (profil.get("konto") or "").strip()
    if not konto:
        raise NtForbindelseFejl(
            "nt_forbindelse.konto mangler — uden konto ville ordren lande paa "
            "NT8's Default account, altsaa dér hvor platformens dropdown "
            "tilfaeldigvis staar")
    if konto.upper() not in SIM_KONTI and not profil.get("tillad_live"):
        raise NtForbindelseFejl(
            f"{konto} staar ikke paa listen over kendte simulationskonti "
            f"{sorted(SIM_KONTI)}, og tillad_live er ikke sat. Ingen ordrer sendes.")


# ═══════════════════════════════════════════════════════════════════════════
# ATI-strømmen — læsevejen
# ═══════════════════════════════════════════════════════════════════════════
def _laes_raat(sekunder: float = LYT_SEK) -> str:
    """Alt ATI pusher i vinduet. ⚠ SENDER INGEN BYTES.

    OIF-kommandoer er ren tekst; enhver stump data på denne socket kunne i
    værste fald fortolkes. Vi åbner, lytter og lukker.
    """
    try:
        s = socket.create_connection((ATI_HOST, ATI_PORT), timeout=5)
    except OSError as e:
        raise NtTilstandUkendt(
            f"kunne ikke naa ATI paa {ATI_HOST}:{ATI_PORT} ({e}) — koerer "
            f"NinjaTrader, og er Automated Trading Interface slaaet til?") from e
    s.settimeout(sekunder)
    buf = b""
    slut = time.time() + sekunder
    try:
        while time.time() < slut:
            try:
                d = s.recv(8192)
                if not d:
                    break
                buf += d
            except socket.timeout:
                break
    finally:
        s.close()
    return buf.replace(b"\x00", b" ").decode("utf-8", "replace")


# Hvor kort vi noejes med naar svaret allerede er der. Maalt 28-09: 0,5 s gav
# samme to konti og samme "ATI True" som 6 s.
LYT_HURTIG = 0.6


def tilstand(sekunder: float = LYT_SEK) -> dict:
    """Hvilke konti er NT8 forbundet til lige nu, og er ATI slået til?

    ⚠ ESKALERER VED TVIVL — DEN FORKORTER IKKE VED HÅB.
    `klar()` kalder denne før hver eneste ordre, og med et fast seks sekunders
    oplæg kostede hver ordre seks sekunder hvor brugerfladen intet sagde. Målt
    28-09 gav 0,6 s nøjagtig samme svar; de seks var ren venten.

    Men en kort aflæsning må ALDRIG kunne blive til "ingen konti" — det ville
    være en kontrol hvis fejl ser ud som et fund, og V3 ville spærre en gyldig
    ordre med en forkert begrundelse. Derfor: læs kort, og er svaret tomt eller
    uden navngivne konti, så læs det fulde vindue før der konkluderes noget.
    Den hurtige vej er kun en genvej NÅR svaret allerede er der.
    """
    raa = _laes_raat(min(LYT_HURTIG, sekunder))
    if not raa.strip() or not re.search(r"CashValue\|\S+\s", raa):
        # Tvivl — brug det fulde vindue, som før.
        raa = _laes_raat(sekunder)
    if not raa.strip():
        # ⚠ Tom stroem er ikke "ingen konti". Se NtTilstandUkendt.
        raise NtTilstandUkendt(
            "ATI svarede tomt — NT8 kan vaere under opstart, eller "
            "Automated Trading Interface er ikke slaaet til")
    konti = {m for m in re.findall(r"CashValue\|(\S*)\s", raa)}
    navngivne = sorted(k for k in konti if k)
    return {
        "raa": raa,
        "konti": navngivne,
        "uden_navn": "" in konti,
        "ati_aktiv": "ATI True" in raa,
    }


def ordre_status(ordre_id: str, raa: str | None = None) -> str:
    """Ordrens tilstand som ATI melder den. "" = ikke set i dette oplæg.

    ⚠ "" BETYDER IKKE "VÆK". Strømmen er et øjebliksbillede; en ordre der
    beviseligt fandtes stod ikke i snapshottet lige før den blev annulleret
    (målt 26-09). Kalderen skal behandle "" som UKENDT, aldrig som terminal.
    """
    t = raa if raa is not None else _laes_raat()
    m = re.search(rf"OrderStatus\|{re.escape(ordre_id)}\s+(\S+)", t)
    return m.group(1) if m else ""


TERMINALE = {"Cancelled", "Rejected", "Filled"}

# ⚠ EN LEVENDE ORDRE ER IKKE EN FAERDIG ORDRE. En exit-ordre skal ligge og
# vente; naar den naar TERMINALE, er den enten udfoert eller doed.
LEVENDE = {"Working", "Accepted"}

# Hvad OIF kan. Trailing er IKKE med — den findes kun i NT8's ATM-strategier.
ORDRETYPER = {"MARKET", "LIMIT", "STOPMARKET", "STOPLIMIT"}


# ═══════════════════════════════════════════════════════════════════════════
# Positioner — ATI pusher dem, og det gjorde vi laenge ikke brug af
# ═══════════════════════════════════════════════════════════════════════════
# ⚠ ANTAGELSEN VAR FORKERT. Hele NT8-stien blev bygget paa at "ATI kan ikke
# spoerges om positioner", og derfor kunne salgsvagten kun laese journalen.
# Maalt 28-09 pusher stroemmen bl.a.:
#
#     MarketPosition|MES DEC26|Sim101 -4
#     AvgEntryPrice|MES DEC26|Sim101 7773.125
#     RealizedPnL|Sim101 -2.5
#
# ⚠ OG HER LIGGER FAELDEN. Vi sender "MES 12-26" i OIF-kommandoen, men
# stroemmens noegle er "MES DEC26" — ikke det samme. Samtidig ligger der
# FORAELDEDE ekkoer under andre stavemaader (@MES, MESZ26, "MES Z6"), som
# stod til 1 mens den rigtige stod til -4. Slaar man den forkerte op, faar man
# et forkert svar der ser fuldstaendig rigtigt ud.
#
# Derfor: ÉN noegle, udledt deterministisk af OIF-navnet, og intet gaetteri paa
# alternativer. Findes den ikke, er svaret UKENDT — ikke "flad".

_MAANED = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN",
           "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")


def ati_noegle(instrument: str) -> str:
    """OIF-navnet -> ATI-stroemmens navn. "MES 12-26" -> "MES DEC26"."""
    m = re.fullmatch(r"(\S+)\s+(\d{2})-(\d{2})", instrument.strip())
    if not m:
        raise NtForbindelseFejl(
            f"kan ikke oversaette {instrument!r} til ATI-navn — forventede "
            f"formen 'MES 12-26'")
    sym, mm, yy = m.group(1), int(m.group(2)), m.group(3)
    if not 1 <= mm <= 12:
        raise NtForbindelseFejl(f"ugyldig maaned i {instrument!r}")
    return f"{sym} {_MAANED[mm - 1]}{yy}"


def position(instrument: str, konto: str, raa: str | None = None) -> dict:
    """Nettoposition hos NT8. `netto=None` betyder UKENDT — aldrig "flad".

    ⚠ FRAVAER AF NOEGLEN ER IKKE NUL. NT8 pusher foerst en MarketPosition-linje
    for et instrument den har set; et instrument der aldrig er handlet paa
    kontoen, staar der slet ikke. Det ligner "flad" og er det maaske ogsaa —
    men vi ved det ikke, og en vagt der behandler tavshed som et svar, er
    praecis den fejlklasse resten af denne fil er bygget imod.

    Er noeglen DER, er tallet til gengaeld autoritativt: efter en flatten stod
    den paa 0, ikke vaek (maalt 28-09).
    """
    noegle = ati_noegle(instrument)
    t = raa if raa is not None else _laes_raat(LYT_HURTIG)
    if not t.strip():
        # Samme regel som `tilstand`: tavshed eskalerer, den konkluderer ikke.
        t = _laes_raat(LYT_SEK)
    if not t.strip():
        raise NtTilstandUkendt(
            "ATI svarede tomt — positionen kan hverken bekraeftes eller "
            "afkraeftes")

    k = re.escape(noegle)
    a = re.escape(konto)
    m_p = re.search(rf"MarketPosition\|{k}\|{a}\s+(-?\d+)", t)
    m_s = re.search(rf"AvgEntryPrice\|{k}\|{a}\s+([\d.]+)", t)
    m_r = re.search(rf"RealizedPnL\|{a}\s+(-?[\d.]+)", t)

    def _f(m, som):
        if not m:
            return None
        try:
            return som(m.group(1))
        except (TypeError, ValueError):
            return None

    return {
        "noegle":    noegle,
        "konto":     konto,
        "netto":     _f(m_p, int),          # None = UKENDT
        "snit":      _f(m_s, float),
        "realiseret": _f(m_r, float),
        "set":       m_p is not None,
    }


def fyldning(ordre_id: str, raa: str | None = None) -> tuple[int, float]:
    """Hvor meget er fyldt, og til hvilken snitpris. (0, 0.0) = intet set.

    ATI pusher det i to felter ved siden af status:

        OrderStatus|TDPROBE1789538169 Rejected
        Filled|TDPROBE1789538169 0
        AvgFillPrice|TDPROBE1789538169 0

    ⚠ DE FELTER HAR LIGGET DER HELE TIDEN og blev ikke laest. Uden dem er
    der ingen fyldpris, og uden fyldpris ingen P&L, ingen entry/exit-parring og
    intet chart — altsaa ingen forensik vaerd at kalde forensik. Det var den
    eneste rigtige hindring for at NT8-handler kunne bogfoeres som IBKR-handler.

    ⚠ (0, 0.0) BETYDER IKKE "IKKE FYLDT". Samme oejebliksbillede-regel som
    `ordre_status`: felterne kan mangle i dette oplaeg og findes i det naeste.
    """
    t = raa if raa is not None else _laes_raat()
    m_a = re.search(rf"Filled\|{re.escape(ordre_id)}\s+(\S+)", t)
    m_p = re.search(rf"AvgFillPrice\|{re.escape(ordre_id)}\s+(\S+)", t)

    def _tal(m, som):
        if not m:
            return som(0)
        try:
            return som(float(m.group(1)))
        except (TypeError, ValueError):
            return som(0)

    return _tal(m_a, int), _tal(m_p, float)


def afvent_ordre(ordre_id: str, sekunder: float = 15.0,
                 oplaeg_sek: float = 0.8) -> dict:
    """Lyt til ATI indtil ordren er terminal, eller tiden gaar.

    Returnerer `{"status", "filled", "avg_fill", "terminal", "oplaeg"}` —
    IBKR-stiens `place_paper_order`-form, saa alt nedenstroems kan vaere faelles
    for de to brokere i stedet for en kopi pr. broker.

    ⚠ DEN PAASTAAR IKKE NOGET DEN IKKE VED. Loeber tiden ud uden terminal
    status, er svaret `terminal=False` og `status=""` — ikke "ikke fyldt".
    Det er praecis den fejl `ordre_uafklaret` blev doebt om for: tre IBKR-ordrer
    blev afskrevet som ufyldte paa ét kig, og fyldte alle tre.
    """
    slut = time.time() + max(sekunder, oplaeg_sek)
    status, antal, pris, oplaeg = "", 0, 0.0, 0
    while True:
        raa = _laes_raat(oplaeg_sek)
        oplaeg += 1
        s = ordre_status(ordre_id, raa)
        a, p = fyldning(ordre_id, raa)
        # ⚠ ET SENERE OPLAEG MAA IKKE SLETTE ET TIDLIGERE FUND. Snapshottet kan
        # tabe ordren igen (maalt 26-09), og saa ville et frisk, tomt kig
        # overskrive en fyldpris vi allerede havde.
        status = s or status
        if a:
            antal = a
        if p:
            pris = p
        if status in TERMINALE:
            break
        if time.time() >= slut:
            break
    return {"status": status, "filled": antal, "avg_fill": pris,
            "terminal": status in TERMINALE, "oplaeg": oplaeg}


# ═══════════════════════════════════════════════════════════════════════════
# Vagterne samlet
# ═══════════════════════════════════════════════════════════════════════════
def klar() -> dict:
    """Kør V1-V4 og returnér den verificerede profil. Kaster hvis noget spærrer.

    Kaldes FØR hver ordre. Det er billigt (én socket-aflæsning) og det er den
    eneste måde at opdage at NT8 er skiftet konto siden sidst.
    """
    profil = accounts.nt_forbindelse()
    if profil is None:
        raise NtForbindelseFejl(
            "ingen nt_forbindelse i account.yaml — denne maskine har ingen "
            "NinjaTrader-ordrevej")

    verificer_profil(profil)                      # V2, før noget røres

    st = tilstand()                               # kaster NtTilstandUkendt
    if not st["ati_aktiv"]:
        raise NtForbindelseFejl(
            "ATI melder ikke 'ATI True' — Automated Trading Interface er ikke "
            "aktiv i NT8 (Tools -> Options -> Automated trading interface)")

    konto = profil["konto"]
    if konto not in st["konti"]:
        # V3 — konfigurationen skal stemme med virkeligheden.
        raise NtForbindelseFejl(
            f"⚠ KONTOEN FINDES IKKE. NT8 er forbundet til {st['konti'] or '(ingen)'}, "
            f"ikke {konto}. Ingen ordrer sendes. Er den rigtige forbindelse valgt "
            f"i platformen?")

    # ── V4: er der dukket en konto op vi ikke kender? ─────────────────────
    #
    # ⚠ DEN ADVARER, DEN SPAERRER IKKE — og det er en aendring fra 28-09.
    #
    # Foer kastede den, saa ÉN ukendt konto i stroemmen standsede AL
    # NT8-handel, ogsaa paa Sim101. Det lyder forsigtigt og er det ikke:
    # live-kontoen 2080414 dukker op i stroemmen i samme oejeblik den
    # finansieres — og markedsdata KRAEVER at den finansieres. Vagten ville
    # altsaa have spaerret Ibens paper-handel, midt i en session, med en fejl
    # hun ikke kunne goere noget ved, som foelge af en handling der var
    # noedvendig for at komme videre.
    #
    # Og beskyttelsen var overfloedig hele tiden: V1 skriver kontoen EKSPLICIT
    # i hver eneste kommando. En live-konto der blot FINDES i platformen, kan
    # ikke modtage en ordre stilet til Sim101. Der var intet at spaerre imod.
    #
    # ⚠ MEN DEN SKAL STADIG SES. En vagt der bliver til en stille kommentar,
    # er en vagt der er fjernet. Advarslen foelger med profilen, og kalderen
    # journaliserer den og sender den til brugerfladen — se main.py.
    advarsler: list[str] = []
    ukendte = [k for k in st["konti"]
               if k.upper() not in SIM_KONTI and k != konto]
    if ukendte and not profil.get("tillad_live"):
        advarsler.append(
            f"⚠ UKENDT KONTO I NT8: {', '.join(ukendte)}. Den er hverken en "
            f"kendt simulationskonto eller den konfigurerede ({konto}). Dine "
            f"ordrer gaar fortsat til {konto} — kontoen skrives eksplicit i "
            f"hver kommando — men NT8 er forbundet til mere end foer.")

    if not INCOMING.is_dir():
        raise NtForbindelseFejl(f"{INCOMING} findes ikke — koerer NT8?")

    return {**profil, "konti_set": st["konti"], "ati_aktiv": True,
            "advarsler": advarsler}


# ═══════════════════════════════════════════════════════════════════════════
# Skrivevejen — OIF-filer
# ═══════════════════════════════════════════════════════════════════════════
def _skriv_oif(kommando: str, maerke: str, vent_sek: int = 15) -> dict:
    """Skriv én OIF-kommando og returnér hvad NT8 skrev om den.

    ⚠ FILNAVNET SKAL BEGYNDE MED `oif`. Det var fejlen der kostede en hel
    session 11-08: filen blev set men aldrig læst, fordi den hed noget andet.
    """
    log = _nyeste_log()
    foer = _loglaengde(log)
    INCOMING.mkdir(parents=True, exist_ok=True)
    fil = INCOMING / f"oif_td_{maerke}_{int(time.time() * 1000)}.txt"
    fil.write_text(kommando + "\n", encoding="ascii")

    # ⚠ Fin-kornet polling. Var 1 s, og NT8 spiser filen paa ~100 ms — saa
    # hvert eneste klik betalte op mod et helt sekund for ingenting.
    spist = False
    for _ in range(int(vent_sek / 0.1)):
        time.sleep(0.1)
        if not fil.exists():
            spist = True
            break

    # ⚠ AT VI IKKE FIK RYDDET OP, ER ET FUND — IKKE EN DETALJE.
    # Her stod `except OSError: pass`. Maalt 27-09 kl. 21:10 holdt NT8 filen
    # aaben ("Device or resource busy"), saa oprydningen fejlede TAVST og en
    # PLACE-kommando blev liggende i mappen NT8 laeser ordrer fra. Bliver den
    # laest senere — ved en genstart, eller naar sessionen aabner — dukker der
    # en ordre op ingen har bedt om, paa et tidspunkt ingen kigger.
    # Kalderen skal kunne se det og sige det videre.
    fil_tilbage = ""
    if not spist:
        try:
            fil.unlink()
        except OSError as e:
            fil_tilbage = f"{fil.name}: {e}"

    time.sleep(0.35)                 # loggen skrives et øjeblik efter
    linjer = [l for l in _nye_logliner(log, foer) if "OIF" in l or "Order=" in l]
    # NT8 logger "processing" naar den LAESER filen. At den skrev en
    # Order=-linje er derimod beviset paa at der blev oprettet noget.
    return {"kommando": kommando, "spist": spist, "logliner": linjer,
            "set_af_nt8": any("processing" in l for l in linjer),
            "ordre_i_log": any("Order=" in l for l in linjer),
            "fil_tilbage": fil_tilbage,
            "ukendt_instrument": any("unknown instrument" in l for l in linjer)}


def send_ordre(*, konto: str, instrument: str, action: str, antal: int,
               ordretype: str = "MARKET", limit: float | None = None,
               stop: float | None = None, tif: str = "DAY",
               ordre_id: str = "", oco: str = "") -> dict:
    """Læg en ordre. V1: kontoen skrives EKSPLICIT.

    ⚠ Kalderen skal have kørt `klar()` først. Denne funktion verificerer ikke
    noget selv — den ville ellers gøre det to gange og dermed friste nogen til
    at springe den ene over.
    """
    if not konto:
        raise NtForbindelseFejl("V1: kontoen er tom — ordren ville gaa til "
                                "NT8's Default account")
    if antal <= 0:
        raise NtForbindelseFejl(f"antal skal vaere positivt, fik {antal}")
    if action.upper() not in ("BUY", "SELL"):
        raise NtForbindelseFejl(f"ukendt action {action!r}")
    if tif.upper() == "GTC":
        # ⚠ Samme regel som ordretesten: en ordre vi ikke faar annulleret,
        # skal doe af sig selv.
        raise NtForbindelseFejl("TIF=GTC er ikke tilladt fra denne vej")

    if ordretype.upper() not in ORDRETYPER:
        # ⚠ OIF kender kun fire. "TRAILINGSTOP" findes i NT8's brugerflade og
        # i ATM-strategier, men IKKE over filgraensefladen — en ordre med den
        # type ville blive laest og lydloest intet goere.
        raise NtForbindelseFejl(
            f"ukendt ordretype {ordretype!r}. OIF kender kun "
            f"{', '.join(sorted(ORDRETYPER))} — trailing findes ikke som type "
            f"og skal laves ved at FLYTTE en STOPMARKET (se aendr).")

    l = "" if limit is None else f"{limit}"
    s = "" if stop is None else f"{stop}"
    # ⚠ FELT 10 ER OCO, og det har staaet tomt siden vejen blev bygget.
    # Formatet har altid haft pladsen; vi har bare aldrig haft to ordrer der
    # skulle annullere hinanden. Se probe_nt_exit_ordrer.py — at NT8 ACCEPTERER
    # feltet er ikke bevist endnu.
    kmd = (f"PLACE;{konto};{instrument};{action.upper()};{antal};"
           f"{ordretype.upper()};{l};{s};{tif.upper()};{oco};{ordre_id};;")
    return _skriv_oif(kmd, "place")


def aendr(ordre_id: str, *, antal: int | None = None,
          limit: float | None = None, stop: float | None = None) -> dict:
    """Flyt en levende ordres pris eller antal.

    ⚠ ALDRIG AFPROEVET MOD NT8. Kun PLACE og CANCEL er verificeret (16-09 og
    26-09). CHANGE er skrevet efter OIF-formatets feltraekkefoelge og ser
    rigtig ud — men "ser rigtig ud" er praecis dét denne kodebase har brugt to
    dage paa at laere ikke at stole paa. Den foerste rigtige brug er
    probe_nt_exit_ordrer.py, scenarie P2.

    Hele trailing-stoppet hviler paa at den virker: OIF har ingen
    trailing-ordretype, saa en TRAIL er en STOPMARKET som backenden flytter.
    Virker CHANGE ikke, skal TRAIL i stedet annulleres og genlaegges ved hvert
    ryk — med et hul uden beskyttelse hver gang.

    Felterne er de samme tretten som PLACE; kun 5 (antal), 7 (limit),
    8 (stop) og 11 (ordre-id) udfyldes.
    """
    if not ordre_id:
        raise NtForbindelseFejl("aendr() kraever et ordre-id")
    if antal is None and limit is None and stop is None:
        # ⚠ En CHANGE uden aendringer er ikke harmloes: den ville blive sendt,
        # og et tomt svar fra NT8 kunne laeses som "det gik godt".
        raise NtForbindelseFejl(
            "aendr() uden antal, limit eller stop aendrer intet — "
            "ingen kommando sendt")
    if antal is not None and antal <= 0:
        raise NtForbindelseFejl(f"antal skal vaere positivt, fik {antal}")

    a = "" if antal is None else f"{antal}"
    l = "" if limit is None else f"{limit}"
    s = "" if stop is None else f"{stop}"
    return _skriv_oif(f"CHANGE;;;;{a};;{l};{s};;;{ordre_id};;", "change")


def afvent_aktiv(ordre_id: str, sekunder: float = 10.0,
                 oplaeg_sek: float = 0.8) -> dict:
    """Lyt indtil ordren er LEVENDE (Working/Accepted) — eller terminal.

    ⚠ `afvent_ordre` venter paa at en ordre bliver FAERDIG. En exit-ordre skal
    ikke blive faerdig; den skal ligge og vente paa markedet. Brugt den ene i
    stedet for den anden ville hver eneste stop loss se ud som en fejl.

    Samme oejebliksbillede-regel som alt andet her: status `""` er UKENDT og
    bliver aldrig til et svar. Loeber tiden ud, er svaret `aktiv=False` OG
    `terminal=False` — altsaa "vi ved det ikke", ikke "den findes ikke".
    """
    slut = time.time() + max(sekunder, oplaeg_sek)
    status, antal, pris, oplaeg = "", 0, 0.0, 0
    while True:
        raa = _laes_raat(oplaeg_sek)
        oplaeg += 1
        s_ = ordre_status(ordre_id, raa)
        a_, p_ = fyldning(ordre_id, raa)
        status = s_ or status
        if a_:
            antal = a_
        if p_:
            pris = p_
        if status in LEVENDE or status in TERMINALE:
            break
        if time.time() >= slut:
            break
    return {"status": status, "aktiv": status in LEVENDE,
            "terminal": status in TERMINALE, "filled": antal,
            "avg_fill": pris, "oplaeg": oplaeg}


def annuller(ordre_id: str) -> dict:
    """Annullér PRÆCIS én ordre. Verificeret 26-09 på en rigtig efterladt ordre."""
    if not ordre_id:
        raise NtForbindelseFejl("annuller() kraever et ordre-id")
    return _skriv_oif(f"CANCEL;;;;;;;;;;{ordre_id};;", "cancel")


def annuller_alt(konto: str) -> dict:
    """Sidste udvej: ryd HELE kontoen for ordrer.

    ⚠ Kun ordrer — aldrig positioner. `FLATTENEVERYTHING` og `CLOSEPOSITION`
    bruges IKKE herfra; de hører til et menneske, ikke til en oprydning.
    """
    if not konto:
        raise NtForbindelseFejl("annuller_alt() kraever en konto")
    return _skriv_oif(f"CANCELALLORDERS;{konto};;;;;;;;;;;", "ryd")


# ── NT8's log: kvitteringen ────────────────────────────────────────────────
def _nyeste_log() -> Optional[pathlib.Path]:
    try:
        filer = list(LOGMAPPE.glob("log.*.txt"))
    except OSError:
        return None
    return max(filer, key=lambda p: p.stat().st_mtime) if filer else None


def _loglaengde(log: Optional[pathlib.Path]) -> int:
    if log is None:
        return 0
    try:
        return len(log.read_text(encoding="utf-8", errors="replace").splitlines())
    except OSError:
        return 0


def _nye_logliner(log: Optional[pathlib.Path], fra: int) -> list[str]:
    if log is None:
        return []
    try:
        return [l.strip() for l in
                log.read_text(encoding="utf-8", errors="replace").splitlines()[fra:]
                if l.strip()]
    except OSError:
        return []


def order_ref(hvem: str = "") -> str:
    """Ordre-id der markerer MANUEL oprindelse gennem NT8.

    ⚠ Id'et BINDER — verificeret 26-09. ATI pusher det som
    `OrderStatus|<id> <tilstand>`, selv om NT8's log skriver `Name=''`.
    Det er dét der gør en NT8-handel tilskrivbar i journalen.
    """
    # ⚠ Id'et skal vaere ENTYDIGT og kort. NT8 accepterer ikke vilkaarligt lange
    # id'er, og millisekunder er rigeligt til at skille to klik ad.
    # "NTM" = NinjaTrader Manuel — saa kilden kan ses paa id'et alene.
    return f"NTM{int(time.time() * 1000)}"
