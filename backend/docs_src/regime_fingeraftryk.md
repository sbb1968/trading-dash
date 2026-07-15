# Regime-fingeraftryk — forstå dit marked

Dette vindue fortæller dig **hvilken slags marked vi står i lige nu**, og dermed hvilke
strategier der har medvind. Markeder skifter karakter over tid: nogle perioder belønner
momentum, andre stock-picking, andre mean-reversion. En strategi der virker glimrende i ét
regime kan tabe penge i et andet. Regime-fingeraftrykket måler regimet objektivt, så vi ikke
gætter — og det er fundamentet for en fremtidig "meta-strategi" der automatisk vælger mellem
vores strategier efter regimet.

**Vigtigt:** Fingeraftrykket er **beskrivende**. Det siger hvad markedet *gør* — ikke hvad du
skal købe. Det er ingen handelsanbefaling og lover ingen gevinst. Det er et landkort, ikke et
kompas.

---

## De tre markedsregimer

Alt i vinduet peger i sidste ende på ét af tre regimer (eller "blandet"). Her er hvad de betyder,
hvad der kendetegner dem, og hvilke af vores strategier der passer til dem.

### 1. Stock-picking-marked (relativ værdi)
**Kendetegn:** Aktierne bevæger sig meget forskelligt fra hinanden (høj spredning), og indekset
som helhed har ingen pålidelig retning. De rigtige navne løber, de forkerte gør ikke — og der er
et bredt spænd imellem.

**Hvad giver edge:** At *vælge mellem* navne. Det nytter ikke at ride markedet (det går ingen
steder), men det betaler sig at finde de stærkeste aktier.

**Vores strategi:** **Relativ Styrke** — rangerer dagens navne mod hinanden og køber de stærkeste.

### 2. Momentum-marked
**Kendetegn:** Det der er stærkt fortsætter. Morgenretningen holder, bevægelsen er koncentreret
tidligt på dagen, og der er en vis trend i indekset.

**Hvad giver edge:** At ride bevægelsen — købe styrke der fortsætter.

**Vores strategier:** **Konfluens 2** og **Trend Join Long** (momentum-breakout).

### 3. Mean-reversion-marked
**Kendetegn:** Bevægelser overdriver og trækkes tilbage inden for dagen (choppy). Morgenretningen
vender oftere end den holder.

**Hvad giver edge:** At købe overdrivelsen den anden vej — dykket efter et for voldsomt fald.

**Vores strategi:** **BuyTheDip** (køber dykket efter en impuls).

### "Blandet / uklart"
Ingen enkelt familie dominerer tydeligt. Så er der ingen klar favorit — kør bredt eller afvent
et klarere billede.

---

## Vinduet linje for linje

Vinduet har fem sektioner. Her forklares hver information: **hvad den måler, hvilke værdier den
kan have, og hvordan du læser dem.**

### Øverst: Nuværende regime
Den store overskrift (fx *"Markedet lige nu er et Stock-picking-marked"*) er konklusionen — det
regime alle tallene nedenunder samlet peger på. Under den står en klar beskrivelse og linjen
**"Passer bedst til:"** som nævner den strategifamilie der har medvind i netop dette regime.
Farven skifter med regimet (grøn = stock-picking, gul = momentum, blå = mean-reversion).

Linjen **"måler perioden ÅÅÅÅ-MM-DD … ÅÅÅÅ-MM-DD"** fortæller hvilke ~30 handelsdage tallene er
baseret på. Bemærk: det er ikke nødvendigvis helt op til i dag — se afsnittet om data-friskhed.

### Sektion: Hvad markedet gør lige nu
De seks rå målinger, oversat til klart sprog. Dette er "beviserne" bag regime-konklusionen.

**Spredning mellem aktier** — *hvor uafhængigt navnene bevæger sig.*
Måler hvor forskelligt dagens aktier bevæger sig fra hinanden (den daglige spredning i procent).
- **Stor** (≥ 3,8 %): navnene går hver sin vej — et udpræget stock-picker-marked.
- **Moderat** (3,0–3,8 %): en del spredning.
- **Lille** (< 3,0 %): navnene følges ad (markedet trækker dem samlet).
Grøn prik ved værdier over 3 %. *Høj spredning taler for stock-picking.*

**Retning i indekset** — *trender markedet, eller går det i ring?*
Måler "trend-persistens": om indeksets dagsretning har tendens til at fortsætte næste dag.
- **~0 (fx −0,09 til +0,05): Ingen pålidelig retning** — indekset er choppy/retningsløst. Det er
  gunstigt for stock-picking (du kan ikke tjene på indeks-retningen, så du må vælge navne).
- **Højere positiv (> ~0,10): Trendende** — indekset har en retning der holder. Mere momentum-venligt.
Tallet er en korrelation: 0 = ingen sammenhæng dag-til-dag, positiv = trend, negativ = tilbagefald.

**Morgenretningen** — *holder den første retning, eller vender den?*
Når en aktie gapper om morgenen, følger den så videre samme vej, eller vender den (fader)?
Måles som "gap follow-through-rate" (andel af gap-dage hvor retningen holdt).
- **Holder (følger igennem)** (> 0,55, dvs. > 55 % af dagene): morgenretningen er pålidelig —
  momentum-venligt. Grøn prik.
- **Vender (fader)** (< 0,45): morgenudbruddet trækkes typisk tilbage — mean-reversion-venligt.
- **Blandet** (0,45–0,55): ingen klar tendens.
Vises som "…% af dagene".

**Hvornår på dagen** — *hvornår sker bevægelsen?*
Fortæller hvornår dagens høj typisk sættes.
- **Mest i de første 30–60 min:** bevægelsen er morgen-domineret — godt for strategier der
  handler tidligt (K2, BuyTheDip, Relativ Styrke beslutter alle om morgenen).
- **Spredt over dagen:** bevægelsen fordeler sig, mindre morgen-koncentreret.

**Inden for dagen** — *er dagen trendende eller choppy?*
Måler 5-minutters-autokorrelation: om en 5-min bevægelse har tendens til at fortsætte (trend)
eller vende (chop) inden for dagen.
- **Trendende** (> +0,05): bevægelser fortsætter intradag.
- **Choppy (mean-rev)** (< −0,05): bevægelser vender hurtigt — savtakket. BuyTheDip-venligt.
- **Neutral** (−0,05 til +0,05): hverken klar trend eller chop.
Tallet er typisk lille (fx −0,015); fortegnet og om det passerer ±0,05 er det vigtige.

**Bredde (grønne navne)** — *hvor mange er oppe?*
Andel af dagens navne der lukker i grønt (op) på en gennemsnitsdag.
- **~50 %:** balanceret marked (lige mange op og ned).
- **Markant over 50 %:** bred styrke (de fleste navne op).
- **Under 50 %:** bred svaghed.
Bruges som kontekst — et højt tal betyder ikke i sig selv at det er let at tjene penge.

### Sektion: Skifter regimet?
Dette er hjertet i at *følge* markedet over tid — ikke bare aflæse et øjebliksbillede.

Øverst sammenlignes **Sidste måned** mod **Nu** (regime-etiketten for hver periode). Derunder står
konklusionen:
- **"Stabilt — samme regime som sidste måned"** (grøn): markedet har samme karakter som før.
- **"Regimet har SKIFTET: fra X til Y"** (gul): markedet har ændret karakter — det er dét man skal
  reagere på, for det er dér en strategi kan gå fra medvind til modvind.

Nedenunder vises fire kernemålinger med en **pil**:
- **▲ (grøn):** værdien er *steget* siden sidste måned.
- **▼ (rød):** værdien er *faldet*.
- **= :** stort set uændret.
Tallene vises som "sidste måned → nu". Bemærk: pilens farve er **neutral-beskrivende** — "op" er
ikke i sig selv godt eller skidt, det viser bare *retningen* af ændringen. Store skift (fx
morgen-follow-through der springer fra 0,45 til 0,60, eller index-trend der falder fra +0,10 til
−0,09) er signalet om at markedet er ved at ændre karakter.

### Sektion: Historik (skift over tid)
En tabel med de seneste kørsler (fingeraftrykket kører automatisk hver mandag). Kolonner: dato,
regime, spredning %, follow-through. **Når "Regime"-kolonnen skifter værdi mellem to rækker, har
markedet skiftet karakter.** Her ser du trenden: fx dispersion der langsomt stiger, eller
index-trend der aftager — de bevægelser der til sidst tipper regimet fra ét til et andet. Med kun
én kørsel er tabellen tom endnu; den bygger sig op uge for uge.

### Sektion: Data-friskhed & tekniske detaljer (sammenklappet)
Klik "vis" for at folde den ud. Her står:
- **Coverage-noter:** hvor friske data er, og hvad der evt. er udeladt. Vigtigst: fingeraftrykket
  måler det **seneste tilgængelige** ~30-dages-vindue — ikke nødvendigvis helt op til i dag, hvis
  de underliggende kursdata ikke er høstet helt frem. Derfor kan "måler perioden …" ende nogle uger
  bagud. Vil du have det helt aktuelt, skal der høstes friske bars først.
- **Den rå tekniske rapport** (samme som "Kopier til Claude" sender). Den er ingeniør-dump med
  variance-ratios, half-life osv. — beregnet til fejlfinding og til at dele med Claude, ikke til
  daglig orientering. Du behøver den ikke for at forstå regimet.

---

## Sådan bruger du det i praksis

1. **Aflæs regimet** (den store overskrift). Det fortæller hvilken strategifamilie der har medvind.
2. **Tjek beviserne** ("Hvad markedet gør lige nu") hvis du vil forstå *hvorfor* — fx høj spredning
   + retningsløst indeks = stock-picking.
3. **Hold øje med skift.** Så længe "Skifter regimet?" siger *Stabilt*, er billedet uændret. Skifter
   det, er det tid til at revurdere hvilke strategier der passer.
4. **Følg historikken** over uger for at se de langsomme bevægelser før de tipper regimet.

Fingeraftrykket **afgør ikke** hvornår du handler, og det garanterer intet. Det giver dig et
objektivt billede af terrænet, så strategivalget hviler på data frem for mavefornemmelse.

---

## Vigtige forbehold

- **Beskrivende, ingen edge-påstand.** Fingeraftrykket måler markedets karakter. Om en given
  strategi rent faktisk tjener penge i regimet afgøres af strategiens egen validering og paper-test
  — ikke af dette vindue.
- **Kort historik.** Enkelte tal på korte vinduer er statistisk tynde. Det er *mønsteret* og
  *skiftene over tid* der bærer, ikke ét enkelt tal på én enkelt kørsel.
- **Data-friskhed.** Vinduet er kun så aktuelt som de høstede kursdata. Tjek "måler perioden …" og
  coverage-noterne hvis tidspunktet betyder noget for din beslutning.
- **Tærskler er vejledende.** Grænserne (fx spredning > 3 %, follow-through > 0,55) er
  fornuftige skillelinjer, ikke naturlove. Værdier tæt på en grænse skal læses med omtanke.
