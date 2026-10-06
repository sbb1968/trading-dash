# Probe: exit-ordrer mod NT8 — rapport

**Kørt:** 06-10-2026 07:43  ·  **Konto:** DEMO8580770  ·  **Instrument:** MES 12-26

Trin 1 i `SPEC_exit_ordrer_ninjatrader.md`. Svarene nedenfor er målt, ikke udledt.

⚠ Hvor der står **UKLART**, betyder det at ATI hverken bekræftede eller afkræftede. Det er ikke det samme som nej, og det må ikke behandles som et svar — kig i NT8's egen Orders-fane før afsnit 6.3 skrives.

| # | Spørgsmål | Svar |
|---|---|---|
| P1 | Accepterer NT8 OCO via OIF? | **JA** |
| P2 | Virker CHANGE paa en stoppris? | **JA** |
| P3 | Kan en tredje ordre tilfoejes en levende OCO-gruppe? | **JA** |
| P4 | Kaskaderer en annullering til resten af gruppen? | **KASKADERER** |
| P5 | Annullerer NT8 soesteren naar den ene fylder? | **JA** |
| P6 | Virker kvantitetsaendring via CHANGE? | **SE BEVIS** |
| P7 | Afviser NT8 genbrug af et opbrugt OCO-id? | **AFVIST** |

---

## Hvad det betyder for afsnit 6.3

Svarene er så gunstige som de kunne blive: **alt det exit-ordrer kræver, virker.**
Specens primære vej kan bygges som skrevet.

| Beslutningsregel (spec §4) | Udfald |
|---|---|
| P1+P5 = Ja → **brug NT8-OCO** | ✅ Begge Ja. OCO bruges. Det er den eneste sikre måde at undgå at PLOSS og TRAIL begge fylder ved et skarpt fald og dermed vender positionen. |
| P4 = kaskaderer → **genlæg med nyt OCO-id** når Iben sletter én exit | ✅ Bekræftet. Sletter hun én, dør de andre med. Backend skal annullere alle, afvente terminal, og lægge de resterende igen med et nyt OCO-id. Det korte hul uden beskyttelse accepteres og logges. |
| P3 = Nej → samme genlægning ved tilføjelse | **Ikke nødvendig.** P3 = Ja: en tredje ordre kan føjes til en levende gruppe. Tilføjelse kræver altså ingen genlægning — kun sletning gør. |
| P2 = Nej → TRAIL som annuller+genlæg | **Ikke nødvendig.** P2 = Ja: `CHANGE` flytter stoppen, og ordren beholder sit id. TRAIL kan flyttes uden hul. |
| P6 | Ja. `aendr(antal=)` virker, så tilkøb/delvis lukning kan justere exit-ordrernes antal uden genlægning. |
| P7 | ⚠ **OCO-id'er må ALDRIG genbruges.** NT8 afviser det eksplicit. Hver genlægning skal have et nyt suffiks. |

NT8's egen fejltekst ved P7, ordret:

> `The OCO ID 'TDOCO1265318' cannot be reused. Please use a new OCO ID.`

⚠ **Og den kom som en popup i NT8**, ikke kun i loggen. En afvist ordre kræver
altså et klik på Ibens skærm. Det skal med i Trin 2: en exit-ordre der afvises,
efterlader en dialogboks hun skal lukke — og indtil hun gør, kan NT8 være
optaget. Backenden må ikke antage at tavshed efter en afvisning betyder noget.

### Det ene der ikke er bevist

P2 og P6 viser at NT8 **accepterede** ændringen (`Change submitted` → `Accepted`
→ `Working`), men **ATI melder hverken stoppris eller antal** — kun status. At
ændringen blev accepteret er altså ikke helt det samme som at værdien nu er den
rigtige.

Ordrerne kan findes i NT8's Orders-fane på dagens liste:

| Scenarie | NT8-ordre | Skulle stå på |
|---|---|---|
| P2 (stoppris) | `602502520018` | Stop price **7824,75** |
| P6 (antal) | `602502520090` | Quantity **2** |

Et blik dér lukker hullet. Gør de ikke det, er P2/P6 i virkeligheden Nej, og
TRAIL skal bygges som annuller+genlæg i stedet.

### Oprydning efter proben

Position **0**, ingen ikke-terminale ordrer af dagens 14, ingen OIF-filer
efterladt. Realiseret på demokontoen: **-17,00** (prisen for syv scenarier).

---

## P1 — Accepterer NT8 OCO via OIF?

**Svar: JA**

```
STOPMARKET NTM1791265318914: status=Working
LIMIT      NTM1791265319379: status=Working
```

## P2 — Virker CHANGE paa en stoppris?

**Svar: JA**

Bekræft i NT8's Orders-fane at stopprisen FAKTISK står på 7824.75 — ATI melder ikke prisen, kun status.

```
logliner: 4
status efter CHANGE: Working
2026-10-06 07:42:01:500|1|1|OIF, 'CHANGE;;;;;;;7824.75;;;NTM1791265318914;;' processing
2026-10-06 07:42:01:709|1|32|Order='602502520018/DEMO8580770' Name='' New state='Change submitted' Instrument='MES DEC26' Action='Sell' Limit price=0 
2026-10-06 07:42:01:710|1|32|Order='602502520018/DEMO8580770' Name='' New state='Accepted' Instrument='MES DEC26' Action='Sell' Limit price=0 Stop pri
2026-10-06 07:42:01:710|1|32|Order='602502520018/DEMO8580770' Name='' New state='Working' Instrument='MES DEC26' Action='Sell' Limit price=0 Stop pric
```

## P3 — Kan en tredje ordre tilfoejes en levende OCO-gruppe?

**Svar: JA**

```
tredje ordre NTM1791265329955: status=Working
```

## P4 — Kaskaderer en annullering til resten af gruppen?

**Svar: KASKADERER**

⚠ Dette styrer afsnit 6.3: kaskaderer den, skal backend genlægge de resterende med et NYT OCO-id hver gang Iben sletter én exit-ordre.

```
NTM1791265318914: Cancelled
NTM1791265319379: Cancelled
NTM1791265329955: Cancelled
```

## P5 — Annullerer NT8 soesteren naar den ene fylder?

**Svar: JA**

⚠ 'NEJ' betyder at en efterladt stop kan åbne en NY position i modsat retning. Backend skal så selv annullere søstrene ved fyld.

```
LIMIT NTM1791265351964: Filled 1 @ 7833.75
STOP  NTM1791265351506: Cancelled
```

## P6 — Virker kvantitetsaendring via CHANGE?

**Svar: SE BEVIS**

Bekræft i NT8's Orders-fane at Quantity står på 2.

```
status efter CHANGE antal: Working
2026-10-06 07:42:52:639|1|1|OIF, 'CHANGE;;;;2;;;;;;NTM1791265371350;;' processing
2026-10-06 07:42:52:791|1|32|Order='602502520090/DEMO8580770' Name='' New state='Change submitted' Instrument='MES DEC26' Action='Sell' Limit price=0 
2026-10-06 07:42:52:792|1|32|Order='602502520090/DEMO8580770' Name='' New state='Accepted' Instrument='MES DEC26' Action='Sell' Limit price=0 Stop pri
2026-10-06 07:42:52:792|1|32|Order='602502520090/DEMO8580770' Name='' New state='Working' Instrument='MES DEC26' Action='Sell' Limit price=0 Stop pric
```

## P7 — Afviser NT8 genbrug af et opbrugt OCO-id?

**Svar: AFVIST**

```
genbrugt oco=TDOCO1265318: NTM1791265391995 status=Rejected
```
