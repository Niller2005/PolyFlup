#!/usr/bin/env python3
"""Test script for enhanced balance validation"""

import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from src.trading.orders.balance_validation import get_enhanced_balance_allowance, get_symbol_config
from src.utils.logger import log

def test_balance_validation():
    """Test the enhanced balance validation functionality"""
    log("🧪 Testing Enhanced Balance Validation")
    
    # Test symbol config
    log("Testing symbol configurations:")
    xrp_config = get_symbol_config("XRP")
    btc_config = get_symbol_config("BTC")
    default_config = get_symbol_config("UNKNOWN")
    
    log(f"XRP Config: {xrp_config}")
    log(f"BTC Config: {btc_config}")
    log(f"Default Config: {default_config}")
    
    # Test enhanced balance validation (mock data)
    log("\nTesting enhanced balance validation:")
    
    # Simulate XRP with zero balance but significant position
    result = get_enhanced_balance_allowance(
        token_id="mock_xrp_token_123",
        symbol="XRP", 
        user_address="0x1234567890abcdef",
        trade_age_seconds=300,  # 5 minutes old
        enable_cross_validation=False  # Disable for testing to avoid API calls
    )
    
    log(f"Enhanced balance result: {result}")
    
    log("✅ Balance validation test completed")

if __name__ == "__main__":
    test_balance_validation()
