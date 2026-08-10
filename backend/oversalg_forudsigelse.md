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

*(udfyldes efter luk 2026-08-10)*
