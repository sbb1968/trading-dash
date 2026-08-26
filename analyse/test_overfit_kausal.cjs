/**
 * test_overfit_kausal.cjs — beviser at sweep'ens felter ikke kigger fremad
 * ═══════════════════════════════════════════════════════════════════════════
 * ⚠ DEN ENESTE PROEVE DER AFGOER OM OEVELSEN ER NOGET VAERD.
 *
 * En overfittet strategi skal vaere overfittet — ikke snyd. Forskellen er om
 * signalet paa bar i kun bruger barer <= i. Kigger det fremad, maaler vi ikke
 * hvor meget profit en fri parametersoegning kan fremstille; vi maaler bare at
 * facit ligger i inputtet, og saa laerer oevelsen ingenting om indikatorerne.
 *
 * METODEN ER BRUTAL OG DERFOR TROVAERDIG: for en raekke indekser i afkortes
 * hele bar-serien til 0..i, felterne bygges FORFRA paa den afkortede serie, og
 * vaerdien ved i sammenlignes med den fra den fulde serie. Er de ikke ens, har
 * den fulde serie brugt noget der laa efter i.
 *
 *     node analyse/test_overfit_kausal.cjs
 */
"use strict";
const O = require("./overfit_25aug.cjs");

let fejl = 0;
const kraev = (ok, hvad) => { console.log(`  ${ok ? "OK  " : "FEJL"} ${hvad}`); if(!ok) fejl++; };

const alle = O.laes();
const fuld = O.bygFelter(alle);

// Indekser spredt ud over serien, med vilje ogsaa MIDT i en 15m-spand
// (i % 15 !== 0) — det er dér en genmalende projektion afsloerer sig.
const PROEVE = [800, 1201, 1207, 1213, 1499, 1500, 1501, 2000, 2317, 2759];
const FELTER = ["z", "wt1", "wt2", "macd", "signal", "rsi", "rvol", "adx", "vwap", "ma"];

console.log("\n[1] Felterne ved bar i er uaendrede naar fremtiden fjernes");
for(const i of PROEVE){
  const afkortet = O.bygFelter(alle.slice(0, i + 1));
  let uenige = [];
  for(const tf of O.TIDSRAMMER){
    for(const f of FELTER){
      const a = fuld[tf][f][i], b = afkortet[tf][f][i];
      const beggeTomme = !O.tal(a) && !O.tal(b);
      if(beggeTomme) continue;
      if(!O.tal(a) || !O.tal(b) || Math.abs(a - b) > 1e-9) uenige.push(`${tf}m.${f}`);
    }
  }
  kraev(uenige.length === 0,
        `bar ${i} (${O.tilDK(alle[i][0])}): ${uenige.length ? uenige.join(", ") : "alle felter identiske"}`);
}

console.log("\n[2] Kendt-negativ: proeven KAN fejle");
// ⚠ En proeve der ikke kan fejle, maaler ingenting. Her bygges en BEVIDST
// genmalende projektion (spandens endelige vaerdi brugt inde i spanden), og
// den skal falde igennem paa netop de indekser der ligger midt i en spand.
function projicerGenmalende(serie, barer1m, tf){
  if(tf === 1) return serie.slice();
  const noegler = barer1m.map(b => O.I.spandnoegle(b[0], tf));
  const idx = new Map();
  let k = -1, sidste = null;
  for(const n of noegler){ if(n !== sidste){ k++; sidste = n; } idx.set(n, k); }
  return noegler.map(n => serie[idx.get(n)] ?? null);   // ⚠ INGEN -1
}
{
  const tf = 15;
  const b = O.I.aggreger(alle, tf);
  const ckFuld = O.I.cockpit(b, 30, 2.0);
  const genFuld = projicerGenmalende(ckFuld.z, alle, tf);
  let afsloeret = 0;
  for(const i of PROEVE){
    const del = alle.slice(0, i + 1);
    const bd = O.I.aggreger(del, tf);
    const genDel = projicerGenmalende(O.I.cockpit(bd, 30, 2.0).z, del, tf);
    const a = genFuld[i], c = genDel[i];
    if(O.tal(a) && O.tal(c) && Math.abs(a - c) > 1e-9) afsloeret++;
  }
  kraev(afsloeret > 0,
        `en genmalende projektion afsloeres paa ${afsloeret} af ${PROEVE.length} indekser`);
}

console.log("\n[3] Fyldningen bruger NAESTE bars aabning");
// Egenskaben proeves paa selve data: bar i's luk og bar i+1's aabning er
// forskellige tal. Var de altid ens, kunne modellen ikke skelnes fra "fyld paa
// luk", og proeven ville intet maale.
let forskellige = 0;
for(let i = 1200; i < 1400; i++) if(alle[i][4] !== alle[i+1][1]) forskellige++;
kraev(forskellige > 20,
      `luk(i) != aabning(i+1) paa ${forskellige} af 200 barer — modellen er maalbar`);

console.log("\n" + "=".repeat(70));
if(fejl){ console.log(`⚠ ${fejl} FEJL — sweep'en maa IKKE koeres foer det er lukket`); process.exit(1); }
console.log("KAUSALITET BEVIST — felterne ved bar i bruger kun barer <= i");
process.exit(0);
