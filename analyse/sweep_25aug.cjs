/**
 * sweep_25aug.cjs — parametersoegning for den kunstige strategi
 * ═══════════════════════════════════════════════════════════════════════════
 * ÉN strategi, mange parametre. Sweep'en vaelger HVILKE betingelser der er
 * aktive og hvilke taerskler de har — men det er den samme strategi hver gang:
 *
 *   ENTRY   alle AKTIVE betingelser skal holde samtidig (AND), i retningen
 *   FYLD    naeste bars aabning
 *   EXIT    stop · target · trailing · tidsstop · modsat Cipher-kryds
 *   SLUT    ⚠ tvangsluk ved vinduets sidste bar — en aaben position maa ikke
 *           kunne skjule et tab. Samme regel som i trading_practice.
 *
 * ⚠ AND, IKKE EN SCORE. En score ("mindst 3 af 8 betingelser") ville vaere
 * endnu nemmere at overfitte, men umulig at fortolke bagefter: man kan ikke se
 * HVAD der udloeste handlen. Med AND er den vindende opsaetning en saetning man
 * kan laese.
 *
 * ⚠ OMKOSTNINGER TRAEKKES PR. HANDEL. Uden dem vinder tusinde 1-tick-scalps
 * altid, og resultatet siger intet om indikatorerne — kun om at nul koster nul.
 */
"use strict";
const O = require("./overfit_25aug.cjs");
const {tal, DOLLAR_PR_POINT, OMKOST_RT} = O;

const alle = O.laes();
const F = O.bygFelter(alle);
const dkMin = alle.map(b => {                       // minutter siden midnat, dansk
  const s = O.tilDK(b[0]);
  return {dag: s.slice(0, 10), m: (+s.slice(11, 13)) * 60 + (+s.slice(14, 16))};
});

// Handelsvinduet: det danske kalenderdoegn 25-08. Alt foer er OPVARMNING —
// indikatorerne bruger det, strategien handler ikke i det.
let FRA = -1, TIL = -1;
for(let i = 0; i < alle.length; i++){
  if(dkMin[i].dag === O.DAG){ if(FRA < 0) FRA = i; TIL = i; }
}

// ── Backtest af ÉN parametersaet ───────────────────────────────────────────
function koer(p, opsamlHandler){
  const handler = opsamlHandler ? [] : null;
  let pos = 0, entry = 0, entryI = 0, bedst = 0, sum = 0;

  const betingelser = (i, lang) => {
    if(p.brugZ){
      const z = F[p.tfZ].z[i];
      if(!tal(z)) return false;
      if(lang ? !(z <= -p.zIn) : !(z >= p.zIn)) return false;
    }
    if(p.brugKryds){
      const f = F[p.tfCB], a = f.wt1[i], b = f.wt2[i], a0 = f.wt1[i-1], b0 = f.wt2[i-1];
      if(!tal(a) || !tal(b) || !tal(a0) || !tal(b0)) return false;
      const opad = a0 <= b0 && a > b, ned = a0 >= b0 && a < b;
      if(lang ? !opad : !ned) return false;
    }
    if(p.brugWt){
      const w = F[p.tfCB].wt2[i];
      if(!tal(w)) return false;
      if(lang ? !(w <= -p.wtEkst) : !(w >= p.wtEkst)) return false;
    }
    if(p.brugRSI){
      const r = F[p.tfRSI].rsi[i];
      if(!tal(r)) return false;
      if(lang ? !(r <= p.rsiLav) : !(r >= p.rsiHoej)) return false;
    }
    if(p.brugMACD){
      const h = F[p.tfMACD].hist[i], h0 = F[p.tfMACD].hist[i-1];
      if(!tal(h) || !tal(h0)) return false;
      if(lang ? !(h > h0) : !(h < h0)) return false;
    }
    if(p.brugRV){
      const v = F[p.tfRV].rvol[i];
      if(!tal(v) || v < p.rvolMin) return false;
    }
    if(p.brugADX){
      const a = F[p.tfADX].adx[i];
      if(!tal(a)) return false;
      if(p.adxOver ? !(a >= p.adxMin) : !(a <= p.adxMin)) return false;
    }
    if(p.brugVWAP){
      const v = F[p.tfVWAP].vwap[i], c = alle[i][4];
      if(!tal(v)) return false;
      if(lang ? !(c < v) : !(c > v)) return false;
    }
    return true;
  };

  // ⚠ TO VINDUER MED HVER SIN RETNING. Dagen har en form — op til middag, ned
  // i eventtimen — og en strategi der kun kender ÉN retning kan ikke tage
  // begge ben. Det er stadig ÉN strategi: samme betingelser, samme exits; kun
  // hvilken vej man maa handle afhaenger af tidspunktet. Det er praecis den
  // slags "sessionsviden" der er rimelig at bygge ind — og praecis den slags
  // der overfitter, naar vinduerne vaelges af en soegning paa én dag.
  const maaHandle = (i, lang) => {
    const m = dkMin[i].m;
    const iV1 = !p.brugTid || (m >= p.tidFra && m <= p.tidTil);
    const iV2 = p.brugTid2 && m >= p.tid2Fra && m <= p.tid2Til;
    if(iV2){ if(p.dir2 === 1 && !lang) return false;
             if(p.dir2 === 2 && lang) return false;
             return true; }
    if(!iV1) return false;
    if(p.dir === 1 && !lang) return false;
    if(p.dir === 2 && lang) return false;
    return true;
  };

  const luk = (i, pris, grund) => {
    const brut = (pris - entry) * pos * DOLLAR_PR_POINT;
    sum += brut - OMKOST_RT;
    if(handler) handler.push({
      ind: entryI, ud: i, side: pos > 0 ? "long" : "short",
      entry, exit: pris, grund,
      pnl: +(brut - OMKOST_RT).toFixed(2),
    });
    pos = 0;
  };

  for(let i = FRA; i <= TIL; i++){
    if(pos !== 0){
      const h = alle[i][2], l = alle[i][3];
      const stop = pos > 0 ? entry - p.sl : entry + p.sl;
      const maal = pos > 0 ? entry + p.tp : entry - p.tp;
      // ⚠ STOP FOERST hvis begge rammes i samme bar. Vi kan ikke vide
      // raekkefoelgen inde i baren, og den optimistiske antagelse ville
      // pynte resultatet praecis dér hvor det er mest volatilt.
      if(pos > 0 ? l <= stop : h >= stop){ luk(i, stop, "stop"); }
      else if(pos > 0 ? h >= maal : l <= maal){ luk(i, maal, "target"); }
      else {
        if(p.brugTrail){
          const gevinst = (alle[i][4] - entry) * pos;
          if(gevinst >= p.trailStart) bedst = Math.max(bedst, gevinst);
          if(bedst > 0 && gevinst <= bedst - p.trailAfst){
            luk(i, alle[i][4], "trail");
          }
        }
        if(pos !== 0 && p.tidsstop && i - entryI >= p.tidsstop) luk(i, alle[i][4], "tid");
        if(pos !== 0 && p.brugModsat){
          const f = F[p.tfCB], a = f.wt1[i], b = f.wt2[i], a0 = f.wt1[i-1], b0 = f.wt2[i-1];
          if(tal(a) && tal(b) && tal(a0) && tal(b0)){
            const vend = pos > 0 ? (a0 >= b0 && a < b) : (a0 <= b0 && a > b);
            if(vend) luk(i, alle[i][4], "modsat");
          }
        }
      }
    }
    // ⚠ Vinduets sidste bar: tvangsluk. En aaben position maa ikke skjule et tab.
    if(pos !== 0 && i === TIL){ luk(i, alle[i][4], "vinduet_slut"); continue; }
    if(pos === 0 && i < TIL){
      const lang = maaHandle(i, true) && betingelser(i, true);
      const kort = !lang && maaHandle(i, false) && betingelser(i, false);
      if(lang || kort){
        pos = lang ? 1 : -1;
        entry = alle[i + 1][1];               // ⚠ NAESTE bars aabning
        entryI = i + 1;
        bedst = 0;
      }
    }
  }
  return {sum: +sum.toFixed(2), antal: handler ? handler.length : null, handler};
}

// ── Tilfaeldig soegning ────────────────────────────────────────────────────
const R = (a) => a[(Math.random() * a.length) | 0];
const Ri = (a, b) => a + Math.floor(Math.random() * (b - a + 1));
const Rf = (a, b, trin) => +(a + Math.round(Math.random() * (b - a) / trin) * trin).toFixed(2);
const B = (p = 0.5) => Math.random() < p;

function proeve(){
  const tf = O.TIDSRAMMER;
  return {
    dir: Ri(0, 2),
    brugZ: B(0.6), tfZ: R(tf), zIn: Rf(0.8, 3.0, 0.1),
    brugKryds: B(0.55), brugWt: B(0.45), tfCB: R(tf), wtEkst: Rf(20, 80, 5),
    brugRSI: B(0.4), tfRSI: R(tf), rsiLav: Ri(15, 45), rsiHoej: Ri(55, 85),
    brugMACD: B(0.35), tfMACD: R(tf),
    brugRV: B(0.4), tfRV: R(tf), rvolMin: Rf(0.5, 4.0, 0.1),
    brugADX: B(0.3), tfADX: R(tf), adxMin: Ri(10, 45), adxOver: B(),
    brugVWAP: B(0.35), tfVWAP: R(tf),
    brugTid: B(0.45), tidFra: Ri(0, 1200), tidTil: Ri(0, 1379),
    brugTid2: B(0.45), tid2Fra: Ri(0, 1200), tid2Til: Ri(0, 1379), dir2: Ri(0, 2),
    sl: Rf(1.0, 25.0, 0.25), tp: Rf(1.0, 45.0, 0.25),
    brugTrail: B(0.35), trailStart: Rf(1, 15, 0.25), trailAfst: Rf(0.5, 10, 0.25),
    tidsstop: B(0.4) ? Ri(3, 240) : 0,
    brugModsat: B(0.35),
  };
}

// ⚠ MINDSTEKRAV TIL ANTAL BETINGELSER — saettes af MIN_BET i miljoeet.
// Bruges til at sammenligne "fri soegning" med "tvungen konfluens": den frie
// soegning vaelger naesten altid FAERRE betingelser, fordi faerre betingelser
// giver flere handler og dermed flere chancer for at ramme dagens stoej. At
// tvinge tre frem viser hvad konfluens KOSTER paa én dag — og det er et af de
// mest laererige tal i hele oevelsen.
const MIN_BET = +(process.env.MIN_BET || 1);

function antalBetingelser(p){
  return (p.brugZ?1:0) + (p.brugKryds?1:0) + (p.brugWt?1:0) + (p.brugRSI?1:0) +
         (p.brugMACD?1:0) + (p.brugRV?1:0) + (p.brugADX?1:0) + (p.brugVWAP?1:0);
}

function gyldig(p){
  if(antalBetingelser(p) < MIN_BET) return false;
  if(p.brugTid && p.tidTil - p.tidFra < 30) return false;
  if(p.brugTid2 && p.tid2Til - p.tid2Fra < 30) return false;
  if(!(p.brugZ || p.brugKryds || p.brugWt || p.brugRSI || p.brugMACD ||
       p.brugRV || p.brugADX || p.brugVWAP)) return false;   // ingen betingelser = handl altid
  return true;
}

module.exports = {koer, proeve, gyldig, antalBetingelser, MIN_BET,
                  FRA, TIL, alle, F, dkMin};

if(require.main === module){
  const N = +(process.argv[2] || 200000);
  console.log(`vindue: bar ${FRA}..${TIL} (${TIL - FRA + 1} barer) · ` +
              `${O.tilDK(alle[FRA][0])} -> ${O.tilDK(alle[TIL][0])}`);
  console.log(`omkostning pr. handel: ${OMKOST_RT.toFixed(2)} USD\n`);
  let bedst = null, n = 0, t0 = Date.now();
  while(n < N){
    const p = proeve();
    if(!gyldig(p)) continue;
    n++;
    const r = koer(p, false);
    if(!bedst || r.sum > bedst.sum) { bedst = {sum: r.sum, p};
      console.log(`  ${String(n).padStart(7)}  ny bedste: ${r.sum.toFixed(2)} USD`); }
  }
  console.log(`\n${n} evalueringer paa ${((Date.now()-t0)/1000).toFixed(1)} s`);
  const fuld = koer(bedst.p, true);
  console.log(`BEDSTE: ${fuld.sum.toFixed(2)} USD paa ${fuld.antal} handler`);
  require("fs").writeFileSync(require("path").join(__dirname, "bedste_25aug.json"),
    JSON.stringify({sum: fuld.sum, antal: fuld.antal, p: bedst.p, handler: fuld.handler}, null, 1));
  console.log("gemt: analyse/bedste_25aug.json");
}
