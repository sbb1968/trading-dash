# NinjaTrader/Tradovate — hvad der faktisk er adgang til

**Målt 11-08-2026 på Sørens workstation.** Undersøgelse, ikke integration.
Der er ikke sendt en eneste ordre.

---

## Spørgsmålet

Kan Trading Dash på sigt eksekvere futures gennem NinjaTrader, sådan som
`spec_execution_ninjatrader.md` forudsætter?

---

## 1. Tradovate REST — vejen er spærret ved API-nøglen

| Trin | Resultat |
|---|---|
| Endepunkterne lever | ✓ `demo` og `live.tradovateapi.com` svarer begge |
| `api.ninjatrader.com` | ✗ **findes ikke** (DNS fejler) |
| Credentials | ✓ `sbborup1968` **accepteres** |
| Registreret applikation | ✗ `"The app is not registered"` |

⚠ **Beviset for at login passerede, er en kontrast.** Samme endepunkt, kun
brugernavnet ændret:

```
opdigtet bruger  ->  "Incorrect username or password"
sbborup1968      ->  "The app is not registered"
```

Skifter svaret, er login-delen forbi. Uden den sammenligning ville den anden
fejl let læses som endnu en loginfejl — og man ville nulstille et password der
virker.

⚠ **HTTP 200 betyder ikke succes her.** Et mislykket login svarer 200 med
`errorText`. Kode der bruger `raise_for_status()` som kontrol, tror det lykkedes.

### Der findes ingen API-side i weboverfladen

`web.ninjatrader.com` → Settings har kun **Preferences** og **Security**.
Ingen "API Access", intet "Application Settings", ingen nøgler.

**Trusted Devices** afslører til gengæld hvilke `appId`-værdier der ER
registrerede: `Ninja_trader(web)`, `tradovate_trader(web)`, `NinjaTrader8`,
`client_trader_dashboard`.

⚠ **De skal ikke bruges.** At sende en af dem som vores `appId` er at udgive sig
for NinjaTraders egen klient. At det teknisk ville virke, gør det ikke rigtigt:
det er sandsynligvis i strid med deres vilkår, det brister ved enhver ændring i
deres ende, og det er et dårligt fundament for noget der senere skal handle for
rigtige penge.

⚠ Bemærk også **"Powered by Payward Europe Digital Solutions (CY) Ltd."** —
EU-udgaven drives af Krakens europæiske selskab. API-adgang er ikke nødvendigvis
en del af det produkt. Det er værd at spørge om direkte frem for at lede videre.

---

## 2. ⚠ NinjaTrader Desktop ATI — den er ÅBEN allerede

```
NinjaTrader.exe   PID 33684   kører nu
  0.0.0.0:36973   ← ATI (Automated Trading Interface)
  0.0.0.0:4530

C:\Users\soren\Documents\NinjaTrader 8\incoming\   ← findes
```

Begge porte accepterer en forbindelse. **Ingen Tradovate-nøgle involveret.**

ATI er NinjaTraders lokale automatiserings-grænseflade og taler to sprog:

- **filer** — OIF-kommandoer lagt i `incoming\`
- **TCP** — samme kommandoer over port 36973

⚠ **Forbindelsestesten sendte INGEN bytes.** ATI tager imod kommandoer som ren
tekst (`PLACE;konto;instrument;…`), og enhver stump data kunne i værste fald
fortolkes som en ordre. Der blev åbnet og lukket, intet andet.

### Formen ligner den vi allerede har bygget

Det er samme todeling som konto 2: **læs ét sted, skriv et andet.**

```
markedsdata   IBKR / TradingView     (som i dag)
eksekvering   NT8 ATI, lokalt        (ny)
```

ATI streamer ikke markedsdata — det skal den heller ikke. Vi har kurser.

---

## 3. Hvad der ikke er afprøvet

- **at ATI-serveren er slået til i NT8's egne indstillinger.** Porten lytter,
  men `Tools → Options → Automated Trading Interface → Enable` er ikke aflæst.
  En lyttende port beviser ikke at kommandoer accepteres.
- **at en ordre kan lægges.** Det kræver en rigtig OIF-kommando mod
  simulationskontoen, og det skal være et bevidst valg — ikke næste linje i et
  script.
- **hvilken konto NT8 er forbundet til.** `DEMO8580770` i weboverfladen; om
  desktop-platformen peger samme sted, er ikke aflæst.

---

## Anbefaling

**Tag ATI-vejen.** Den er åben i dag, kræver ingen tilladelse fra nogen, og har
samme form som den to-forbindelses-opsætning vi allerede har bevist virker.

Tradovate REST kan søges parallelt — skriv til NinjaTraders support og spørg
konkret om **API-adgang til en simulationskonto på EU-entiteten**. Men lad det
ikke blokere noget: svaret kan tage dage, og ATI venter ikke på det.

⚠ Og uanset vej: det er stadig **futures-only**. Det ændrer intet ved crypto
(§11) eller ved de europæiske aktier — begge dele ligger uden for dette lag.
