/* ═══════════════════════════════════════════════════════════════════════════
   signal_eval.js — har nogen af indikatorerne prædiktiv værdi ved fald i MES?
   ═══════════════════════════════════════════════════════════════════════════
   Spørgsmålet er ikke "kan jeg finde et signal der ser godt ud". Det kan man
   altid, hvis man prøver nok kombinationer. Spørgsmålet er om et signal slår
   BASISRATEN — hvor tit faldet sker i forvejen — med mere end tilfældet ville
   give, og om det holder på data det ikke er valgt på.

   Kørsel:
       node analyse/signal_eval.cjs
       node analyse/signal_eval.cjs --tf 5 --ud analyse/ud

   ⚠ .cjs, ikke .js. trading_dash's package.json har "type": "module", saa en
   .js-fil her ville blive laest som ESM — og indikatorer.js er CommonJS.

   ⚠ INDIKATORERNE ER IKKE SKREVET OM HER. Der findes ÉN oversættelse af
   Sørens Pine — trading_practice/web/indikatorer.js — og den er prøvet
   (tests/test_indikatorer.js). En kopi mere ville drive fra de andre; det er
   sket i dette hus før, hvor MACD fandtes i to udgaver og den man SÅ ikke var
   den man PRØVEDE. Denne fil læser den originale.

   ⚠ INGEN BAGKLOGSKAB PÅ HØJERE TIDSRAMMER. En 2m-bar kl. 09:32 må kun kende
   den 15m-bar der er LUKKET — altså 09:15-bar'en, ikke den 09:30 der stadig
   danner sig. Det svarer til request.security(..., lookahead_off) i Pine.
   Bruger man den ufærdige, kender man fremtiden, og ethvert signal ser godt ud.

   ⚠ MANGE SAMMENLIGNINGER ER OGSÅ KURVETILPASNING. Vi prøver 3 N × 3 X ×
   ~14 signaler = ~126 kombinationer. Ved p < 0,05 ville ~6 af dem bestå ved
   ren tilfældighed. Derfor rapporteres antallet af prøvede kombinationer, og
   dommen bruger en Bonferroni-korrigeret tærskel — ikke den nøgne z-værdi.

   ⚠ OVERLAPPENDE HÆNDELSER ER IKKE UAFHÆNGIGE. To nabobarer deler næsten hele
   deres fremtidsvindue, så en z-test på andele er OPTIMISTISK. Den står der
   som en grov sortering, ikke som en p-værdi man kan tro på. Den rigtige
   kontrol er ud-af-prøve-halvdelen, som køres separat.
   ═══════════════════════════════════════════════════════════════════════════ */
"use strict";

const fs   = require("fs");
const path = require("path");

// ── Indikatorerne: den ene, prøvede udgave ─────────────────────────────────
const IND_STI = path.resolve(__dirname, "..", "..", "trading_practice",
                             "web", "indikatorer.js");
if (!fs.existsSync(IND_STI)) {
  console.error(`⚠ Finder ikke indikatorerne: ${IND_STI}\n` +
    "  Denne analyse LÅNER trading_practice's oversættelse med vilje —\n" +
    "  en kopi mere ville drive fra den. Klon trading_practice ved siden af\n" +
    "  trading_dash, eller ret IND_STI.");
  process.exit(1);
}
const IND = require(IND_STI);
const erTal = IND.erTal;

// ── Parametre ───────────────────────────────────────────────────────────────
const arg = (navn, fald) => {
  const i = process.argv.indexOf("--" + navn);
  return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : fald;
};

const DATA   = arg("data", path.resolve(__dirname, "..", "backend", "data_harvest",
                                        "mes_m2k_stitched", "MES_1min.csv"));
const UD     = arg("ud", path.resolve(__dirname, "signal_eval_ud"));
const BASIS_TF = +arg("tf", 2);          // analysegitteret — den man trigger på
const HTF      = [2, 5, 15, 60];         // tidsrammer der logges z/ADX for

// Del 1: hændelsen
const N_LISTE = [5, 10, 20];             // barer frem
const X_LISTE = [0.3, 0.5, 0.8];         // procent fald

// Hvor længe efter et signal må hændelsen begynde, for at signalet "gik forud"?
const LEAD_MAX = +arg("lead", 10);

// Analysevindue i ET-minutter. 08:00 fordi 08:30-nyhederne skal med;
// 16:00 fordi RTH lukker der.
const VINDUE_FRA = 8 * 60;
const VINDUE_TIL = 16 * 60;

// Del 4: nyhedsvinduer (ET), ± minutter
const NYHED_TIDER = [[8, 30], [10, 0], [14, 0], [9, 30], [16, 0]];
const NYHED_SLIP  = 15;

// Dommen. ⚠ Alle tre skal være opfyldt — et løft på 3× betyder ingenting på
// 11 observationer, og en pæn z-værdi betyder ingenting hvis løftet er 1,02.
const KRAV = { n: 30, loeft: 1.20, z: 0 };   // z sættes efter Bonferroni nedenfor

// ── CSV ind ─────────────────────────────────────────────────────────────────
function laesCsv(sti) {
  console.log(`læser ${path.basename(sti)} …`);
  const txt = fs.readFileSync(sti, "utf8");
  const linjer = txt.split("\n");
  const ud = [];
  for (let i = 1; i < linjer.length; i++) {
    const l = linjer[i];
    if (!l || l.length < 10) continue;
    const d = l.split(",");
    // [tid, o, h, l, c, v] — samme form som indikatorer.js forventer
    ud.push([d[0], +d[1], +d[2], +d[3], +d[4], +d[5]]);
  }
  return ud;
}

// ── Tid ─────────────────────────────────────────────────────────────────────
// ⚠ Tidsstemplerne ER allerede i ET (-04:00/-05:00), så minuttet læses direkte
// af strengen. At parse til Date og bruge getHours() ville give MASKINENS
// tidszone — og så ville hele nyhedsvindue-analysen pege på de forkerte barer.
const etMinut = (t) => +t.slice(11, 13) * 60 + +t.slice(14, 16);
const datoAf  = (t) => t.slice(0, 10);

const iNyhedsvindue = (t) => {
  const m = etMinut(t);
  return NYHED_TIDER.some(([h, mi]) => Math.abs(m - (h * 60 + mi)) <= NYHED_SLIP);
};

// ── Højere tidsramme uden bagklogskab ───────────────────────────────────────
// For hver basis-bar: værdien fra den seneste HTF-bar der er LUKKET når
// basis-baren lukker. HTF-bar der starter S dækker [S, S+M) og er lukket ved
// S+M. Basis-bar der starter T lukker ved T+B.
function somAf(basis, basisMin, htf, htfMin, serie) {
  const ud = new Array(basis.length).fill(null);
  let j = -1;                                  // seneste HTF-bar der er lukket
  for (let i = 0; i < basis.length; i++) {
    const slut = etAbsolut(basis[i][0]) + basisMin;
    while (j + 1 < htf.length && etAbsolut(htf[j + 1][0]) + htfMin <= slut) j++;
    ud[i] = j >= 0 ? serie[j] : null;
  }
  return ud;
}
// minutter siden epoke, af ET-strengen — nok til at ordne og sammenligne
function etAbsolut(t) {
  const d = Date.UTC(+t.slice(0, 4), +t.slice(5, 7) - 1, +t.slice(8, 10));
  return d / 60000 + etMinut(t);
}

// ── Hændelser ───────────────────────────────────────────────────────────────
// ⚠ MÅLT FRA LUKKEKURSEN på baren, til den LAVESTE low i de næste N barer.
// Ikke fra low til low: man kan ikke handle på et low man ikke har set.
function haendelser(r, N, Xpct) {
  const n = r.length;
  const ud = new Array(n).fill(false);
  for (let i = 0; i < n - N; i++) {
    const graense = r[i][4] * (1 - Xpct / 100);
    let lav = Infinity;
    for (let k = i + 1; k <= i + N; k++) if (r[k][3] < lav) lav = r[k][3];
    ud[i] = lav <= graense;
  }
  return ud;
}

// ── Signalerne ──────────────────────────────────────────────────────────────
// ⚠ HVER ENKELT ER EN PÅSTAND DER KAN VÆRE FORKERT. De er valgt fordi de er
// dem Søren og Iben faktisk kigger på — ikke fordi de så gode ud i en test.
// Tærsklerne er indikatorernes EGNE (Cipher B's ob=53/ob2=60, cockpittets ±2,
// ADX 25), ikke tal jeg har justeret bagefter.
function byg(r, ind, z) {
  const n = r.length;
  const cb = ind.cb, md = ind.md, rv = ind.rv;
  const nul = () => new Array(n).fill(false);
  const S = {};

  // ── WaveTrend / Cipher B ────────────────────────────────────────────────
  S["wt_kryds_ned"] = nul();
  S["wt_kryds_ned_ob"] = nul();          // over 53 (Cipher B's egen ob)
  S["wt_kryds_ned_ob2"] = nul();         // over 60
  S["wt_roed_prik"] = nul();             // Cipher B's sælg-prik
  for (let i = 1; i < n; i++) {
    const a = cb.wt1[i - 1], b = cb.wt2[i - 1], c = cb.wt1[i], d = cb.wt2[i];
    if (![a, b, c, d].every(erTal)) continue;
    const ned = (a - b) >= 0 && (c - d) < 0;
    S["wt_kryds_ned"][i] = ned;
    S["wt_kryds_ned_ob"][i]  = ned && d >= 53;
    S["wt_kryds_ned_ob2"][i] = ned && d >= 60;
    S["wt_roed_prik"][i] = !!cb.saelg[i];
  }
  S["wt_bear_div"] = cb.dWT.bear.map(Boolean);

  // ── Cockpit z ───────────────────────────────────────────────────────────
  const zSig = (tf, graense) => {
    const s = nul(), v = z[tf];
    for (let i = 1; i < n; i++)
      s[i] = erTal(v[i]) && v[i] >= graense;
    return s;
  };
  const zRetur = (tf, graense) => {          // krydser TILBAGE ned gennem båndet
    const s = nul(), v = z[tf];
    for (let i = 1; i < n; i++)
      s[i] = erTal(v[i]) && erTal(v[i - 1]) && v[i - 1] >= graense && v[i] < graense;
    return s;
  };
  S["z15_over_2"]      = zSig(15, 2.0);
  S["z15_retur_fra_2"] = zRetur(15, 2.0);
  S["z5_over_2"]       = zSig(5, 2.0);
  S["z2_over_2"]       = zSig(2, 2.0);
  S["z15_over_25"]     = zSig(15, 2.5);

  // ── MACD ────────────────────────────────────────────────────────────────
  S["macd_hist_neg"] = nul();
  S["macd_kryds_ned"] = nul();
  for (let i = 1; i < n; i++) {
    if (erTal(md.hist[i]) && erTal(md.hist[i - 1]))
      S["macd_hist_neg"][i] = md.hist[i - 1] >= 0 && md.hist[i] < 0;
    if ([md.macd[i], md.signal[i], md.macd[i - 1], md.signal[i - 1]].every(erTal))
      S["macd_kryds_ned"][i] =
        md.macd[i - 1] >= md.signal[i - 1] && md.macd[i] < md.signal[i];
  }

  // ── Volumen og trendstyrke ──────────────────────────────────────────────
  S["rvol_over_15_roed"] = nul();
  S["adx_over_25"] = nul();
  for (let i = 1; i < n; i++) {
    S["rvol_over_15_roed"][i] = erTal(rv.rvol[i]) && rv.rvol[i] >= 1.5 &&
                                r[i][4] < r[i][1];
    S["adx_over_25"][i] = erTal(rv.adx[i]) && rv.adx[i] >= 25;
  }

  // ── KONTROLLER ──────────────────────────────────────────────────────────
  // ⚠ DE HER TO ER IKKE SIGNALER. De er kontroller, og de skal med FØR man
  // tror på noget som helst andet i tabellen.
  //
  // 1. SPEJLET. Hvis høj volumen på en RØD bar varsler fald, men høj volumen
  //    på en GRØN bar varsler det lige så godt, så måler signalet VOLATILITET
  //    og ikke retning — og så er "det virker" en misforståelse af hvad man
  //    ser. Kontrollen skal have et løft nær 1 for at rød-udgaven betyder
  //    noget som retningssignal.
  S["kontrol_rvol_groen"] = nul();
  for (let i = 1; i < n; i++)
    S["kontrol_rvol_groen"][i] = erTal(rv.rvol[i]) && rv.rvol[i] >= 1.5 &&
                                 r[i][4] > r[i][1];

  // 2. TILFÆLDIGE BARER i samme mængde som et typisk signal. Måleapparatet
  //    SKAL give løft ≈ 1 her. Gør det ikke det, er der en fejl i maskineriet
  //    — og det var der: første udgave sammenlignede et 11-bars vindue mod en
  //    1-bars basisrate og gav ALT et løft omkring 5.
  //    ⚠ Deterministisk pseudo-tilfældig, så kørslen kan gentages.
  S["kontrol_tilfaeldig"] = nul();
  let froe = 12345;
  for (let i = 0; i < n; i++) {
    froe = (froe * 1103515245 + 12345) & 0x7fffffff;
    S["kontrol_tilfaeldig"][i] = (froe / 0x7fffffff) < 0.09;   // ~9 % af barerne
  }

  // ── Konfluens: to uafhængige ting samtidig ──────────────────────────────
  S["z15_2_og_wt_ned"] = S["z15_over_2"].map((v, i) => v && S["wt_kryds_ned"][i]);
  S["roed_prik_og_rvol"] = S["wt_roed_prik"].map((v, i) => v && S["rvol_over_15_roed"][i]);

  return S;
}

// ── Statistik ───────────────────────────────────────────────────────────────
// Precision: af alle signaler — hvor mange efterfølges af en hændelse der
// begynder inden for LEAD_MAX barer?
// Recall:    af alle hændelser — hvor mange havde signalet inden for LEAD_MAX
//            barer forud?
function maal(sig, ev, gyldig, lead) {
  const n = sig.length;
  let nSig = 0, sigMedEv = 0, nEv = 0, evMedSig = 0;
  const forspring = [];

  for (let i = 0; i < n; i++) {
    if (!gyldig[i]) continue;

    if (sig[i]) {
      nSig++;
      for (let k = 0; k <= lead && i + k < n; k++)
        if (ev[i + k]) { sigMedEv++; break; }
    }
    if (ev[i]) {
      nEv++;
      for (let k = 0; k <= lead && i - k >= 0; k++)
        if (sig[i - k]) { evMedSig++; forspring.push(k); break; }
    }
  }
  forspring.sort((a, b) => a - b);
  const median = forspring.length
    ? (forspring.length % 2
        ? forspring[(forspring.length - 1) / 2]
        : (forspring[forspring.length / 2 - 1] + forspring[forspring.length / 2]) / 2)
    : null;
  return { nSig, sigMedEv, nEv, evMedSig, median,
           precision: nSig ? sigMedEv / nSig : null,
           recall:    nEv  ? evMedSig / nEv  : null };
}

// ⚠ EN GROV SORTERING, IKKE EN P-VÆRDI. Overlappende hændelser gør
// observationerne afhængige, så den her overvurderer. Se filhovedet.
function zTest(p, n, p0) {
  if (!n || p0 <= 0 || p0 >= 1) return null;
  return (p - p0) / Math.sqrt(p0 * (1 - p0) / n);
}

// ── Kør ─────────────────────────────────────────────────────────────────────
function main() {
  fs.mkdirSync(UD, { recursive: true });

  const min1 = laesCsv(DATA);
  console.log(`  ${min1.length.toLocaleString("da")} 1-min barer  ` +
              `${min1[0][0].slice(0,10)} → ${min1[min1.length-1][0].slice(0,10)}`);

  // ── Aggreger og beregn ────────────────────────────────────────────────────
  // ⚠ INDIKATORERNE REGNES PÅ HELE DEN KONTINUERLIGE SERIE, også natten over.
  // Skar vi til RTH først, ville hver EMA og hvert σ løbe hen over hullerne, og
  // 09:30-baren ville have et gennemsnit der sprang fra i går kl. 16 til i dag.
  // Den fejl kostede en dag i trading_practice; den gentages ikke her.
  const bar = {}, ind = {}, z = {};
  for (const tf of HTF) {
    process.stdout.write(`  ${tf}m: aggregerer …`);
    bar[tf] = IND.aggreger(min1, tf);
    process.stdout.write(` ${bar[tf].length.toLocaleString("da")} barer, indikatorer …`);
    ind[tf] = {
      ck: IND.cockpit(bar[tf], 30, 2.0),
      rv: IND.relativVolumen(bar[tf]),
      md: IND.macd(bar[tf]),
      cb: tf === BASIS_TF ? IND.cipherB(bar[tf]) : null,
    };
    console.log(" ok");
  }

  const r = bar[BASIS_TF];
  const n = r.length;

  // z og ADX fra hver tidsramme, lagt ned på basisgitteret UDEN bagklogskab
  for (const tf of HTF) {
    z[tf] = tf === BASIS_TF ? ind[tf].ck.z
          : somAf(r, BASIS_TF, bar[tf], tf, ind[tf].ck.z);
  }
  const adx = {};
  for (const tf of HTF) {
    adx[tf] = tf === BASIS_TF ? ind[tf].rv.adx
            : somAf(r, BASIS_TF, bar[tf], tf, ind[tf].rv.adx);
  }

  const basisInd = { cb: ind[BASIS_TF].cb, md: ind[BASIS_TF].md, rv: ind[BASIS_TF].rv };
  const SIG = byg(r, basisInd, z);
  let navne = Object.keys(SIG);

  // ⚠ TO NAVNE, ÉN BETINGELSE. macd_hist_neg og macd_kryds_ned er samme
  // ting (hist = macd - signal, så et fortegnsskift I hist ER et kryds), og
  // Cipher B's røde prik ER "kryds ned med wt2 >= 53". Talt som to signaler
  // ville de fylde dobbelt i Bonferroni-korrektionen og se ud som to
  // uafhængige bekræftelser i en konklusion. Find dem, og sig det.
  const dubletter = [];
  for (let a = 0; a < navne.length; a++) {
    for (let b = a + 1; b < navne.length; b++) {
      let ens = true;
      for (let i = 0; i < n && ens; i++)
        if (!!SIG[navne[a]][i] !== !!SIG[navne[b]][i]) ens = false;
      if (ens) dubletter.push([navne[a], navne[b]]);
    }
  }
  for (const [, b] of dubletter) navne = navne.filter(x => x !== b);
  if (dubletter.length) {
    console.log("\n⚠ identiske signaler (samme betingelse, to navne) — " +
                "kun den første tæller med:");
    for (const [a, b] of dubletter) console.log(`     ${a}  ≡  ${b}`);
  }

  // ── Gyldige barer: inden for analysevinduet OG med alle indikatorer klar ──
  const gyldig = new Array(n).fill(false);
  for (let i = 0; i < n; i++) {
    const m = etMinut(r[i][0]);
    gyldig[i] = m >= VINDUE_FRA && m < VINDUE_TIL &&
                erTal(z[15][i]) && erTal(z[2][i]) &&
                erTal(basisInd.cb.wt2[i]) && erTal(basisInd.md.hist[i]) &&
                erTal(basisInd.rv.rvol[i]);
  }
  const nGyldig = gyldig.filter(Boolean).length;

  // ── Halvering til ud-af-prøve ────────────────────────────────────────────
  // ⚠ EFTER TID, ikke tilfældigt. Tilfældig opdeling ville lade nabobarer —
  // som deler næsten hele deres fremtidsvindue — ligge på hver sin side, og så
  // "ser" holdout-sættet træningssættet. Datoen deler rent.
  const midt = Math.floor(n / 2);
  const skilleDato = r[midt][0].slice(0, 10);
  const halv = (fra, til) => gyldig.map((g, i) => g && i >= fra && i < til);

  console.log(`\nanalysevindue 08:00–16:00 ET · ${nGyldig.toLocaleString("da")} `
            + `gyldige ${BASIS_TF}m-barer · skille ${skilleDato}\n`);

  // ── Del 2: log pr. bar ───────────────────────────────────────────────────
  skrivBarLog(path.join(UD, `bar_log_${BASIS_TF}m.csv`),
              r, gyldig, z, adx, basisInd, SIG,
              haendelser(r, 10, 0.5));

  // ── Del 3 + 4 ────────────────────────────────────────────────────────────
  const raekker = [];
  const kombinationer = N_LISTE.length * X_LISTE.length * navne.length;
  // Bonferroni: vi prøver `kombinationer` ting, så tærsklen strammes
  KRAV.z = tilZ(0.05 / kombinationer);
  console.log(`prøver ${kombinationer} kombinationer → Bonferroni-tærskel `
            + `z ≥ ${KRAV.z.toFixed(2)} (svarer til p < ${(0.05/kombinationer).toExponential(1)})\n`);

  for (const N of N_LISTE) {
    for (const X of X_LISTE) {
      const ev = haendelser(r, N, X);
      const basisrate = andel(ev, gyldig, LEAD_MAX);
      // ⚠ TÆL HÆNDELSERNE PR. HALVDEL. Uden det står et løft på 0,00 i anden
      // halvdel som "signalet holdt op med at virke" — men det kan lige så godt
      // være at der ikke VAR nogen hændelser at ramme. To vidt forskellige ting.
      let ev1 = 0, ev2 = 0;
      for (let i = 0; i < n; i++) if (ev[i] && gyldig[i]) (i < midt ? ev1++ : ev2++);
      const udenNyhed = gyldig.map((g, i) => g && !iNyhedsvindue(r[i][0]));
      const basisUden = andel(ev, udenNyhed, LEAD_MAX);

      for (const navn of navne) {
        const m  = maal(SIG[navn], ev, gyldig, LEAD_MAX);
        const mU = maal(SIG[navn], ev, udenNyhed, LEAD_MAX);
        const m1 = maal(SIG[navn], ev, halv(0, midt), LEAD_MAX);
        const m2 = maal(SIG[navn], ev, halv(midt, n), LEAD_MAX);
        const b1 = andel(ev, halv(0, midt), LEAD_MAX),
              b2 = andel(ev, halv(midt, n), LEAD_MAX);

        raekker.push({
          N, X, signal: navn,
          basisrate, precision: m.precision, recall: m.recall,
          loeft: m.precision != null && basisrate > 0 ? m.precision / basisrate : null,
          z: zTest(m.precision, m.nSig, basisrate),
          n_signal: m.nSig, n_haendelser: m.nEv, median_forspring: m.median,
          precision_uden_nyhed: mU.precision,
          basisrate_uden_nyhed: basisUden,
          loeft_uden_nyhed: mU.precision != null && basisUden > 0 ? mU.precision / basisUden : null,
          loeft_1halvdel: m1.precision != null && b1 > 0 ? m1.precision / b1 : null,
          loeft_2halvdel: m2.precision != null && b2 > 0 ? m2.precision / b2 : null,
          n_signal_1: m1.nSig, n_signal_2: m2.nSig,
          n_haendelser_1: ev1, n_haendelser_2: ev2,
        });
      }
    }
  }
  for (const x of raekker) x.dom = dom(x);

  skrivStat(path.join(UD, "signal_statistik.csv"), raekker);
  bevaegelseIVinduer(r, gyldig, path.join(UD, "nyhedsvinduer.csv"));
  rapport(raekker, { nGyldig, skilleDato, kombinationer, navne, min1, r });
}

// ⚠ SAMME VINDUE SOM PRAECISIONEN. Foerste udgave taalte "begynder en
// haendelse PAA denne bar" mens praecisionen spurgte "begynder en inden for de
// naeste 11 barer". Med 11 chancer mod 1 gav ALT et loeft omkring 5 — ogsaa
// ADX>25, som ikke forudsiger noget som helst. Det var ikke et signal; det var
// en broek med to forskellige naevnere.
function andel(ev, gyldig, lead) {
  let a = 0, b = 0;
  for (let i = 0; i < ev.length; i++) {
    if (!gyldig[i]) continue;
    b++;
    for (let k = 0; k <= lead && i + k < ev.length; k++)
      if (ev[i + k]) { a++; break; }
  }
  return b ? a / b : 0;
}

// z-værdi for et tosidet p — Acklams approksimation, rigelig præcis her
function tilZ(p) {
  const q = 1 - p / 2;
  const a = [-39.69683028665376, 220.9460984245205, -275.9285104469687,
             138.3577518672690, -30.66479806614716, 2.506628277459239];
  const b = [-54.47609879822406, 161.5858368580409, -155.6989798598866,
             66.80131188771972, -13.28068155288572];
  const c = [-0.007784894002430293, -0.3223964580411365, -2.400758277161838,
             -2.549732539343734, 4.374664141464968, 2.938163982698783];
  const d = [0.007784695709041462, 0.3224671290700398, 2.445134137142996,
             3.754408661907416];
  const pl = 0.02425;
  let x;
  if (q < pl) {
    const u = Math.sqrt(-2 * Math.log(q));
    x = (((((c[0]*u+c[1])*u+c[2])*u+c[3])*u+c[4])*u+c[5]) /
        ((((d[0]*u+d[1])*u+d[2])*u+d[3])*u+1);
  } else if (q <= 1 - pl) {
    const u = q - 0.5, t = u * u;
    x = (((((a[0]*t+a[1])*t+a[2])*t+a[3])*t+a[4])*t+a[5])*u /
        (((((b[0]*t+b[1])*t+b[2])*t+b[3])*t+b[4])*t+1);
  } else {
    const u = Math.sqrt(-2 * Math.log(1 - q));
    x = -(((((c[0]*u+c[1])*u+c[2])*u+c[3])*u+c[4])*u+c[5]) /
         ((((d[0]*u+d[1])*u+d[2])*u+d[3])*u+1);
  }
  return x;
}

// ⚠ Dommen kræver ALLE fire. Et løft på 3× på 11 observationer er støj; en
// pæn z-værdi med løft 1,02 er ligegyldig; og et resultat der kun gælder i
// første halvdel af data er kurvetilpasning uanset hvor pænt det ser ud.
function dom(x) {
  if (x.n_signal < KRAV.n) return "for få";
  if (x.loeft == null || x.z == null) return "—";
  const staerk = x.loeft >= KRAV.loeft && x.z >= KRAV.z;
  if (!staerk) return "nej";
  const holder = x.loeft_1halvdel >= 1.1 && x.loeft_2halvdel >= 1.1;
  return holder ? "JA" : "kun 1. halvdel";
}

// ── Del 2-filen ─────────────────────────────────────────────────────────────
function skrivBarLog(sti, r, gyldig, z, adx, ind, SIG, ev) {
  const navne = Object.keys(SIG);
  const ud = ["tid_et,min_siden_aabning,i_nyhedsvindue,close," +
              "z2,z5,z15,z60,adx2,adx5,adx15,adx60," +
              "wt1,wt2,macd,signal,hist,rvol," +
              navne.join(",") + ",haendelse_N10_X05"];
  const f = (v) => erTal(v) ? (+v).toFixed(4) : "";
  for (let i = 0; i < r.length; i++) {
    if (!gyldig[i]) continue;
    ud.push([
      r[i][0], etMinut(r[i][0]) - 570, iNyhedsvindue(r[i][0]) ? 1 : 0, r[i][4],
      f(z[2][i]), f(z[5][i]), f(z[15][i]), f(z[60][i]),
      f(adx[2][i]), f(adx[5][i]), f(adx[15][i]), f(adx[60][i]),
      f(ind.cb.wt1[i]), f(ind.cb.wt2[i]),
      f(ind.md.macd[i]), f(ind.md.signal[i]), f(ind.md.hist[i]),
      f(ind.rv.rvol[i]),
      ...navne.map(nv => SIG[nv][i] ? 1 : 0),
      ev[i] ? 1 : 0,
    ].join(","));
  }
  fs.writeFileSync(sti, ud.join("\n"), "utf8");
  console.log(`skrev ${path.basename(sti)}  (${(ud.length-1).toLocaleString("da")} rækker)`);
}

function skrivStat(sti, raekker) {
  const kol = Object.keys(raekker[0]);
  const f = (v) => v == null ? "" : typeof v === "number" ? +v.toFixed(5) : v;
  fs.writeFileSync(sti,
    [kol.join(","), ...raekker.map(x => kol.map(k => f(x[k])).join(","))].join("\n"),
    "utf8");
  console.log(`skrev ${path.basename(sti)}  (${raekker.length} rækker)`);
}

// ── Del 4: hvor meget af dagens bevægelse sker i vinduerne? ─────────────────
// ⚠ SUM AF ABSOLUTTE BAR-BEVÆGELSER, ikke dagens spænd. Spændet siger hvor
// langt prisen KOM; summen siger hvor meget den BEVÆGEDE SIG — og det er dét
// man handler på. En dag der går op og ned igen har et lille spænd og masser
// af bevægelse.
function bevaegelseIVinduer(r, gyldig, sti) {
  const dage = new Map();
  for (let i = 1; i < r.length; i++) {
    if (!gyldig[i]) continue;
    const d = datoAf(r[i][0]);
    if (!dage.has(d)) dage.set(d, { i_alt: 0, i_vindue: 0, barer: 0, barer_vindue: 0 });
    const s = dage.get(d);
    const bev = Math.abs(r[i][4] - r[i - 1][4]);
    s.i_alt += bev; s.barer++;
    if (iNyhedsvindue(r[i][0])) { s.i_vindue += bev; s.barer_vindue++; }
  }
  const linjer = ["dato,bevaegelse_i_alt,bevaegelse_i_vindue,andel,barer,barer_i_vindue,andel_barer"];
  let sumAlt = 0, sumVin = 0, sumBar = 0, sumBarVin = 0;
  for (const [d, s] of [...dage].sort()) {
    if (!s.i_alt) continue;
    sumAlt += s.i_alt; sumVin += s.i_vindue;
    sumBar += s.barer; sumBarVin += s.barer_vindue;
    linjer.push([d, s.i_alt.toFixed(2), s.i_vindue.toFixed(2),
                 (s.i_vindue / s.i_alt).toFixed(4), s.barer, s.barer_vindue,
                 (s.barer_vindue / s.barer).toFixed(4)].join(","));
  }
  fs.writeFileSync(sti, linjer.join("\n"), "utf8");
  const aBev = sumVin / sumAlt, aBar = sumBarVin / sumBar;
  console.log(`skrev ${path.basename(sti)}  (${dage.size} dage)`);
  console.log(`\nDEL 4 — nyhedsvinduerne (±${NYHED_SLIP} min om ` +
              NYHED_TIDER.map(([h,m]) => `${h}:${String(m).padStart(2,"0")}`).join(", ") + ")");
  console.log(`   ${(aBar*100).toFixed(1)} % af barerne  ` +
              `→  ${(aBev*100).toFixed(1)} % af bevægelsen` +
              `   (koncentration ${(aBev/aBar).toFixed(2)}×)`);
  return { aBev, aBar };
}

// ── Del 5: konklusionen ─────────────────────────────────────────────────────
function rapport(raekker, ctx) {
  const L = [];
  const p = (s = "") => { L.push(s); console.log(s); };

  p("\n" + "═".repeat(78));
  p("DEL 3 — slår nogen af signalerne basisraten?");
  p("═".repeat(78));

  const bestaaet = raekker.filter(x => x.dom === "JA");
  const kun1     = raekker.filter(x => x.dom === "kun 1. halvdel");

  // Tabel: vis den bedste opsætning pr. signal, uanset om den består
  p("\nBedste (N, X) pr. signal — sorteret efter løft:");
  p("  " + "signal".padEnd(22) + "N   X%   n     præc.  basis  løft   z      " +
    "1.halv 2.halv  dom");
  p("  " + "─".repeat(90));
  const bedst = new Map();
  for (const x of raekker) {
    const nu = bedst.get(x.signal);
    if (!nu || (x.loeft || 0) > (nu.loeft || 0)) bedst.set(x.signal, x);
  }
  for (const x of [...bedst.values()].sort((a, b) => (b.loeft || 0) - (a.loeft || 0))) {
    p("  " + x.signal.padEnd(22) +
      String(x.N).padEnd(4) + String(x.X).padEnd(5) +
      String(x.n_signal).padEnd(6) +
      pct(x.precision).padEnd(7) + pct(x.basisrate).padEnd(7) +
      num(x.loeft, 2).padEnd(7) + num(x.z, 1).padEnd(7) +
      num(x.loeft_1halvdel, 2).padEnd(7) + num(x.loeft_2halvdel, 2).padEnd(8) +
      x.dom);
  }

  p("\n" + "═".repeat(78));
  p("KONKLUSION");
  p("═".repeat(78));
  p(`Data:        ${ctx.min1[0][0].slice(0,10)} → ` +
    `${ctx.min1[ctx.min1.length-1][0].slice(0,10)}  ` +
    `(${ctx.nGyldig.toLocaleString("da")} gyldige barer i 08:00–16:00 ET)`);
  p(`Prøvet:      ${ctx.kombinationer} kombinationer ` +
    `(${ctx.navne.length} signaler × ${N_LISTE.length} N × ${X_LISTE.length} X)`);
  p(`Krav for JA: n ≥ ${KRAV.n}, løft ≥ ${KRAV.loeft}, z ≥ ${KRAV.z.toFixed(2)} ` +
    `(Bonferroni), OG løft ≥ 1,1 i BEGGE halvdele`);
  p("");

  // ⚠ KONTROLLERNE FØRST. Står de nederst i en tabel, læser man dem ikke — og
  // de er det eneste der afgør om resten betyder noget.
  const kon = [...bedst.values()].filter(x => x.signal.startsWith("kontrol_"));
  const tilf = kon.find(x => x.signal === "kontrol_tilfaeldig");
  const groen = kon.find(x => x.signal === "kontrol_rvol_groen");
  p("KONTROLLER — støjgulvet og spejlet");
  if (tilf)
    p(`  Tilfældige barer, bedste af ${N_LISTE.length * X_LISTE.length} (N,X): ` +
      `løft ${num(tilf.loeft, 2)}.  ` +
      `Alt under det kan ikke skelnes fra støj.`);
  if (groen) {
    const roed = bedst.get("rvol_over_15_roed");
    p(`  Høj volumen på GRØN bar: løft ${num(groen.loeft, 2)}  ` +
      `mod ${num(roed && roed.loeft, 2)} for rød bar.`);
    if (roed && groen.loeft >= roed.loeft * 0.85) {
      p("  → ⚠ SPEJLET VIRKER LIGE SÅ GODT. RVOL måler altså VOLATILITET, ikke");
      p("    retning — høj volumen varsler BEVÆGELSE, ikke bevægelse NEDAD.");
      p("    At kalde det et fald-signal er en misforståelse af hvad man ser.");
    }
  }
  p("");

  const uKontrol = bestaaet.filter(x => !x.signal.startsWith("kontrol_"));
  const stoejgulv = tilf && tilf.loeft != null ? tilf.loeft : 1.0;
  const overStoej = uKontrol.filter(x => x.loeft > stoejgulv);
  if (uKontrol.length && !overStoej.length) {
    p(`⚠ ${uKontrol.length} kombination(er) består kravene, men INGEN af dem ligger`);
    p(`  over støjgulvet på ${num(stoejgulv, 2)} som de tilfældige barer satte.`);
    p("");
  }

  if (!bestaaet.length) {
    p("⚠ INGEN af de prøvede signaler slår basisraten efter kravene.");
    p("");
    p("  Det er ikke et nulresultat man skal lede efter en undtagelse i. Med");
    p(`  ${ctx.kombinationer} kombinationer ville ~${(0.05*ctx.kombinationer).toFixed(0)} bestå`);
    p("  ved ren tilfældighed på en ukorrigeret 5 %-tærskel — og selv dét sker");
    p("  ikke her efter korrektion.");
    if (kun1.length) {
      p("");
      p(`  ${kun1.length} kombination(er) bestod i FØRSTE halvdel og faldt i anden.`);
      p("  Det er den klassiske signatur på kurvetilpasning:");
      for (const x of kun1.slice(0, 6))
        p(`     ${x.signal} (N=${x.N}, X=${x.X}%): ` +
          `løft ${num(x.loeft_1halvdel,2)} → ${num(x.loeft_2halvdel,2)}`);
    }
  } else {
    p(`${bestaaet.length} kombination(er) består ALLE kravene:`);
    for (const x of bestaaet.sort((a, b) => b.loeft - a.loeft))
      p(`   ${x.signal} (N=${x.N}, X=${x.X}%)  ` +
        `præcision ${pct(x.precision)} mod basis ${pct(x.basisrate)}  ` +
        `= løft ${num(x.loeft,2)}  ·  n=${x.n_signal}  ·  ` +
        `holder ud-af-prøve (${num(x.loeft_2halvdel,2)})`);
  }

  // ⚠ SØREN BAD UDTRYKKELIGT OM DEM DER IKKE VIRKER. Et signal med løft under
  // 1 er ikke bare "ikke godt" — det er en betingelse der gør faldet MINDRE
  // sandsynligt, og det er lige så brugbart at vide.
  p("");
  p("─".repeat(78));
  p("Signaler der er DÅRLIGERE end basisraten (løft < 1, n ≥ " + KRAV.n + ")");
  p("─".repeat(78));
  const daarlige = new Map();
  for (const x of raekker) {
    if (x.n_signal < KRAV.n || x.loeft == null || x.loeft >= 1) continue;
    const nu = daarlige.get(x.signal);
    if (!nu || x.loeft < nu.loeft) daarlige.set(x.signal, x);
  }
  if (!daarlige.size) p("  (ingen)");
  for (const x of [...daarlige.values()].sort((a, b) => a.loeft - b.loeft))
    p(`  ${x.signal.padEnd(22)} N=${String(x.N).padEnd(3)} X=${x.X}%  ` +
      `præcision ${pct(x.precision)} mod basis ${pct(x.basisrate)}  ` +
      `= løft ${num(x.loeft, 2)}  (z ${num(x.z, 1)}, n=${x.n_signal})`);

  // Del 4-hypotesen
  p("");
  p("─".repeat(78));
  p("DEL 4 — mister signalerne værdien når nyhedsvinduerne fjernes?");
  p("─".repeat(78));
  // ⚠ HYPOTESEN HANDLER OM DE SIGNALER DER HAR NOGET AT MISTE. Et gennemsnit
  // over alle 144 kombinationer måler mest støj fra dem med løft under 1 —
  // og et signal der ikke virker, kan ikke "miste sin værdi i nyhedsvinduerne".
  // Testen køres derfor på dem med et løft værd at tale om.
  const med = raekker.filter(x => x.n_signal >= KRAV.n && x.loeft != null &&
                                  x.loeft >= 1.1 && x.loeft_uden_nyhed != null);
  if (!med.length) {
    p("Ingen kombination har et løft over 1,1 at miste — hypotesen kan ikke");
    p("afgøres, fordi der ikke er nogen værdi at fjerne.");
  } else {
    const faldne = med.filter(x => x.loeft_uden_nyhed < x.loeft * 0.9);
    const snitMed  = gennemsnit(med.map(x => x.loeft));
    const snitUden = gennemsnit(med.map(x => x.loeft_uden_nyhed));
    p(`Målt på de ${med.length} kombinationer med løft ≥ 1,1:`);
    p(`   gennemsnitligt løft MED nyhedsvinduer:  ${num(snitMed, 3)}`);
    p(`   gennemsnitligt løft UDEN:               ${num(snitUden, 3)}`);
    p(`   ${faldne.length} af ${med.length} taber over 10 % af deres løft.`);
    p("");
    p("   " + "signal".padEnd(22) + "N   X%   løft m/  løft u/  ændring");
    for (const x of med.sort((a, b) => b.loeft - a.loeft).slice(0, 12))
      p("   " + x.signal.padEnd(22) + String(x.N).padEnd(4) + String(x.X).padEnd(5) +
        num(x.loeft, 2).padEnd(9) + num(x.loeft_uden_nyhed, 2).padEnd(9) +
        ((x.loeft_uden_nyhed / x.loeft - 1) * 100).toFixed(0) + " %");
    p("");
    p(snitUden < snitMed * 0.9
      ? "→ HYPOTESEN BEKRÆFTET: værdien ligger overvejende i nyhedsvinduerne."
      : "→ HYPOTESEN AFKRÆFTET: løftet holder også uden for vinduerne.");
  }

  fs.writeFileSync(path.join(UD, "konklusion.txt"), L.join("\n"), "utf8");
  p(`\nskrev konklusion.txt`);
}

const pct = (v) => v == null ? "—" : (v * 100).toFixed(1) + "%";
const num = (v, d) => v == null || !isFinite(v) ? "—" : v.toFixed(d);
const gennemsnit = (a) => a.length ? a.reduce((s, v) => s + v, 0) / a.length : null;

main();
