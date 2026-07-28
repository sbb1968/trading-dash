# P&L-backtest af long/short-signaturen

Genereret af `backtest_signatur.py`. Validerings-backtest af den
signatur der bestod IS/OOS-testen — ikke en parameterjagt.

**Signal (live-triggerbart, ingen look-ahead):**

```
LONG   z_15m <= -2.0 & rvol_15m >= 1.5 & seneste bekraeftede 3m-prik == kraftig_groen
SHORT  z_15m >= 2.0 & rvol_15m >= 1.5 & seneste bekraeftede 3m-prik == kraftig_roed

Evalueres paa LUKKET 15m-bar -> entry paa naeste bars open.
Target  retur til 15m-middel (SMA30), lagt som limit foer baren aabner
Stop    variant z: middel -/+ 3.5 x std   |   variant atr: entry -/+ 2.0 x ATR
Tid     luk efter 20 barer (300 min)
Omkost. 1.00 $/side kommission + 1 tick slippage/side = 4.50 $ round-trip
```

**Vigtigt om realismen.** Event-datasaettets startbar er en swing-pivot
der foerst bekraeftes 3 barer senere — den kan ikke handles i realtid.
Denne backtest bruger den derfor ikke. Signalet er bygget forfra paa de
raa barer og evalueret paa lukkede barer. Til gengaeld betyder det at
handlerne her IKKE er de samme events som blev valideret; de er hvad
signaturen faktisk ville have udloest live.

---

## 1. Alle varianter, samlet

Hver raekke er én komplet backtest. `netto` = efter kommission og slippage.

| Stop | Ben | Omkost. | n | Hitrate | Gns. vinder $ | Gns. taber $ | PF | Expectancy $ | P&L $ | MaxDD $ | Gns. min |
|---|---|---|---|---|---|---|---|---|---|---|---|
| stop=z | kun hoved | brutto | 1,048 | 26.6 % | 91 | -33 | 0.99 | -0.28 | -296 | -1,527 | 64 |
| stop=z | kun hoved | netto | 1,048 | 26.4 % | 87 | -38 | 0.83 | -4.78 | -5,012 | -5,394 | 64 |
| stop=z | hale | brutto | 1,292 | 26.1 % | 92 | -32 | 1.02 | 0.37 | 479 | -1,820 | 61 |
| stop=z | hale | netto | 1,292 | 25.8 % | 88 | -36 | 0.85 | -4.13 | -5,335 | -5,490 | 61 |
| stop=atr | kun hoved | brutto | 908 | 45.4 % | 87 | -69 | 1.05 | 1.96 | 1,777 | -1,726 | 120 |
| stop=atr | kun hoved | netto | 908 | 44.9 % | 84 | -73 | 0.94 | -2.54 | -2,309 | -3,435 | 120 |
| stop=atr | hale | brutto | 1,188 | 44.3 % | 90 | -68 | 1.05 | 1.87 | 2,225 | -2,215 | 117 |
| stop=atr | hale | netto | 1,188 | 43.4 % | 88 | -72 | 0.94 | -2.63 | -3,121 | -4,367 | 117 |

---

## 2. Pr. side (netto)

**stop=z, kun hovedben**

| Side | n | Hitrate | Gns. vinder $ | Gns. taber $ | PF | Expectancy $ | P&L $ | MaxDD $ | Gns. min |
|---|---|---|---|---|---|---|---|---|---|
| long | 573 | 26.5 % | 93 | -41 | 0.81 | -5.88 | -3,371 | -3,702 | 63 |
| short | 475 | 26.3 % | 81 | -34 | 0.86 | -3.45 | -1,641 | -2,792 | 65 |

**stop=atr, kun hovedben**

| Side | n | Hitrate | Gns. vinder $ | Gns. taber $ | PF | Expectancy $ | P&L $ | MaxDD $ | Gns. min |
|---|---|---|---|---|---|---|---|---|---|
| long | 501 | 46.7 % | 89 | -80 | 0.98 | -0.96 | -479 | -1,992 | 118 |
| short | 407 | 42.8 % | 77 | -65 | 0.88 | -4.50 | -1,831 | -2,385 | 121 |

---

## 3. Hvorfor blev handlerne lukket?

**stop=z**

| Aarsag | n | Andel | Median point | P&L netto $ |
|---|---|---|---|---|
| target | 225 | 21.5 % | 13.49 | 19,650 |
| stop | 757 | 72.2 % | -5.10 | -28,712 |
| tid | 66 | 6.3 % | 9.00 | 4,050 |

**stop=atr**

| Aarsag | n | Andel | Median point | P&L netto $ |
|---|---|---|---|---|
| target | 316 | 34.8 % | 14.15 | 27,600 |
| stop | 468 | 51.5 % | -12.79 | -35,710 |
| tid | 124 | 13.7 % | 7.50 | 5,801 |

---

## 4. Pr. futures-kontrakt (netto, kun hovedben)

Spec B4: afkastet maa ikke haenge paa én periode eller ét regime.

**stop=z**

| Periode | Side | n | Hitrate | Expectancy $ | P&L $ |
|---|---|---|---|---|---|
| kontrakt 0 | long | 66 | 26 % | -2.39 | -157 |
| kontrakt 0 | short | 59 | 25 % | -16.85 | -994 |
| kontrakt 0 | begge | 125 | 26 % | -9.21 | -1,152 |
| kontrakt 1 | long | 84 | 33 % | 2.96 | 248 |
| kontrakt 1 | short | 59 | 15 % | -10.05 | -593 |
| kontrakt 1 | begge | 143 | 26 % | -2.41 | -344 |
| kontrakt 2 | long | 81 | 23 % | -8.76 | -710 |
| kontrakt 2 | short | 53 | 34 % | -4.34 | -230 |
| kontrakt 2 | begge | 134 | 28 % | -7.02 | -940 |
| kontrakt 3 | long | 66 | 24 % | -6.00 | -396 |
| kontrakt 3 | short | 67 | 22 % | -0.11 | -7 |
| kontrakt 3 | begge | 133 | 23 % | -3.03 | -403 |
| kontrakt 4 | long | 62 | 31 % | -3.50 | -217 |
| kontrakt 4 | short | 58 | 21 % | -8.36 | -485 |
| kontrakt 4 | begge | 120 | 26 % | -5.85 | -701 |
| kontrakt 5 | long | 71 | 21 % | -14.98 | -1,064 |
| kontrakt 5 | short | 48 | 40 % | 15.05 | 722 |
| kontrakt 5 | begge | 119 | 29 % | -2.87 | -342 |
| kontrakt 6 | long | 82 | 28 % | -4.21 | -345 |
| kontrakt 6 | short | 55 | 25 % | -14.80 | -814 |
| kontrakt 6 | begge | 137 | 27 % | -8.46 | -1,159 |
| kontrakt 7 | long | 58 | 26 % | -6.53 | -378 |
| kontrakt 7 | short | 70 | 31 % | 12.08 | 846 |
| kontrakt 7 | begge | 128 | 29 % | 3.65 | 467 |
| kontrakt 8 | long | 3 | 0 % | -117.26 | -352 |
| kontrakt 8 | short | 6 | 17 % | -14.26 | -86 |
| kontrakt 8 | begge | 9 | 11 % | -48.59 | -437 |

**stop=atr**

| Periode | Side | n | Hitrate | Expectancy $ | P&L $ |
|---|---|---|---|---|---|
| kontrakt 0 | long | 56 | 48 % | 5.94 | 333 |
| kontrakt 0 | short | 52 | 44 % | -12.21 | -635 |
| kontrakt 0 | begge | 108 | 46 % | -2.80 | -302 |
| kontrakt 1 | long | 74 | 53 % | 1.06 | 79 |
| kontrakt 1 | short | 50 | 32 % | -6.93 | -347 |
| kontrakt 1 | begge | 124 | 44 % | -2.16 | -268 |
| kontrakt 2 | long | 78 | 42 % | -6.28 | -490 |
| kontrakt 2 | short | 48 | 54 % | -1.35 | -65 |
| kontrakt 2 | begge | 126 | 47 % | -4.40 | -555 |
| kontrakt 3 | long | 55 | 47 % | 8.26 | 455 |
| kontrakt 3 | short | 52 | 42 % | -4.83 | -251 |
| kontrakt 3 | begge | 107 | 45 % | 1.90 | 203 |
| kontrakt 4 | long | 53 | 53 % | -0.69 | -37 |
| kontrakt 4 | short | 49 | 37 % | -10.71 | -525 |
| kontrakt 4 | begge | 102 | 45 % | -5.50 | -561 |
| kontrakt 5 | long | 60 | 42 % | -12.95 | -777 |
| kontrakt 5 | short | 43 | 53 % | 10.90 | 469 |
| kontrakt 5 | begge | 103 | 47 % | -3.00 | -309 |
| kontrakt 6 | long | 73 | 44 % | -2.88 | -210 |
| kontrakt 6 | short | 47 | 38 % | -11.19 | -526 |
| kontrakt 6 | begge | 120 | 42 % | -6.13 | -736 |
| kontrakt 7 | long | 49 | 45 % | -0.35 | -17 |
| kontrakt 7 | short | 60 | 42 % | -0.44 | -26 |
| kontrakt 7 | begge | 109 | 43 % | -0.40 | -43 |
| kontrakt 8 | long | 3 | 67 % | 62.21 | 187 |
| kontrakt 8 | short | 6 | 50 % | 12.54 | 75 |
| kontrakt 8 | begge | 9 | 56 % | 29.10 | 262 |

---

## 5. Hvor meget aeder omkostningerne?

Det afgoerende regnestykke: bruttoen pr. handel mod round-trip-
omkostningen. Er bruttoen mindre, findes der ingen omkostningsstruktur
der redder strategien — kun en anden exit-model kan.

| Stop | Ben | Brutto/handel $ | Omkostning/handel $ | Break-even i ticks (round-trip) | Daekker bruttoen omkostningen? |
|---|---|---|---|---|---|
| stop=z | kun hoved | -0.28 | 4.50 | — | **NEJ** |
| stop=z | hale | 0.37 | 4.50 | 0.3 | **NEJ** |
| stop=atr | kun hoved | 1.96 | 4.50 | 1.6 | **NEJ** |
| stop=atr | hale | 1.87 | 4.50 | 1.5 | **NEJ** |

Til sammenligning koster ét tick $1,25, og round-trip med 1 tick
slippage pr. side + $1.00 kommission pr. side er $4.50.

**Foelsomhed — netto expectancy ved forskellige omkostningsniveauer:**

| Stop | Ben | $0.00 | $1.00 | $2.00 | $3.00 | $4.50 | $6.00 |
|---|---|---|---|---|---|---|---|
| stop=z | kun hoved | -0.28 | -1.28 | -2.28 | -3.28 | -4.78 | -6.28 |
| stop=z | hale | 0.37 | -0.63 | -1.63 | -2.63 | -4.13 | -5.63 |
| stop=atr | kun hoved | 1.96 | 0.96 | -0.04 | -1.04 | -2.54 | -4.04 |
| stop=atr | hale | 1.87 | 0.87 | -0.13 | -1.13 | -2.63 | -4.13 |

Kun kolonnerne helt til venstre er positive — og de svarer til at
handle uden kommission og uden slippage.

---

## 6. Forbehold og fortolkning

1. **z-stoppet er strukturelt skaevt** — og det er en egenskab ved
   selve reglen, ikke ved implementeringen. Stoppet ligger fast ved
   |z| = 3.5, mens signalet kan fyre hvor som helst fra |z| = 2.0 og
   nedefter. Fyrer det ved |z| = 3.2, ligger stoppet 0,3 std vaek, og
   handlen stoppes naesten med det samme: **47 % af alle stops i
   z-varianten rammer paa selve entry-baren**. ATR-stoppet giver en
   konstant risiko pr. handel og er derfor den variant der siger noget
   om signalet. Jeg har ikke aendret z-reglen — det ville vaere den
   parameterjagt specen forbyder — men den skal ikke laeses som et
   ligevaerdigt alternativ.
2. **~99 % retningstraef betoed aldrig 99 % vinderhandler.** Det tal
   blev maalt BLANDT event-starter, altsaa betinget af at en
   bevaegelse allerede var defineret. Det rigtige tal at forvente er
   praecisionen (~30-40 %), og backtestens hitrate paa 27-45 % ligger
   praecis der. Valideringen og backtesten modsiger ikke hinanden.
3. **Handlerne her er ikke de validerede events.** Signalet fyrer paa
   ~1.340 barer, hvoraf kun ~380 er event-starter. Resten er de ~70 %
   hvor signaturen fyrer uden at en stor bevaegelse foelger. En
   backtest skal tage dem alle med — det er dem der betaler regningen.
4. **Kun ét exit-design er afproevet.** At target = middelvaerdien er
   statistisk velbegrundet (bevaegelserne doer der), men et signal kan
   have edge og alligevel tabe med et bestemt stop/target-forhold.
   Resultatet her afviser dette design — ikke enhver anvendelse af
   signaturen.
5. **Ingen sizing, ingen filtrering paa tid/regime.** Fast 1 kontrakt,
   alle timer, alle dage. Expectancy pr. handel er rapporteret, saa
   sizing kan laegges ovenpaa senere.

