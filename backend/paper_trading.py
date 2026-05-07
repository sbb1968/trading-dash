from datetime import datetime
from portfolio import load_portfolio, save_portfolio, reset_portfolio, get_summary

class PaperTrading:
    """
    Paper trading engine der håndterer køb og salg af aktier.
    """

    def __init__(self):
        self.portfolio = load_portfolio()
        print(f"[PaperTrading] Portfolio indlæst — balance: ${self.portfolio['balance']:,.2f}")

    def buy(self, ticker: str, shares: float, price: float) -> dict:
        """Køb aktier."""
        cost = shares * price
        commission = 0.0  # Paper trading har ingen kommission

        if cost > self.portfolio["balance"]:
            return {
                "success": False,
                "error": f"Ikke nok kapital. Kræver ${cost:,.2f}, har ${self.portfolio['balance']:,.2f}",
            }

        if shares <= 0:
            return {"success": False, "error": "Antal aktier skal være større end 0"}

        # Tjek om der allerede er en position
        positions = self.portfolio["positions"]

        if ticker in positions:
            # Tilføj til eksisterende position — beregn ny gennemsnitspris
            existing = positions[ticker]
            total_shares = existing["shares"] + shares
            total_cost = (existing["shares"] * existing["avg_price"]) + cost
            avg_price = total_cost / total_shares

            positions[ticker] = {
                **existing,
                "shares": round(total_shares, 4),
                "avg_price": round(avg_price, 4),
                "last_updated": datetime.now().isoformat(),
            }
        else:
            # Ny position
            positions[ticker] = {
                "ticker": ticker,
                "shares": round(shares, 4),
                "avg_price": round(price, 4),
                "entry_price": round(price, 4),
                "opened_at": datetime.now().isoformat(),
                "last_updated": datetime.now().isoformat(),
            }

        # Træk fra balance
        self.portfolio["balance"] -= cost
        self.portfolio["last_updated"] = datetime.now().isoformat()
        save_portfolio(self.portfolio)

        print(f"[PaperTrading] KØB {shares} {ticker} @ ${price:.2f} — kost ${cost:,.2f}")

        return {
            "success": True,
            "action": "buy",
            "ticker": ticker,
            "shares": shares,
            "price": price,
            "cost": round(cost, 2),
            "new_balance": round(self.portfolio["balance"], 2),
            "position": positions[ticker],
        }

    def sell(self, ticker: str, shares: float, price: float) -> dict:
        """Sælg aktier."""
        positions = self.portfolio["positions"]

        if ticker not in positions:
            return {"success": False, "error": f"Ingen position i {ticker}"}

        position = positions[ticker]

        if shares > position["shares"]:
            return {
                "success": False,
                "error": f"Kan ikke sælge {shares} aktier — har kun {position['shares']}",
            }

        # Beregn P&L
        proceeds = shares * price
        cost_basis = shares * position["avg_price"]
        pnl = proceeds - cost_basis
        pnl_pct = (pnl / cost_basis * 100) if cost_basis > 0 else 0

        # Opdater eller fjern position
        remaining_shares = round(position["shares"] - shares, 4)

        if remaining_shares <= 0.0001:
            # Luk position helt
            trade = {
                "ticker": ticker,
                "action": "sell",
                "shares": shares,
                "entry_price": position["avg_price"],
                "exit_price": round(price, 4),
                "proceeds": round(proceeds, 2),
                "cost_basis": round(cost_basis, 2),
                "pnl": round(pnl, 2),
                "pnl_pct": round(pnl_pct, 2),
                "opened_at": position["opened_at"],
                "closed_at": datetime.now().isoformat(),
            }
            self.portfolio["trades"].append(trade)
            del positions[ticker]
        else:
            # Delvist salg
            positions[ticker] = {
                **position,
                "shares": remaining_shares,
                "last_updated": datetime.now().isoformat(),
            }
            trade = {
                "ticker": ticker,
                "action": "partial_sell",
                "shares": shares,
                "entry_price": position["avg_price"],
                "exit_price": round(price, 4),
                "proceeds": round(proceeds, 2),
                "cost_basis": round(cost_basis, 2),
                "pnl": round(pnl, 2),
                "pnl_pct": round(pnl_pct, 2),
                "opened_at": position["opened_at"],
                "closed_at": datetime.now().isoformat(),
            }
            self.portfolio["trades"].append(trade)

        # Tilføj til balance
        self.portfolio["balance"] += proceeds
        self.portfolio["last_updated"] = datetime.now().isoformat()
        save_portfolio(self.portfolio)

        print(f"[PaperTrading] SALG {shares} {ticker} @ ${price:.2f} — P&L ${pnl:,.2f} ({pnl_pct:.2f}%)")

        return {
            "success": True,
            "action": "sell",
            "ticker": ticker,
            "shares": shares,
            "price": price,
            "proceeds": round(proceeds, 2),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
            "new_balance": round(self.portfolio["balance"], 2),
            "trade": trade,
        }

    def get_position(self, ticker: str) -> dict | None:
        """Hent position for en specifik aktie."""
        return self.portfolio["positions"].get(ticker)

    def get_summary(self, current_prices: dict) -> dict:
        """Hent portfolio summary med live priser."""
        return get_summary(self.portfolio, current_prices)

    def reset(self) -> dict:
        """Nulstil portfolio."""
        self.portfolio = reset_portfolio()
        print("[PaperTrading] Portfolio nulstillet")
        return self.portfolio