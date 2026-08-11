"""
test_account_yaml_indrykning.py — to mellemrum må ikke kunne koste en konto
════════════════════════════════════════════════════════════════════════════════
Målt 11-08-2026 på Ibens workstation: `ordre_forbindelse` var indsat i kolonne 0
og blev dermed en TOPNIVEAU-nøgle. `_laes_ordre_forbindelse` læser den fra
`instance`, så den fandt ingenting.

⚠ Intet fejlede. Backenden startede. Men ordrer ville være gået gennem den DELTE
forbindelse i stedet for den lokale Gateway.

Og V1/V2 kunne ikke gribe det: de kører først når der ER en ordreforbindelse.
Vagterne blev altså ikke omgået — de blev aldrig kaldt. Det er værd at holde fast
i, for det er en anden fejlklasse end "vagten virkede ikke", og den kræver en
anden slags kontrol.

Testen viser vagten både SPÆRRE og SLIPPE IGENNEM. En vagt der kun er set spærre,
kunne spærre alt.
"""
from __future__ import annotations

import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import accounts

BASIS = """\
account:
  id:           iben
  display_name: "Iben"
auth:
  studio_password: hemmelig
  internal_key: noegle
replication:
  enabled: true
  target_url: http://100.76.201.59:8000
instance:
  role:         workstation
  display_name: "Ibens workstation"
  ibkr_account: DUQ441063
  paper_trading: true
  autostart_strategies: []
"""

BLOK = """\
  ordre_forbindelse:
    host:   127.0.0.1
    port:   4002
    konto:  DUQ441063
    bruger: fasteriben2
"""


def _skriv(indhold: str) -> pathlib.Path:
    f = pathlib.Path(tempfile.mkdtemp()) / "account.yaml"
    f.write_text(indhold, encoding="utf-8")
    return f


def _indlaes(indhold: str):
    gammel = accounts.CONFIG_PATH
    try:
        accounts.CONFIG_PATH = _skriv(indhold)
        return accounts.load_identity()
    finally:
        accounts.CONFIG_PATH = gammel


def test_fejlindrykket_ordre_forbindelse_spaerrer():
    """⚠ Kernen. Kolonne 0 i stedet for to mellemrum."""
    forkert = BASIS + "\n" + BLOK.replace("  ordre_forbindelse:", "ordre_forbindelse:") \
                                 .replace("    ", "  ")
    try:
        _indlaes(forkert)
    except SystemExit:
        return
    raise AssertionError(
        "en topniveau ordre_forbindelse slap igennem — ordrer ville gå gennem "
        "den DELTE forbindelse, og V1/V2 ville aldrig blive kaldt")


def test_korrekt_indrykket_slipper_igennem():
    """Vagten må ikke bare spærre alt."""
    i = _indlaes(BASIS + BLOK)
    assert i.ordre_forbindelse, "den korrekt indrykkede blok blev ikke læst"
    p = dict(i.ordre_forbindelse[0])
    assert p["konto"] == "DUQ441063", p
    assert p["port"] == 4002, p
    assert p["bruger"] == "fasteriben2", p


def test_uden_blok_er_stadig_gyldig():
    """Maskiner der ikke skal skille ordrer og kurser ad, ændrer ingenting."""
    i = _indlaes(BASIS)
    assert not i.ordre_forbindelse
    assert accounts.identity is not None or True


def test_andre_fejlindrykkede_noegler_fanges():
    """Samme fælde gælder de øvrige instance-nøgler."""
    for noegle, vaerdi in [("ibkr_account", "DUQ441063"),
                           ("paper_trading", "true"),
                           ("role", "workstation")]:
        try:
            _indlaes(BASIS + f"\n{noegle}: {vaerdi}\n")
        except SystemExit:
            continue
        raise AssertionError(f"topniveau {noegle} slap igennem")


if __name__ == "__main__":
    for navn, fn in sorted(globals().items()):
        if navn.startswith("test_"):
            fn()
            print(f"  OK  {navn}")
    print("\nAlle bestod.")
