# v1-falsifikation — kan den gamle kaskade skelne?

Genereret af `regime_backfill.py` (spec fase 1.6). Formålet er at
efterprøve statusrapportens påstand om at v1 kun kan producere én
etiket — **før** vi bygger noget nyt oven på den antagelse.

Serie: 998 handelsdage, 2022-05-03 .. 2026-04-30
(design-snit 2026-04-30).

---

## Resultat

**Kaskaden kan ikke evalueres på denne serie.**

v1's tre etiket-grene kræver alle mindst én aktie-metrik:

| Gren | Kræver |
|---|---|
| Stock-picking | `m5_dispersion` (aktier) + `m7` (futures) |
| Momentum | `m1_followthrough` + `m2_autocorr` + `m4_hod` (alle aktier) |
| Intraday mean-reversion | `m1_followthrough` + `m2_autocorr` (aktier) |

På **0 af 998 dage** i design-perioden er aktie-metrikkerne
beregnelige (se dækningskortet, fase 0.2). Kaskaden falder derfor
igennem til `Blandet / uklart` hver eneste dag — men det siger noget
om **datadækningen**, ikke om kaskadens evne til at skelne.

**Statusrapportens påstand står derfor uafkræftet, ikke bekræftet.**
Den oprindelige observation (4 vinduer → 1 etiket) er stadig det
eneste belæg, og den blev målt på de få dage hvor aktie-data fandtes.

---

## Fordeling over HELE serien (inkl. dage uden aktie-data)

Medtaget for fuldstændighedens skyld. Domineres af `Blandet / uklart`
som ren dataartefakt — læs den ikke som et regime-udsagn.

| Etiket | Dage |
|---|---|
| Blandet / uklart | 998 |
