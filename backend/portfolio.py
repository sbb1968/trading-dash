import json
import os
from datetime import datetime
from typing import Optional

PORTFOLIO_FILE = "portfolio.json"
STARTING_BALANCE = 100_000.0

def load_portfolio() -> dict:
    """Indlæs portfolio fra fil eller opret ny."""
    if os.path.exists(PORTFOLIO_FILE):
        with open(PORTFOLIO_FILE, "r") as f:
            return json.load(f)
    return _empty_portfolio()

def save_portfolio(portfolio: dict):
    """Gem portfolio til fil."""
    with open(PORTFOLIO_FILE, "w") as f:
        json.dump(portfolio, f, indent=2)

def _empty_portfolio() -> dict:
    return {
        "balance": STARTING_BALANCE,
        "starting_balance": STARTING_BALANCE,
        "positions": {},       # ticker -> position dict
        "trades": [],          # historik over alle afsluttede trades
        "created_at": datetime.now().isoformat(),
        "last_updated": datetime.now().isoformat(),
    }

def reset_portfolio():
    """Nulstil portfolio til $100.000."""
    portfolio = _empty_portfolio()
    save_portfolio(portfolio)
    return portfolio

def get_summary(portfolio: dict, current_prices: dict) -> dict:
    """Beregn samlet portfolio summary med live P&L."""
    positions = portfolio["positions"]
    balance = portfolio["balance"]

    total_unrealized_pnl = 0.0
    total_position_value = 0.0
    positions_with_pnl = []

    for ticker, pos in positions.items():
        current_price = current_prices.get(ticker, pos["avg_price"])
        position_value = pos["shares"] * current_price
        cost_basis = pos["shares"] * pos["avg_price"]
        unrealized_pnl = position_value - cost_basis
        unrealized_pnl_pct = (unrealized_pnl / cost_basis * 100) if cost_basis > 0 else 0

        total_unrealized_pnl += unrealized_pnl
        total_position_value += position_value

        positions_with_pnl.append({
            **pos,
            "current_price": current_price,
            "position_value": round(position_value, 2),
            "unrealized_pnl": round(unrealized_pnl, 2),
            "unrealized_pnl_pct": round(unrealized_pnl_pct, 2),
        })

    total_equity = balance + total_position_value
    total_pnl = total_equity - portfolio["starting_balance"]
    total_pnl_pct = (total_pnl / portfolio["starting_balance"] * 100)

    # Beregn realiseret P&L fra trades
    realized_pnl = sum(t.get("pnl", 0) for t in portfolio["trades"])

    return {
        "balance": round(balance, 2),
        "starting_balance": round(portfolio["starting_balance"], 2),
        "total_position_value": round(total_position_value, 2),
        "total_equity": round(total_equity, 2),
        "total_pnl": round(total_pnl, 2),
        "total_pnl_pct": round(total_pnl_pct, 2),
        "unrealized_pnl": round(total_unrealized_pnl, 2),
        "realized_pnl": round(realized_pnl, 2),
        "positions": positions_with_pnl,
        "trades": portfolio["trades"][-50:],  # Seneste 50 trades
        "num_positions": len(positions),
        "num_trades": len(portfolio["trades"]),
    }