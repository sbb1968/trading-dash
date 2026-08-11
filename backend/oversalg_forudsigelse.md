# Præregistreret forudsigelse — IOVA

**Skrevet:** 2026-08-10, mens begge positioner er ÅBNE
**Konto:** DUO509856 · `paper_trading=True` · præfiks `D` = paper. Bekræftet før
denne forudsigelse blev godkendt. En ny short koster ingenting.

⚠ **Denne fil committes FØR udfaldet kendes.** Det er hele pointen: git-tidsstemplet
er beviset for at forudsigelsen kom først. Retrospektiv størrelsesmatchning — som
O2-rapporten hviler på — er altid svagere end den ser ud, fordi man ved hvad man
leder efter.

---

## 1. Udgangspunktet, målt

```
IBKR-konto DUO509856:   IOVA  +78   (kostpris 6,3056)

Åbne journalrækker:
  BuyTheDip     long  39 stk   entry 2026-08-10 09:57 ET  @ 6,29
  Konfluens 2   long  39 stk   entry 2026-08-10 09:53 ET  @ 6,265
```

39 + 39 = 78. Kontoen og journalen er **enige** lige nu. To strategier holder
hver sin lige store andel af samme ticker.

---

## 2. Mekanismen der forudsiges

`strategy_base._ibkr_still_holds()` spørger IBKR om positionen stadig findes, før
en lukkeordre gen-afgives. Den læser **kontoens** nettoposition og betragter
enhver resterende eksponering på samme side som "holder stadig".

På en ticker to strategier deler, kan den ikke skelne *"mine aktier er der endnu"*
fra *"den anden strategis aktier er der"*.

Kæden:

1. Strategi A sender SELL 39. Den fylder, men bekræftelsen misses.
2. A spørger `_ibkr_still_holds("IOVA", "long", 39)`. IBKR viser **+39** — B's
   aktier. Samme side → **True** = "ægte ufyldt, genforsøg".
3. A sender SELL 39 igen. 78 solgt, 78 var der. Kontoen flad — men **B's aktier er
   væk**, og B ved det ikke.
4. B lukker senere sine 39. Kontoen går til **−39**.

---

## 3. FORUDSIGELSEN

> **Opstår over-salget, ender kontoen på præcis IOVA −39** — én strategis andel,
> ikke to.

Forudsigelsen er **betinget**, og det skal siges rent: over-salget kræver at en
bekræftelse mistes. Det sker ikke hver gang. Et fladt udfald betyder derfor ikke
at mekanismen er forkert — det betyder at udløseren ikke fyrede.

---

## 4. Hvad der bekræfter, og hvad der falsificerer

| Udfald | Fortolkning |
|---|---|
| **IOVA −39** | **Stærk bekræftelse.** Præcis én andel, som mekanismen kræver |
| **IOVA 0 (fladt)** | Hverken/eller. Udløseren fyrede ikke. Mekanismen står uafklaret |
| **IOVA −78** | ⚠ **Falsificerer den nuværende mekanisme.** Begge strategier over-solgte, hvilket kræver en anden forklaring |
| **Andet tal** (fx −19, −45) | ⚠ **Mekanismen er ufuldstændig.** Andelene er 39/39; enhver anden short peger på delvise fyldninger eller en tredje kilde |
| **IOVA +39** | Kun én strategi lukkede. Ikke over-salg — den anden hænger stadig |

⚠ Et fladt udfald må **ikke** læses som "så var der ikke noget galt". Fejlen er
betinget af en tabt bekræftelse; fraværet af den betingelse siger intet om koden.
Skulle nogen senere finde denne fil, er det den vigtigste sætning i den.

---

## 5. Måling

Måles efter markedets luk 16:00 ET, når begge rækker er lukkede:

```bash
curl -s -H "X-Internal-Key: <noegle>" http://100.76.201.59:8000/account/dash-snapshot
curl -s -H "X-Internal-Key: <noegle>" \
  "http://100.76.201.59:8000/journal/trades?symbol=IOVA&date_from=2026-08-10"
```

Notér: kontoens IOVA-position, begge rækkers exit-tid og exit-pris, og om der
ligger flere lukkerækker end de to åbnede.

**Udfaldet skrives ind nedenfor — ikke i en ny fil, og uden at ovenstående
redigeres.**

---

## 6. UDFALD

**Målt 2026-08-11 kl. 07:25 dansk. Udfald: IOVA 0 — fladt.**

Begge rækker lukkede normalt, med fyldning og pris:

| Strategi | Stk. | Entry | Exit | Årsag |
|---|---|---|---|---|
| Konfluens 2 | 39 | 09:53:00 @ 6.265 | 13:05:01 @ 6.46 | `trail_pct` |
| BuyTheDip | 39 | 09:57:59 @ 6.29 | 12:06:16 @ 6.54 | `target` |

78 købt, 78 solgt, kontoen flad. IOVA står ikke i IBKR's opgørelse.

⚠ Grundlaget i §1 var rigtigt. Der er en tredje IOVA-række i journalen, men den er
fra **16. juli** (Relativ Styrke, 17 stk.) — ikke fra måledagen. Nettoen d. 10.
august var altså præcis de +78 som §1 målte, og forudsigelsen blev afprøvet på det
grundlag den blev skrevet på.

### Fortolkning — efter §4's egen tabel

**Hverken bekræftet eller falsificeret.** Udløseren fyrede ikke.

§4's advarsel gælder ordret: dette må ikke læses som "så var der ikke noget galt".

### ⚠ Og nu ved vi hvorfor udløseren ikke fyrede

Begge lukkeordrer **fyldte prompte** — begge rækker har exit-pris og exit-årsag.
Genafgivelsesvejen, hvor `_ibkr_still_holds` overhovedet bliver spurgt, åbner sig
kun når en lukkeordre *ikke* fylder. Eksperimentet var derfor aldrig ladt.

Det er en brugbar præcisering af mekanismen: **delt ticker er ikke nok.** Der skal
også en ufyldt lukkeordre til. Det er netop den betingelse `_lukkeordre_ufyldt`
(d42cd75) griber.

---

## 7. Det stærkere bevis — seks andre tickere

Samme morgen viste afstemningen noget IOVA ikke kunne: **journalen har nul åbne
rækker, IBKR holder syv positioner.**

| Ticker | IBKR | Seneste journal-lukning | Journalrækker |
|---|---|---|---|
| TE | **−86** | 08-10 14:24, 46 stk. | 9, alle lukkede |
| NUAI | **−46** | 08-10 15:27, 47 stk. | 8, alle lukkede |
| ALOY | **−24** | 08-07 12:28, 20 stk. | 9, alle lukkede |
| VELO | **−19** | 08-10 10:23, 17 stk. | 7, alle lukkede |
| XE | **−12** | 08-06 15:27, 11 stk. | 5, alle lukkede |
| WOLF | **−10** | 08-10 12:12, 8 stk. | 8, alle lukkede |
| SHAZ | +4 | — | **0** — ældre end journalen |

⚠ **Alle seks shorts er opstået på strategier der kun går long.** En long-only
strategi kan ikke ende i en short ved at handle som tiltænkt. Aktierne er solgt to
gange.

⚠ **Størrelserne er hele positioner, ikke brøkdele.** −46 er en 46-lot. −24 er en
24-lot. −86 er 40+46. Det er signaturen på et dobbeltsalg — ikke på delvise
fyldninger, som ville give skæve tal.

Det er stærkere end IOVA-forsøget ville have været, og det peger samme vej:
mekanismen i §2 er virkelig. IOVA viste kun at den er **betinget**, præcis som §3
sagde.

