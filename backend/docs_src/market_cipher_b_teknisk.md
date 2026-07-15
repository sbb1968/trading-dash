# Market Cipher B — teknisk gennemgang

*Fuld reference til Søren, så du kan hjælpe Iben med spørgsmål. Dette dækker hver facet af
indikatoren — hvad den beregner, hver parameter, hvert signal og de præcise betingelser bag dem.
Alt herunder er læst direkte ud af Pine-scriptet ("VuManChu B Divergences" / VMC Cipher_B).*

---

## 1. Hvad indikatoren er

Det er en **klon af Market Cipher B**, bygget oven på **WaveTrend**-oscillatoren (LazyBears
klassiker), med tilføjet pengestrøm, RSI, Stochastic RSI, Schaff Trend Cycle og — det vigtigste —
et lag af **divergens-detektion**. Den er en `study` (indikator i et separat panel), ikke en
`strategy`, så den placerer ingen ordrer og har ingen backtest. Forfatteren skriver selv at det er
begynder-kode sammensat af andres offentliggjorte scripts, og at **standard-indstillingerne er
tunet til 4-timers grafer** (testet på 2h og 4h).

Alle "signaler" er **information, ikke ordrer**. Ingen af prikkerne er sikre.

---

## 2. WaveTrend — motoren (de blå/lilla flader)

Hele indikatoren drejer om WaveTrend. Den beregnes sådan (kilde = `hlc3` = (high+low+close)/3):

```
esa = ema(src, 9)                    # glidende middel af prisen
de  = ema(|src − esa|, 9)            # gennemsnitlig afstand fra middel
ci  = (src − esa) / (0.015 · de)     # normaliseret afstand ("hvor langt fra normalen")
wt1 = ema(ci, 12)                    # HURTIG bølge
wt2 = sma(wt1, 3)                    # LANGSOM bølge (glattet wt1)
```

**Intuition:** WaveTrend måler hvor langt prisen er kommet væk fra sit eget glidende gennemsnit,
normaliseret så tallet svinger om nul. Store positive værdier = strakt op (overkøbt); store
negative = strakt ned (oversolgt).

- **wt1** = den hurtige bølge (lys blå fyld). **wt2** = den langsomme (mørk lilla fyld).
- **Krydset mellem wt1 og wt2** er kernesignalet: når wt1 krydser op gennem wt2 = begyndende
  bullish momentum; ned = bearish.
- **wtVwap = wt1 − wt2** (den hvide "Fast WT"-flade) — afstanden mellem de to bølger, dvs.
  momentum-accelerationen. Positiv og voksende = momentum tiltager opad.

**Parametre (WaveTrend Settings):** Channel Length 9, Average Length 12, MA Source hlc3, MA
Length 3.

**Niveauer:**
- Overkøbt: **+53** (niveau 1), **+60** (niveau 2, den stiplede step-linje), **+100** (niveau 3).
- Oversolgt: **−53**, **−60**, **−75**.
- "Overkøbt" i koden = `wt2 ≥ 53`; "oversolgt" = `wt2 ≤ −53`.

---

## 3. Signal-prikkerne — de præcise betingelser

Dette er dét Iben oftest spørger om. Her er nøjagtigt hvornår hver prik tændes:

**Lille prik (hvert kryds).** Tændes ved *ethvert* wt1/wt2-kryds — grøn hvis op, rød hvis ned.
Det er bare "der skete et kryds nu", uden filter. Mindst betydningsfuld.

**Stor grøn cirkel — køb** (`buySignal`): kryds **OG** wt1 krydser op **OG** bølgen er oversolgt
(`wt2 ≤ −53`). Altså: et op-kryds mens vi er i det oversolgte område.

**Stor rød cirkel — salg** (`sellSignal`): kryds **OG** wt1 krydser ned **OG** overkøbt
(`wt2 ≥ 53`).

> Derfor: hvis bølgen krydser, men *ikke* er i overkøbt/oversolgt, kommer der KUN en lille prik,
> ikke en stor cirkel. Det er den hyppigste "hvorfor kom der ingen cirkel?"-forklaring.

**Guld/orange cirkel** (`wtGoldBuy`) — den sjældne. Alle disse skal være opfyldt samtidig:
1. Der er en bullish WT- eller RSI-divergens, **og**
2. bølgens forrige bund (`wtLow_prev`) var **≤ −75** (ekstremt oversolgt), **og**
3. bølgen er nu **over −75** (på vej op igen), **og**
4. dykket var kraftigt (`wtLow_prev − wt2 ≤ −5`), **og**
5. RSI ved den bund var **< 30**.

Det er altså: et voldsomt oversolgt dyk med divergens, der begynder at vende. **Vigtig note:**
forfatterens egen kommentar i koden siger *"DONT BUY WHEN GOLD CIRCLE APPEAR"* — han behandler den
som en advarsel om et ekstremt strakt marked, ikke et køb. (I den *originale* Market Cipher B er
guld-prikken derimod det stærkeste købssignal — så her adskiller denne klon sig. Sig til Iben:
**behandl guld som "ekstrem — vær forsigtig", ikke som et automatisk køb.**)

**Divergens-prikker** (`buySignalDiv` / `sellSignalDiv`) — en lidt større prik der tændes når der
er en divergens (WT, 2. WT-range, Stoch eller RSI). Grøn for bullish, rød for bearish.

---

## 4. Divergenser — indikatorens egentlige kerne

En **divergens** = pris og momentum peger hver sin vej. Det er dét indikatoren er bedst til.

**Sådan findes de (fraktaler).** Koden bruger en 5-bars fraktal: en top er bekræftet når en bar er
højere end de 2 barer før OG de 2 barer efter (og omvendt for bunde). Det giver en **iboende
2-bars forsinkelse**: en top/bund kan først bekræftes 2 barer senere. Derfor tegnes divergens-
prikker og -linjer med `offset = −2` — de placeres tilbage ved selve toppen/bunden, men dukker
først op 2 barer efter. **Det er ikke "repainting" der forsvinder**, men et signal der bekræftes
med 2 barers forsinkelse. (God at kende — Iben kan opleve at en divergens "kom lidt sent".)

**Regular (almindelig) divergens** — varsler en *vending*:
- **Bearish:** prisen laver en **højere** top, men WT laver en **lavere** top → skjult svaghed.
- **Bullish:** prisen laver en **lavere** bund, men WT laver en **højere** bund → skjult styrke.

**Hidden (skjult) divergens** — varsler *trend-fortsættelse* (default slået fra):
- **Hidden bearish:** pris lavere top, WT højere top (i en nedtrend → fortsætter ned).
- **Hidden bullish:** pris højere bund, WT lavere bund (i en optrend → fortsætter op).

**Hvor den leder:** primært på **WaveTrend** (wt2). Der er også:
- En **2. WT-range** (default vist): bruger blødere grænser (bearish min **+15**, bullish min
  **−40**) → fanger *flere*, men *svagere* divergenser. De tegnes lidt gennemsigtige.
- **RSI-** og **Stoch-divergenser** (default slået fra) — kan tændes for ekstra bekræftelse.
- De "hårde" WT-grænser for regular divergens er bearish-min **+45**, bullish-min **−65** (dvs.
  divergensen skal ske et stykke ude i overkøbt/oversolgt for at tælle).

**"Lilla trekant".** I forfatterens noter beskrives en lilla trekant som "divergens + WT-kryds i
et yderpunkt" — den stærkeste variant. I praksis tegner denne version divergensen som en **prik +
en kort linje** på bølgen (ikke en bogstavelig trekant i alle tilfælde); i vores anatomi-diagram
har jeg vist den som en trekant for tydelighed. Pointen er: **divergens der falder sammen med et
kryds i overkøbt/oversolgt er det stærkeste tegn.**

---

## 5. RSI + MFI — pengestrømmen (grøn/rød flade + bund-bjælken)

```
rsiMFI = sma( ((close − open) / (high − low)) · 150 , 60 ) − 2.5
```

**Intuition:** `(close − open)/(high − low)` måler hvor i barens spænd lukket ligger — tæt på
toppen (købere vandt) eller bunden (sælgere vandt). Glattet over 60 barer bliver det et
**køber/sælger-pres**-mål. **Grøn når > 0** (penge strømmer ind), **rød når < 0** (ud). Det ses
både som den grøn/røde flade nær nul OG som den tykke bjælke i bunden af panelet.

Det bruges som **bekræftelse**: en grøn WT-bølge oven på grøn pengestrøm er stærkere end bølgen
alene. Parametre: Period 60, multiplier 150, Y-position 2.5 (kosmetisk placering).

---

## 6. RSI-linjen

Almindelig `rsi(close, 14)`, tegnet som en linje der **skifter farve**: grøn når RSI ≤ 30
(oversolgt), rød når RSI ≥ 60 (overkøbt), lilla imellem. Har sine egne divergenser (default
slået fra, kan tændes). Bruges mest som ekstra overkøbt/oversolgt-kontekst.

---

## 7. Stochastic RSI (to linjer + fyld)

En Stochastic beregnet **oven på RSI** (ikke på prisen direkte): `stoch(rsi, …)` glattet til en
**K-** og en **D-linje** (blå/lilla), med et fyld imellem. Reagerer hurtigere end RSI og er god
til at time små vendinger. Parametre: Stoch-længde 14, RSI-længde 14, K-udglatning 3, D-udglatning
3, log-skala til, "gennemsnit af K & D" fra. Har egne divergenser (default fra).

---

## 8. Schaff Trend Cycle (default slået fra)

En cyklus-oscillator (0–100) der reagerer hurtigere end MACD på at fange trend-skift. Kan tændes
som en ekstra linje. Parametre: længde 10, hurtig 23, langsom 50, faktor 0.5. Bruges sjældent i
den daglige aflæsning — mest for erfarne der vil have et hurtigt cyklus-mål oveni.

---

## 9. Sommi Flag & Diamond (default slået fra — avancerede højere-tidsramme-mønstre)

To ekstra bekræftelses-mønstre der inddrager **højere tidsrammer**:

**Sommi Flag (⚑).** Kombinerer: pengestrøm, wt2's niveau, et WT-kryds, OG en WT-VWAP-bølge fra en
**højere tidsramme** (default 720 min = 12h). Bull-flag når alt peger op på tværs af TF; bear-flag
modsat. Markeres med et lille flag-ikon.

**Sommi Diamond (◆).** Bruger **Heikin-Ashi-candles fra to højere tidsrammer** (default 60 og 240
min) sammen med et WT-kryds: bull-diamant når begge HTF-candles er grønne og WT krydser op; bear
modsat. Markeres med en diamant.

Idéen med begge: *"kryds på min graf, bekræftet af retningen på en større tidsramme"*. Kraftfuldt,
men avanceret — lad dem være slået fra indtil Iben er fortrolig med basis.

---

## 10. MACD-farver på WT (default slået fra)

Kan farve WaveTrend-bølgen efter en **MACD på en højere tidsramme** (default 240 min, MACD 28/42/9).
Grønne nuancer når HTF-MACD er bullish, røde når bearish. Rent visuelt lag — ændrer ingen signaler,
giver bare et hurtigt HTF-trend-fingerpeg via farven.

---

## 11. Alle visuelle lag (opsummering af hvad der tegnes)

| Lag | Udseende | Slået til? |
|---|---|---|
| WT1 (hurtig bølge) | lys blå flade | ja |
| WT2 (langsom bølge) | mørk lilla flade | ja |
| Fast WT (wt1−wt2) | hvid flade | ja |
| Pengestrøm (RSI+MFI) | grøn/rød flade + bund-bjælke | ja |
| RSI | farveskiftende linje | ja |
| Stochastic RSI | K/D-linjer + fyld | ja |
| OB/OS-niveauer | step-linjer (±60) + prik-linje (±100/−75) | ja |
| Nul-linje | hvid | ja |
| Signal-cirkler | grøn/rød/guld + små prikker | ja |
| Divergens-prikker/linjer | grøn/rød, offset −2 | ja |
| Schaff Trend Cycle | linje | nej |
| Sommi Flag/Diamond | ⚑ / ◆ | nej |
| MACD-farver | farvet WT | nej |

---

## 12. Indstillinger — grupperne i menuen

Alt kan slås til/fra. De vigtigste grupper: **WaveTrend Settings** (bølge-længder, OB/OS-niveauer,
divergens-grænser), **MFI Settings** (pengestrøm), **RSI Settings**, **Stoch Settings**, **Schaff
Settings**, **Sommi Settings** (HTF-tidsrammer), **MACD Settings**, og **Mode** (Dark mode). For
daglig brug behøver Iben kun standard-opsætningen; resten er finjustering.

---

## 13. Alarmer (alertconditions man kan sætte op)

Køb: grøn cirkel · grøn cirkel + divergens · **GULD-køb** · Sommi bull-flag/diamant · lille grøn
prik. Salg: rød cirkel · rød cirkel + divergens · Sommi bear-flag/diamant · lille rød prik. Man
vælger dem i TradingViews alarm-dialog efter behov.

---

## 14. Tidsrammer

Standard-indstillingerne er tunet til **4h** (og virker på 2h). På **15-min** (som i Ibens
eksempel) kommer der **flere** signaler, men også mere **støj** — divergenserne er svagere og
mindre pålidelige på lave tidsrammer. Generelt: jo højere tidsramme, jo stærkere divergenser.

---

## 15. Sådan aflæses den — konfluens

Den bærende idé: **ét signal alene er svagt; flere der peger samme vej er stærkt.**

- **Long-konfluens:** grøn cirkel i oversolgt **+** bullish divergens **+** grøn pengestrøm
  (+ evt. RSI/Stoch oversolgt og på vej op).
- **Short-konfluens:** rød cirkel i overkøbt **+** bearish divergens **+** rød pengestrøm.

Indikatoren fortæller *hvornår* momentum vender — ikke *hvad* der skal handles. Den bruges til at
**time og bekræfte** setups, ikke som selvstændigt system.

---

## 16. Ærlige forbehold (vigtige at kende)

- **Guld-cirklen:** forfatterens note siger "køb ikke" — modsat den originale Market Cipher. Behandl
  som "ekstremt strakt marked".
- **2-bars forsinkelse på divergenser:** fraktaler bekræftes 2 barer efter → signalet vises lidt
  forsinket. Ikke en fejl, men forklarer "hvorfor kom den så sent".
- **Hidden divergenser** er default slået fra af en grund — de er sværere at bruge; lad dem være
  indtil videre.
- **Begynder-kode:** forfatteren erklærer selv at det er lærings-kode klippet sammen fra andre
  scripts. Det virker, men er ikke institutionel kvalitet.
- **Ingen indikator er sikker.** Alt er sandsynligheder og timing-hjælp, ikke facit.

---

## 17. "Hvis Iben spørger…" — hurtig FAQ til dig

**"Bølgen krydsede, men der kom ingen cirkel?"** → Store cirkler kræver at krydset sker i
overkøbt/oversolgt (`wt2 ≥ 53` eller `≤ −53`). Ellers kun en lille prik.

**"Hvad er forskellen på den lille og den store prik?"** → Lille = et hvilket som helst kryds.
Stor = kryds *i* et yderpunkt (rigtigt signal). Divergens-prikken = kryds hvor der også er divergens.

**"Divergens-linjen kom sent / rykkede sig?"** → 2-bars fraktal-bekræftelse. Normalt.

**"Skal jeg købe på den gyldne prik?"** → Nej — i denne version er den en *advarsel* om et ekstremt
oversolgt marked, ikke et køb.

**"Hvad er den hvide flade / den lilla?"** → Lilla = langsom WT-bølge (wt2). Hvid = Fast WT
(wt1−wt2), dvs. hvor hurtigt momentum ændrer sig.

**"Grøn eller rød bund-bjælke?"** → Pengestrøm: grøn = penge ind (købspres), rød = ud (sælgerpres).

**"Hvorfor virker den bedre på 4h end 15m?"** → Indstillingerne er tunet til 4h, og divergenser er
stærkere på højere tidsrammer; 15m giver flere men mere støjfyldte signaler.

**"Er det et handelssystem?"** → Nej. Det er et timing-/konfluens-værktøj til at bekræfte egne
setups. Kombinér altid flere signaler, og husk risikostyring.
