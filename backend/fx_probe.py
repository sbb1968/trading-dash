"""
fx_probe.py — kan vi handle spot FX gennem vores egen stak, og til hvilken gearing?
════════════════════════════════════════════════════════════════════════════════
Proben producerer VIDEN, ikke funktionalitet. Ingen strategi, ingen signaler,
ingen execution-kode. Se spec 30-08-2026 og fx_probe_rapport.md.

⚠ DET VIGTIGSTE DESIGNVALG: APPARAT-KONTROLLEN
Et tomt whatIf-svar kan betyde to vidt forskellige ting:

    (a) "denne konto faar ingen margin paa FX"      ← et fund
    (b) "whatIf svarer ikke lige nu"                ← en maalefejl

De ser ens ud i API'et. Derfor maaler proben ALTID MES foerst — et instrument
hvis margin vi kender — og naegter at rapportere gearing hvis MES ogsaa er tom.
Uden det skel ville proben producere saetningen "FX har ingen gearing" paa et
lukket marked, og det ville vaere en paastand uden maaling bag.
Det er projektets tilbagevendende fejlklasse: en kontrol hvis fejl behandles
som en beslutning.

⚠ MAALT 30-08-2026 (soendag, marked lukket) paa DUN748991 / TWS :7497:
    whatIf kom TOM tilbage for BAADE EURUSD, EUR.USD-CFD og MES.
    → P2 kan ikke maales med lukket marked. Koer den efter FX-aabning
      soendag 23:15 dansk tid. Se rapporten §P2.

⚠ AFVIGELSE FRA SPECEN §S2 ("kun port 4002")
Der er ingen Gateway paa 4002 paa denne maskine — DUQ441063 koerer TWS paa
7497. Reglens FORMAAL er "ram aldrig en live-port", og det haandhaeves i
stedet med:
    · en ALLOWLIST af paper-porte {4002, 7497}
    · hard refuse paa live-porte {4001, 7496}
    · S1-assertionen paa DU-praefiks, som er den egentlige garanti
Afvigelsen staar i rapporten.

    python fx_probe.py --kun P0,P1,P2
    python fx_probe.py --kun P3 --tillad-ordre      # kraever aabent marked
    python fx_probe.py --kun P7 --vindue london_ny  # spread-sampling
    python fx_probe.py --kun P8                     # ren kodescanning, ingen IBKR
"""
from __future__ import annotations

import asyncio
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import argparse
import io
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from ib_async import IB, Contract, Forex, Future, MarketOrder

import ibkr_client_ids

# ── Sikkerhed (spec §1) ────────────────────────────────────────────────────
PAPER_PORTE = {4002, 7497}          # S2 (se afvigelsesnoten i hovedet)
LIVE_PORTE = {4001, 7496}
CLIENT_ID = ibkr_client_ids.for_script("fx_probe.py")
HOST = "127.0.0.1"

UD = Path(__file__).parent / "fx_probe_output"
STOEJ = {2104, 2106, 2158, 2119, 2100, 10349}   # forbindelses-/preset-stoej

# ── Instrumentsaet (spec §2) ───────────────────────────────────────────────
MAJORS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCHF", "USDCAD", "NZDUSD"]
KRYDS = ["EURGBP", "EURJPY", "GBPJPY"]
SKANDI = ["EURDKK", "USDDKK", "EURSEK", "EURNOK"]
FX_ALLE = MAJORS + KRYDS + SKANDI

# Kontrolgruppen. Uden den kan man ikke skelne "FX opfoerer sig anderledes"
# fra "proben er skrevet forkert".
KONTROL_FUT = ("MES", "20260918", "CME")

P2_STOERRELSER = [25_000, 100_000, 500_000]
P2_PAR = MAJORS + ["EURDKK"]

MANGLER = None   # S6: manglende data er None -> "—" i rapporten. Aldrig 0.

# ── ESMA-forventningen (praeregistreret, bekraeftet 30-08-2026) ────────────
# Client Portal: juridisk enhed IBIE (interactivebrokers.ie), MiFID-kategori
# "Retail Client". Dermed gaelder ESMA's produktintervention, og gearingen har
# et LOFT vi kan forudsige FOER vi maaler:
#
#   major     30:1  ->  3,33 % initial margin
#   ikke-major 20:1  ->  5,00 %
#
# ⚠ ESMA's "major" er snaevrere end dagligsprogets: par sammensat af TO af
# {USD, EUR, JPY, GBP, CAD, CHF}. AUD og NZD er IKKE med — AUDUSD og NZDUSD er
# altsaa ikke-majors under reglen, selv om enhver handelsplatform kalder dem
# majors. Det er den skarpeste enkeltforudsigelse proben kan afproeve.
#
# ⚠ LOFTET ER ET LOFT, IKKE ET LOEFTE. IBKR maa kraeve MERE end reglens
# minimum (husmargin), og goer det ofte. Maaler vi 3,33 %, er reglen bindende;
# maaler vi mere, er det IBKR's egen margin. Begge dele er svar — men "mindre
# end 3,33 %" ville betyde at en af mine antagelser er forkert.
ESMA_MAJOR_VALUTAER = {"USD", "EUR", "JPY", "GBP", "CAD", "CHF"}


def esma_forventning(par: str) -> dict:
    """Forventet gearingsloft for et par under ESMA, for en retail-klient."""
    basis, kvot = par[:3], par[3:]
    major = basis in ESMA_MAJOR_VALUTAER and kvot in ESMA_MAJOR_VALUTAER
    return {"esma_major": major,
            "forventet_gearing": 30 if major else 20,
            "forventet_margin_pct": 3.33 if major else 5.00}


def pip_stoerrelse(par: str) -> float:
    """JPY-par kvoteres i to decimaler; pip er 0,01 og ikke 0,0001."""
    return 0.01 if par.endswith("JPY") else 0.0001


@dataclass
class Fejlopsamler:
    poster: list = field(default_factory=list)

    def haendelse(self, reqId, code, msg, contract):
        self.poster.append((code, msg))

    def relevante(self):
        return [(k, m) for k, m in self.poster if k not in STOEJ]

    def nulstil(self):
        self.poster.clear()


def skriv(navn: str, data) -> Path:
    UD.mkdir(exist_ok=True)
    sti = UD / f"{navn}.json"
    sti.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str),
                   encoding="utf-8")
    return sti


def v(x):
    """S6: vis manglende som '—', aldrig som 0 eller tom streng."""
    return "—" if x is None or x == "" else x


async def forbind(port: int) -> tuple[IB, Fejlopsamler]:
    # S2 — porten kontrolleres FOER der forbindes.
    if port in LIVE_PORTE:
        raise SystemExit(f"AFBRUDT: {port} er en LIVE-port. Proben koerer kun paper.")
    if port not in PAPER_PORTE:
        raise SystemExit(
            f"AFBRUDT: {port} staar ikke paa paper-allowlisten {sorted(PAPER_PORTE)}.")

    ib = IB()
    f = Fejlopsamler()
    ib.errorEvent += f.haendelse
    await ib.connectAsync(HOST, port, clientId=CLIENT_ID, timeout=25)

    # S1 — den egentlige garanti. Fejl, ikke advarsel.
    konti = ib.managedAccounts()
    if not konti:
        ib.disconnect()
        raise SystemExit("AFBRUDT (S1): ingen managed accounts — kan ikke bekraefte paper.")
    ikke_paper = [k for k in konti if not k.startswith("DU")]
    if ikke_paper:
        ib.disconnect()
        raise SystemExit(f"AFBRUDT (S1): ikke-paper konto i sessionen: {ikke_paper}")
    print(f"forbundet · port {port} · konti {konti} · clientId {CLIENT_ID}")
    return ib, f


async def whatif(ib: IB, ct: Contract, order, f: Fejlopsamler) -> dict:
    """Ét whatIf-kald. Returnerer ALTID en status, aldrig et gaet.

    S3: whatIf assertes True umiddelbart foer kaldet — en whatIf-ordre naar
    aldrig markedet.
    """
    f.nulstil()
    order.whatIf = True
    assert order.whatIf is True, "S3: whatIf skal vaere True foer placeOrder"
    try:
        r = await asyncio.wait_for(ib.whatIfOrderAsync(ct, order), timeout=25)
    except asyncio.TimeoutError:
        return {"status": "timeout", "fejl": [], "felter": {}}

    # ⚠ LAD FEJLEN NAA FREM FOER DEN AFLAESES.
    # whatIfOrderAsync' future resolverer paa openOrderEnd, men fejlbeskeden
    # (fx 201 "FX trade would expose account to currency leverage") ankommer
    # et oejeblik SENERE paa errorEvent. Aflaeses opsamleren med det samme,
    # staar der ingen fejl — og et AFVIST kald bogfoeres som "tomt_uden_fejl".
    # Det er netop den skelnen hele P2 haenger paa: tomt uden fejl = apparatet
    # er nede; tomt MED fejl 201 = et fund om FX. Uden ventetiden ville proben
    # kassere sit eget vigtigste svar.
    await asyncio.sleep(0.4)

    fejl = [{"kode": k, "tekst": m} for k, m in f.relevante()]
    if isinstance(r, list):
        # Tomt svar. Om det er en afvisning eller et doedt apparat afgoeres
        # IKKE her — det afgoer apparat_kontrol().
        return {"status": "afvist" if fejl else "tomt_uden_fejl",
                "fejl": fejl, "felter": {}}

    felter = {k: (getattr(r, k, None) or None) for k in
              ("initMarginChange", "maintMarginChange", "equityWithLoanChange",
               "initMarginBefore", "initMarginAfter", "commission",
               "commissionCurrency", "warningText", "status")}
    return {"status": "maalt", "fejl": fejl, "felter": felter}


async def apparat_kontrol(ib: IB, f: Fejlopsamler) -> dict:
    """⚠ VIRKER whatIf OVERHOVEDET LIGE NU?

    Maaler MES front-maaned, hvis initial margin vi kender stoerrelsesordenen
    paa. Kommer DEN tom tilbage, kan et tomt FX-svar ikke laeses som et udsagn
    om FX. Saa er det apparatet der er nede.
    """
    fut = Future(KONTROL_FUT[0], KONTROL_FUT[1], KONTROL_FUT[2], currency="USD")
    await ib.qualifyContractsAsync(fut)
    r = await whatif(ib, fut, MarketOrder("BUY", 1), f)
    virker = (r["status"] == "maalt"
              and r["felter"].get("initMarginChange") not in (None, ""))
    return {
        "apparatet_virker": virker,
        "kontrol_instrument": f"{KONTROL_FUT[0]} {KONTROL_FUT[1]}",
        "kontrol_svar": r,
        "betydning": ("whatIf svarer — tomme FX-svar er udsagn om FX"
                      if virker else
                      "⚠ whatIf svarer IKKE. Tomme FX-svar siger INTET om FX. "
                      "Sandsynligvis lukket marked. P2 skal koeres igen naar "
                      "FX er aabent."),
    }


# ── P0 · konto og klassifikation ───────────────────────────────────────────
async def p0(ib: IB, f: Fejlopsamler) -> dict:
    await ib.reqAccountSummaryAsync()
    vaerdier: dict[str, dict[str, str]] = {}
    for av in ib.accountValues():
        if av.currency in ("USD", "BASE", ""):
            vaerdier.setdefault(av.tag, {})[av.currency or "-"] = av.value

    oensket = ["AccountType", "NetLiquidation", "AvailableFunds", "BuyingPower",
               "Cushion", "FullInitMarginReq", "FullMaintMarginReq",
               "Leverage-S", "FxCashBalance", "NLVAndMarginInReview"]
    ud = {
        "konti": ib.managedAccounts(),
        "alle_er_paper": all(k.startswith("DU") for k in ib.managedAccounts()),
        "udvalgte": {t: vaerdier.get(t, MANGLER) for t in oensket},
        "juridisk_enhed": MANGLER,
        "retail_eller_professionel": MANGLER,
        "note": ("API'et eksponerer hverken juridisk enhed (IBIE/IBLLC) eller "
                 "retail/professionel-klassifikation. AccountType siger kun "
                 "kontoform (INDIVIDUAL), ikke ESMA-status. Skal bekraeftes i "
                 "Client Portal — se rapporten §P0."),
    }
    for t in oensket:
        print(f"  {t:24} {v(ud['udvalgte'][t])}")
    print(f"  {'juridisk enhed':24} {v(MANGLER)}  (ikke eksponeret i API)")
    return ud


# ── P1 · tilgaengelighed og kontraktfakta ──────────────────────────────────
async def p1(ib: IB, f: Fejlopsamler) -> dict:
    ud: dict = {"fx": {}, "kontrol": {}}
    for par in FX_ALLE:
        f.nulstil()
        try:
            cds = await ib.reqContractDetailsAsync(Forex(par))
        except Exception as e:
            ud["fx"][par] = {"handelbar": False, "fejl": f"{type(e).__name__}: {e}"}
            print(f"  {par:7} FEJL {type(e).__name__}")
            continue
        if not cds:
            # ⚠ Tom liste uden fejl. Ligner "findes ikke", kan vaere manglende
            # tilladelse (fejl 200). Begge dele noteres som uafklaret.
            ud["fx"][par] = {
                "handelbar": False,
                "fejl": [{"kode": k, "tekst": m} for k, m in f.relevante()] or MANGLER,
                "note": "tom ContractDetails — findes ikke ELLER manglende tilladelse",
            }
            print(f"  {par:7} TOM")
            continue
        d = cds[0]
        r = {
            "handelbar": True,
            "conId": d.contract.conId,
            "boers": d.contract.exchange,
            "minTick": d.minTick,
            "minSize": getattr(d, "minSize", MANGLER),
            "sizeIncrement": getattr(d, "sizeIncrement", MANGLER),
            "suggestedSizeIncrement": getattr(d, "suggestedSizeIncrement", MANGLER),
            "priceMagnifier": getattr(d, "priceMagnifier", MANGLER),
            "validExchanges": d.validExchanges,
            "orderTypes": d.orderTypes,
            "tradingHours": d.tradingHours,
            "liquidHours": d.liquidHours,
            "timeZoneId": d.timeZoneId,
        }
        ud["fx"][par] = r
        print(f"  {par:7} minTick={r['minTick']:<9} minSize={v(r['minSize']):<8} "
              f"sizeIncr={v(r['sizeIncrement']):<8} {r['boers']}")

    fut = Future(KONTROL_FUT[0], KONTROL_FUT[1], KONTROL_FUT[2], currency="USD")
    cds = await ib.reqContractDetailsAsync(fut)
    if cds:
        d = cds[0]
        ud["kontrol"][KONTROL_FUT[0]] = {
            "minTick": d.minTick, "multiplier": d.contract.multiplier,
            "tradingHours": d.tradingHours, "liquidHours": d.liquidHours,
            "timeZoneId": d.timeZoneId,
        }

    # ⚠ Websitets tal vs. ContractDetails — specen §P1 kraever afvigelsen noteret.
    ud["websted_vs_api"] = {
        "webstedets_paastand": "IDEALPRO minimum 20.000-25.000 enheder",
        "api_svar": {p: ud["fx"][p].get("minSize") for p in P2_PAR if p in ud["fx"]},
        "afvigelse": ("ContractDetails.minSize melder 0,01 (eller 1,0) enheder for "
                      "alle par — IKKE 20.000-25.000. Enten daekker minSize kun "
                      "IDEALFX-rutning, eller IDEALPRO-minimum haandhaeves foerst "
                      "ved ordreafgivelse. Uafklaret; kraever en aegte P3-ordre "
                      "under minimum for at afgoere."),
    }
    return ud


# ── P2 · gearingsmaalingen ─────────────────────────────────────────────────
async def _basis_i_usd(ib: IB, valuta: str) -> float | None:
    """Kurs fra en basisvaluta til USD. None hvis den ikke kan hentes.

    ⚠ NOEDVENDIG FOR AT GEARINGEN BLIVER RIGTIG. Ordrestoerrelsen er i
    BASISVALUTA (25.000 EUR), men marginen kommer i kontoens valuta (USD).
    Deler man de to tal direkte, faar man gearingen ganget med vekselkursen —
    for EURUSD 25,7:1 i stedet for 30:1. Tallet ville se ud som om det
    modsagde ESMA-loftet, og fejlen ville vaere usynlig fordi resultatet
    ligger i et troevaerdigt leje.
    """
    if valuta == "USD":
        return 1.0
    c = Forex(f"{valuta}USD")
    try:
        await ib.qualifyContractsAsync(c)
    except Exception:
        return None
    ib.reqMarketDataType(3)
    t = ib.reqMktData(c, "", True, False)
    for _ in range(12):
        await asyncio.sleep(0.5)
        if t.midpoint() == t.midpoint() or t.close == t.close:
            break
    kurs = next((float(x) for x in (t.midpoint(), t.last, t.close)
                 if x == x and x), None)
    ib.cancelMktData(c)
    return kurs


async def p2(ib: IB, f: Fejlopsamler) -> dict:
    app = await apparat_kontrol(ib, f)
    print(f"  apparat-kontrol: {'OK' if app['apparatet_virker'] else '⚠ NEDE'}")
    print(f"    {app['betydning']}")

    # Kurser til notional-omregningen, hentet ÉN gang.
    kurser: dict[str, float | None] = {}
    for basis in {p[:3] for p in P2_PAR}:
        kurser[basis] = await _basis_i_usd(ib, basis)
    print(f"  basiskurser mod USD: "
          + ", ".join(f"{k}={v(x)}" for k, x in sorted(kurser.items())))

    maalinger = []
    for par in P2_PAR:
        c = Forex(par)
        try:
            await ib.qualifyContractsAsync(c)
        except Exception as e:
            maalinger.append({"par": par, "status": "kunne_ikke_kvalificeres",
                              "fejl": str(e)})
            continue
        for qty in P2_STOERRELSER:
            for side in ("BUY", "SELL"):
                r = await whatif(ib, c, MarketOrder(side, qty), f)
                im = r["felter"].get("initMarginChange")
                kurs = kurser.get(par[:3])
                notional_usd = round(qty * kurs, 2) if kurs else MANGLER
                gearing = margin_pct = dom = MANGLER
                if im and notional_usd:
                    try:
                        m = abs(float(im))
                        if m:
                            gearing = round(notional_usd / m, 1)
                            margin_pct = round(100 * m / notional_usd, 2)
                    except (ValueError, ZeroDivisionError):
                        pass
                forv = esma_forventning(par)
                if gearing is not MANGLER:
                    # ⚠ Loftet er et loft. Mere margin end reglen = IBKR's egen
                    # husmargin (normalt). MINDRE = en af antagelserne er gal.
                    if gearing <= forv["forventet_gearing"] * 1.02:
                        dom = ("som ventet" if gearing >= forv["forventet_gearing"] * 0.95
                               else "strammere end ESMA-loftet (husmargin)")
                    else:
                        dom = "⚠ HOEJERE END ESMA-LOFTET — antagelse gal"
                maalinger.append({
                    "par": par, "side": side, "enheder": qty,
                    "basiskurs_usd": kurs, "notional_usd": notional_usd,
                    "status": r["status"], "fejl": r["fejl"],
                    "initMarginChange": im,
                    "maintMarginChange": r["felter"].get("maintMarginChange"),
                    "commission": r["felter"].get("commission"),
                    "commissionCurrency": r["felter"].get("commissionCurrency"),
                    "warningText": r["felter"].get("warningText"),
                    "implicit_gearing": gearing,
                    "margin_pct": margin_pct,
                    "esma": forv,
                    "dom": dom,
                })
                print(f"  {par} {side} {qty:>7}  {r['status']:16} "
                      f"initM={v(im)}  gearing={v(gearing)}:1 "
                      f"({v(margin_pct)} %)  forventet {forv['forventet_gearing']}:1"
                      f"  {v(dom)}")

    # CFD-sammenligningen (spec P2 punkt 5)
    cfd_ud = []
    cfd = Contract(symbol="EUR", secType="CFD", currency="USD", exchange="SMART")
    try:
        await ib.qualifyContractsAsync(cfd)
        for qty in P2_STOERRELSER:
            r = await whatif(ib, cfd, MarketOrder("BUY", qty), f)
            cfd_ud.append({"enheder": qty, "status": r["status"], "fejl": r["fejl"],
                           "initMarginChange": r["felter"].get("initMarginChange"),
                           "commission": r["felter"].get("commission")})
            print(f"  CFD EUR.USD BUY {qty:>7}  {r['status']}")
    except Exception as e:
        cfd_ud.append({"status": "kunne_ikke_kvalificeres", "fejl": str(e)})

    maalt = [m for m in maalinger if m["status"] == "maalt"]

    # ⚠ TO FORSKELLIGE TOMME SVAR, OG DE BETYDER IKKE DET SAMME.
    #   tomt_uden_fejl  -> apparatet kunne ikke regne (lukket marked)
    #   afvist          -> IBKR sagde aktivt nej, med en begrundelse
    # Et apparat der er nede forklarer det FOERSTE. Det forklarer ikke en
    # eksplicit, konsistent afvisning — den baerer information uanset.
    afvist = [m for m in maalinger if m["status"] == "afvist"]
    koder = {fe["kode"] for m in afvist for fe in (m["fejl"] or [])}
    tekster = {fe["tekst"] for m in afvist for fe in (m["fejl"] or [])}
    ensartet = len(afvist) == len([m for m in maalinger if m["status"] != "maalt"]) \
        and len(koder) == 1

    if maalt:
        eur = [m for m in maalt if m["par"] == "EURUSD" and m["side"] == "BUY"]
        svar = (f"EURUSD BUY: {eur[0]['implicit_gearing']}:1 "
                f"({eur[0]['margin_pct']} % margin) — {eur[0]['dom']}" if eur else
                f"{len(maalt)} maalinger lykkedes; se tabellen.")
    elif afvist and ensartet:
        svar = (f"ALLE {len(afvist)} spot-FX-kald AFVIST med samme kode {koder}: "
                f"{'; '.join(sorted(tekster))[:160]}. "
                "⚠ Det er en aktiv afvisning, ikke et tomt svar — den staar selv "
                "om apparatet er nede. Men den skal BEKRAEFTES med aabent marked, "
                "foer den bogfoeres som 'kontoen tillader ikke gearet spot FX'.")
    elif afvist:
        svar = (f"{len(afvist)} kald afvist, resten tomt. Blandet billede — "
                "se tabellen og koer igen med aabent marked.")
    elif not app["apparatet_virker"]:
        svar = ("KAN IKKE BESVARES. whatIf svarede ikke paa kontrolinstrumentet "
                "MES heller — apparatet er nede (sandsynligvis lukket marked). "
                "Et tomt FX-svar er derfor ikke et udsagn om FX.")
    print(f"\n  → {v(svar)}")

    return {"apparat_kontrol": app, "spot_fx": maalinger, "forex_cfd": cfd_ud,
            "gearingssvar": svar,
            "note_paper_vs_live": ("Paper-konti bruger ikke altid samme marginmodel "
                                   "som live. En gearing maalt her er ikke "
                                   "noedvendigvis den der gaelder med rigtige penge.")}


# ── P3 · positionsrepraesentationen ────────────────────────────────────────
async def snapshot(ib: IB) -> dict:
    await ib.reqAccountSummaryAsync()
    return {
        "tid": datetime.now(timezone.utc).isoformat(),
        "positions": [{"konto": p.account, "symbol": p.contract.symbol,
                       "secType": p.contract.secType, "antal": p.position,
                       "avgCost": p.avgCost} for p in ib.positions()],
        "portfolio": [{"symbol": i.contract.symbol, "secType": i.contract.secType,
                       "antal": i.position, "marketValue": i.marketValue,
                       "unrealizedPNL": i.unrealizedPNL} for i in ib.portfolio()],
        "CashBalance": {av.currency: av.value for av in ib.accountValues()
                        if av.tag == "CashBalance"},
        "ExchangeRate": {av.currency: av.value for av in ib.accountValues()
                         if av.tag == "ExchangeRate"},
        "FxCashBalance": {av.currency: av.value for av in ib.accountValues()
                          if av.tag == "FxCashBalance"},
        "NetLiquidationByCurrency": {av.currency: av.value for av in ib.accountValues()
                                     if av.tag == "NetLiquidationByCurrency"},
    }


async def _vent_paa_slut(handel, sekunder: int = 60) -> bool:
    """S5: Submitted er ikke Filled. Returnerer True kun ved verificeret fill."""
    for _ in range(sekunder):
        await asyncio.sleep(1)
        if handel.orderStatus.status == "Filled":
            return True
        if handel.orderStatus.status in ("Cancelled", "Inactive", "ApiCancelled"):
            return False
    return False


async def _annuller_og_verificer(ib: IB, handel, sekunder: int = 20) -> dict:
    """⚠ EN ORDRE DER IKKE FYLDTE SKAL VAEK, IKKE BARE FORLADES.

    En markedsordre lagt paa et lukket marked staar 'PreSubmitted' og fylder
    naar markedet aabner — timer senere, uden nogen til at lukke den. Scriptet
    ville forlaenge sig selv til en ejerloes position. Det er samme fejlklasse
    som over-salget 31-07: en ordre man troede var faerdig, fordi man holdt op
    med at kigge.
    """
    if handel.orderStatus.status in ("Filled", "Cancelled", "ApiCancelled"):
        return {"handling": "ingen", "slutstatus": handel.orderStatus.status}
    ib.cancelOrder(handel.order)
    for _ in range(sekunder):
        await asyncio.sleep(1)
        if handel.orderStatus.status in ("Cancelled", "ApiCancelled", "Filled"):
            break
    return {"handling": "annulleret", "slutstatus": handel.orderStatus.status,
            "⚠": (None if handel.orderStatus.status in ("Cancelled", "ApiCancelled")
                  else "ORDREN ER IKKE BEKRAEFTET ANNULLERET — tjek TWS MANUELT")}


def _markedet_er_aabent(detaljer, nu: datetime | None = None) -> tuple[bool, str]:
    """⚠ P3 MAA IKKE LAEGGE EN ORDRE IND I ET LUKKET MARKED.

    Parser tradingHours i kontraktens EGEN tidszone. Kan tiden ikke afgoeres,
    svares NEJ — en usikker aabningstid maa ikke blive til en ordre.
    """
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(detaljer.timeZoneId)
    except Exception as e:
        return False, f"kunne ikke afgoere tidszone ({detaljer.timeZoneId}): {e}"
    nu = nu or datetime.now(tz)
    for blok in (detaljer.tradingHours or "").split(";"):
        if not blok or blok.endswith("CLOSED") or "-" not in blok:
            continue
        try:
            a, b = blok.split("-")
            start = datetime.strptime(a, "%Y%m%d:%H%M").replace(tzinfo=tz)
            slut = datetime.strptime(b, "%Y%m%d:%H%M").replace(tzinfo=tz)
        except ValueError:
            continue
        if start <= nu <= slut:
            return True, f"aabent ({blok}, {detaljer.timeZoneId})"
    naeste = next((b for b in (detaljer.tradingHours or "").split(";")
                   if b and not b.endswith("CLOSED") and "-" in b), "—")
    return False, (f"LUKKET. Nu er {nu:%Y-%m-%d %H:%M} {detaljer.timeZoneId}; "
                   f"foerste blok i tradingHours er {naeste}")


async def p3(ib: IB, f: Fejlopsamler, tillad_ordre: bool) -> dict:
    if not tillad_ordre:
        return {"status": "sprunget_over",
                "grund": "S4: P3 kraever --tillad-ordre (defaulter til False)",
                "udgangspunkt": await snapshot(ib)}

    par = "EURUSD"
    c = Forex(par)
    await ib.qualifyContractsAsync(c)
    qty = 25_000    # IDEALPRO-minimum jf. websted; minSize fra API er lavere

    # ⚠ AABENT MARKED ER EN FORUDSAETNING, IKKE EN OMSTAENDIGHED.
    # Uden denne port ville en markedsordre paa et lukket marked staa i koe og
    # fylde ved aabning — timer senere, uden opsyn.
    cds = await ib.reqContractDetailsAsync(c)
    if not cds:
        return {"status": "afbrudt", "grund": "ingen ContractDetails for EURUSD"}
    aabent, forklaring = _markedet_er_aabent(cds[0])
    print(f"  marked: {forklaring}")
    if not aabent:
        return {"status": "afbrudt_marked_lukket", "grund": forklaring,
                "note": ("P3 laegger en AEGTE ordre. Paa et lukket marked ville "
                         "den staa PreSubmitted og fylde ved aabning uden at "
                         "nogen lukkede den igen. Koer efter FX-aabning."),
                "udgangspunkt": await snapshot(ib)}

    trin = {"1_foer": await snapshot(ib)}
    print(f"  snapshot foer: positions={len(trin['1_foer']['positions'])}")

    # ── Aabn ────────────────────────────────────────────────────────────────
    f.nulstil()
    ordre = MarketOrder("BUY", qty)
    assert ordre.whatIf is False, "P3 er den ENESTE aegte ordre — og kun her"
    handel = ib.placeOrder(c, ordre)
    fyldt = await _vent_paa_slut(handel)
    print(f"  aabn: status={handel.orderStatus.status} fyldt={fyldt} "
          f"filled={handel.orderStatus.filled}")
    trin["2_efter_koeb"] = await snapshot(ib)
    trin["2_ordrestatus"] = {"status": handel.orderStatus.status,
                             "filled": handel.orderStatus.filled,
                             "avgFillPrice": handel.orderStatus.avgFillPrice,
                             "fejl": [{"kode": k, "tekst": m} for k, m in f.relevante()]}

    # ── Luk i SAMME koersel (S5) ───────────────────────────────────────────
    if fyldt:
        f.nulstil()
        luk = ib.placeOrder(c, MarketOrder("SELL", qty))
        lukket = await _vent_paa_slut(luk)
        if not lukket:
            # ⚠ Lukkeordren fyldte ikke. Annuller den, saa den ikke ligger og
            # venter — og RAAB OP. Her er der en aaben position tilbage.
            trin["3_annullering"] = await _annuller_og_verificer(ib, luk)
            print("  ⚠⚠ LUKKEORDREN FYLDTE IKKE — der kan staa en AABEN "
                  "position paa EURUSD. TJEK TWS.")
        print(f"  luk: status={luk.orderStatus.status} fyldt={lukket}")
        trin["3_efter_luk"] = await snapshot(ib)
        trin["3_ordrestatus"] = {"status": luk.orderStatus.status,
                                 "filled": luk.orderStatus.filled,
                                 "lukket_verificeret": lukket,
                                 "fejl": [{"kode": k, "tekst": m} for k, m in f.relevante()]}
    else:
        # ⚠ Koebet fyldte ikke. Ordren skal VAEK — ikke bare forlades.
        trin["2_annullering"] = await _annuller_og_verificer(ib, handel)
        print(f"  koeb fyldte ikke -> {trin['2_annullering']}")
        trin["3_efter_luk"] = await snapshot(ib)

    def diff(a: dict, b: dict) -> dict:
        ud: dict = {}
        for felt in ("CashBalance", "ExchangeRate", "FxCashBalance",
                     "NetLiquidationByCurrency"):
            for k in set(a.get(felt, {})) | set(b.get(felt, {})):
                f0, f1 = a.get(felt, {}).get(k), b.get(felt, {}).get(k)
                if f0 != f1:
                    ud[f"{felt}.{k}"] = {"foer": f0, "efter": f1}
        return ud

    # ⚠ SLUTKONTROL, UANSET HVILKEN VEJ VI KOM HERTIL.
    # Ingen efterladte ordrer, ingen efterladt eksponering. Kontrollen spoerger
    # BROKEREN, ikke vores egen forestilling om hvad der skete.
    await asyncio.sleep(2)
    aabne = [o for o in await ib.reqAllOpenOrdersAsync()
             if o.contract.secType == "CASH"]
    slutkontrol = {
        "aabne_fx_ordrer": [{"symbol": o.contract.symbol + o.contract.currency,
                             "action": o.order.action, "antal": o.order.totalQuantity,
                             "status": o.orderStatus.status} for o in aabne],
        "rene_boeger": not aabne,
    }
    if aabne:
        print(f"  ⚠⚠ {len(aabne)} AABEN FX-ORDRE TILBAGE EFTER KOERSEL — TJEK TWS")
    else:
        print("  slutkontrol: ingen aabne FX-ordrer tilbage")

    efter_koeb = trin.get("2_efter_koeb", {})
    ud = {
        "status": "koert", "par": par, "enheder": qty, "fyldt": fyldt,
        "slutkontrol": slutkontrol,
        "trin": trin,
        "aendringer_ved_koeb": diff(trin["1_foer"], efter_koeb),
        "fx_i_positions": any(p["secType"] == "CASH"
                              for p in efter_koeb.get("positions", [])),
        "fx_i_portfolio": any(p["secType"] == "CASH"
                              for p in efter_koeb.get("portfolio", [])),
        "flad_bagefter": len(trin.get("3_efter_luk", {}).get("positions", [])) == 0,
    }
    ud["reconcile_svar"] = (
        "FX optraadte IKKE i positions() — reconcile kan ikke afstemme FX mod "
        "positions(). Eksponeringen skal laeses af valutabalancerne "
        "(CashBalance/FxCashBalance pr. valuta). Det er et DESIGNKRAV til "
        "execution, ikke en detalje."
        if not ud["fx_i_positions"] else
        "FX optraadte i positions() — undersoeg om det er reelt eller virtuelt.")
    print(f"  → {ud['reconcile_svar']}")
    return ud


# ── P4 · tick-gitter og pipvaerdi ──────────────────────────────────────────
async def p4(ib: IB, f: Fejlopsamler) -> dict:
    ud: dict = {}
    ib.reqMarketDataType(3)                 # 3 = forsinket, virker uden abonnement
    for par in FX_ALLE:
        c = Forex(par)
        try:
            cds = await ib.reqContractDetailsAsync(c)
            if not cds:
                ud[par] = {"status": "ikke_handelbar"}
                continue
            mt = cds[0].minTick
            await ib.qualifyContractsAsync(c)
        except Exception as e:
            ud[par] = {"status": "fejl", "fejl": str(e)}
            continue

        t = ib.reqMktData(c, "", True, False)
        for _ in range(12):
            await asyncio.sleep(0.5)
            if t.bid == t.bid and t.ask == t.ask:      # ikke-NaN
                break
        kurs = MANGLER
        for kandidat in (t.midpoint(), t.last, t.close):
            if kandidat == kandidat and kandidat:      # ikke NaN, ikke 0
                kurs = float(kandidat)
                break
        ib.cancelMktData(c)

        pip = pip_stoerrelse(par)
        kvot = par[3:]
        post = {
            "minTick": mt,
            "pip": pip,
            "ticks_pr_pip": round(pip / mt, 3) if mt else MANGLER,
            "kvoteringsvaluta": kvot,
            "pipvaerdi_fast_i_usd": kvot == "USD",
            "kurs": kurs,
            "pipvaerdi_25k": MANGLER,
            "pipvaerdi_100k": MANGLER,
        }
        # USD-kvoteret: pipvaerdien er fast i USD og uafhaengig af kursen.
        # Alt andet flyder og kraever et kryds mod USD — det maa ikke gaettes.
        if kvot == "USD":
            post["pipvaerdi_25k"] = round(25_000 * pip, 4)
            post["pipvaerdi_100k"] = round(100_000 * pip, 4)
        else:
            post["pipvaerdi_note"] = (
                f"flydende — kvoteret i {kvot}, kraever {kvot}USD-kurs for "
                "USD-vaerdi. Ikke beregnet her; skal hentes samtidig.")
        ud[par] = post
        print(f"  {par:7} minTick={mt:<9} pip={pip:<7} "
              f"ticks/pip={v(post['ticks_pr_pip']):<6} kurs={v(post['kurs'])}  "
              f"pipvaerdi/25k={v(post['pipvaerdi_25k'])}")
    return ud


# ── P5 · datadybde og -kvalitet ────────────────────────────────────────────
P5_BARER = ["1 day", "1 hour", "15 mins", "5 mins", "1 min"]
P5_SKIVE = {"1 day": "10 D", "1 hour": "5 D", "15 mins": "3 D",
            "5 mins": "2 D", "1 min": "1 D"}


async def p5(ib: IB, f: Fejlopsamler) -> dict:
    maal = [("EURUSD", Forex("EURUSD")), ("GBPUSD", Forex("GBPUSD")),
            ("USDJPY", Forex("USDJPY")), ("EURDKK", Forex("EURDKK")),
            (KONTROL_FUT[0] + " (kontrol)",
             Future(KONTROL_FUT[0], KONTROL_FUT[1], KONTROL_FUT[2], currency="USD"))]
    ud: dict = {}
    for navn, ct in maal:
        try:
            await ib.qualifyContractsAsync(ct)
        except Exception as e:
            ud[navn] = {"status": "fejl", "fejl": str(e)}
            continue

        post: dict = {"headstamp": {}, "hentninger": {}}
        for what in ("TRADES", "MIDPOINT"):
            try:
                hs = await asyncio.wait_for(
                    ib.reqHeadTimeStampAsync(ct, whatToShow=what, useRTH=False,
                                             formatDate=2), timeout=25)
                post["headstamp"][what] = str(hs) if hs else MANGLER
            except Exception as e:
                post["headstamp"][what] = f"fejl: {type(e).__name__}"

        for bar in P5_BARER:
            for what in ("TRADES", "MIDPOINT"):
                f.nulstil()
                try:
                    bars = await asyncio.wait_for(ib.reqHistoricalDataAsync(
                        ct, endDateTime="", durationStr=P5_SKIVE[bar],
                        barSizeSetting=bar, whatToShow=what, useRTH=False,
                        formatDate=2, timeout=30), timeout=45)
                except Exception as e:
                    post["hentninger"][f"{bar}|{what}"] = {
                        "status": "exception", "fejl": type(e).__name__}
                    continue
                fejl = [{"kode": k, "tekst": m} for k, m in f.relevante()]
                vol = [b.volume for b in bars] if bars else []
                s = {
                    "antal_barer": len(bars),
                    "status": ("tom_med_fejl" if not bars and fejl else
                               "TOM_UDEN_FEJL" if not bars else "ok"),
                    "fejl": fejl or MANGLER,
                    "foerste": str(bars[0].date) if bars else MANGLER,
                    "sidste": str(bars[-1].date) if bars else MANGLER,
                    # ⚠ -1 betyder "ingen volumen" hos IBKR. 0 ville betyde
                    # "ingen handel". De to maa ikke blandes sammen.
                    "volumen_alle_minus1": bool(vol) and all(x == -1 for x in vol),
                    "volumen_eksempel": vol[-1] if vol else MANGLER,
                    "stillestaaende_lukkekurser": (
                        len({b.close for b in bars}) == 1 if len(bars) > 1 else MANGLER),
                }
                post["hentninger"][f"{bar}|{what}"] = s
                print(f"  {navn:18} {bar:8} {what:9} {s['antal_barer']:>4} barer  "
                      f"{s['status']}")
                await asyncio.sleep(1.2)        # pacing
        ud[navn] = post

    ud["_hovedfund"] = {
        "trades_paa_fx": ("whatToShow=TRADES giver 0 barer paa spot FX med fejl 162 "
                          "('No historical market data for EUR/CASH@FXSUBPIP'). "
                          "MIDPOINT virker. Ethvert harvest-script skrevet til "
                          "futures vil hente TOMT paa FX."),
        "volumen": ("FX-barer melder volume = -1, ikke 0. Det er faktisk godt: -1 "
                    "kan skelnes fra 'ingen handel'. Men enhver kode der regner "
                    "paa volumen faar -1 ind som tal."),
        "aarsagstaksonomi": ("Kontraktlevetid bortfalder (ingen expiries paa spot). "
                             "Retention, harvest-parameter og vendor-onboarding "
                             "daekker resten. En FEMTE kategori er noedvendig: "
                             "'whatToShow-uforenelighed' — data findes, men kun "
                             "under et andet whatToShow. Den er ikke en "
                             "harvest-parameterfejl, for parameteren er gyldig; "
                             "den er uforenelig med aktivklassen."),
    }
    return ud


# ── P6 · handelstimer ──────────────────────────────────────────────────────
def _parse_timer(s: str) -> list[dict]:
    ud = []
    for blok in (s or "").split(";"):
        if not blok or ":" not in blok:
            continue
        if blok.endswith("CLOSED"):
            ud.append({"raa": blok, "lukket": True})
            continue
        try:
            start, slut = blok.split("-")
            ud.append({"raa": blok, "start": start, "slut": slut, "lukket": False})
        except ValueError:
            ud.append({"raa": blok, "uparset": True})
    return ud


async def p6(ib: IB, f: Fejlopsamler) -> dict:
    ud: dict = {}
    for navn, ct in [("EURUSD", Forex("EURUSD")), ("USDJPY", Forex("USDJPY")),
                     ("EURDKK", Forex("EURDKK")),
                     (KONTROL_FUT[0] + " (kontrol)",
                      Future(KONTROL_FUT[0], KONTROL_FUT[1], KONTROL_FUT[2],
                             currency="USD"))]:
        cds = await ib.reqContractDetailsAsync(ct)
        if not cds:
            ud[navn] = {"status": "ingen_kontrakt"}
            continue
        d = cds[0]
        ud[navn] = {
            "timeZoneId": d.timeZoneId,
            "tradingHours_raa": d.tradingHours,
            "liquidHours_raa": d.liquidHours,
            "tradingHours": _parse_timer(d.tradingHours),
            "liquidHours": _parse_timer(d.liquidHours),
            "tradingHours_lig_liquidHours": d.tradingHours == d.liquidHours,
        }
        print(f"  {navn:18} tz={d.timeZoneId}  "
              f"RTH==ETH: {ud[navn]['tradingHours_lig_liquidHours']}")

    ud["_fortolkning"] = {
        "handelsdag_paa_fx": ("En FX-'dag' loeber fra 17:15 ET til 17:00 ET dagen "
                              "efter — hen over midnat, med et ophold paa 15 min. "
                              "Den deler IKKE graense med et kalenderdoegn og heller "
                              "ikke med futures-dagen."),
        "kompatibilitet": ("⚠ IKKE kompatibel med projektets sessionstaelling, som "
                           "bygger paa NYSE-kalenderen og en RTH/ETH-opdeling. FX "
                           "har ingen RTH: tradingHours og liquidHours er identiske. "
                           "En sessionstaeller der spoerger "
                           "nyse_kalender.er_handelsdag() svarer forkert for FX."),
        "sommertid": ("US og EU skifter sommertid paa FORSKELLIGE datoer (typisk 1-2 "
                      "ugers forskydning i baade marts og oktober/november). Da "
                      "kontrakten er ankret i US/Eastern, forskyder de danske "
                      "klokkeslet sig i det vindue. Reel fejlkilde for enhver "
                      "scheduler der bruger faste danske tider."),
    }
    return ud


# ── P7 · omkostninger ──────────────────────────────────────────────────────
P7_VINDUER = {
    "asien":       "01:00-09:00 dansk",
    "london_aabn": "09:00-10:00 dansk",
    "london_ny":   "15:30-17:30 dansk",
    "ny_eftm":     "18:00-21:00 dansk",
    "rollover":    "22:30-23:30 dansk",   # ⚠ vigtigste maaling
}


def _percentil(xs: list[float], q: float):
    if not xs:
        return MANGLER
    s = sorted(xs)
    k = max(0, min(len(s) - 1, int(round(q * (len(s) - 1)))))
    return s[k]


async def p7(ib: IB, f: Fejlopsamler, vindue: str, minutter: int) -> dict:
    if vindue not in P7_VINDUER:
        raise SystemExit(f"--vindue skal vaere en af {sorted(P7_VINDUER)}")
    par = MAJORS + ["EURDKK"]
    ib.reqMarketDataType(3)
    tickere = {}
    for p in par:
        c = Forex(p)
        await ib.qualifyContractsAsync(c)
        tickere[p] = (c, ib.reqMktData(c, "", False, False))
    await asyncio.sleep(5)

    proever: dict[str, list] = {p: [] for p in par}
    n = int(minutter * 60 / 5)
    print(f"  sampler {n} gange a 5 s ({minutter} min) i vindue '{vindue}'")
    for i in range(n):
        await asyncio.sleep(5)
        for p, (c, t) in tickere.items():
            if t.bid == t.bid and t.ask == t.ask and t.bid and t.ask:
                proever[p].append({"tid": datetime.now(timezone.utc).isoformat(),
                                   "bid": t.bid, "ask": t.ask,
                                   "spread": round(t.ask - t.bid, 8)})
        if i % 12 == 0:
            print(f"    {i * 5:>4} s ...")

    for c, _ in tickere.values():
        ib.cancelMktData(c)

    ud: dict = {"vindue": vindue, "beskrivelse": P7_VINDUER[vindue],
                "afsluttet": datetime.now(timezone.utc).isoformat(), "par": {}}
    for p, xs in proever.items():
        pip = pip_stoerrelse(p)
        spr = [x["spread"] for x in xs]
        post = {
            "antal_proever": len(xs),
            "median_spread_pip": round(_percentil(spr, .5) / pip, 2) if spr else MANGLER,
            "p95_spread_pip": round(_percentil(spr, .95) / pip, 2) if spr else MANGLER,
            "min_spread_pip": round(min(spr) / pip, 2) if spr else MANGLER,
            "raa": xs,
        }
        ud["par"][p] = post
        print(f"  {p:7} n={len(xs):>3} median={v(post['median_spread_pip'])} pip "
              f"p95={v(post['p95_spread_pip'])} pip")
    ud["_note"] = ("Kommission hentes fra P2's whatIf. Finansiering over natten "
                   "(rentedifferential + IBKR-spread, plus 1 % for retail-"
                   "klassificerede CFD-kunder) kan ikke laeses via API — skal "
                   "slaas op manuelt i Client Portal.")
    return ud


# ── P8 · fejlklassejagt i kodebasen ────────────────────────────────────────
P8_MOENSTRE = [
    ("secType antaget FUT/STK", r'secType\s*=\s*["\'](FUT|STK|CONTFUT)["\']'),
    ("positions() som sandhed", r'\.positions\(\)'),
    ('whatToShow="TRADES" hardkodet', r'whatToShow\s*=\s*["\']TRADES["\']'),
    ("volumen antaget > 0", r'\bvolume\b\s*[><]=?\s*0|\bvolume\b\s*\*|sum\([^)]*volume'),
    ("USD_PER_POINT-agtige konstanter",
     r'(USD_PER_POINT|DOLLAR_PR_POINT|POINT_VALUE|MULTIPLIER|PUNKT_VAERDI)\s*='),
    ("RTH/ETH som meningsfuld", r'useRTH\s*=\s*(True|1)'),
    ("NYSE-kalender som DEN kalender", r'nyse_kalender|er_handelsdag'),
    ("tick-afrunding med futures-gitter",
     r'(round_to_tick|afrund_tick|tick_size|TICK_SIZE|minTick)'),
]


def p8(rod: Path) -> dict:
    ud: dict = {}
    filer = [p for p in rod.rglob("*.py")
             if "venv" not in p.parts and "_archive" not in p.parts
             and "site-packages" not in p.parts and p.name != "fx_probe.py"]
    for navn, moenster in P8_MOENSTRE:
        rx = re.compile(moenster)
        traef = []
        for fil in filer:
            try:
                linjer = fil.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for n, linje in enumerate(linjer, 1):
                if rx.search(linje):
                    traef.append({"fil": str(fil.relative_to(rod)), "linje": n,
                                  "kode": linje.strip()[:160]})
        ud[navn] = {"antal": len(traef), "traef": traef[:60],
                    "afkortet": len(traef) > 60}
        print(f"  {navn:38} {len(traef):>4} traef")
    ud["_note"] = ("Fund, ikke rettelser. Specen §P8: 'Rapportér fund — ret ikke "
                   "uden aftale.' Et traef er ikke i sig selv en fejl; det er et "
                   "sted hvor en FX-antagelse ville briste.")
    return ud


# ── Koersel ────────────────────────────────────────────────────────────────
async def koer(args) -> None:
    punkter = ([p.strip().upper() for p in args.kun.split(",")] if args.kun
               else ["P0", "P1", "P2", "P4", "P5", "P6", "P8"])
    resultat: dict = {}
    rod = Path(__file__).parent

    if "P8" in punkter:
        print("\n=== P8 · fejlklassejagt i kodebasen (ingen IBKR) ===")
        resultat["P8"] = p8(rod)
        skriv("P8_kodebase", resultat["P8"])

    if punkter == ["P8"]:
        print(f"\nskrevet til {UD}")
        return

    ib, f = await forbind(args.port)
    try:
        if "P0" in punkter:
            print("\n=== P0 · konto og klassifikation ===")
            resultat["P0"] = await p0(ib, f)
            skriv("P0_konto", resultat["P0"])
        if "P1" in punkter:
            print("\n=== P1 · kontraktfakta ===")
            resultat["P1"] = await p1(ib, f)
            skriv("P1_kontrakter", resultat["P1"])
        if "P2" in punkter:
            print("\n=== P2 · gearingsmaalingen ===")
            resultat["P2"] = await p2(ib, f)
            skriv("P2_gearing", resultat["P2"])
        if "P3" in punkter:
            print("\n=== P3 · positionsrepraesentation ===")
            resultat["P3"] = await p3(ib, f, args.tillad_ordre)
            skriv("P3_position", resultat["P3"])
        if "P4" in punkter:
            print("\n=== P4 · tick-gitter og pipvaerdi ===")
            resultat["P4"] = await p4(ib, f)
            skriv("P4_tick_pip", resultat["P4"])
        if "P5" in punkter:
            print("\n=== P5 · datadybde og -kvalitet ===")
            resultat["P5"] = await p5(ib, f)
            skriv("P5_data", resultat["P5"])
        if "P6" in punkter:
            print("\n=== P6 · handelstimer ===")
            resultat["P6"] = await p6(ib, f)
            skriv("P6_handelstimer", resultat["P6"])
        if "P7" in punkter:
            print("\n=== P7 · omkostninger (spread-sampling) ===")
            resultat["P7"] = await p7(ib, f, args.vindue, args.minutter)
            skriv(f"P7_spread_{args.vindue}", resultat["P7"])
    finally:
        ib.disconnect()

    skriv("_koersel", {"tid": datetime.now(timezone.utc).isoformat(),
                       "punkter": punkter, "port": args.port,
                       "tillad_ordre": args.tillad_ordre,
                       "punkter_med_data": sorted(resultat)})
    print(f"\nskrevet til {UD}")


def main() -> None:
    ap = argparse.ArgumentParser(description="FX-probe mod IBKR — kun paper, kun viden.")
    ap.add_argument("--kun", default="", help="fx P0,P1,P2 (default: alle uden P3/P7)")
    ap.add_argument("--port", type=int, default=7497,
                    help=f"paper-port, en af {sorted(PAPER_PORTE)} (default 7497)")
    ap.add_argument("--tillad-ordre", dest="tillad_ordre", action="store_true",
                    default=False, help="S4: kraeves for P3's aegte paper-ordre")
    ap.add_argument("--vindue", default="london_ny", help=f"P7: {sorted(P7_VINDUER)}")
    ap.add_argument("--minutter", type=int, default=10, help="P7: sampling-laengde")
    args = ap.parse_args()
    asyncio.run(koer(args))


if __name__ == "__main__":
    main()
