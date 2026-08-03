# Relativ Styrke — sådan virker strategien

Relativ Styrke er en long-only intradag-strategi der hver morgen rangerer dagens aktier
mod hinanden og køber de tre stærkeste. Den er anderledes end vores andre strategier:
hvor de kigger på **hver aktie for sig** (har DENNE aktie et setup?), sammenligner Relativ
Styrke aktierne **på tværs** og satser på at toppen af ranglisten slår resten af feltet.

## Idéen i én sætning

Når markedet er et "stock-picker-marked" — hvor de rigtige navne løber og de forkerte
ikke gør, med et bredt spænd imellem — så er de aktier der er stærkest om morgenen også
dem der oftest fører resten af dagen; dem køber vi.

## Hvorfor netop denne strategi

Vi lavede en måling af hvilket slags marked vi står i, og den pegede entydigt på
"relativ værdi / stock-picking": stor spredning mellem enkelt-aktier, men ingen pålidelig
retning i selve indekset. Med andre ord ligger fordelen i at **vælge mellem navne**, ikke
i at ride markedet. Relativ Styrke er bygget direkte til at udnytte netop det — og den er
den **første** af vores strategier der bestod sin egen forud-fastlagte test.

## Hvilke aktier kigger den på

Et TradingView-scan efter de mest **svingende** amerikanske aktier i small- og mid-cap
(300 mio.–10 mia. i markedsværdi), mellem $5 og $50 og med god volumen. Typisk ~25 navne.
Relativ Styrke kører sit eget scan, adskilt fra de andre strategier.

To ting ved den liste er vigtigere end de lyder:

**Vi vælger på svingninger — ikke på hvem der stiger.** Listen indeholder med vilje både
aktier der er oppe og aktier der er nede den dag. Det er hele forudsætningen: strategien
skal *rangere* et bredt felt. Sorterede vi på dagens største stigninger, ville alle på
listen allerede være vindere, og så er der ikke rigtig noget at vælge imellem — vi ville
måle det samme to gange.

**Listen blev rettet i august 2026.** Indtil da hentede den live dagens største stigninger
i et meget bredere prisspænd ($3–500), og feltet blev derfor fire gange så spredt som det
strategien blev testet på — $2-aktier side om side med $195-aktier. Det er ikke ligegyldigt:
i det testede felt er et morgen-udsving på 2 % et signal, mens dagens topnavn på den gamle
liste typisk var oppe 14 %, hvilket ofte er et nyhedsspring der falder tilbage igen. Nu
handler vi i det samme felt som vi testede i, blot udvidet nedad til også at rumme small cap.

## Hvornår køber den

Der er **én** beslutning om dagen:

1. **Kl. 09:45** amerikansk tid måles hver akties **morgen-styrke** — hvor mange procent
   den er steget fra åbningskursen (09:30) frem til nu.
2. Alle dagens navne **rangeres** efter det tal, fra stærkest til svagest.
3. Strategien køber **long i de 3 øverste**, med lige stor position i hver.

Beslutningen fyrer teknisk set kl. 09:46, når 09:45-candlen er lukket, så den regner på
præcis samme kurser som backtesten gjorde. Der er ingen jagt resten af dagen — ét snapshot,
ét valg.

## Hvornår sælger den

Positionerne holdes hele dagen og lukkes samlet ved **force-close kl. 15:30** amerikansk
tid. Der er bevidst **ingen** target eller stop undervejs — det var netop den simple
"hold-til-lukketid"-regel der virkede i valideringen, og live skal matche den. Beskyttelsen
ligger i det daglige tabs-loft, ikke i en kurs-stop.

## Hvor mange penge per handel

Sizingen er **notional-baseret**: omkring 3 % af kontoens værdi fordeles ligeligt på de tre
navne (cirka 1 % pr. navn), men aldrig mere end 1.000 dollar i én position. Højst tre
positioner ad gangen — kun de tre stærkeste. Fordi der ingen kurs-stop er, giver
"risiko = afstand til stop" ikke mening her; derfor styres størrelsen på beløb i stedet.

## Hvad vi ved om den

Relativ Styrke er long-only, så dens **rå** dags-resultat indeholder markedets retning —
falder hele feltet, falder de tre valgte typisk med. Den egentlige fordel er derfor ikke
det rå afkast, men **selection alpha**: slår de tre valgte gennemsnittet af hele dagens
liste? Det måler vi hver dag efter lukketid og samler op over tid. I valideringen var den
positiv i både udviklings- og kontrol-perioden med et robust mønster — men på en lille
stikprøve.

Forbeholdet er nu stikprøven: 43 dage er ikke meget. Det andet forbehold — at live kørte i
et andet felt end backtesten — blev lukket i august 2026 (se ovenfor). Vær opmærksom på at
paper-tal fra *før* den dato er målt på det gamle, fire gange bredere felt og derfor ikke
kan sammenlignes direkte med tal bagefter. Derfor er **paper-handlen
selv dommeren**: først når selection alpha holder over mange rigtige sessioner, overvejes
mere. Strategien kører **udelukkende paper** (Ibens konto), auto-startes på handelsserveren
kort før beslutningstidspunktet, og startes manuelt på Sørens maskine.

## Kort sagt

Relativ Styrke rangerer dagens likvide amerikanske aktier efter morgen-styrke kl. 09:45,
køber de tre stærkeste med lige vægt, holder til lukketid og lukker alt kl. 15:30. Dens
fordel er at vælge de rigtige navne — målt som selection alpha — og den er stadig i
paper-test-fasen.
