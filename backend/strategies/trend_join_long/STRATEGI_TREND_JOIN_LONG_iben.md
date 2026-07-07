# Trend Join Long — sådan virker strategien

Trend Join Long er en long-only "gap-and-go"-strategi bygget efter HumbledTraders
opskrift. Den jagter dagens største **gappere** — aktier der åbner væsentligt højere
end gårsdagens luk — men kun dem hvor gappet er drevet af en **frisk, positiv nyhed**.
Når sådan en aktie fortsætter opad og laver ny dagshøjde over premarket-toppen,
"joiner" strategien momentum.

## Idéen i én sætning

Køb med op i en aktie der gapper på en rigtig nyhedskatalysator og bekræfter styrken
ved at bryde til ny dagshøjde — og styr ud igen med en flertrins-exit der låser gevinst
undervejs.

## Kernen: nyhedsfilteret

Det vigtigste led er nyhedsfilteret. En aktie kommer **kun** i puljen hvis der er en
frisk positiv nyhed på den i dag. Et gap uden katalysator fader typisk i løbet af dagen
— så det filtreres fra. Det er præcis den del man ikke kan efterprøve billigt på
historiske data, og derfor testes strategien live på paper i stedet for i en backtest.

Nyhederne hentes **direkte fra IBKR/TWS** (jeres eksisterende forbindelse) — de samme
professionelle kilder som Dow Jones og Briefing.com, med rene enkeltnavn-katalysatorer
(regnskaber, handelsstop, insider-køb, partnerskaber). Det giver en langt dybere
dækning af de små micro-cap-gappere end en gratis nyhedskilde ville.

## Hvilke aktier kigger den på

Dagens top-gappere, **re-scannet hvert 30. minut** gennem dagen (ikke bare ét scan ved
start): aktier fra 3 dollar og op, med mindst 500.000 i volumen, hvor både 1-dags,
1-uges og 1-måneds ændring alle er positive (en optrend-bekræftelse der matcher
join-tesen). De 25 øverste gappere er kandidaterne, som nyhedsfilteret og
trend-tjekket derefter skærer ned til den endelige pulje.

## Hvornår køber den

En handel kræver flere ting på plads samtidig:

- **Gap** på mindst 3 % over gårsdagens luk.
- Gårsdagens luk **over det 200-dages gennemsnit** (aktien er i langsigtet optrend).
- **Relativ volumen** på mindst 2× det normale (der er reelt liv i aktien).
- En **frisk positiv nyhed** som katalysator.

Selve entry sker når aktien laver **ny dagshøjde over premarket-toppen** — altså når
momentum bekræftes — mellem kl. 10:05 og 15:30 amerikansk tid.

## Hvornår sælger den (flertrins)

Trend Join Long styrer ud i flere trin, så gevinst sikres undervejs:

- **Initial stop:** 1 % under dagens laveste (LOD − 1 %).
- **Delvis gevinst:** sælg 1/3 af positionen ved 0,75R (¾ af den oprindelige risiko).
- **Breakeven:** flyt stoppet op til købskursen ved 1,0R — herfra kan handlen ikke
  længere tabe.
- **Trailing:** derefter trailes stoppet under 5-minutters swing-lavpunkter, så gevinst
  låses mens trenden løber.
- **Force-close:** alt lukkes senest kl. 15:51 — aldrig over natten.

## Hvor mange penge per handel

Sizingen er risiko-baseret på kontostørrelsen: højst **1 %** risiko per handel, og
højst **10 %** af porteføljen i én position. Kan kontoværdien ikke aflæses, bruges
faste fallback-beløb. Den holder højst **5** positioner åbne samtidig, og ved flere
kandidater på én gang prioriteres den med det største gap.

## Hvad vi ved om den

Trend Join Long er **ny og under live paper-test** (den såkaldte "vej B") — kernen,
nyhedskatalysatoren, kan ikke valideres historisk, så den skal bevises live.
Strategien har **manuel start** og auto-starter aldrig af sig selv.

Tidligt i live-testen viste en gratis nyhedskilde sig for tynd til micro-cap-gappere;
det er nu **løst** ved at hente nyhederne direkte fra IBKR/TWS (Dow Jones/Briefing.com),
som dækker de små navne langt bedre. Strategien skal stadig vise konsistente resultater
på paper, før rigtige penge kommer på tale.

## Kort sagt

Trend Join Long joiner momentum i nyhedsdrevne gappere når de bryder til ny dagshøjde,
med en flertrins-exit der låser gevinst undervejs. Nyhedsfilteret er både kernen og
den største åbne udfordring (datadækning). Strategien er i en tidlig paper-fase.
