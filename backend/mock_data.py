import random
from datetime import datetime

TICKERS = [
    "AAPL","TSLA","NVDA","AMD","MSFT","META","AMZN","GOOGL","PLTR","SOFI",
    "RIVN","LCID","NIO","BBBY","GME","AMC","MULN","CLOV","WISH","OPEN"
]

BASE_PRICES = {
    "AAPL":189.50, "TSLA":245.30, "NVDA":875.20, "AMD":178.40, "MSFT":415.80,
    "META":525.60, "AMZN":192.30, "GOOGL":172.90, "PLTR":24.50, "SOFI":8.75,
    "RIVN":12.30,  "LCID":4.20,   "NIO":7.80,    "BBBY":0.85,  "GME":18.40,
    "AMC":5.60,    "MULN":0.42,   "CLOV":2.10,   "WISH":1.30,  "OPEN":3.75
}

FLOAT_DATA = {
    "AAPL":"15.4B", "TSLA":"3.2B", "NVDA":"2.4B", "AMD":"1.6B",  "MSFT":"7.4B",
    "META":"2.5B",  "AMZN":"10.2B","GOOGL":"12.1B","PLTR":"2.1B","SOFI":"980M",
    "RIVN":"890M",  "LCID":"1.9B", "NIO":"1.7B",  "BBBY":"340M", "GME":"302M",
    "AMC":"518M",   "MULN":"2.1B", "CLOV":"275M", "WISH":"590M", "OPEN":"420M"
}

NEWS_TEMPLATES = [
    ("{ticker} rapporterer stærke kvartalstal — slår forventningerne", "bullish"),
    ("Analytiker opgraderer {ticker} til Køb med kursmål hævet 15%", "bullish"),
    ("{ticker} vinder stor regeringskontrakt — aktien stiger i pre-market", "bullish"),
    ("Insiderkøb i {ticker}: CEO køber aktier for $2M", "bullish"),
    ("{ticker} annoncerer aktietilbagekøbsprogram på $500M", "bullish"),
    ("Short squeeze mulig i {ticker} — short interest rammer 40%", "bullish"),
    ("{ticker} får FDA godkendelse til nyt produkt", "bullish"),
    ("{ticker} nedjusterer årsforventninger — aktien falder", "bearish"),
    ("Analytiker nedgraderer {ticker} til Sælg", "bearish"),
    ("{ticker} tabte stor kontrakt til konkurrent", "bearish"),
    ("SEC undersøger {ticker} for regnskabsuregelmæssigheder", "bearish"),
    ("{ticker} varsler masseafskedigelser — restrukturering i gang", "bearish"),
    ("{ticker} indgår strategisk partnerskab med tech-gigant", "neutral"),
    ("{ticker} holder investormøde — opdaterer strategi", "neutral"),
    ("Markedsvolatilitet rammer {ticker} på trods af stærke fundamentals", "neutral"),
]

NEWS_SOURCES = [
    "Benzinga", "Reuters", "Bloomberg",
    "MarketWatch", "Newsfilter", "PRNewswire", "SEC Filing"
]

# Module-level state — nulstilles ved genstart af backend
current_prices:  dict[str, float] = {t: BASE_PRICES[t] * (1 + (random.random() - 0.5) * 0.04) for t in TICKERS}
current_volumes: dict[str, int]   = {t: random.randint(100000, 1000000) for t in TICKERS}
rel_vol_daily:   dict[str, float] = {t: round(random.uniform(0.5, 4.5), 2) for t in TICKERS}
gap_percent:     dict[str, float] = {t: round(random.uniform(-8, 8), 2) for t in TICKERS}


def simulate_tick() -> list[dict]:
    """Simuler én prisopdatering for alle aktier med hård grænse på ±10% fra basispris."""
    ticks = []
    for ticker in TICKERS:
        old_price = current_prices[ticker]
        base      = BASE_PRICES[ticker]

        # Max ±0.5% per tick, ingen opadgående bias
        change = (random.random() - 0.5) * 1.0
        raw    = old_price * (1 + change / 100)

        # Hård grænse: aldrig mere end ±10% fra basispris
        new_price = round(max(base * 0.90, min(base * 1.10, raw)), 2)
        current_prices[ticker] = new_price

        change_pct = round((new_price - base) / base * 100, 2)

        # Volumen: lille stigning per tick, max 3M
        current_volumes[ticker] = min(
            current_volumes[ticker] + random.randint(0, 5000),
            3000000
        )

        rel_vol_5min = round(random.uniform(0.5, 6.5), 2)

        ticks.append({
            "ticker":         ticker,
            "price":          new_price,
            "prev_price":     old_price,
            "change_percent": max(-10.0, min(10.0, change_pct)),
            "volume":         current_volumes[ticker],
            "rel_vol_daily":  rel_vol_daily[ticker],
            "rel_vol_5min":   rel_vol_5min,
            "gap_percent":    gap_percent[ticker],
            "float":          FLOAT_DATA[ticker],
            "news":           random.random() > 0.85,
            "timestamp":      datetime.now().isoformat(),
        })
    return ticks


def generate_news_item(news_id: int) -> dict:
    """Generer én mock nyhed."""
    ticker           = random.choice(TICKERS)
    template, sentiment = random.choice(NEWS_TEMPLATES)
    source           = random.choice(NEWS_SOURCES)
    return {
        "id":        news_id,
        "ticker":    ticker,
        "headline":  template.replace("{ticker}", ticker),
        "sentiment": sentiment,
        "source":    source,
        "time":      datetime.now().strftime("%H:%M:%S"),
        "timestamp": datetime.now().isoformat(),
    }
