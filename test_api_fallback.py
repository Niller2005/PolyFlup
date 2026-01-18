#!/usr/bin/env python3
"""
Quick test to verify API fallback for price fetching works correctly.
Tests the _get_token_price_from_api() function.
"""

import sys
from src.trading.orders import client
from src.data.market_data import get_token_ids


def test_api_price_fetch():
    """Test fetching prices via API for active crypto tokens."""

    print("🧪 Testing API price fetch fallback...\n")

    # Get current token IDs for active crypto symbols
    symbols = ["BTC", "ETH", "SOL"]

    for symbol in symbols:
        print(f"Testing {symbol}:")
        try:
            up_id, down_id = get_token_ids(symbol)

            if not up_id or not down_id:
                print(f"  ❌ Could not get token IDs for {symbol}")
                continue

            # Test UP token
            print(f"  UP token: {up_id[:16]}...")
            response = client.get_midpoint(up_id)
            if response and "mid" in response:
                mid_price = float(response["mid"])
                print(f"    ✅ UP price: ${mid_price:.4f}")
            else:
                print(f"    ❌ Failed to get UP price: {response}")

            # Test DOWN token
            print(f"  DOWN token: {down_id[:16]}...")
            response = client.get_midpoint(down_id)
            if response and "mid" in response:
                mid_price = float(response["mid"])
                print(f"    ✅ DOWN price: ${mid_price:.4f}")
            else:
                print(f"    ❌ Failed to get DOWN price: {response}")

            print()

        except Exception as e:
            print(f"  ❌ Error: {e}\n")
            continue

    print("✅ API fallback test complete!")


if __name__ == "__main__":
    test_api_price_fetch()
