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

import logging
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


async def hent(genforbind: bool = True) -> IBKRConnection:
    """Den skrivende forbindelse, klar til brug. Kaster hvis en vagt spærrer."""
    global _forbindelse

    profil = accounts.ordre_forbindelse()
    if profil is None:
        raise OrdreForbindelseFejl(
            "ingen ordre_forbindelse i account.yaml — ordrer går gennem den "
            "delte forbindelse")

    verificer_profil(profil)                                    # V2, før connect

    if _forbindelse is not None and _forbindelse.connected:
        return _forbindelse
    if not genforbind:
        raise OrdreForbindelseFejl("ordreforbindelsen er nede")

    _forbindelse = IBKRConnection(
        paper_trading=not profil.get("tillad_live"),
        account=profil["konto"],
        host=profil["host"],
        port=profil["port"],
        client_id=CLIENT_ID,
        kraev_konto=True,     # §3.2: en glemt konto skal fejle, ikke gaettes
    )
    ok = await _forbindelse.connect()
    if not ok or not _forbindelse.connected:
        _forbindelse = None
        raise OrdreForbindelseFejl(
            f"kunne ikke forbinde til Gateway på {profil['host']}:{profil['port']} "
            f"— kører den, og er API'et slået til?")

    # ── V1: kontobekræftelse ────────────────────────────────────────────────
    styrede = [a.strip().upper() for a in (_forbindelse.ib.managedAccounts() or [])]
    if profil["konto"] not in styrede:
        _forbindelse.disconnect()      # synkron — ikke await
        _forbindelse = None
        raise OrdreForbindelseFejl(
            f"⚠ FORKERT KONTO. Gatewayen på port {profil['port']} styrer "
            f"{styrede or '(ingen)'}, ikke {profil['konto']}. Ingen ordrer sendes. "
            f"Er Gatewayen logget ind som {profil.get('bruger') or 'den rigtige bruger'}?")

    # ── V2 igen, nu mod det IBKR faktisk melder ─────────────────────────────
    # Konfigurationen kan sige ét og virkeligheden noget andet; her er det
    # virkeligheden der tjekkes.
    for k in styrede:
        if not k.startswith("D") and not profil.get("tillad_live"):
            _forbindelse.disconnect()      # synkron — ikke await
            _forbindelse = None
            raise OrdreForbindelseFejl(
                f"⚠ Gatewayen styrer en LIVE-konto ({k}) og tillad_live er ikke "
                f"sat. Forbindelsen lukkes.")

    logger.info(f"[Ordre] forbundet {profil['host']}:{profil['port']} "
                f"clientId={CLIENT_ID} konto={profil['konto']} (kun ordrer, "
                f"ingen markedsdata)")
    return _forbindelse


async def luk() -> None:
    global _forbindelse
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
