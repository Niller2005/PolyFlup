"""Position and balance management"""

from typing import Optional, List, Any
import requests
from py_clob_client_v2 import BalanceAllowanceParams, AssetType
from src.utils.logger import log
from src.config.settings import DATA_API_BASE
from .client import client
from src.config.settings import PAPER_TRADING
from src import paper
from .constants import SELL
from .market import place_market_order
from .limit import place_limit_order


def get_balance_allowance(token_id: Optional[str] = None) -> Optional[dict]:
    """Get balance and allowance"""
    if PAPER_TRADING:
        return paper.balance_allowance(token_id)
    try:
        atype: Any = AssetType.CONDITIONAL if token_id else AssetType.COLLATERAL
        params: Any = BalanceAllowanceParams(asset_type=atype, token_id=token_id or "")
        result: Any = client.get_balance_allowance(params)
        if isinstance(result, dict):
            return {
                "balance": float(result.get("balance", 0)) / 1_000_000.0,
                "allowance": float(result.get("allowance", 0)) / 1_000_000.0,
            }
        elif (
            result is not None
            and hasattr(result, "balance")
            and hasattr(result, "allowance")
        ):
            return {
                "balance": float(getattr(result, "balance")) / 1_000_000.0,
                "allowance": float(getattr(result, "allowance")) / 1_000_000.0,
            }
        return None
    except Exception as e:
        log(f"⚠️  Error getting balance/allowance: {e}")
        return None


def _fetch_positions_v2(
    user: str,
    status: str = "OPEN",
    limit: int = 100,
    max_pages: int = 10,
    dust_floor: float = 0.001,
) -> List[dict]:
    """Fetch a user's positions from Data API v2 (cursor pagination, snake_case rows).

    v2 defaults to a 0.1-share dust floor for OPEN/REDEEMABLE; `dust_floor` lowers it
    so tiny residual balances still sync. CLOSED sets ignore the floor.
    """
    params: dict = {
        "user": user,
        "status": status,
        "limit": limit,
        "filter_amount": dust_floor,
    }
    rows: List[dict] = []
    for _ in range(max_pages):
        resp = requests.get(f"{DATA_API_BASE}/v2/positions", params=params, timeout=10)
        resp.raise_for_status()
        body = resp.json()
        rows.extend(body.get("data") or [])
        cursor = (body.get("pagination") or {}).get("next_cursor")
        if not cursor:
            break
        # The cursor carries page size, status and sort; send only the cursor + user
        params = {"user": user, "cursor": cursor}
    return rows


def get_current_positions(user_address: str) -> List[dict]:
    """Get current positions for a user from Data API v2.

    Row keys: token_id, condition_id, current_size, avg_price, slug, title, outcome, ...
    """
    if PAPER_TRADING:
        return paper.positions()
    try:
        if not user_address:
            log("   ❌ Error: user_address is empty for position fetch")
            return []

        log(f"   🔍 Fetching positions from Data API for {user_address[:10]}...")
        positions = _fetch_positions_v2(user_address, status="OPEN")

        valid_positions = []
        for p in positions:
            try:
                if float(p.get("current_size", 0)) > 0.001:  # Filter out dust
                    valid_positions.append(p)
            except (TypeError, ValueError):
                continue

        log(f"   ✅ Data API returned {len(valid_positions)} open positions")
        return valid_positions
    except Exception as e:
        log(f"⚠️  Error getting positions from Data API: {e}")
        return []


def get_closed_positions(user: str, limit: int = 100) -> List[dict]:
    """Get closed positions for a user (Data API v2, most recent page only).

    Row keys: token_id, condition_id, realized_pnl, total_pnl, avg_price, ...
    """
    if PAPER_TRADING:
        return []
    try:
        return _fetch_positions_v2(user, status="CLOSED", limit=limit or 100, max_pages=1)
    except Exception as e:
        log(f"⚠️  Error getting closed positions: {e}")
        return []


def sell_position(
    token_id: str,
    size: float,
    current_price: float,
    max_retries: int = 3,
    use_market_order: bool = True,
) -> dict:
    retry_delays = [2, 3, 5]
    import time

    remaining_size = size

    for attempt in range(max_retries):
        try:
            if attempt > 0:
                # Before retry, check actual balance to see what's left
                balance_info = get_balance_allowance(token_id)
                remaining_size = (
                    balance_info.get("balance", 0) if balance_info else remaining_size
                )
                if remaining_size < 0.1:
                    return {
                        "success": True,
                        "sold": size - remaining_size,
                        "status": "FILLED",
                    }

                log(
                    f"🔄 Retry {attempt} selling {remaining_size:.2f} shares... waiting {retry_delays[attempt - 1]}s"
                )
                time.sleep(retry_delays[attempt - 1])

            if use_market_order:
                result = place_market_order(
                    token_id=token_id,
                    amount=remaining_size,
                    side=SELL,
                    order_type="FAK",
                    silent_on_error=(attempt < max_retries - 1),
                )
            else:
                sell_price = round(max(0.01, current_price - 0.01), 2)
                result = place_limit_order(
                    token_id=token_id,
                    price=sell_price,
                    size=remaining_size,
                    side=SELL,
                    silent_on_balance_error=(attempt < max_retries - 1),
                    order_type="FAK" if attempt == 0 else "GTC",
                )

            if result["success"]:
                # Check if it was a full fill
                matched = float(result.get("size_matched", 0))

                # If size_matched is missing but we have an order ID, fetch it
                if matched == 0 and result.get("order_id"):
                    try:
                        from .management import get_order

                        o_info = get_order(result["order_id"])
                        if o_info:
                            matched = float(o_info.get("size_matched", 0))
                    except:
                        pass

                if matched >= remaining_size * 0.99:
                    return {
                        "success": True,
                        "sold": size,
                        "price": result.get("price", current_price),
                        "status": result["status"],
                        "order_id": result["order_id"],
                    }
                else:
                    # Partial fill - allow loop to retry with remaining
                    log(
                        f"   ⚠️  Partial sell fill: {matched:.2f}/{remaining_size:.2f} matched."
                    )
                    continue

            err = result.get("error", "").lower()
            if "balance" in err and attempt < max_retries - 1:
                continue
            if (
                use_market_order
                and ("fok" in err or "no match" in err)
                and attempt < max_retries - 1
            ):
                continue
            if attempt == max_retries - 1:
                return {"success": False, "error": err}
        except Exception as e:
            if "balance" in str(e).lower() and attempt < max_retries - 1:
                continue
            if attempt == max_retries - 1:
                return {"success": False, "error": str(e)}
    return {"success": False, "error": "Max retries exceeded"}
