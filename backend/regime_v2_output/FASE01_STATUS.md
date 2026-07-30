# Regime-motor v2 — statusnotat efter fase 0 og 1

**Til:** Cowork / desktop Claude
**Fra:** VS Code Claude, 2026-07-30
**Status:** Fase 0 og 1 gennemført. **Fase 2–3 kan ikke bygges som specificeret** —
én designbeslutning skal træffes først.

---

## Kort version

Percentil-fixet virker. Den futures-baserede motor producerer 56 regimeskift over
878 dage med en gennemsnitlig opholdstid på 15,4 dage — den ville bestå V1 og V4.
Kernidéen i spec'en er altså rigtig, og v1's ene-tilstands-problem er løst.

Men aktie-siden findes ikke i data. Ikke "umoden" som spec'en antager — **0 gyldige
dage i design-perioden, og maksimalt 25 dage nogensinde**, selv uden design-snittet.
Det gør `A_dispersion` ubereg­nelig og "Stock-picking"-etiketten uopnåelig, og
reducerer `A_retning` til én af tre komponenter.

Motoren kan altså bygges — men som en **én-metriks futures-motor**, ikke som den
tre-akse-motor spec'en beskriver. Det er en scope-ændring, ikke en implementeringsdetalje.

---

## 1. Hvad der er leveret

| Fase | Leverance | Status |
|---|---|---|
| 0.4 | `regime_guard.py` — design-mode-vagt, håndhæver `DESIGN_END = 2026-04-30` | ✔ |
| 0.2 | `regime_daekning.py` → `regime_v2_output/regime_data_daekning.md` | ✔ |
| 0.1 | Scheduler-undersøgelse | ✔ (delvist — se §5) |
| 1 | `regime_backfill.py` → `regime_metrics_daily.parquet` (998 dage) | ✔ |
| 1.6 | `regime_v1_falsifikation.md` | ✔ (med forbehold — se §4) |

Vagten skar 25 futures-datoer og 20 universdatoer fra ved `DESIGN_END`. Holdout er
urørt.

---

## 2. Datastatus (fase 0.2)

| Kilde | Handelsdage | Spænd | Gyldige backfill-dage ≤ DESIGN_END |
|---|---|---|---|
| ES_1day | 1.052 | 2022-03-22 .. 2026-06-05 | **998** |
| NQ_1day | 805 | 2023-03-20 .. 2026-06-05 | 758 |
| RTY_1day | 802 | 2023-03-20 .. 2026-06-05 | 758 |
| MES/M2K 15-min | 633 | 2024-06-21 .. 2026-06-30 | 443 |
| bar_cache (aktier) | 1.267 | 2021-08-11 .. 2026-06-30 | *(se nedenfor)* |
| **aktie-univers** | **43** | **2026-03-30 .. 2026-05-29** | **0** |

"Gyldige backfill-dage" = dage hvor et 30-dages vindue opfylder `MIN_COVERAGE = 0.80`.

### Aktie-fælden

`bar_cache` ser ud til at have 1.267 handelsdage tilbage til 2021 — men bredden er en
illusion. De eneste tickere med data før 2026 er **EURUSD og GBPUSD**; det er
valutapar, ikke small-caps. Reel aktie-bredde: 223 tickere, kun i 2026.

| År | Handelsdage | Tickere med data |
|---|---|---|
| 2021–2025 | 1.140 | 1–2 (EURUSD, GBPUSD) |
| 2026 | 127 | 223 |

Og metrikkerne løber ikke over cachen — de løber over **universlisten**
(`smallcap_metrics`: `days_in_win` hentes fra `uni`). Universlisten dækker 43 datoer.

Loftet for aktie-aksen, ved forskellige snit:

| DESIGN_END | Gyldige aktie-dage |
|---|---|
| 2026-04-30 (spec) | **0** |
| 2026-05-15 | 11 |
| 2026-05-29 | 20 |
| ingen snit, alt data | **25** |

Med `PCTL_BURNIN_EQ = 20` ville selv det bedste tilfælde efterlade 5 brugbare dage.

**Spor 0b hjælper ikke som antaget.** Spec'en vurderer omkostningen til "timer pr.
måned" for at bygge universer bagud. Men `build_historical_universe.py` bygger kun
*listen* — 1-minuts-barerne for de udvalgte navne findes ikke før 2026 og skulle også
hentes. Multi-års aktie-historik er derfor et stort dataprojekt, ikke en
weekend-opgave.

---

## 3. Konsekvens for akserne

| Akse | Komponenter (spec) | Beregnelige i design | Konsekvens |
|---|---|---|---|
| `A_retning` | m7 (fut), m2 (aktier), m1 (aktier) | **kun m7** | 1 af 3 |
| `A_dispersion` | m5_dispersion (aktier) | **ingen** | **akse findes ikke** |
| `A_vol` | m3 (aktier), m9 (fut) | kun m9 | indgår alligevel ikke i reglen |

Beslutningsreglens gren 1 (`A_dispersion >= CUT_HIGH → Stock-picking`) kan aldrig
fyre. Det er værd at bemærke hvad det rammer: **"Stock-picking" er den eneste etiket
v1 nogensinde producerede, og den peger på Relativ Styrke — den eneste strategi der
har bestået sin præregistrerede test.**

---

## 4. Hvad futures-motoren faktisk gør (fase 2-forsøg)

Percentil-transformationen af `median(m7)` over ES/NQ/RTY, ekspanderende reference,
`PCTL_BURNIN_FUT = 120`, spec'ens cutoffs og hysterese:

| Mål | Resultat | V-kriterium |
|---|---|---|
| Dage med gyldig percentil | 878 af 998 | — |
| Distinkte AKTIV-etiketter | 3 af 4 | V1: ≥ 3 → **bestået** |
| Største etiket-andel | 37,1 % | V1: ≤ 70 % → **bestået** |
| Regimeskift | **56** | V1: ≥ 6 → **bestået** |
| Gns. opholdstid | 15,4 dage | V4: [10; 60] → **bestået** |

Fordeling: mean-reversion 37,1 %, blandet 35,6 %, momentum 27,2 %.

**Læs det med det rette forbehold.** Kriterierne består, men på en motor der reelt
kun har én input-metrik. V1's formulering "≥ 3 af 4 etiketter" opfyldes præcis fordi
den fjerde er strukturelt uopnåelig — ikke fordi markedet aldrig er i den tilstand.
Det er en bestået test på et degenereret grundlag, og jeg vil ikke rapportere det som
en ren beståelse i fase 4 uden at dette står øverst.

### v1-falsifikationen (1.6) kunne ikke gennemføres

Alle tre af v1-kaskadens grene kræver mindst én aktie-metrik. På 0 af 998 dage er de
beregnelige, så kaskaden falder igennem til "Blandet / uklart" hver dag — hvilket
siger noget om dækningen, ikke om kaskaden. **Statusrapportens påstand om at v1 kun
kan producere én etiket står derfor uafkræftet, ikke bekræftet.** Det eneste belæg er
stadig de fire vinduer fra juli-kørslen.

---

## 5. Scheduler-mysteriet (0.1)

Hvad jeg kan fastslå herfra:

- Jobbet er **korrekt wiret**: `generate_regime_fingerprint`, mandag 06:30 dansk,
  `run_on=is_first_trading_day_of_week`, `retry_until_success=True`,
  algoserver-gated via `instance_role`, subprocess med 600 s timeout og
  fejl-notifikation.
- Append-logikken **kan** akkumulere: `_persist_regime_history` er idempotent pr.
  `run_date` og bevarer øvrige rækker.
- Siden kørslen 2026-07-15 er der kun gået to mandage (20/7 og 27/7).

Hvad jeg **ikke** kan fastslå: om jobbet reelt fyrede på algoserveren. Det kræver
adgang til dens log — jeg kan ikke tilgå den herfra. Det bør tjekkes før den daglige
kadence sættes op.

**Én ting fandt jeg som bør med i v2-designet:** historikken nøgles på `run_date`,
ikke på data-datoen. Da vinduet i v1 følger cachen, ville gentagne kørsler mod en
stående cache tilføje nye rækker med *identiske* målinger under nye datoer — en
tidsserie der ser levende ud, men står stille. v2 bør nøgle på `vindue_slut` eller
som minimum flagge når data-datoen ikke rykker sig.

---

## 6. Beslutningen der skal træffes

Fase 2–3 kan ikke bygges før én af disse vælges. Alle fire er farbare; de har
forskellige omkostninger og forskellig ærlighed.

**A. Futures-only v2.0, og fjern `A_dispersion` fra reglen.**
Motoren bygges på det der findes. "Stock-picking"-etiketten udgår af v2.0 og
genindføres i v2.1 når aktie-historik er akkumuleret fremadrettet. Ærligt, men
rosteren mister sin eneste validerede strategi (Relativ Styrke) indtil da.

**B. Futures-only, men omdefinér `A_dispersion` fra futures-data.**
Fx spredningen mellem ES/NQ/RTY's afkast eller ES-RTY-spreadets volatilitet. Det
måler "bevæger indeksene sig sammen eller fra hinanden" — en fætter til
tværsnitsdispersion, beregnelig over alle 998 dage. Kræver at desktop Claude
præregistrerer den nye definition; jeg må ikke selv vælge den, da jeg har set data.

**C. Flyt `DESIGN_END` til 2026-05-29 og accepter en 20-dages aktie-akse.**
Giver aktie-aksen 20 dage — under burn-in. Løser reelt ingenting og bruger holdout op.
Jeg fraråder den.

**D. Udskyd projektet og prioritér dataopsamling.**
Kør harvest + daglig universliste fremad; genoptag når aktie-siden har ~250 dage
(≈ 1 år). Den dyreste i tid, den billigste i risiko for at bygge noget skævt.

Min anbefaling: **B**, med **A** som fallback hvis en futures-baseret
dispersionsdefinition ikke kan præregistreres overbevisende. B bevarer motorens
tre-akse-struktur og alle fire etiketter, koster ingen ventetid, og den nye metrik kan
valideres på 998 dage. Uanset valget bør 0.3 (harvest-genoplivning) startes parallelt
— uden frisk cache kan motoren aldrig gå i drift, uanset hvor god den er.

---

## 7. Uændret fra spec'en

Kontaminations-reglerne er overholdt. Jeg har ikke åbnet `meanrev_regime.py`,
`washout_regime.py`, backtests eller P&L af nogen art. Design-vagten er aktiv i al kode
og rapporterer hvad den skærer fra. Holdout efter 2026-04-30 er urørt.
