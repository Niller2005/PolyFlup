"""
Test script to demonstrate window start price buffer functionality.

This shows how the buffer helps account for small discrepancies between
our cached Binance price and Polymarket's actual settlement reference price.
"""

from src.data.market_data import get_window_start_price, get_window_start_price_range
from src.config.settings import WINDOW_START_PRICE_BUFFER_PCT


def test_price_buffer():
    """Test the price buffer functionality"""

    print("=" * 70)
    print("Window Start Price Buffer Test")
    print("=" * 70)
    print()

    # Example: ETH price scenario from the issue
    print(f"Buffer Configuration: ±{WINDOW_START_PRICE_BUFFER_PCT}%")
    print()

    symbols = ["ETH", "BTC", "SOL", "XRP"]

    for symbol in symbols:
        print(f"{symbol}:")
        print("-" * 40)

        # Get current window start price
        center_price = get_window_start_price(symbol)

        if center_price > 0:
            # Get buffered range
            center, lower, upper = get_window_start_price_range(symbol)

            print(f"  Center Price:  ${center:,.2f}")
            print(f"  Lower Bound:   ${lower:,.2f}")
            print(f"  Upper Bound:   ${upper:,.2f}")
            print(f"  Buffer Range:  ±${abs(upper - center):,.2f}")
            print()

            # Example: Show what would be acceptable with buffer
            print(f"  ✓ Acceptable range for comparison:")
            print(f"    Any price between ${lower:,.2f} and ${upper:,.2f}")
            print(f"    would be within tolerance.")
            print()
        else:
            print(f"  ❌ Error fetching price for {symbol}")
            print()

    print("=" * 70)
    print()
    print("Usage in code:")
    print("  center, lower, upper = get_window_start_price_range('ETH')")
    print("  if lower <= actual_price <= upper:")
    print("      # Price is within acceptable buffer range")
    print("=" * 70)


if __name__ == "__main__":
    test_price_buffer()
