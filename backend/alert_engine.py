from datetime import datetime
from typing   import Callable

class AlertEngine:
    """
    Overvåger prisopdateringer og fyrer alerts
    når en aktie bevæger sig over tærsklen.

    Bruger to mekanismer for at undgå spam:
      1. Bucket-logik: Når en ticker har alert'et på 0.5%, alert'er den
         ikke igen før den når 1.0% (næste bucket).
      2. Cooldown: Max ét alert per ticker per 60 sek, selv inden for
         samme bucket.
    """

    COOLDOWN_SECONDS = 60   # Mindste tid mellem alerts på samme ticker

    def __init__(self, threshold: float = 0.5):
        self.threshold = threshold
        self.alert_id  = 0
        self._callbacks: list[Callable] = []
        # Tracker per ticker: hvilken bucket har vi senest alert'et på,
        # og hvornår fyrede vi sidst en alert.
        self._last_bucket: dict[str, int]      = {}
        self._last_alert_at: dict[str, float]  = {}

    def set_threshold(self, threshold: float):
        """Opdater tærsklen dynamisk fra frontend."""
        self.threshold = threshold
        # Nulstil bucket-tracking når tærsklen ændres,
        # ellers vil gamle buckets give forkert opførsel
        self._last_bucket.clear()
        print(f"[AlertEngine] Tærskel opdateret til {threshold}%")

    def on_alert(self, callback: Callable):
        """Registrer en callback der kaldes når en alert fyrer."""
        self._callbacks.append(callback)

    def process_ticks(self, ticks: list[dict]) -> list[dict]:
        """
        Gennemgå alle ticks og returner liste af alerts
        for aktier der har bevæget sig ind i en ny bucket.
        """
        import time

        alerts = []
        now    = time.time()

        for tick in ticks:
            ticker = tick["ticker"]
            change = abs(tick["change_percent"])

            if change < self.threshold:
                # Hvis aktien er faldet under tærsklen igen, nulstil bucket
                # så den kan alert'e fra ny hvis den stiger igen
                self._last_bucket.pop(ticker, None)
                continue

            # Beregn bucket: hvis threshold er 0.5%, så er buckets
            # 0.5-1%, 1-1.5%, 1.5-2%, osv. Bucket-nummer = floor(change/threshold).
            bucket = int(change / self.threshold)

            # Tjek 1: er det en ny bucket for denne ticker?
            last_bucket = self._last_bucket.get(ticker, 0)
            if bucket <= last_bucket:
                continue

            # Tjek 2: er cooldown udløbet?
            last_at = self._last_alert_at.get(ticker, 0)
            if (now - last_at) < self.COOLDOWN_SECONDS:
                continue

            # Klar til alert
            self._last_bucket[ticker]   = bucket
            self._last_alert_at[ticker] = now

            alert = self._create_alert(tick)
            alerts.append(alert)
            for cb in self._callbacks:
                cb(alert)

        return alerts

    def _create_alert(self, tick: dict) -> dict:
        """Opret et alert objekt fra et tick."""
        self.alert_id += 1
        direction = "up" if tick["change_percent"] > 0 else "down"
        emoji = "🚀" if direction == "up" else "🔻"
        sign = "+" if tick["change_percent"] > 0 else ""

        return {
            "id": self.alert_id,
            "ticker": tick["ticker"],
            "price": tick["price"],
            "change_percent": tick["change_percent"],
            "direction": direction,
            "message": f"{emoji} {tick['ticker']} {sign}{tick['change_percent']:.2f}% · ${tick['price']:.2f}",
            "time": datetime.now().strftime("%H:%M:%S"),
            "timestamp": datetime.now().isoformat(),
        }