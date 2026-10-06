"""
probe_nt_exit_ordrer.py — kan NT8 overhovedet det exit-ordrerne kræver?
════════════════════════════════════════════════════════════════════════════════
Trin 1 i SPEC_exit_ordrer_ninjatrader.md. Bygger INGEN funktionalitet — den
svarer på syv spørgsmål, så afsnit 6.3 kan skrives ud fra målinger i stedet for
antagelser.

To ting i OIF-grænsefladen er aldrig blevet afprøvet:

  · **OCO-feltet** (felt 10) har stået tomt siden vejen blev bygget.
  · **CHANGE** er aldrig sendt. Kun PLACE (16-09) og CANCEL (26-09) er verificeret.

Hele trailing-stoppet hviler på CHANGE, fordi OIF ikke har nogen
trailing-ordretype. Og OCO er den eneste sikre måde at undgå at et stop loss og
et trailing stop begge fylder ved et skarpt fald og dermed vender positionen.

⚠ DEN LÆGGER RIGTIGE ORDRER. På DEMO8580770 — Tradovates demo, rigtige kurser,
legetøjspenge. Scriptet nægter at køre mod enhver anden konto, og det rydder op
efter hvert scenarie og til sidst igen.

    python probe_nt_exit_ordrer.py              # alle syv
    python probe_nt_exit_ordrer.py --kun P1,P2  # udvalgte
    python probe_nt_exit_ordrer.py --toer       # vis hvad der ville ske, send intet
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import nt_forbindelse as NT

# ⚠ KUN DENNE KONTO. Ibens DEMO8635291 ligger på hendes maskine, og live
# 2080414 må aldrig se en probe.
KONTO = "DEMO8580770"
SYMBOL = "MES"
TICK = 0.25
RAPPORT = "probe_nt_exit_ordrer_rapport.md"

fund: list[dict] = []
toer = False


def log(s: str = "") -> None:
    print(s)


def noter(nr: str, spm: str, svar: str, bevis: list[str], note: str = "") -> None:
    fund.append({"nr": nr, "spm": spm, "svar": svar, "bevis": bevis, "note": note})
    log(f"\n  ══ {nr}: {svar} ══")
    if note:
        log(f"     {note}")


def tik(x: float) -> float:
    """Nærmeste hele tick. MES handler i 0,25."""
    return round(round(x / TICK) * TICK, 2)


def instrument() -> str:
    """NT8-navnet for frontkontrakten.

    ⚠ HENTES, GÆTTES IKKE. `nt_instrument()` kræver en kvalificeret IBKR-kontrakt
    netop for ikke at gætte måneden — MESU6 udløb 18-09, og en konstant skrevet i
    august ville have virket i seks uger og derefter været tavst forkert.
    Backendens /futures/katalog kender den rigtige.
    """
    try:
        with urllib.request.urlopen(
                "http://127.0.0.1:8000/futures/katalog", timeout=10) as r:
            d = json.loads(r.read())
    except Exception as e:
        raise SystemExit(
            f"  ⚠ Kunne ikke hente kontraktmåneden fra backenden ({e}).\n"
            f"    Start den (uvicorn main:app --host 0.0.0.0) eller angiv\n"
            f"    --instrument \"MES 12-26\" manuelt.")
    for post in (d.get("futures") or d.get("katalog") or []):
        if post.get("symbol") != SYMBOL:
            continue
        k = post.get("kontrakt")
        if not k:
            raise SystemExit("  ⚠ Backenden kender MES, men har ingen kontrakt "
                             "— er IBKR forbundet?")

        class _K:
            lastTradeDateOrContractMonth = k.get("udloeb", "")
            localSymbol = k.get("local_symbol", "")
        return NT.nt_instrument(SYMBOL, _K())
    raise SystemExit(f"  ⚠ {SYMBOL} findes ikke i futures-kataloget.")


# ══════════════════════════════════════════════════════════════════════════
# Byggeklodser
# ══════════════════════════════════════════════════════════════════════════
def pos(instr: str) -> int | None:
    return NT.position(instr, KONTO)["netto"]


def send(instr: str, action: str, antal: int, **kw) -> tuple[str, dict]:
    ref = NT.order_ref()
    if toer:
        log(f"     [tør] {action} {antal} {instr} {kw} id={ref}")
        return ref, {"logliner": []}
    svar = NT.send_ordre(konto=KONTO, instrument=instr, action=action,
                         antal=antal, ordre_id=ref, **kw)
    for l in (svar.get("logliner") or [])[:3]:
        log(f"     NT8: {str(l)[:150]}")
    if svar.get("fil_tilbage"):
        log(f"     ⚠ OIF-fil blev liggende: {svar['fil_tilbage']}")
    return ref, svar


def marked(instr: str, action: str, antal: int) -> tuple[str, float]:
    """Markedsordre, og fyldprisen tilbage. Den er vores kurskilde."""
    ref, _ = send(instr, action, antal, ordretype="MARKET")
    if toer:
        return ref, 0.0
    r = NT.afvent_ordre(ref, 20.0)
    log(f"     {action} {antal}: {r['status']} {r['filled']} @ {r['avg_fill']}")
    return ref, r["avg_fill"]


def ryd_op(instr: str, hvorfor: str) -> bool:
    """Annullér alt og bliv flad. Køres efter hvert scenarie."""
    log(f"\n  ── oprydning ({hvorfor}) ──")
    if toer:
        return True
    try:
        NT.annuller_alt(KONTO)
    except Exception as e:
        log(f"     ⚠ annuller_alt fejlede: {e}")
    time.sleep(2)
    n = pos(instr)
    if n is None:
        log("     ⚠ positionen er UKENDT — tjek NT8's Positions-fane manuelt")
        return False
    if n != 0:
        log(f"     position {n} -> lukker med markedsordre")
        marked(instr, "BUY" if n < 0 else "SELL", abs(n))
        time.sleep(2)
        n = pos(instr)
    log(f"     position nu: {n}")
    return n == 0


# ══════════════════════════════════════════════════════════════════════════
# Scenarierne
# ══════════════════════════════════════════════════════════════════════════
def P1(instr: str) -> dict:
    """Accepterer NT8 OCO via OIF? Ses begge som Working?"""
    log("\n╔═ P1 · long 1 MES + STOPMARKET og LIMIT med samme OCO-id ═══════════")
    _, fyld = marked(instr, "BUY", 1)
    if not toer and not fyld:
        return {"svar": "UKLART", "bevis": ["markedsordren fyldte ikke — ingen kurs at regne ud fra"]}

    oco = "TDOCO" + str(int(time.time()))[-7:]
    stop_pris = tik(fyld - 40 * TICK)     # 10 point under
    limit_pris = tik(fyld + 40 * TICK)    # 10 point over
    log(f"     fyld={fyld}  stop={stop_pris}  limit={limit_pris}  oco={oco}")

    s_id, _ = send(instr, "SELL", 1, ordretype="STOPMARKET",
                   stop=stop_pris, oco=oco)
    t_id, _ = send(instr, "SELL", 1, ordretype="LIMIT",
                   limit=limit_pris, oco=oco)
    if toer:
        return {"svar": "(tør)", "bevis": []}

    rs = NT.afvent_aktiv(s_id, 12.0)
    rt = NT.afvent_aktiv(t_id, 12.0)
    bevis = [f"STOPMARKET {s_id}: status={rs['status'] or '(ikke set)'}",
             f"LIMIT      {t_id}: status={rt['status'] or '(ikke set)'}"]
    for b in bevis:
        log(f"     {b}")

    if rs["aktiv"] and rt["aktiv"]:
        svar = "JA"
    elif rs["terminal"] or rt["terminal"]:
        svar = "NEJ"
    else:
        svar = "UKLART"
    return {"svar": svar, "bevis": bevis, "oco": oco,
            "stop_id": s_id, "tprof_id": t_id,
            "stop_pris": stop_pris, "fyld": fyld}


def P2(instr: str, st: dict) -> dict:
    """Virker CHANGE? Beholder ordren sit id?"""
    log("\n╔═ P2 · flyt stoppen 4 ticks op med CHANGE ══════════════════════════")
    s_id, gammel = st.get("stop_id"), st.get("stop_pris")
    if not s_id or gammel is None:
        return {"svar": "SPRUNGET OVER", "bevis": ["P1 gav ingen aktiv stop"]}
    ny = tik(gammel + 4 * TICK)
    log(f"     {gammel} -> {ny}")
    if toer:
        log(f"     [tør] CHANGE stop paa {s_id}")
        return {"svar": "(tør)", "bevis": []}

    svar = NT.aendr(s_id, stop=ny)
    for l in (svar.get("logliner") or [])[:4]:
        log(f"     NT8: {str(l)[:150]}")
    time.sleep(2)
    status = NT.ordre_status(s_id)
    bevis = [f"logliner: {len(svar.get('logliner') or [])}",
             f"status efter CHANGE: {status or '(ikke set)'}",
             *[str(l)[:150] for l in (svar.get("logliner") or [])[:4]]]
    # ⚠ Loggen er beviset. ATI melder ikke stopprisen, kun status — så en
    # uændret "Working" betyder ikke at flytningen skete.
    linjer = " ".join(svar.get("logliner") or [])
    if "Change" in linjer or "change" in linjer:
        resultat = "JA" if status in NT.LEVENDE or status == "" else "UKLART"
    elif not (svar.get("logliner") or []):
        resultat = "UKLART"
        bevis.append("⚠ NT8 skrev ingen loglinje — hverken accept eller afvisning")
    else:
        resultat = "UKLART"
    return {"svar": resultat, "bevis": bevis, "ny_stop": ny,
            "note": "Bekræft i NT8's Orders-fane at stopprisen FAKTISK står på "
                    f"{ny} — ATI melder ikke prisen, kun status."}


def P3(instr: str, st: dict) -> dict:
    """Kan man tilføje en tredje ordre til en levende OCO-gruppe?"""
    log("\n╔═ P3 · tredje STOPMARKET paa samme OCO-id ══════════════════════════")
    oco = st.get("oco")
    fyld = st.get("fyld")
    if not oco or not fyld:
        return {"svar": "SPRUNGET OVER", "bevis": ["P1 gav ingen OCO-gruppe"]}
    pris = tik(fyld - 60 * TICK)
    x_id, _ = send(instr, "SELL", 1, ordretype="STOPMARKET", stop=pris, oco=oco)
    if toer:
        return {"svar": "(tør)", "bevis": []}
    r = NT.afvent_aktiv(x_id, 12.0)
    bevis = [f"tredje ordre {x_id}: status={r['status'] or '(ikke set)'}"]
    log(f"     {bevis[0]}")
    return {"svar": "JA" if r["aktiv"] else ("NEJ" if r["terminal"] else "UKLART"),
            "bevis": bevis, "tredje_id": x_id}


def P4(instr: str, st: dict, st3: dict) -> dict:
    """⚠ Kaskaderer en annullering til resten af OCO-gruppen?"""
    log("\n╔═ P4 · annullér ÉN ordre i gruppen ════════════════════════════════")
    s_id, t_id = st.get("stop_id"), st.get("tprof_id")
    x_id = st3.get("tredje_id")
    if not s_id or not t_id:
        return {"svar": "SPRUNGET OVER", "bevis": ["ingen gruppe at annullere i"]}
    log(f"     annullerer KUN {s_id}")
    if toer:
        return {"svar": "(tør)", "bevis": []}

    NT.annuller(s_id)
    time.sleep(3)
    raa = NT._laes_raat(6.0)
    res = {i: NT.ordre_status(i, raa) for i in (s_id, t_id, x_id) if i}
    bevis = [f"{i}: {s or '(ikke set)'}" for i, s in res.items()]
    for b in bevis:
        log(f"     {b}")

    soeskende = [s for i, s in res.items() if i != s_id]
    if all(s == "Cancelled" for s in soeskende if s):
        svar = "KASKADERER"
    elif any(s in NT.LEVENDE for s in soeskende):
        svar = "NEJ — søskende lever videre"
    else:
        svar = "UKLART"
    return {"svar": svar, "bevis": bevis,
            "note": "⚠ Dette styrer afsnit 6.3: kaskaderer den, skal backend "
                    "genlægge de resterende med et NYT OCO-id hver gang Iben "
                    "sletter én exit-ordre."}


def P5(instr: str) -> dict:
    """Annullerer NT8 selv søsteren når den ene fylder?"""
    log("\n╔═ P5 · flyt LIMIT ind i markedet saa den fylder ════════════════════")
    _, fyld = marked(instr, "BUY", 1)
    if not toer and not fyld:
        return {"svar": "UKLART", "bevis": ["kunne ikke åbne position"]}
    oco = "TDOCO" + str(int(time.time()))[-7:]
    s_id, _ = send(instr, "SELL", 1, ordretype="STOPMARKET",
                   stop=tik(fyld - 40 * TICK), oco=oco)
    t_id, _ = send(instr, "SELL", 1, ordretype="LIMIT",
                   limit=tik(fyld + 40 * TICK), oco=oco)
    if toer:
        return {"svar": "(tør)", "bevis": []}
    NT.afvent_aktiv(s_id, 10.0)
    NT.afvent_aktiv(t_id, 10.0)

    # ⚠ Flyt limit UNDER markedet — en sælg-limit under kurs fylder straks.
    ny = tik(fyld - 8 * TICK)
    log(f"     flytter LIMIT til {ny} (under kurs) saa den fylder")
    NT.aendr(t_id, limit=ny)
    r = NT.afvent_ordre(t_id, 20.0)
    time.sleep(3)
    raa = NT._laes_raat(6.0)
    s_status = NT.ordre_status(s_id, raa)
    bevis = [f"LIMIT {t_id}: {r['status'] or '(ikke set)'} "
             f"{r['filled']} @ {r['avg_fill']}",
             f"STOP  {s_id}: {s_status or '(ikke set)'}"]
    for b in bevis:
        log(f"     {b}")

    if r["status"] == "Filled" and s_status == "Cancelled":
        svar = "JA"
    elif r["status"] == "Filled" and s_status in NT.LEVENDE:
        svar = "NEJ — stoppen lever efter at limiten fyldte"
    else:
        svar = "UKLART"
    return {"svar": svar, "bevis": bevis,
            "note": "⚠ 'NEJ' betyder at en efterladt stop kan åbne en NY "
                    "position i modsat retning. Backend skal så selv annullere "
                    "søstrene ved fyld."}


def P6(instr: str) -> dict:
    """Virker kvantitetsændring via CHANGE?"""
    log("\n╔═ P6 · CHANGE antal fra 1 til 2 ═══════════════════════════════════")
    _, fyld = marked(instr, "BUY", 2)
    if not toer and not fyld:
        return {"svar": "UKLART", "bevis": ["kunne ikke åbne position"]}
    s_id, _ = send(instr, "SELL", 1, ordretype="STOPMARKET",
                   stop=tik(fyld - 40 * TICK))
    if toer:
        return {"svar": "(tør)", "bevis": []}
    NT.afvent_aktiv(s_id, 12.0)
    svar = NT.aendr(s_id, antal=2)
    for l in (svar.get("logliner") or [])[:4]:
        log(f"     NT8: {str(l)[:150]}")
    time.sleep(2)
    status = NT.ordre_status(s_id)
    bevis = [f"status efter CHANGE antal: {status or '(ikke set)'}",
             *[str(l)[:150] for l in (svar.get("logliner") or [])[:4]]]
    return {"svar": "UKLART" if not (svar.get("logliner") or []) else "SE BEVIS",
            "bevis": bevis,
            "note": "Bekræft i NT8's Orders-fane at Quantity står på 2."}


def P7(instr: str, brugt_oco: str | None) -> dict:
    """Afviser NT8 genbrug af et OCO-id hvis alle medlemmer er terminale?"""
    log("\n╔═ P7 · genbrug et opbrugt OCO-id ══════════════════════════════════")
    if not brugt_oco:
        return {"svar": "SPRUNGET OVER", "bevis": ["intet brugt OCO-id"]}
    _, fyld = marked(instr, "BUY", 1)
    if not toer and not fyld:
        return {"svar": "UKLART", "bevis": ["kunne ikke åbne position"]}
    s_id, _ = send(instr, "SELL", 1, ordretype="STOPMARKET",
                   stop=tik(fyld - 40 * TICK), oco=brugt_oco)
    if toer:
        return {"svar": "(tør)", "bevis": []}
    r = NT.afvent_aktiv(s_id, 12.0)
    bevis = [f"genbrugt oco={brugt_oco}: {s_id} status="
             f"{r['status'] or '(ikke set)'}"]
    log(f"     {bevis[0]}")
    return {"svar": "ACCEPTERET" if r["aktiv"] else
                    ("AFVIST" if r["terminal"] else "UKLART"),
            "bevis": bevis}


# ══════════════════════════════════════════════════════════════════════════
def skriv_rapport(instr: str) -> None:
    nu = datetime.datetime.now()
    L = [f"# Probe: exit-ordrer mod NT8 — rapport",
         "",
         f"**Kørt:** {nu:%d-%m-%Y %H:%M}  ·  **Konto:** {KONTO}  ·  "
         f"**Instrument:** {instr}", "",
         "Trin 1 i `SPEC_exit_ordrer_ninjatrader.md`. Svarene nedenfor er målt, "
         "ikke udledt.", "",
         "⚠ Hvor der står **UKLART**, betyder det at ATI hverken bekræftede "
         "eller afkræftede. Det er ikke det samme som nej, og det må ikke "
         "behandles som et svar — kig i NT8's egen Orders-fane før afsnit 6.3 "
         "skrives.", "",
         "| # | Spørgsmål | Svar |", "|---|---|---|"]
    for f in fund:
        L.append(f"| {f['nr']} | {f['spm']} | **{f['svar']}** |")
    L += ["", "---", ""]
    for f in fund:
        L += [f"## {f['nr']} — {f['spm']}", "", f"**Svar: {f['svar']}**", ""]
        if f["note"]:
            L += [f["note"], ""]
        if f["bevis"]:
            L += ["```"] + [str(b) for b in f["bevis"]] + ["```", ""]
    pathlib_write(RAPPORT, "\n".join(L))
    log(f"\n  rapport skrevet: {RAPPORT}")


def pathlib_write(sti: str, indhold: str) -> None:
    import pathlib
    pathlib.Path(sti).write_text(indhold, encoding="utf-8")


def main() -> int:
    global toer
    ap = argparse.ArgumentParser()
    ap.add_argument("--kun", default="", help="fx P1,P2")
    ap.add_argument("--toer", action="store_true", help="send intet")
    ap.add_argument("--instrument", default="", help="override, fx 'MES 12-26'")
    a = ap.parse_args()
    toer = a.toer
    vaelg = {x.strip().upper() for x in a.kun.split(",") if x.strip()}

    log("  ── probe: exit-ordrer mod NT8 ──")

    # ⚠ V1-V4 FØRST. Og derefter en ekstra lås: denne probe lægger rigtige
    # ordrer, så den må kun røre præcis den ene konto.
    profil = NT.klar()
    if profil["konto"] != KONTO:
        log(f"\n  ⛔ account.yaml peger paa {profil['konto']}, ikke {KONTO}.")
        log("     Proben lægger rigtige ordrer og nægter at røre andre konti.")
        return 1
    for adv in (profil.get("advarsler") or []):
        log(f"  {adv}")
    log(f"  konto: {KONTO}   konti i NT8: {profil['konti_set']}")

    instr = a.instrument or instrument()
    log(f"  instrument: {instr}")

    n = pos(instr)
    if n is None:
        log("\n  ⛔ Positionen kan ikke læses. UKENDT er ikke flad — stopper.")
        return 1
    if n != 0:
        log(f"\n  ⛔ Der er allerede en position paa {n}. Luk den først.")
        return 1

    brugt_oco = None
    try:
        st1 = st3 = {}
        if not vaelg or "P1" in vaelg:
            st1 = P1(instr)
            noter("P1", "Accepterer NT8 OCO via OIF?", st1["svar"], st1["bevis"])
            brugt_oco = st1.get("oco")
        if not vaelg or "P2" in vaelg:
            r = P2(instr, st1)
            noter("P2", "Virker CHANGE paa en stoppris?", r["svar"], r["bevis"],
                  r.get("note", ""))
        if not vaelg or "P3" in vaelg:
            st3 = P3(instr, st1)
            noter("P3", "Kan en tredje ordre tilfoejes en levende OCO-gruppe?",
                  st3["svar"], st3["bevis"])
        if not vaelg or "P4" in vaelg:
            r = P4(instr, st1, st3)
            noter("P4", "Kaskaderer en annullering til resten af gruppen?",
                  r["svar"], r["bevis"], r.get("note", ""))
        ryd_op(instr, "efter P1-P4")

        if not vaelg or "P5" in vaelg:
            r = P5(instr)
            noter("P5", "Annullerer NT8 soesteren naar den ene fylder?",
                  r["svar"], r["bevis"], r.get("note", ""))
            ryd_op(instr, "efter P5")
        if not vaelg or "P6" in vaelg:
            r = P6(instr)
            noter("P6", "Virker kvantitetsaendring via CHANGE?", r["svar"],
                  r["bevis"], r.get("note", ""))
            ryd_op(instr, "efter P6")
        if not vaelg or "P7" in vaelg:
            r = P7(instr, brugt_oco)
            noter("P7", "Afviser NT8 genbrug af et opbrugt OCO-id?", r["svar"],
                  r["bevis"])
    finally:
        # ⚠ SKER UANSET HVAD. En efterladt stop på en demokonto er stadig en
        # ordre der kan fylde, og en efterladt position er stadig forkert.
        flad = ryd_op(instr, "afsluttende")
        if not flad:
            log("\n  ⚠⚠ IKKE FLAD. Tjek NT8's Positions- og Orders-faner MANUELT.")
        if fund:
            skriv_rapport(instr)

    log("\n  ── færdig ──")
    uklare = [f["nr"] for f in fund if "UKLART" in f["svar"]]
    if uklare:
        log(f"  ⚠ {', '.join(uklare)} er UKLARE — bekræft i NT8's Orders-fane "
            f"før afsnit 6.3 skrives.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
