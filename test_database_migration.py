#!/usr/bin/env python3
"""Test database.py normalized schema migration"""

import sys

sys.path.insert(0, "/mnt/d/dev/polyastra")

from src.data.database import (
    has_side_for_window,
    has_trade_for_window,
    get_total_exposure,
    generate_statistics,
)

print("=" * 60)
print("Testing database.py migrations to normalized schema")
print("=" * 60)

print("\n1. Testing has_side_for_window()...")
result = has_side_for_window("ETH", "2026-01-09T18:00:00-05:00", "UP")
print(f"   has_side_for_window(ETH, 2026-01-09T18:00:00-05:00, UP): {result}")

print("\n2. Testing has_trade_for_window()...")
result = has_trade_for_window("ETH", "2026-01-09T18:00:00-05:00")
print(f"   has_trade_for_window(ETH, 2026-01-09T18:00:00-05:00): {result}")

print("\n3. Testing get_total_exposure()...")
exposure = get_total_exposure()
print(f"   Total exposure: ${exposure:.2f}")

print("\n4. Testing generate_statistics()...")
generate_statistics()

print("\n" + "=" * 60)
print("✓ All database.py functions working with normalized schema!")
print("=" * 60)
