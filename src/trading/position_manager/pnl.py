"""P&L calculation logic"""

from typing import Optional, Dict
from src.trading.orders import get_clob_client, get_midpoint
from src.utils.websocket_manager import ws_manager

def _get_position_pnl(token_id: str, entry_price: float, size: float, cached_prices: Optional[Dict[str, float]] = None) -> Optional[dict]:
    """Get current market price and calculate P&L"""
    current_price = None
    if cached_prices and str(token_id) in cached_prices:
        current_price = cached_prices[str(token_id)]
    
    if current_price is None:
        current_price = ws_manager.get_price(token_id)
    
    if current_price is None:
        current_price = get_midpoint(token_id)

    if current_price is None:
        try:
            from src.trading.orders import get_clob_client
            client = get_clob_client()
            book = client.get_order_book(token_id)
            if isinstance(book, dict):
                bids, asks = book.get("bids", []), book.get("asks", [])
            else:
                bids, asks = getattr(book, "bids", []), getattr(book, "asks", [])
            if not bids or not asks:
                return None
            best_bid = float(
                bids[-1].price if hasattr(bids[-1], "price") else bids[-1].get("price", 0)
            )
            best_ask = float(
                asks[-1].price if hasattr(asks[-1], "price") else asks[-1].get("price", 0)
            )
            current_price = (best_bid + best_ask) / 2.0
        except Exception:
            return None
    pnl_usd = (current_price * size) - (entry_price * size)
    pnl_pct = (pnl_usd / (entry_price * size)) * 100 if size > 0 else 0
    return {
        "current_price": current_price,
        "pnl_pct": pnl_pct,
        "pnl_usd": pnl_usd,
        "price_change_pct": ((current_price - entry_price) / entry_price) * 100,
    }
