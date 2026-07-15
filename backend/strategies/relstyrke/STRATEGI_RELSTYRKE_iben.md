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

Et TradingView top-gainer-scan: likvide amerikanske aktier mellem $3 og $500 med god
volumen — dagens mest aktive navne. Det er et bredt felt (typisk ~25 navne), for hele
pointen er at have nok at rangere imellem. Relativ Styrke kører sit eget scan, adskilt
fra de andre strategier.

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

Positionerne holdes hele dagen og lukkes samlet ved **force-close kl. 15:51** amerikansk
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

To vigtige forbehold: backtesten kørte på et pænt, udvalgt univers, mens live kører på
scannerens rigtige (mere rodede) liste — og stikprøven var lille. Derfor er **paper-handlen
selv dommeren**: først når selection alpha holder over mange rigtige sessioner, overvejes
mere. Strategien kører **udelukkende paper** (Ibens konto), auto-startes på handelsserveren
kort før beslutningstidspunktet, og startes manuelt på Sørens maskine.

## Kort sagt

Relativ Styrke rangerer dagens likvide amerikanske aktier efter morgen-styrke kl. 09:45,
køber de tre stærkeste med lige vægt, holder til lukketid og lukker alt kl. 15:51. Dens
fordel er at vælge de rigtige navne — målt som selection alpha — og den er stadig i
paper-test-fasen.
