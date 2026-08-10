# Trading Dash mod konto 2 — opsætning og flytning

**Formål:** Trading Dash handler manuelt på en konto gennem en **lokal IB Gateway**,
mens kurserne kommer fra algoserverens forbindelse. Bygget og bevist på Sørens
workstation mod DUQ441063, så det kan flyttes til Ibens maskine med en kendt
opskrift.

⚠ **Ingen maskinnavne, stier eller adresser står i koden.** Alt maskinspecifikt
står i `account.yaml` og er nævnt nedenfor. Kan opsætningen ikke flyttes ved at
rette den fil alene, er der en fejl at rette.

---

## 1. Formen

| | Forbindelse | Bruger | Formål |
|---|---|---|---|
| **Læs** | Algoserverens Gateway, over Tailscale | `fasteriben` | Markedsdata, kurser |
| **Skriv** | Lokal Gateway på maskinen | `fasteriben2` | Ordrer, positioner, konto |

De slås ikke om noget, **fordi kun den ene beder om data**. Konflikten opstod
fordi TWS automatisk abonnerer på alt i watchlisten ved opstart. En Gateway gør
ingenting af sig selv — den beder først om data når en klient gør det.

**TWS er ikke nødvendig nogen steder i denne opsætning.**

---

## 2. Forudsætninger — tjek FØR noget installeres

| # | Hvad | Hvor | ⚠ |
|---|---|---|---|
| F1 | **Futures-tilladelse** på kontoen | Client Portal → Settings → Trading Permissions | Paper arver fra live. Mangler den, afvises MES uanset hvor rigtigt alt andet er sat op |
| F2 | **Porten er fri** | `netstat -ano \| findstr :4002` | IB Gateway paper lytter på 4002 |
| F3 | Gateway-version noteret | Gateway → Help → About | Skal med i denne fil når den flyttes |

Fejler F1, **stop** — tilladelsen skal søges før resten giver mening.

---

## 3. Installation

1. **IB Gateway** (ikke TWS) installeres på maskinen.
2. Gateway → Configure → Settings → API:
   - Enable ActiveX and Socket Clients: **✓**
   - Socket port: **4002** (paper)
   - Read-Only API: **✗**
   - Allow connections from localhost only: **✓**
   - **Master API client ID: lad stå tom.** Sættes den, modtager enhver klient
     med det id ordreopdateringer fra *alle* klienter — og kode der antager "de
     ordrer jeg kan se, er mine" bliver da forkert uden at fejle.

---

## 4. ⚠ Credentials

**Password gemmes ikke.** Gateway startes manuelt med login-dialog.

Maskinen er en arbejdsplads hvor et menneske alligevel er til stede når der
handles manuelt — i modsætning til algoserveren, hvor uovervåget genstart kræver
lagring. Så her er der ingen grund til at løbe risikoen.

**Aldrig i repoet.** Er automatisk start senere nødvendig, læses passwordet fra en
fil **uden for** repoet med begrænsede rettigheder — og den fil skal nævnes her
når den oprettes.

---

## 5. Konfiguration — det eneste der ændres pr. maskine

I `account.yaml` under `instance:`:

```yaml
  ordre_forbindelse:
    host:   127.0.0.1        # Gatewayen kører lokalt
    port:   4002             # IB Gateway paper
    konto:  DUQ441063        # ⚠ den konto ordrer SKAL lande på
    bruger: fasteriben2      # dokumentation + fejlbesked ved forkert login
    # tillad_live: true      # KUN hvis en live-konto bevidst skal bruges
```

Mangler blokken, går ordrer gennem den delte forbindelse som hidtil. Maskiner der
ikke skal skille tingene ad, ændrer altså ingenting.

**Client-id** kommer fra `ibkr_client_ids.ORDRE` (**201**) og skal ikke sættes her.

---

## 6. Vagterne — hvad der spærrer, og hvorfor

| | Vagt | Udløser | Hvad der sker |
|---|---|---|---|
| V1 | Kontobekræftelse | Gatewayen styrer en anden konto end den konfigurerede | **Hård fejl.** Forbindelsen lukkes, ingen ordrer |
| V2 | Paper-bekræftelse | Kontoen mangler D-præfiks og `tillad_live` er ikke sat | **Hård fejl.** Tjekkes både på konfigurationen (før connect) og på det IBKR melder |
| V3 | Konto på ordren | `kraev_konto=True` — en ordre uden konto sendes aldrig | Ordren afvises frem for at lande hvor IBKR selv vælger |

**En spærret vagt falder ALDRIG tilbage til den delte forbindelse.** Så ville
ordren lande på en anden konto end brugeren tror — den værst tænkelige udgang, og
den ville se ud som en succes.

`test_ordre_forbindelse.py` viser hver vagt udløse **og** slippe det rigtige
igennem.

---

## 7. Testen — tjekliste ved flytning

Kør hele listen på den nye maskine. Kan den ikke køres igennem, er flytningen
ikke bevist — den er håbet.

| # | Handling | Verificér | ☐ |
|---|---|---|---|
| T0 | `python algoserver_vagt.py --gem` | Udgangspunkt gemt (exit 0) | ☐ |
| T1 | Gateway startet, API forbinder | Forbindelsen kommer op. **Notér om der kommer nogen konfliktbesked overhovedet** | ☐ |
| ↳ | `python algoserver_vagt.py --i-vindue` | ⚠ exit 0. Andet = STOP | ☐ |
| T2 | `reqAccountSummary` | Svarer, og kontoen er den rigtige | ☐ |
| ↳ | `python algoserver_vagt.py --i-vindue` | ⚠ exit 0. Andet = STOP | ☐ |
| T3 | `reqPositions` | Svarer uden fejl (tomt svar = flad konto, ikke fejl) | ☐ |
| ↳ | `python algoserver_vagt.py --i-vindue` | ⚠ exit 0. Andet = STOP | ☐ |
| T4 | Limitordre MES, 1 stk., pris langt fra markedet | Accepteres. `orderRef` = `manuel:…`, konto korrekt | ☐ |
| T5 | Annullér T4 | Annulleringen bekræftes | ☐ |
| T6 | Marketable ordre, MES, 1 stk. | **Fill modtaget** | ☐ |
| T7 | **Positionskontrol** | Positionen står i **den rigtige konto** — og **ikke** i den anden | ☐ |
| T8 | Journalkontrol | Rækken har korrekt konto, `paper`-flag og `orderRef` | ☐ |
| T9 | Luk positionen | Fill modtaget | ☐ |
| T10 | Fladkontrol | Ingen position tilbage | ☐ |
| ↳ | `python algoserver_vagt.py --i-vindue` | ⚠ sidste kontrol | ☐ |

⚠ **T7 er testens kerne.** Det er ikke nok at ordren blev fyldt — den skal
beviseligt være landet i den rigtige konto og beviseligt ikke i den forkerte. En
ordre der havner forkert, ville se ud som en succes hvis man kun kigger på
fill-bekræftelsen, og det er den fejl der ville koste mest ved flytningen.

**Notér fejlkoder ordret.** Trinnet og koden afgør om det er handelsadgang,
session eller konfiguration der mangler.

MES handler næsten døgnet rundt, så testen behøver ikke vente på et
markedsvindue.

---

## 8. ⚠ Algoserveren må ikke ryge af — vinduet og afbrydelsesreglen

### 8.1 Kontostrukturen, som den faktisk er

Ét hovedlogin, `fasteriben`, med to konti under sig. Konto 2 har fået tildelt
TWS-brugeren `fasteriben2`. Det er derfor delings-dropdownen peger på
`fasteriben` — **abonnementet hænger på hovedloginet, ikke på den ekstra bruger.**

Forskellige brugernavne må efter IBKR's egen model gerne have samtidige sessioner
— det er hele formålet med ekstra brugere. Og vi har målt det én gang: da
workstationen loggede på TWS som `fasteriben2`, **blev algoserveren kørende**.
Det var den nytilkomne der blev nægtet data, ikke den siddende. Gateway-vejen er
en svagere påvirkning end den, fordi den slet ikke beder om markedsdata.

Men det er ét enkelt tilfælde. Vi tester som om det kunne gå galt.

### 8.2 Vinduet: dansk formiddag, cirka 08:00–14:00

Da er algoserverens Gateway oppe — så samtidigheden bliver **reelt** afprøvet —
men strategierne handler ikke, fordi auto-start først er kl. 15:20. Går noget
galt, er der en hel dag til at rette det inden den skal i gang.

MES handler næsten døgnet rundt og er rigelig likvid til én kontrakt, så testen
mister ingenting ved at ligge dér.

### 8.3 Afbrydelsesreglen — en kommando, ikke en huskeregel

```bash
python algoserver_vagt.py --gem                 # FØR T1: gem udgangspunktet
python algoserver_vagt.py --i-vindue            # efter T1, T2, T3 — og efter T10
```

⚠ **`--i-vindue` hører til vinduet 08:00–14:00**, hvor strategierne ikke handler.
Da tæller et ændret positionstal med som stopsignal. Kører du uden for vinduet,
så udelad flaget — ellers råber vagten ved hver eneste handel, og *en vagt der
råber hele tiden, lærer man at se forbi på en time.* Mistet forbindelse, stoppet
algo og skiftet konto stopper altid, uanset flaget.

| Exit | Betyder |
|---|---|
| **0** | Uændret — fortsæt |
| **1** | ⚠ **ÆNDRET — STOP** |
| **2** | Kunne ikke måles. **Ikke** det samme som uændret |

⚠ Exit 2 er ikke grønt. Kan tilstanden ikke måles, ved vi ikke om den er
uændret — og "vi ved det ikke" må ikke se ud som "alt er fint". Samme skelnen som
`_lukkeordre_ufyldt`s `None`.

**Sker det:**

1. Luk workstationens Gateway
2. Bring algoserveren op
3. Rapportér **hvilket trin** der udløste det
4. **Fortsæt ikke** for at se om det var et tilfælde

Sker det, er det i sig selv det vigtigste resultat af hele øvelsen — og langt
billigere at opdage nu, hvor intet står på spil, end på en dag hvor strategierne
har positioner.

Vagten sammenligner: IBKR-forbindelse, `algo_running`, kontonummer og antal
positioner. I test-vinduet handler strategierne ikke, så **også et ændret
positionstal er et signal**.

---

## 9. Værdier for denne maskine

*(udfyldes pr. maskine)*

| Felt | Sørens workstation | Ibens maskine |
|---|---|---|
| Gateway-version | `______` | `______` |
| Port | 4002 | `______` |
| Konto | DUQ441063 | `______` |
| Bruger | fasteriben2 | `______` |
| Futures-tilladelse (F1) | ☐ | ☐ |
| Master API client ID | `______` | `______` |
| T1: konfliktbesked? | `______` | `______` |
