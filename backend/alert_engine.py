from datetime import datetime
from typing import Callable

class AlertEngine:
    """
    Overvåger prisopdateringer og fyrer alerts
    når en aktie bevæger sig over tærsklen.
    """

    def __init__(self, threshold: float = 0.5):
        self.threshold = threshold
        self.alert_id = 0
        self._callbacks: list[Callable] = []

    def set_threshold(self, threshold: float):
        """Opdater tærsklen dynamisk fra frontend."""
        self.threshold = threshold
        print(f"[AlertEngine] Tærskel opdateret til {threshold}%")

    def on_alert(self, callback: Callable):
        """Registrer en callback der kaldes når en alert fyrer."""
        self._callbacks.append(callback)

    def process_ticks(self, ticks: list[dict]) -> list[dict]:
        """
        Gennemgå alle ticks og returner liste af alerts
        for aktier der har bevæget sig over tærsklen.
        """
        alerts = []
        for tick in ticks:
            change = abs(tick["change_percent"])
            if change >= self.threshold:
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