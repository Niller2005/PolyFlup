"""Trade execution utilities"""

from typing import Optional, Dict, Any
from src.utils.logger import log, log_error, send_discord
from src.data.database import save_trade
from src.trading.orders import place_order, get_order, get_balance_allowance


def execute_trade(
    trade_params: Dict[str, Any], is_reversal: bool = False, cursor=None
) -> Optional[int]:
    """
    Execute a trade and save to database.
    Returns trade_id if successful, None otherwise.
    """
    symbol = trade_params["symbol"]
    side = trade_params["side"]
    token_id = trade_params["token_id"]
    price = trade_params["price"]
    size = trade_params["size"]

    # Pre-flight balance check
    est_cost = size * price
    bal_info = get_balance_allowance()
    if bal_info:
        usdc_balance = bal_info.get("balance", 0)
        if usdc_balance < est_cost:
            log(
                f"[{symbol}] ❌ Insufficient funds (Need ${est_cost:.2f}, Have ${usdc_balance:.2f})"
            )
            return None

    # Place order
    result = place_order(token_id, price, size)

    if not result["success"]:
        log(f"[{symbol}] ❌ Order failed: {result.get('error')}")
        return None

    actual_size = size
    actual_price = price
    actual_status = result["status"]
    order_id = result["order_id"]

    # Try to sync execution details immediately if filled
    if actual_status.upper() in ["FILLED", "MATCHED"]:
        try:
            o_data = get_order(order_id)
            if o_data:
                sz_m = float(o_data.get("size_matched", 0))
                pr_m = float(o_data.get("price", 0))
                if sz_m > 0:
                    actual_size = sz_m
                    if pr_m > 0:
                        actual_price = pr_m
                    trade_params["bet_usd"] = actual_size * actual_price
        except Exception as e:
            log_error(f"[{symbol}] Could not sync execution details immediately: {e}")

    # Discord notification
    reversal_prefix = "🔄 REVERSAL " if is_reversal else ""
    send_discord(
        f"**{reversal_prefix}[{symbol}] {side} ${trade_params['bet_usd']:.2f}** | Confidence {trade_params['confidence']:.1%} | Price {actual_price:.4f}"
    )

    try:
        raw_scores = trade_params.get("raw_scores", {})
        trade_id = save_trade(
            cursor=cursor,
            symbol=symbol,
            window_start=trade_params["window_start"].isoformat()
            if hasattr(trade_params["window_start"], "isoformat")
            else trade_params["window_start"],
            window_end=trade_params["window_end"].isoformat()
            if hasattr(trade_params["window_end"], "isoformat")
            else trade_params["window_end"],
            slug=trade_params["slug"],
            token_id=token_id,
            side=side,
            edge=trade_params["confidence"],
            price=actual_price,
            size=actual_size,
            bet_usd=trade_params["bet_usd"],
            p_yes=trade_params.get("p_up", 0.5),
            best_bid=trade_params.get("best_bid"),
            best_ask=trade_params.get("best_ask"),
            imbalance=trade_params.get("imbalance", 0.5),
            funding_bias=trade_params.get("funding_bias", 0.0),
            order_status=actual_status,
            order_id=order_id,
            limit_sell_order_id=None,
            is_reversal=is_reversal,
            target_price=trade_params.get("target_price"),
            up_total=raw_scores.get("up_total"),
            down_total=raw_scores.get("down_total"),
            momentum_score=raw_scores.get("momentum_score"),
            momentum_dir=raw_scores.get("momentum_dir"),
            flow_score=raw_scores.get("flow_score"),
            flow_dir=raw_scores.get("flow_dir"),
            divergence_score=raw_scores.get("divergence_score"),
            divergence_dir=raw_scores.get("divergence_dir"),
            vwm_score=raw_scores.get("vwm_score"),
            vwm_dir=raw_scores.get("vwm_dir"),
            pm_mom_score=raw_scores.get("pm_mom_score"),
            pm_mom_dir=raw_scores.get("pm_mom_dir"),
            adx_score=raw_scores.get("adx_score"),
            adx_dir=raw_scores.get("adx_dir"),
            lead_lag_bonus=raw_scores.get("lead_lag_bonus"),
        )

        emoji = trade_params.get("emoji", "🚀")
        entry_type = trade_params.get("entry_type", "Trade")
        log(
            f"{emoji} [{symbol}] {entry_type}: {trade_params.get('core_summary', '')} | #{trade_id} {side} ${trade_params['bet_usd']:.2f} @ {actual_price:.4f} | ID: {order_id[:10] if order_id else 'N/A'}"
        )
        return trade_id
    except Exception as e:
        log_error(f"[{symbol}] Trade completion error: {e}")
        return None
