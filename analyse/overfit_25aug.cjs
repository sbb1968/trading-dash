/**
 * overfit_25aug.cjs — en BEVIDST overfittet strategi paa ét doegn MES
 * ═══════════════════════════════════════════════════════════════════════════
 * Formaalet er IKKE en strategi der kan bruges. Formaalet er at se hvor meget
 * in-sample-profit man kan fremstille naar man har lov til at soege frit — og
 * hvordan indikatorerne opfoerer sig undervejs. Resultatet er pr. konstruktion
 * vaerdiloest ud af prove; det er selve pointen.
 *
 * ⚠ MEN MASKINERIET SKAL VAERE AERLIGT, ellers laerer oevelsen ingenting.
 * Overfittingen skal komme af PARAMETERSOEGNINGEN, ikke af at kigge fremad. Tre
 * regler haandhaeves derfor stramt:
 *
 *   1. INGEN LOOK-AHEAD I SIGNALET. Et signal paa bar i bruger kun barer <= i.
 *   2. FYLDNING PAA NAESTE BARS AABNING. Samme model som trading_practice:
 *      ser man bar i's luk, er markedet gaaet videre. Fyld paa i+1's open.
 *   3. ⚠ HOEJERE TIDSRAMMER BRUGER SIDSTE LUKKEDE SPAND. En 15m-vaerdi maa
 *      foerst bruges naar den 15m-candle ER faerdig. Bruger man spandens
 *      endelige vaerdi inde i spanden, kender man dens luk foer den er sket —
 *      og saa er "profitten" ikke overfit, men snyd. Det er den fejl der er
 *      lettest at lave og sværest at se.
 *
 * ⚠ OMKOSTNINGER ER MED. Uden dem degenererer optimum til tusinde 1-tick-scalps.
 * MES: 5 USD/point. Kurtage 0,62/side (maalt paa DUQ441063 19-08). Slippage sat
 * til 1 tick (0,25 point = 1,25 USD) hver vej — markedsordrer krydser spreadet.
 *
 * Indikatorerne kommer fra trading_practice/web/indikatorer.js — projektets
 * EGNE. Cipher B er dermed Soerens Pine-port, ikke min gengivelse af den.
 *
 *     node analyse/overfit_25aug.cjs
 */
"use strict";
const fs = require("fs");
const path = require("path");
const I = require(path.join(__dirname, "..", "..", "trading_practice", "web", "indikatorer.js"));

// ── Konstanter ─────────────────────────────────────────────────────────────
const DOLLAR_PR_POINT = 5.0;
const KURTAGE_PR_SIDE = 0.62;
const SLIPPAGE_POINT  = 0.25;              // 1 tick hver vej
const OMKOST_RT       = 2 * KURTAGE_PR_SIDE + 2 * SLIPPAGE_POINT * DOLLAR_PR_POINT;

const CSV = path.join(__dirname, "..", "backend", "data_harvest", "overfit_25aug",
                      "MES_202609_1min.csv");
const DAG = process.env.DAG || "2026-08-25";   // dansk kalenderdoegn
const TIDSRAMMER = [1, 2, 3, 5, 15];

// ── Indlaesning ────────────────────────────────────────────────────────────
function laes(){
  const linjer = fs.readFileSync(CSV, "utf-8").trim().split(/\r?\n/);
  const ud = [];
  for(let n = 1; n < linjer.length; n++){
    const f = linjer[n].split(",");
    ud.push([f[0], +f[1], +f[2], +f[3], +f[4], +f[5]]);
  }
  return ud;
}

// ISO med ET-offset -> dansk vaegur "YYYY-MM-DD HH:MM"
function tilDK(iso){
  const d = new Date(iso);
  const s = d.toLocaleString("sv-SE", {timeZone: "Europe/Copenhagen"});
  return s.slice(0, 16);
}

// ── ⚠ Projektion af hoejere tidsramme, IKKE-GENMALENDE ─────────────────────
// For hver 1-min bar: vaerdien fra den SIDST LUKKEDE spand paa tidsrammen.
// Er vi inde i spand k, bruges spand k-1. Det er den eneste variant der er
// kausal — og forskellen paa overfit og selvbedrag.
function projicerLukket(serie, barer1m, tf){
  if(tf === 1) return serie.slice();
  const noegler = barer1m.map(b => I.spandnoegle(b[0], tf));
  // spandnoegle -> index i den aggregerede serie
  const idx = new Map();
  let k = -1, sidste = null;
  const aggNoegler = [];
  for(const n of noegler){
    if(n !== sidste){ k++; sidste = n; aggNoegler.push(n); }
    idx.set(n, k);
  }
  return noegler.map(n => {
    const j = idx.get(n) - 1;              // ⚠ MINUS ÉN: sidste LUKKEDE spand
    return j >= 0 ? serie[j] : null;
  });
}

const tal = v => v !== null && v !== undefined && Number.isFinite(v);

// ── Byg alle indikatorer, alignet til 1-min indeks ─────────────────────────
function bygFelter(barer1m){
  const F = {};
  for(const tf of TIDSRAMMER){
    const b = I.aggreger(barer1m, tf);
    const ck = I.cockpit(b, 30, 2.0);
    const cb = I.cipherB(b);
    const md = I.macd(b);
    const rs = I.rsi(b.map(x => x[4]), 14);
    const rv = I.relativVolumen(b);
    const ad = I.dmi(b, 14, 14);
    const vw = I.vwap(b, true);
    const sr = I.stochRsi(b.map(x => x[4]));
    const p = (s) => projicerLukket(s, barer1m, tf);
    F[tf] = {
      z:      p(ck.z),
      ma:     p(ck.ma),
      oevre:  p(ck.oevre),
      nedre:  p(ck.nedre),
      wt1:    p(cb.wt1),
      wt2:    p(cb.wt2),
      mfi:    p(cb.mfi ?? cb.rsiMfi ?? []),
      koeb:   p(cb.koeb),
      saelg:  p(cb.saelg),
      macd:   p(md.macd),
      signal: p(md.signal),
      hist:   p(md.hist),
      rsi:    p(rs),
      rvol:   p(rv && rv.rvol ? rv.rvol : rv),
      adx:    p(ad && ad.adx ? ad.adx : ad),
      vwap:   p(vw),
      stoch:  p(sr && sr.k ? sr.k : sr),
    };
  }
  return F;
}

module.exports = {laes, tilDK, projicerLukket, bygFelter, tal,
                  DOLLAR_PR_POINT, OMKOST_RT, DAG, TIDSRAMMER, I};

if(require.main === module){
  const alle = laes();
  console.log(`indlaest: ${alle.length} barer  ${tilDK(alle[0][0])} -> ${tilDK(alle[alle.length-1][0])}`);
  const F = bygFelter(alle);
  const i = alle.length - 1;
  console.log("\nsidste bar, felter pr. tidsramme:");
  for(const tf of TIDSRAMMER){
    const f = F[tf];
    console.log(`  ${tf}m  z=${f.z[i]?.toFixed?.(2)}  wt1=${f.wt1[i]?.toFixed?.(1)}  ` +
                `wt2=${f.wt2[i]?.toFixed?.(1)}  rsi=${f.rsi[i]?.toFixed?.(1)}  ` +
                `macd=${f.macd[i]?.toFixed?.(2)}  rvol=${f.rvol[i]?.toFixed?.(2)}  ` +
                `adx=${f.adx[i]?.toFixed?.(1)}  vwap=${f.vwap[i]?.toFixed?.(2)}`);
  }
}
