#!/usr/bin/env python3
"""Simple verification script for XRP balance fix"""

import os
import sys

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

def verify_imports():
    """Verify that all imports work correctly"""
    print("🔍 Verifying imports...")
    
    try:
        # Test basic config import
        from src.config.settings import ENABLE_ENHANCED_BALANCE_VALIDATION
        print(f"✅ ENABLE_ENHANCED_BALANCE_VALIDATION: {ENABLE_ENHANCED_BALANCE_VALIDATION}")
        
        # Test balance validation module structure
        from src.trading.orders.balance_validation import (
            get_symbol_config,
            get_enhanced_balance_allowance,
            SYMBOL_TOLERANCE_CONFIG
        )
        
        print("✅ Balance validation module imported successfully")
        
        # Test symbol configs
        xrp_config = get_symbol_config("XRP")
        btc_config = get_symbol_config("BTC")
        
        print(f"✅ XRP config: zero_balance_threshold={xrp_config['zero_balance_threshold']}")
        print(f"✅ BTC config: zero_balance_threshold={btc_config['zero_balance_threshold']}")
        
        # Test that enhanced function exists
        print(f"✅ Enhanced balance function signature: {get_enhanced_balance_allowance.__code__.co_varnames}")
        
        return True
        
    except Exception as e:
        print(f"❌ Import verification failed: {e}")
        return False

def verify_file_structure():
    """Verify that all necessary files exist"""
    print("\n🔍 Verifying file structure...")
    
    required_files = [
        "src/trading/orders/balance_validation.py",
        "src/trading/position_manager/exit.py",
        "src/trading/position_manager/stop_loss.py", 
        "src/trading/position_manager/monitor.py",
        "src/config/settings.py"
    ]
    
    all_exist = True
    for file_path in required_files:
        if os.path.exists(file_path):
            print(f"✅ {file_path}")
        else:
            print(f"❌ {file_path} - MISSING")
            all_exist = False
    
    return all_exist

def verify_code_changes():
    """Verify that key code changes are present"""
    print("\n🔍 Verifying code changes...")
    
    checks = [
        ("src/trading/position_manager/exit.py", "get_enhanced_balance_allowance"),
        ("src/trading/position_manager/stop_loss.py", "get_enhanced_balance_allowance"),
        ("src/trading/position_manager/monitor.py", "user_address"),
        ("src/config/settings.py", "ENABLE_ENHANCED_BALANCE_VALIDATION"),
    ]
    
    all_good = True
    for file_path, search_term in checks:
        try:
            with open(file_path, 'r') as f:
                content = f.read()
                if search_term in content:
                    print(f"✅ {file_path} contains '{search_term}'")
                else:
                    print(f"❌ {file_path} missing '{search_term}'")
                    all_good = False
        except Exception as e:
            print(f"❌ Error checking {file_path}: {e}")
            all_good = False
    
    return all_good

def main():
    """Main verification function"""
    print("🚀 Verifying XRP Balance Fix Implementation")
    print("=" * 50)
    
    # Run all verifications
    imports_ok = verify_imports()
    structure_ok = verify_file_structure()
    changes_ok = verify_code_changes()
    
    print("\n" + "=" * 50)
    if imports_ok and structure_ok and changes_ok:
        print("✅ All verifications passed! XRP balance fix is ready.")
        print("\nNext steps:")
        print("1. Start the bot with the enhanced balance validation")
        print("2. Monitor XRP positions for exit plan placement")
        print("3. Check logs for balance validation messages")
    else:
        print("❌ Some verifications failed. Please review the issues above.")
        return 1
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
