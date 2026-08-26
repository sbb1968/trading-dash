/**
 * optimer_25aug.cjs — stor soegning + lokal forfining
 * ═══════════════════════════════════════════════════════════════════════════
 * To faser, fordi tilfaeldig soegning finder OMRAADET og bakkeklatring finder
 * TOPPEN. Antallet af evalueringer taelles og rapporteres — det er selve
 * doseringen af overfit, og det tal er oevelsens vigtigste maaletal.
 *
 *     node analyse/optimer_25aug.cjs [antal_tilfaeldige] [antal_starter]
 */
"use strict";
const S = require("./sweep_25aug.cjs");
const fs = require("fs");
const path = require("path");

let EVAL = 0;
const score = (p) => { EVAL++; return S.koer(p, false).sum; };

// ── Naboer: ét greb ad gangen ──────────────────────────────────────────────
const TAL_FELTER = {
  zIn: [0.4, 0.1, 3.5], wtEkst: [10, 5, 95], rsiLav: [5, 1, 60], rsiHoej: [5, 1, 95],
  rvolMin: [0.4, 0.1, 6], adxMin: [5, 1, 60], sl: [2, 0.25, 40], tp: [3, 0.25, 60],
  trailStart: [2, 0.25, 25], trailAfst: [1.5, 0.25, 20],
  tidFra: [45, 5, 1379], tidTil: [45, 5, 1379], tidsstop: [30, 1, 400],
  tid2Fra: [45, 5, 1379], tid2Til: [45, 5, 1379],
};
const FLAG = ["brugZ","brugKryds","brugWt","brugRSI","brugMACD","brugRV","brugADX",
              "brugVWAP","brugTid","brugTid2","brugTrail","brugModsat","adxOver"];
const TF_FELTER = ["tfZ","tfCB","tfRSI","tfMACD","tfRV","tfADX","tfVWAP"];
const TF = [1,2,3,5,15];

function* naboer(p){
  for(const [f,[spred,trin,maks]] of Object.entries(TAL_FELTER)){
    for(const retning of [-1, 1]){
      for(const skala of [1, 0.35]){
        const d = Math.max(trin, Math.round(spred*skala/trin)*trin) * retning;
        const v = +(p[f] + d).toFixed(2);
        if(v < 0 || v > maks) continue;
        yield {...p, [f]: v};
      }
    }
  }
  for(const f of FLAG) yield {...p, [f]: !p[f]};
  for(const f of TF_FELTER) for(const t of TF) if(t !== p[f]) yield {...p, [f]: t};
  for(const d of [0,1,2]) if(d !== p.dir) yield {...p, dir: d};
  for(const d of [0,1,2]) if(d !== p.dir2) yield {...p, dir2: d};
}

function klatr(start, bedstSum){
  let p = start, sum = bedstSum;
  for(let runde = 0; runde < 60; runde++){
    let forbedret = false;
    for(const q of naboer(p)){
      if(!S.gyldig(q)) continue;
      const s = score(q);
      if(s > sum + 1e-9){ sum = s; p = q; forbedret = true; }
    }
    if(!forbedret) break;
  }
  return {sum, p};
}

// ── Iteret lokal soegning: forstyr toppen og klatr igen ────────────────────
// ⚠ Ren bakkeklatring saetter sig fast i det foerste lokale maksimum den moeder.
// Et lille spark ud af toppen, efterfulgt af en ny klatring, kommer videre —
// og det er praecis dét der goer overfittingen dybere. Hvert spark er en ny
// chance for at ramme netop denne dags stoej.
function forstyr(p){
  const q = {...p};
  const felter = Object.keys(TAL_FELTER);
  const antal = 1 + Math.floor(Math.random() * 3);
  for(let k = 0; k < antal; k++){
    const vaelg = Math.random();
    if(vaelg < 0.45){
      const f = felter[(Math.random()*felter.length)|0];
      const [spred, trin, maks] = TAL_FELTER[f];
      const d = (Math.random() < 0.5 ? -1 : 1) *
                Math.max(trin, Math.round((0.6 + Math.random()*2) * spred / trin) * trin);
      const v = +(q[f] + d).toFixed(2);
      if(v >= 0 && v <= maks) q[f] = v;
    } else if(vaelg < 0.75){
      const f = FLAG[(Math.random()*FLAG.length)|0];
      q[f] = !q[f];
    } else if(vaelg < 0.92){
      const f = TF_FELTER[(Math.random()*TF_FELTER.length)|0];
      q[f] = TF[(Math.random()*TF.length)|0];
    } else {
      q[Math.random() < 0.5 ? "dir" : "dir2"] = (Math.random()*3)|0;
    }
  }
  return q;
}

const N   = +(process.argv[2] || 3000000);
const TOP = +(process.argv[3] || 25);

// ⚠ GENOPTAG. Hver koersel skal staa paa skuldrene af den forrige — ellers
// kaster vi timers soegning vaek, og "flere iterationer" bliver bare "flere
// koersler fra nul". Den gemte bedste laegges i puljen som en start.
let gemt = null;
try {
  const g = JSON.parse(fs.readFileSync(path.join(__dirname, process.env.UDFIL || "bedste_25aug.json"), "utf-8"));
  if(g && g.p && S.gyldig(g.p)){ gemt = {sum: score(g.p), p: g.p};
    console.log(`genoptager fra gemt bedste: ${gemt.sum.toFixed(2)} USD`); }
} catch(e){ /* foerste koersel */ }

console.log(`FASE 1 — tilfaeldig soegning, ${N.toLocaleString("da")} kandidater`);
const t0 = Date.now();
let pulje = [];
let n = 0;
while(n < N){
  const p = S.proeve();
  if(!S.gyldig(p)) continue;
  n++;
  const s = score(p);
  if(pulje.length < TOP){ pulje.push({sum: s, p}); pulje.sort((a,b)=>a.sum-b.sum); }
  else if(s > pulje[0].sum){ pulje[0] = {sum: s, p}; pulje.sort((a,b)=>a.sum-b.sum); }
}
if(gemt) pulje.push(gemt);
pulje.sort((a,b)=>b.sum-a.sum);
console.log(`  bedste raa: ${pulje[0].sum.toFixed(2)} USD` +
            `   (${((Date.now()-t0)/1000).toFixed(0)} s)`);

console.log(`\nFASE 2 — bakkeklatring fra de ${TOP} bedste`);
let bedst = pulje[0];
for(let k = 0; k < pulje.length; k++){
  const r = klatr(pulje[k].p, pulje[k].sum);
  if(r.sum > bedst.sum){
    bedst = r;
    console.log(`  start ${String(k+1).padStart(2)}: ${pulje[k].sum.toFixed(2)} -> ` +
                `${r.sum.toFixed(2)} USD  ⬅ ny bedste`);
  }
}

console.log(`
FASE 3 — iteret lokal soegning (spark + ny klatring)`);
const SPARK = +(process.argv[4] || 4000);
// ⚠ FLERE KAEDER, ikke én. Sparker man kun fra det globale bedste, arbejder
// hele fase 3 i den samme dal. Fem kaeder fra fem forskellige optima daekker
// mere — og de deler stadig det globale bedste, saa et fund et sted loefter alle.
const KAEDER = Math.min(5, pulje.length);
for(let k = 0; k < KAEDER; k++){
  let lokal = klatr(pulje[k].p, pulje[k].sum);
  for(let s = 0; s < Math.floor(SPARK / KAEDER); s++){
    const start = forstyr(lokal.p);
    if(!S.gyldig(start)) continue;
    const r = klatr(start, score(start));
    if(r.sum > lokal.sum + 1e-9) lokal = r;
    if(r.sum > bedst.sum + 1e-9){
      bedst = r;
      console.log(`  kaede ${k+1}, spark ${String(s+1).padStart(5)}: ` +
                  `${r.sum.toFixed(2)} USD  ⬅ ny bedste`);
    }
  }
}

const fuld = S.koer(bedst.p, true);
console.log(`\n${EVAL.toLocaleString("da")} evalueringer i alt` +
            `   (${((Date.now()-t0)/1000).toFixed(0)} s)`);
console.log(`BEDSTE: ${fuld.sum.toFixed(2)} USD paa ${fuld.antal} handler`);
fs.writeFileSync(path.join(__dirname, process.env.UDFIL || "bedste_25aug.json"),
  JSON.stringify({sum: fuld.sum, antal: fuld.antal, evalueringer: EVAL,
                  p: bedst.p, handler: fuld.handler}, null, 1));
console.log("gemt: analyse/bedste_25aug.json");
