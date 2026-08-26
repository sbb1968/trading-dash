/**
 * rapport_25aug.cjs — laes den vindende opsaetning og skriv den ud som noget
 * et menneske kan laese: én saetning der beskriver strategien, og tabellen over
 * de handler den tog.
 */
"use strict";
const fs = require("fs");
const path = require("path");
const O = require("./overfit_25aug.cjs");
const S = require("./sweep_25aug.cjs");

const d = JSON.parse(fs.readFileSync(path.join(__dirname, process.argv[2] || "bedste_25aug.json"), "utf-8"));
const p = d.p;
const alle = S.alle;
const kl = (i) => O.tilDK(alle[i][0]).slice(11);
const tfNavn = (t) => `${t}m`;

// ── Strategien som saetning ────────────────────────────────────────────────
const led = [];
if(p.brugZ)     led.push(`baand-z paa ${tfNavn(p.tfZ)} naar ${p.zIn} sigma fra snittet`);
if(p.brugKryds) led.push(`Cipher B krydser paa ${tfNavn(p.tfCB)}`);
if(p.brugWt)    led.push(`Cipher B's wt2 er forbi ±${p.wtEkst} paa ${tfNavn(p.tfCB)}`);
if(p.brugRSI)   led.push(`RSI(${tfNavn(p.tfRSI)}) under ${p.rsiLav} / over ${p.rsiHoej}`);
if(p.brugMACD)  led.push(`MACD-histogrammet paa ${tfNavn(p.tfMACD)} vokser i retningen`);
if(p.brugRV)    led.push(`relativ volumen paa ${tfNavn(p.tfRV)} mindst ${p.rvolMin}x`);
if(p.brugADX)   led.push(`ADX(${tfNavn(p.tfADX)}) ${p.adxOver ? "over" : "under"} ${p.adxMin}`);
if(p.brugVWAP)  led.push(`prisen er paa rette side af VWAP(${tfNavn(p.tfVWAP)})`);

const hhmm = (m) => `${String(Math.floor(m/60)).padStart(2,"0")}:${String(m%60).padStart(2,"0")}`;
const retning = (r) => r === 0 ? "begge veje" : r === 1 ? "kun long" : "kun short";

console.log("═".repeat(78));
console.log("  DEN KUNSTIGE STRATEGI — vindende opsaetning");
console.log("═".repeat(78));
console.log(`\n  Evalueringer:  ${d.evalueringer.toLocaleString("da")}`);
console.log(`  Resultat:      ${d.sum.toFixed(2)} USD paa ${d.antal} handler (1 kontrakt MES)\n`);
console.log("  ENTRY — alle disse skal holde samtidig:");
for(const l of led) console.log(`     · ${l}`);
console.log("\n  HVORNAAR OG HVILKEN VEJ:");
if(p.brugTid) console.log(`     · ${hhmm(p.tidFra)}-${hhmm(p.tidTil)} dansk: ${retning(p.dir)}`);
else          console.log(`     · hele doegnet: ${retning(p.dir)}`);
if(p.brugTid2) console.log(`     · ${hhmm(p.tid2Fra)}-${hhmm(p.tid2Til)} dansk: ${retning(p.dir2)}  (vinder over vindue 1)`);
console.log("\n  EXIT:");
console.log(`     · stop ${p.sl} point · target ${p.tp} point`);
if(p.brugTrail)  console.log(`     · trailing: aktiveres ved +${p.trailStart} point, afstand ${p.trailAfst} point`);
if(p.tidsstop)   console.log(`     · tidsstop efter ${p.tidsstop} barer`);
if(p.brugModsat) console.log(`     · modsat Cipher B-kryds paa ${tfNavn(p.tfCB)}`);
console.log(`     · tvangsluk paa doegnets sidste bar`);

// ── Handlerne ─────────────────────────────────────────────────────────────
console.log("\n" + "─".repeat(78));
console.log("  HANDLERNE");
console.log("─".repeat(78));
console.log("    # " + "ind".padStart(6) + " " + "ud".padStart(6) + "  side  " +
            "entry".padStart(9) + " " + "exit".padStart(9) + " " +
            "point".padStart(7) + " " + "USD".padStart(9) + "   min  grund");
let sum = 0, vind = 0;
d.handler.forEach((h, n) => {
  const pt = (h.exit - h.entry) * (h.side === "long" ? 1 : -1);
  sum += h.pnl; if(h.pnl > 0) vind++;
  console.log("  " + String(n+1).padStart(3) + " " + kl(h.ind).padStart(6) + " " +
              kl(h.ud).padStart(6) + "  " + h.side.padEnd(5) + " " +
              h.entry.toFixed(2).padStart(9) + " " + h.exit.toFixed(2).padStart(9) + " " +
              pt.toFixed(2).padStart(7) + " " + h.pnl.toFixed(2).padStart(9) + "  " +
              String(h.ud-h.ind).padStart(4) + "  " + h.grund);
});
console.log("─".repeat(78));
const tab = d.handler.filter(h => h.pnl <= 0);
const gv = d.handler.filter(h => h.pnl > 0).reduce((a,h)=>a+h.pnl,0);
const tb = -tab.reduce((a,h)=>a+h.pnl,0);
console.log(`  I ALT ${sum.toFixed(2)} USD   ·   ${vind}/${d.handler.length} vindere ` +
            `(${(100*vind/d.handler.length).toFixed(0)} %)   ·   ` +
            `profitfaktor ${tb > 0 ? (gv/tb).toFixed(2) : "uendelig (ingen tab)"}`);
const varigheder = d.handler.map(h => h.ud - h.ind);
console.log(`  gennemsnitlig varighed ${(varigheder.reduce((a,b)=>a+b,0)/varigheder.length).toFixed(0)} min ` +
            `· laengste ${Math.max(...varigheder)} min · korteste ${Math.min(...varigheder)} min`);
