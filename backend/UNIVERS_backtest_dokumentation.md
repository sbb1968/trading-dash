# Historisk backtest-univers — april + maj 2026

**Formål:** Et point-in-time aktieunivers med cachede 1-minuts bars, bygget til
backtest af 1-min strategier (primært Konfluens 2). Dækker to sammenhængende
måneder, så strategier kan testes på ét datasæt og valideres out-of-sample på
det andet.

---

## Hvad universet er

To point-in-time universer, bygget med `build_historical_universe.py`. For hver
handelsdag i perioden er dagens stærkeste gainere udvalgt (score baseret på
intradag-gain og volumen) ud fra et fast kandidat-sæt. Det er point-in-time:
kun data der var kendt den pågældende morgen indgår i scoringen, så der er ingen
look-ahead. En mild survivorship-effekt findes dog (se forbehold).

**Universe-filer (i `backend/`):**
- `historical_universe_2026-04-01_2026-04-30.json` — 21 handelsdage, ~108 unikke aktier
- `historical_universe_2026-05-01_2026-05-29.json` — 20 handelsdage, ~105 unikke aktier

Hver fil er en JSON med én post pr. handelsdag: dato → liste af tickers (op til
25 pr. dag, de højest scorende gainere den dag).

---

## Cachede 1-min bars

1-minuts bars for alle universets aktier er hentet fra IBKR og cachet på disk i
`backend/bar_cache/` som CSV. Filnavn-mønster:

    {TICKER}_{fetch_start}_{fetch_end}_1min.csv

`fetch_start` ligger nogle dage før periodens første dag (warmup til indikatorer).
For de to perioder:
- April: `{TICKER}_2026-03-20_2026-04-30_1min.csv`
- Maj:   `{TICKER}_2026-04-17_2026-05-29_1min.csv`

En fuldt hentet fil er typisk ~600 KB (ca. 11.300 bars). En **tom fil på 38 bytes**
(kun CSV-header) betyder at hentningen fejlede/blev afbrudt for den ticker — IKKE
at aktien er død. Se "Kendt faldgrube" nedenfor.

**Cachen genhentes ikke automatisk:** når en cache-fil findes (også en tom), læser
backtesten den og henter IKKE fra IBKR igen. Det er bevidst (undgår at gen-forsøge
ægte døde tickers), men det er også kilden til faldgruben nedenfor.

`bar_cache/`, universe-JSON-filerne og backtest-CSV-output er i `.gitignore` —
de er genererbare data, ikke kildekode, og deles ikke via Git. Hver maskine
bygger/henter sin egen cache.

---

## Sådan bruges universet til backtest

Kør Konfluens 2-backtesten mod en univers-fil:

    cd backend
    python backtest_confluence2.py --universe file \
        --universe-file historical_universe_2026-05-01_2026-05-29.json \
        --variant A_impulse_low

Første kørsel mod et nyt univers henter 1-min bars fra IBKR (kræver TWS på port
7497, og rammer IBKR-pacing: ~1 ticker/minut for ucachede). Efterfølgende
kørsler er øjeblikkelige (alt cachet).

Udelad `--variant` for at sweepe alle varianter. Output:
- En variant-sammenligningstabel (P&L + PF ved 0/1/2¢ slippage)
- En portefølje-simulationstabel (1% risk/equity, max 3 samtidige, fifo + priority)
- En CSV med alle handler i `backend/data/`

**Anvendelsesmønster (in-sample vs out-of-sample):** brug den ene periode til at
udvikle/vælge en variant, og den anden til at bekræfte at edgen holder på data
strategien aldrig er "set" imod. Maj blev brugt som primær test; april som
out-of-sample-bekræftelse.

---

## Kendt faldgrube: tomme cache-filer (38 bytes)

Hvis en hentning afbrydes midtvejs (IBKR-pacing-timeout eller TWS-hikke), gemmes
de ikke-hentede tickers som **tomme 38-byte cache-filer**. Næste backtest læser
dem som "data findes, men tom" og springer aktierne over — så backtesten kører
i stilhed på et amputeret univers, og resultaterne bliver misvisende.

**Sådan opdages det:** i hentnings-loggen står "0 bars (cache)" for de ramte
tickers. Et komplet univers har næsten ingen 0-resultater.

**Sådan rettes det** (eksempel for april-spændet — juster datospænd efter behov):

    # Se størrelser (tomme = 38 bytes)
    Get-ChildItem bar_cache\*_2026-03-20_2026-04-30_1min.csv | Select Name, Length | Sort Length

    # Slet KUN de tomme, behold de gode
    Get-ChildItem bar_cache\*_2026-03-20_2026-04-30_1min.csv | Where-Object { $_.Length -eq 38 } | Remove-Item

Kør derefter backtesten igen — de slettede gen-hentes fra IBKR. Bekræft at de nu
viser "(IBKR)" og et fornuftigt antal bars (~11.300), ikke 0.

**Skelnen der betyder noget:** "0 bars fra IBKR" = ægte død/afnoteret ticker (kan
ikke gøres noget ved, udelukkes). "0 bars fra cache" = vores fejlagtige tomme fil
(slet og genhent).

---

## Forbehold ved resultater fra dette univers

- **Point-in-time, men mild survivorship:** kandidat-sættet er valgt nu og
  indeholder aktier vi ved var momentum-navne i perioden. Aktier der ikke er i
  kandidat-listen kan aldrig vælges. Live skal en scanner selv finde dem hver
  morgen og vil ramme ved siden af nogle gange. Resultaterne er derfor en let
  optimistisk øvre grænse for hvad live-udvælgelse ville give.
- **Begrænset periode:** to måneder / ~40 handelsdage. Nok til et signal, ikke
  nok til at konkludere på tværs af markedsregimer (trend vs. choppy vs. fald).
- **Slippage:** backtesten modellerer slippage (0/1/2¢), men 1-2¢ er stadig
  optimistisk for small-cap stop-exits der ofte går gennem niveauet. Aflæs altid
  PF ved mindst 1¢, helst 2¢, ikke brutto.
- **Sizing:** portefølje-simulationen bruger 1% risk af løbende equity med max 3
  samtidige positioner — det realistiske tal. Variant-tabellens absolutte P&L
  (fast sizing, intet loft) er IKKE realistisk og bruges kun til relativ
  variant-sammenligning.

---

## Genopbygning / udvidelse

For at bygge et nyt univers (anden periode):

    python build_historical_universe.py --start YYYY-MM-DD --end YYYY-MM-DD

Tilføj egne kandidat-tickers med `--candidates TICKER1,TICKER2,...` hvis du kender
navne der løb i perioden men ikke er i default-sættet (giver et mindre
survivorship-skævt univers).
