import yfinance as yf
import pandas as pd
from pathlib import Path
import time

OUTPUT_DIR = Path(__file__).parent / "data"
OUTPUT_DIR.mkdir(exist_ok=True)

# Bredere liste af small cap momentum-aktier
TICKERS = [
    "NVAX", "CLOV", "SPRT", "BBIG", "ATER",
    "SKLZ", "MVIS", "RIDE", "OCGN", "TLRY",
    "AMC", "GME", "BBBY", "SNDL", "EXPR",
    "SPY", "QQQ", "IWM",
]

print("=" * 60)
print("Downloader historiske data")
print("=" * 60)

downloaded = []

for ticker in TICKERS:
    try:
        obj = yf.Ticker(ticker)

        # ── 5-minut data — seneste 60 dage (max for Yahoo) ──
        print(f"  {ticker:8s} 5m  ...", end=" ", flush=True)
        df5 = obj.history(period="60d", interval="5m", auto_adjust=True)
        if not df5.empty:
            df5.columns = [c.lower() for c in df5.columns]
            df5.index.name = "datetime"
            path = OUTPUT_DIR / f"{ticker}_5m_60d.csv"
            df5.to_csv(path)
            print(f"✓ {len(df5)} rækker")
            downloaded.append(str(path))
        else:
            print("ingen data")

        time.sleep(0.5)

        # ── 1-minut data — seneste 7 dage ───────────────────
        print(f"  {ticker:8s} 1m  ...", end=" ", flush=True)
        df1 = obj.history(period="7d", interval="1m", auto_adjust=True)
        if not df1.empty:
            df1.columns = [c.lower() for c in df1.columns]
            df1.index.name = "datetime"
            path = OUTPUT_DIR / f"{ticker}_1m_7d.csv"
            df1.to_csv(path)
            print(f"✓ {len(df1)} rækker")
            downloaded.append(str(path))
        else:
            print("ingen data")

        time.sleep(0.5)

    except Exception as e:
        print(f"✗ FEJL: {e}")

print()
print("=" * 60)
print(f"✓ Downloadet: {len(downloaded)} filer")
print(f"Gemt i: {OUTPUT_DIR.absolute()}")
print("=" * 60)
print()
print("Klar! Kør nu:")
print("  python backtest_momentum.py")
