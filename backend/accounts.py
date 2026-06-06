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

import sys
from dataclasses import dataclass
from pathlib import Path

import yaml


CONFIG_PATH = Path(__file__).parent / "account.yaml"


@dataclass(frozen=True)
class AccountIdentity:
    """Identiteten for denne backend-instans. Frozen = kan ikke ændres efter init."""
    account_id:           str       # "soren", "iben" — skattepligtig identitet
    account_display_name: str
    instance_role:        str       # "workstation" eller "algoserver"
    instance_display_name: str
    ibkr_account:         str       # IBKR-kontonummer fx "DUNXXXXXXX"
    paper_trading:        bool
    autostart_strategies: list[str]
    studio_password:      str       # Password til Studio (login fra browser/mobil)
    internal_key:         str       # Fælles nøgle til maskine-til-maskine-tillid (Tailscale)
    # ── Replikering ──
    replication_enabled:    bool = False
    replication_target_url: str  = ""
    source_id:              str  = ""   # afledt: "<account_id>_<instance_role>"


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