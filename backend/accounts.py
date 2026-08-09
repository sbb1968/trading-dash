"""
accounts.py — Læser identiteten for denne installation.

Dette modul er det eneste sted i systemet der har autoritet over
"hvem ejer denne backend?". Alle andre moduler får identiteten
via importen herfra — de gætter aldrig.

Filosofien er knivskarp:
  - account.yaml SKAL findes
  - account.yaml SKAL have alle påkrævede felter
  - Hvis noget mangler: backend nægter at starte

Det er bedre at fejle på en tom skærm ved opstart end at handle på
forkert konto i 30 sekunder før nogen opdager det.

Placering: C:\\Projects\\Trading_Dash\\backend\\accounts.py
"""

import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml


CONFIG_PATH = Path(__file__).parent / "account.yaml"

# Hvilken af de tilladte konti maskinen handler paa LIGE NU. Adskilt fra
# account.yaml med vilje: yaml'en indeholder ogsaa internal_key, studio-password
# og replikeringsopsaetning, og en UI der skriver den fil kan i vaerste fald
# spaerre maskinens adgang til resten af fleet'et. Samme opdeling som
# risk_config.json: yaml = hvad der er TILLADT, json = hvad der er VALGT.
AKTIV_PATH = Path(__file__).parent / "aktiv_konto.json"

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AccountIdentity:
    """Identiteten for denne backend-instans. Frozen = kan ikke ændres efter init."""
    account_id:           str       # "soren", "iben" — skattepligtig identitet
    account_display_name: str
    instance_role:        str       # "workstation" eller "algoserver"
    instance_display_name: str
    ibkr_account:         str       # IBKR-kontonummer fra yaml — STANDARDVALGET.
                                    # Den konto der handles paa nu: aktiv_konto()
    paper_trading:        bool
    autostart_strategies: list[str]
    studio_password:      str       # Password til Studio (login fra browser/mobil)
    internal_key:         str       # Fælles nøgle til maskine-til-maskine-tillid (Tailscale)
    # ── Replikering ──
    replication_enabled:    bool = False
    replication_target_url: str  = ""
    source_id:              str  = ""   # afledt: "<account_id>_<instance_role>"
    # Konti maskinen MAA handle paa: [{"id": "DUQ441063", "label": "Iben paper 2"}]
    # Hvidlisten ER sikkerheden — man kan ikke vaelge en konto ingen har erklaeret.
    ibkr_konti:             tuple = ()


def _fail(msg: str) -> None:
    """Print en stor fejlbesked og afslut processen."""
    bar = "═" * 70
    print()
    print(bar)
    print("FATAL: account.yaml fejl — backend kan ikke starte")
    print(bar)
    print(msg)
    print(bar)
    print()
    sys.exit(1)


def _laes_konti(instance: dict) -> tuple:
    """Hvidlisten fra account.yaml:

        instance:
          ibkr_account: DUO509856          # standardvalget
          ibkr_konti:
            - id: DUO509856   label: "Iben konto 1"
            - id: DUQ441063   label: "Iben konto 2"

    ⚠ ALLE konti paa listen skal ligge under SAMME TWS-login. Paper og live er to
    forskellige logins paa hver sin port (7497/7496), saa et skift mellem dem kan
    software ikke klare alene — det kraever en TWS der er logget paa den anden.
    Derfor er der intet paper-felt pr. konto: instance.paper_trading gaelder alle.

    Mangler listen, bliver den standardkontoen alene. Maskiner der ikke skal
    skifte konto behoever altsaa ikke aendre noget.
    """
    raa = instance.get("ibkr_konti") or []
    standard = str(instance["ibkr_account"]).strip().upper()

    ud = []
    for post in raa:
        if isinstance(post, str):
            post = {"id": post}
        if not isinstance(post, dict) or not post.get("id"):
            _fail(f"instance.ibkr_konti: hver post skal have et 'id', fik {post!r}")
        kid = str(post["id"]).strip().upper()
        if any(k["id"] == kid for k in ud):
            _fail(f"instance.ibkr_konti: {kid} staar to gange")
        ud.append({"id": kid, "label": str(post.get("label", "") or kid)})

    if not ud:
        return ({"id": standard, "label": str(instance.get("display_name", standard))},)

    # ⚠ Standardkontoen SKAL vaere paa listen. Ellers ville en tom eller
    # beskadiget aktiv_konto.json falde tilbage til en konto der ikke er tilladt.
    if not any(k["id"] == standard for k in ud):
        _fail(f"instance.ibkr_account ({standard}) staar ikke i instance.ibkr_konti. "
              f"Tilfoej den, eller ret standardkontoen.")
    return tuple(ud)


def tilladte_konti() -> tuple:
    """Konti denne maskine maa handle paa."""
    return identity.ibkr_konti


def aktiv_konto() -> str:
    """Den IBKR-konto maskinen handler paa LIGE NU.

    ⚠ Brug denne frem for identity.ibkr_account overalt hvor der handles,
    stemples eller vises. identity.ibkr_account er kun standardvalget fra yaml
    og aendrer sig ikke naar man skifter konto i UI'en.

    Er det gemte valg ikke laengere paa hvidlisten — fordi account.yaml er
    aendret siden — falder vi tilbage til standarden OG siger det hoejt. At
    handle videre paa en konto der ikke laengere er tilladt ville vaere den
    slags tavse fejl der foerst opdages i en kontoudskrift.
    """
    tilladte = [k["id"] for k in identity.ibkr_konti]
    try:
        if AKTIV_PATH.exists():
            valgt = str(json.loads(AKTIV_PATH.read_text(encoding="utf-8"))
                        .get("konto", "")).strip().upper()
            if valgt and valgt in tilladte:
                return valgt
            if valgt:
                logger.error(
                    f"[Konto] {AKTIV_PATH.name} peger paa {valgt}, som IKKE er paa "
                    f"hvidlisten {tilladte}. Bruger {identity.ibkr_account}.")
    except (OSError, ValueError) as e:
        logger.error(f"[Konto] Kunne ikke laese {AKTIV_PATH.name} ({e}) — "
                     f"bruger {identity.ibkr_account}")
    return identity.ibkr_account


def saet_aktiv_konto(konto: str, hvem: str = "") -> str:
    """Skriv det aktive kontovalg. Kaster ValueError hvis kontoen ikke er tilladt."""
    kid = (konto or "").strip().upper()
    tilladte = [k["id"] for k in identity.ibkr_konti]
    if kid not in tilladte:
        raise ValueError(f"{kid or '(tom)'} er ikke paa hvidlisten i account.yaml "
                         f"({', '.join(tilladte)})")
    from datetime import datetime, timezone
    AKTIV_PATH.write_text(json.dumps({
        "konto": kid,
        "aendret": datetime.now(timezone.utc).isoformat(),
        "aendret_af": hvem or "ukendt",
    }, indent=2), encoding="utf-8")
    logger.info(f"[Konto] Aktiv konto sat til {kid} (af {hvem or 'ukendt'})")
    return kid


def konto_label(konto: str = "") -> str:
    kid = (konto or aktiv_konto()).upper()
    for k in identity.ibkr_konti:
        if k["id"] == kid:
            return k["label"]
    return kid


def load_identity() -> AccountIdentity:
    """
    Læs account.yaml og returnér identiteten.
    Fejler hårdt hvis filen mangler eller er ufuldstændig.
    """
    if not CONFIG_PATH.exists():
        _fail(
            f"Filen findes ikke: {CONFIG_PATH}\n"
            f"Opret en account.yaml med følgende felter:\n"
            f"  account.id, account.display_name,\n"
            f"  instance.role, instance.display_name,\n"
            f"  instance.ibkr_account, instance.paper_trading,\n"
            f"  instance.autostart_strategies"
        )

    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        _fail(f"Filen er ugyldig YAML: {e}")
    except OSError as e:
        _fail(f"Kunne ikke læse filen: {e}")

    if not isinstance(data, dict):
        _fail("account.yaml er tom eller har forkert format på topniveau")

    try:
        account  = data["account"]
        instance = data["instance"]
        auth     = data.get("auth", {}) or {}
        repl     = data.get("replication", {}) or {}

        identity = AccountIdentity(
            account_id            = str(account["id"]),
            account_display_name  = str(account["display_name"]),
            instance_role         = str(instance["role"]),
            instance_display_name = str(instance["display_name"]),
            ibkr_account          = str(instance["ibkr_account"]),
            paper_trading         = bool(instance["paper_trading"]),
            autostart_strategies  = list(instance.get("autostart_strategies", [])),
            studio_password       = str(auth.get("studio_password", "")),
            internal_key          = str(auth.get("internal_key", "")),
            replication_enabled    = bool(repl.get("enabled", False)),
            replication_target_url = str(repl.get("target_url", "")),
            source_id              = f"{str(account['id'])}_{str(instance['role'])}",
            ibkr_konti             = _laes_konti(instance),
        )
    except (KeyError, TypeError) as e:
        _fail(f"Manglende eller forkert felt i account.yaml: {e}")

    # Valider værdier
    if identity.instance_role not in ("workstation", "algoserver"):
        _fail(f"instance.role skal være 'workstation' eller 'algoserver', "
              f"ikke '{identity.instance_role}'")

    if not identity.account_id or " " in identity.account_id:
        _fail(f"account.id skal være ét ord uden mellemrum, "
              f"ikke '{identity.account_id}'")

    if not identity.studio_password:
        _fail(
            "auth.studio_password er ikke sat i account.yaml.\n"
            "Tilføj følgende til account.yaml:\n"
            "  auth:\n"
            "    studio_password: <dit-password>"
        )

    return identity


# Indlæs én gang ved module-load. Hvis det fejler, dør processen her.
identity: AccountIdentity = load_identity()