"""Position and balance management"""

from typing import Optional, List, Any
import requests
from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
from src.utils.logger import log
from src.config.settings import DATA_API_BASE
from .client import client
from .constants import SELL
from .market import place_market_order
from .limit import place_limit_order


def get_balance_allowance(token_id: Optional[str] = None) -> Optional[dict]:
    """Get balance and allowance"""
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


def get_current_positions(user_address: str) -> List[dict]:
    """Get current positions for a user from Data API"""
    try:
        if not user_address:
            log("   ❌ Error: user_address is empty for position fetch")
            return []

        url = f"{DATA_API_BASE}/positions?user={user_address}"
        log(f"   🔍 Fetching positions from Data API for {user_address[:10]}...")

        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        positions = []
        if isinstance(data, list):
            positions = data
        elif isinstance(data, dict):
            positions = data.get("positions", [])

        valid_positions = []
        for p in positions:
            try:
                size = float(p.get("size", 0))
                if size > 0.001:  # Filter out dust
                    valid_positions.append(p)
            except:
                continue

        log(f"   ✅ Data API returned {len(valid_positions)} active positions")
        return valid_positions
    except Exception as e:
        log(f"⚠️  Error getting positions from Data API: {e}")
        return []


def get_closed_positions(user: str, limit: int = 100) -> List[dict]:
    """Get closed positions for a user from Data API"""
    try:
        url = f"{DATA_API_BASE}/closed-positions?user={user}"
        if limit:
            url += f"&limit={limit}"

        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return data
        return data.get("positions", []) if isinstance(data, dict) else []
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
