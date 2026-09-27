"""
test_nt_forensik.py — en NT8-handel skal efterlade samme spor som en IBKR-handel
════════════════════════════════════════════════════════════════════════════════
Kravet var: *"samme rige forensik til handler med ninjatrader som vi gør med
ibkr, såvidt muligt."*

Måden det er løst på er værd at forstå: `broker` er **et argument**, ikke en ny
kodesti. Begge brokere går gennem `registrer_entry` / `registrer_exit`, samme
`trade_forensics`-buildere, samme `trades`-række. En kopi af de funktioner til
NT8 ville drive fra hinanden inden for en måned.

Det kan lade sig gøre fordi **bars og indikatorer kommer fra IBKR uanset hvem
der udførte ordren**. ATI leverer ingen kurser; det skal den heller ikke.

⚠ DET FARLIGE VED TO BROKERE ER PARRINGEN.
`registrer_exit` finder den åbne handel med `find_aaben(journal, symbol, konto)`.
Bruger NT8-salget IBKR's konto, parres det med en IBKR-handel — og to rækker
bliver forkerte i samme skrivning. Det er nøjagtig kryds-konto-fejlen fra
11-09-2026, bare mellem to *brokere* i stedet for to IBKR-konti. Dengang kostede
den en genåbning og en fejlbogført exit på 10 USD.

Denne test kræver derfor at en NT8-exit **ikke kan** ramme en IBKR-entry.

    python test_nt_forensik.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

import aiosqlite

import manuel_forensik as MF

fejl: list[str] = []


def kraev(betingelse: bool, hvad: str) -> None:
    print(f"  {'OK  ' if betingelse else 'FEJL'} {hvad}")
    if not betingelse:
        fejl.append(hvad)


class FalskJournal:
    """Kun det forensikken rører. Registrerer hvad der blev skrevet."""

    def __init__(self, db):
        self.db = db
        self.events: list[dict] = []
        self.aabne: dict[str, dict] = {}

    async def log_trade_open(self, **kw):
        tid = f"t{len(self.aabne) + 1}"
        self.aabne[tid] = {**kw, "trade_id": tid}
        await self.db.execute(
            "INSERT INTO trades (trade_id, account_id, instance_id, ibkr_account,"
            " source, symbol, side, shares, entry_time_utc, entry_time_et,"
            " entry_price, entry_reason, capital_used, payload_json)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, "iben", "workstation",
             (kw.get("payload") or {}).get("konto") or "DUQ441063",
             kw["source"], kw["symbol"], kw["side"], kw["shares"],
             kw["entry_time"].isoformat(), kw["entry_time"].isoformat(),
             kw["entry_price"], kw.get("entry_reason") or "", kw["entry_price"],
             json.dumps(kw.get("payload") or {})))
        await self.db.commit()
        return tid

    async def log_trade_close(self, **kw):
        self.aabne.pop(kw.get("trade_id"), None)
        return True

    async def log_event(self, **kw):
        self.events.append(kw)


async def _byg_db(sti: Path):
    db = await aiosqlite.connect(str(sti))
    db.row_factory = aiosqlite.Row
    skema = (Path(__file__).parent / "db_schema.sql").read_text(encoding="utf-8")
    await db.executescript(skema)
    await db.commit()
    return db


async def koer() -> None:
    import datetime
    ET = datetime.timezone(datetime.timedelta(hours=-4))

    with tempfile.TemporaryDirectory() as d:
        db = await _byg_db(Path(d) / "t.db")
        try:
            j = FalskJournal(db)

            class FalskIbkr:
                account = "DUQ441063"

            ibkr = FalskIbkr()

            # ── 1. En IBKR-handel og en NT8-handel, samme vej ind ─────────
            t_ibkr = await MF.registrer_entry(
                j, ibkr, symbol="MES", side="long", shares=1, fill_pris=7600.0,
                ordre_id=124, ordre_status="Filled", et_tz=ET)
            t_nt = await MF.registrer_entry(
                j, ibkr, symbol="MES", side="long", shares=1, fill_pris=7610.0,
                ordre_id="NTM1790515", ordre_status="Working", et_tz=ET,
                broker="NT8", konto="Sim101")
            kraev(t_ibkr is not None and t_nt is not None,
                  f"begge brokere skrev en trades-raekke ({t_ibkr}, {t_nt})")

            raekker = {r["trade_id"]: r for r in await (
                await db.execute("SELECT * FROM trades")).fetchall()}
            p_ibkr = json.loads(raekker[t_ibkr]["payload_json"])
            p_nt = json.loads(raekker[t_nt]["payload_json"])

            # ── 2. SAMME felter, forskelligt indhold ─────────────────────
            faelles = {"broker", "konto", "ordre_id", "ordre_status", "indgang"}
            kraev(faelles <= set(p_ibkr) and faelles <= set(p_nt),
                  "begge payloads har de samme neutrale felter")
            kraev(p_ibkr["broker"] == "IBKR" and p_nt["broker"] == "NT8",
                  f"brokeren staar i payloaden ({p_ibkr['broker']} / {p_nt['broker']})")
            kraev(p_nt["konto"] == "Sim101",
                  f"NT8-handlen er bogfoert paa NT8-kontoen ({p_nt['konto']})")
            kraev(p_ibkr["konto"] == "DUQ441063",
                  f"IBKR-handlen paa IBKR-kontoen ({p_ibkr['konto']})")

            # ⚠ Bagudkompatibilitet: de 17 eksisterende handler har kun de
            # gamle noegler, og afstemningen laeser dem.
            kraev("ibkr_order_id" in p_ibkr,
                  "IBKR skriver stadig ibkr_order_id (historikken kan laeses)")
            kraev("ibkr_order_id" not in p_nt,
                  "NT8 skriver IKKE ibkr_order_id — navnet ville luge")

            # ── 3. Forensik-snapshot for BEGGE ───────────────────────────
            snaps = [e for e in j.events if e.get("event_type") == "trade_forensics"]
            kraev(len(snaps) == 2,
                  f"begge handler fik et trade_forensics-snapshot ({len(snaps)})")
            kraev(all(e["payload"].get("trade_id") for e in snaps),
                  "snapshottene baerer trade_id — deterministisk kobling")

            # ── 4. ⚠ AFSTEMNINGEN SKAL SE BEGGE ─────────────────────────
            ider = await MF._bogfoerte_ordre_ider(j)
            kraev("124" in ider and "NTM1790515" in ider,
                  f"afstemningen finder BEGGE brokeres ordre-id'er ({sorted(ider)})")

            # ── 5. ⚠ KRYDS-BROKER-PARRING SKAL VAERE UMULIG ─────────────
            # Et NT8-salg maa ikke kunne lukke IBKR-handlen.
            fundet_nt = await MF.find_aaben(j, "MES", konto="Sim101")
            fundet_ibkr = await MF.find_aaben(j, "MES", konto="DUQ441063")
            kraev(fundet_nt is not None and fundet_nt["trade_id"] == t_nt,
                  "et NT8-opslag finder NT8-handlen")
            kraev(fundet_ibkr is not None and fundet_ibkr["trade_id"] == t_ibkr,
                  "et IBKR-opslag finder IBKR-handlen")
            kraev(fundet_nt["trade_id"] != fundet_ibkr["trade_id"],
                  "⚠ de to brokere kan IKKE parres med hinandens handler")

            # ── 6. ⚠ JOURNAL-VAGTEN: advarer, men blokerer ALDRIG ───────
            print("\n  ── journal-vagten (NT8 kan ikke spoerges om positioner) ──")
            sager = [
                ("SELL", 1, "Sim101", "", "salg af praecis det journalen kender"),
                ("SELL", 3, "Sim101", "short", "salg af MERE end journalen kender"),
                ("SELL", 1, "DEMO8580770", "INGEN", "salg uden kendt position"),
                ("BUY",  1, "Sim101", "oeger", "koeb oven i en aaben position"),
            ]
            for action, antal, konto, forvent, hvad in sager:
                ok, besked, det = await MF.kontroller_ordre_journal(
                    j, "MES", action, antal, konto)
                kraev(ok is True, f"{hvad}: BLOKERER IKKE")
                kraev(det["kontrolleret"] is False,
                      f"    …og markeres som UKONTROLLERET")
                kraev(det["kilde"] == "journal", "    kilden staar i detaljerne")
                if forvent and forvent != "INGEN":
                    kraev(forvent in besked.lower() or "⚠" in besked,
                          f"    advarer: {besked[:58] or '(ingen besked)'}")
                if forvent == "INGEN":
                    kraev("INGEN" in besked,
                          f"    advarer om ukendt position: {besked[:52]}")

            # ⚠ Det vigtigste: den maa ALDRIG returnere kontrolleret=True.
            alle = [await MF.kontroller_ordre_journal(j, "MES", a, n_, k)
                    for a, n_, k, _f, _h in sager]
            kraev(all(d["kontrolleret"] is False for _o, _b, d in alle),
                  "⚠ ingen af udfaldene paastaar at brokeren blev spurgt")

            # ── 7. Mutation: lad NT8-exit bruge IBKR's konto ─────────────
            print("\n  ── mutation: NT8-exit med IBKR's konto ──")
            forkert = await MF.find_aaben(j, "MES", konto="DUQ441063")
            kraev(forkert["trade_id"] == t_ibkr,
                  "med forkert konto rammer opslaget IBKR-handlen — dét er faren")
            kraev(forkert["trade_id"] != t_nt,
                  "…og altsaa IKKE den NT8-handel salget hoerte til")
        finally:
            await db.close()


def main() -> int:
    print("  ── NT8-forensik ──")
    asyncio.run(koer())
    print(f"\n  {'ALLE BESTAAET' if not fejl else f'⚠ {len(fejl)} FEJLEDE'}")
    return 1 if fejl else 0


if __name__ == "__main__":
    sys.exit(main())
