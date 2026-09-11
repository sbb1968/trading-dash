"""
ordre_forbindelse.py — den SKRIVENDE forbindelse, adskilt fra den læsende
════════════════════════════════════════════════════════════════════════════════
To forbindelser med hvert sit formål:

    LÆS    algoserverens Gateway, bruger `fasteriben`  — markedsdata, kurser
    SKRIV  lokal Gateway på workstationen, `fasteriben2` — ordrer på DUQ441063

De slås ikke om noget, fordi **kun den ene beder om data**. Konflikten opstod
fordi TWS automatisk abonnerer på alt i watchlisten ved opstart; en Gateway gør
ingenting af sig selv — den beder først om data når en klient gør det. Sender
Trading Dash kun ordrer gennem den lokale forbindelse, udløses konflikten aldrig.

────────────────────────────────────────────────────────────────────────────────
⚠ TRE VAGTER, OG DE ER BYGGET NU FREM FOR NÅR DET FLYTTES

Opsætningen skal senere til Ibens maskine, hvor den før eller siden vil stå ved
siden af en rigtig konto. En vagt der skrives "når vi får brug for den", findes
ikke den dag man får brug for den.

  V1  KONTOBEKRÆFTELSE. Efter connect læses den faktiske konto. Er den ikke den
      konfigurerede: hård fejl, ingen ordrer. Ikke en advarsel — det er netop den
      fejl der ville gøre mest skade når opsætningen flyttes, og en advarsel i en
      log er ikke en spærring.

  V2  PAPER-BEKRÆFTELSE. Kontonummeret skal begynde med D. Gør det ikke, og er
      live ikke eksplicit tilladt: hård fejl. Samme krydstjek som journalens
      paper/live-mærke (c47465b) — IBKR's paper-konti begynder med D, live med U.

  V3  INGEN MARKEDSDATA. Forbindelsen må aldrig bede om kurser. Det er ikke en
      begrænsning vi lever med — det er hele grunden til at den kan eksistere.

Alle tre kan udløses på kommando, og `test_ordre_forbindelse.py` viser det.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

import accounts
import ibkr_client_ids
from ibkr_connect import IBKRConnection

logger = logging.getLogger(__name__)

# Egen client-id, uden for scripts' interval og adskilt fra den delte forbindelse.
# Begge kører på SAMME maskine, så en kollision ville være reel.
CLIENT_ID = ibkr_client_ids.ORDRE


class OrdreForbindelseFejl(Exception):
    """Rejses når en vagt spærrer. Aldrig fanget og logget videre — en spærret
    ordreforbindelse skal stoppe kaldet, ikke farve det."""


_forbindelse: Optional[IBKRConnection] = None

# ── ⚠ ÉN AD GANGEN. Kapløbet der laaste Gateway'en ude ─────────────────────
# Tre kaldere deler denne funktion: /orders/list (Ordre-vinduet),
# /account/dash-snapshot (watchlisten, hvert 5. sekund) og selve ordrevejen.
# De kan vaere inde samtidig, og foer laasen var udfaldet dette:
#
#   1. A kommer ind, bygger en IBKRConnection og venter paa connect().
#   2. Under den `await` kommer B ind, ser at objektet ikke er `connected` endnu,
#      og bygger sit EGET — med samme clientId (201).
#   3. En af dem faar 326 "client id is already in use", og dens oprydning
#      nulstillede modulets `_forbindelse` — altsaa ogsaa VINDEREN, som stadig
#      var forbundet og stadig holdt 201. Den blev forældreløs: ingen kunne naa
#      den, og ingen lukkede den.
#   4. Derefter fik hvert eneste forsoeg 326, fordi vi selv sad paa id'et.
#
# Maalt 10-09-2026 i backend_2026-09-10_15-52.log: kl. 15:52:25 lykkedes en
# forbindelse ("Konto: ['DUQ441063']") og blev kasseret i samme sekund. Kl.
# 15:52:47 lykkedes endnu en — den holdt syv sekunder. De fire backend-sessioner
# den dag overlappede ikke, saa kollisionen kom IKKE fra to processer. Den kom
# herfra.
#
# Afkoelingen nedenfor daempede frekvensen, men lukkede ikke kapløbet: ordrevejen
# kalder `hent(tving=True)` og gaar med vilje uden om afkoelingen. Et menneske
# der trykker Saelg kunne altsaa stadig kollidere med en poller.
#
# ⚠ Laasen kan faa en ordre til at vente. Det er det rigtige bytte: connectAsync
# har timeout=15, saa ventetiden er begraenset — og alternativet er ikke en
# hurtigere ordre, men et nyt 326 der spaerrer forbindelsen for alle.
_laas = asyncio.Lock()

# ── ⚠ AFKOELING EFTER EN FEJLET FORBINDELSE ────────────────────────────────
# Maalt 10-09-2026 paa Ibens workstation: watchlisten henter
# /account/dash-snapshot hvert 5. sekund. Paa en maskine med ordre_forbindelse
# gaar det kald GENNEM denne funktion, saa der blev forsoegt ~170 forbindelser
# paa 14 minutter — alle med samme clientId (201).
#
# Afkoelingen goer at en poller hoejst udloeser ét forsoeg pr. AFKOELING_SEK.
# Et menneske der trykker Saelg skal derimod IKKE vente paa den: ordrestien
# kalder hent(tving=True). Den springer afkoelingen over — men IKKE laasen.
AFKOELING_SEK = 20.0
_sidste_fejl_tid: float = 0.0
_sidste_fejl: str = ""


def _spaer(besked: str) -> OrdreForbindelseFejl:
    """Notér fejlen, saa afkoelingen daekker den, og returnér den til `raise`.

    ⚠ OGSAA NAAR EN VAGT SPAERRER. Foer gjaldt afkoelingen kun connect-fejl, saa
    en Gateway paa den FORKERTE konto blev forbundet og lukket igen hvert 5.
    sekund af watchlistens polling — en ny session hvert femte sekund, med samme
    clientId, mod noget vi allerede vidste vi ikke ville bruge. Et menneske
    rammes ikke: ordrestien bruger tving=True og faar fejlen med det samme.
    """
    global _sidste_fejl_tid, _sidste_fejl
    _sidste_fejl     = besked
    _sidste_fejl_tid = time.monotonic()
    return OrdreForbindelseFejl(besked)


def konfigureret() -> bool:
    """Er der overhovedet en separat ordreforbindelse på denne maskine?"""
    return accounts.ordre_forbindelse() is not None


def verificer_profil(profil: dict) -> None:
    """V2 på konfigurationen — FØR der forbindes.

    ⚠ Rækkefølgen er ikke ligegyldig. Opdages en live-konto først EFTER connect,
    har vi allerede en session åben mod noget vi ikke ville røre. Det billige tjek
    tages først.
    """
    konto = (profil.get("konto") or "").upper()
    if not konto:
        raise OrdreForbindelseFejl("ordre_forbindelse.konto mangler")
    if not konto.startswith("D") and not profil.get("tillad_live"):
        raise OrdreForbindelseFejl(
            f"{konto} ligner en LIVE-konto (ikke D-præfiks), og tillad_live er "
            f"ikke sat. Ordreforbindelsen oprettes IKKE.")


async def hent(genforbind: bool = True, tving: bool = False) -> IBKRConnection:
    """Den skrivende forbindelse, klar til brug. Kaster hvis en vagt spærrer.

    `tving=True` går uden om afkølingen. Brug det når et MENNESKE venter på
    svaret (en ordre), aldrig fra noget der poller.
    """
    global _forbindelse, _sidste_fejl_tid, _sidste_fejl

    profil = accounts.ordre_forbindelse()
    if profil is None:
        raise OrdreForbindelseFejl(
            "ingen ordre_forbindelse i account.yaml — ordrer går gennem den "
            "delte forbindelse")

    verificer_profil(profil)                                    # V2, før connect

    # Den hurtige vej, uden laas. `_forbindelse` tildeles kun ét sted — inde i
    # laasen, og foerst naar forbindelsen er oprettet OG alle vagter passeret —
    # saa det her er enten en brugbar forbindelse eller None. Aldrig en halvfaerdig.
    if _forbindelse is not None and _forbindelse.connected:
        _sidste_fejl_tid, _sidste_fejl = 0.0, ""
        return _forbindelse
    if not genforbind:
        raise OrdreForbindelseFejl("ordreforbindelsen er nede")

    async with _laas:                       # ⚠ se noten ved _laas
        # Spoerg igen. Ventede vi paa laasen, kan den der havde den vaere
        # lykkedes i mellemtiden — og saa skal vi IKKE bygge én mere med samme
        # clientId. Det var praecis den ekstra forbindelse der gav 326.
        if _forbindelse is not None and _forbindelse.connected:
            _sidste_fejl_tid, _sidste_fejl = 0.0, ""
            return _forbindelse

        # ⚠ Afkoeling — se noten ved AFKOELING_SEK. Tjekkes INDE i laasen, saa
        # en poller der ventede paa et forsoeg der netop fejlede, arver
        # afkoelingen frem for straks at starte sit eget.
        if not tving and _sidste_fejl:
            gaaet = time.monotonic() - _sidste_fejl_tid
            if gaaet < AFKOELING_SEK:
                raise OrdreForbindelseFejl(
                    f"{_sidste_fejl} (forsoegt for {gaaet:.0f} s siden; "
                    f"proever igen om {AFKOELING_SEK - gaaet:.0f} s)")

        # Et doedt objekt fra sidste gang holder stadig clientId 201. Luk det
        # her, hvor vi ved at ingen andre bruger det.
        if _forbindelse is not None:
            try:
                _forbindelse.disconnect()
            except Exception:
                pass
            _forbindelse = None

        # ⚠ BYGGES LOKALT. Modulets `_forbindelse` roeres ikke foer alt er i
        # orden — ellers ville en samtidig kalder se et objekt der endnu ikke er
        # forbundet, og en fejlet oprydning kunne kassere en anden kalders
        # fungerende forbindelse.
        ny = IBKRConnection(
            paper_trading=not profil.get("tillad_live"),
            account=profil["konto"],
            host=profil["host"],
            port=profil["port"],
            client_id=CLIENT_ID,
            kraev_konto=True,     # §3.2: en glemt konto skal fejle, ikke gaettes
        )
        ok = await ny.connect()
        if not ok or not ny.connected:
            # ⚠ Luk det mislykkede objekt frem for bare at slippe det. Ellers bliver
            # klienten liggende med clientId 201 og spaerrer for naeste forsoeg.
            try:
                ny.disconnect()
            except Exception:
                pass
            _sidste_fejl = (f"kunne ikke forbinde til Gateway på "
                            f"{profil['host']}:{profil['port']} — kører den, og er "
                            f"API'et slået til?")
            _sidste_fejl_tid = time.monotonic()
            raise OrdreForbindelseFejl(_sidste_fejl)

        # ── V1: kontobekræftelse ────────────────────────────────────────────
        styrede = [a.strip().upper() for a in (ny.ib.managedAccounts() or [])]
        if profil["konto"] not in styrede:
            ny.disconnect()                # synkron — ikke await
            raise _spaer(
                f"⚠ FORKERT KONTO. Gatewayen på port {profil['port']} styrer "
                f"{styrede or '(ingen)'}, ikke {profil['konto']}. Ingen ordrer sendes. "
                f"Er Gatewayen logget ind som {profil.get('bruger') or 'den rigtige bruger'}?")

        # ── V2 igen, nu mod det IBKR faktisk melder ─────────────────────────
        # Konfigurationen kan sige ét og virkeligheden noget andet; her er det
        # virkeligheden der tjekkes.
        for k in styrede:
            if not k.startswith("D") and not profil.get("tillad_live"):
                ny.disconnect()            # synkron — ikke await
                raise _spaer(
                    f"⚠ Gatewayen styrer en LIVE-konto ({k}) og tillad_live er ikke "
                    f"sat. Forbindelsen lukkes.")

        logger.info(f"[Ordre] forbundet {profil['host']}:{profil['port']} "
                    f"clientId={CLIENT_ID} konto={profil['konto']} (kun ordrer, "
                    f"ingen markedsdata)")
        # ⚠ FOERST HER. En vagt der spaerrer maa ikke efterlade en global der
        # peger paa en lukket forbindelse.
        _forbindelse = ny
        _sidste_fejl_tid, _sidste_fejl = 0.0, ""
        return _forbindelse


async def luk() -> None:
    # ⚠ Under laasen. Ellers kunne vi lukke en forbindelse `hent()` var midt i at
    # tage i brug — og efterlade clientId 201 optaget af et objekt ingen ejer.
    global _forbindelse, _sidste_fejl_tid, _sidste_fejl
    async with _laas:
        _sidste_fejl_tid, _sidste_fejl = 0.0, ""
        if _forbindelse is not None:
            try:
                _forbindelse.disconnect()      # synkron — ikke await
            except Exception as e:
                logger.warning(f"[Ordre] kunne ikke lukke pænt: {e}")
            _forbindelse = None


def order_ref(hvem: str = "") -> str:
    """orderRef der markerer MANUEL oprindelse.

    ⚠ Det er dét der gør Ibens handler tilskrivbare i regnskabet. Uden en
    entydig markering ville de være ejerløse på præcis samme måde som SHAZ:
    en position hos IBKR som ingen kodesti kender og intet lukker igen.
    """
    return f"manuel:{hvem or accounts.identity.account_id}"
