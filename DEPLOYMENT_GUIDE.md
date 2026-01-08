# XRP Balance Fix - Deployment Guide

## Summary
This implementation provides a targeted fix for XRP balance API sync issues where the balance consistently shows 0.0000 despite active positions, preventing exit plans from being placed.

## Changes Made

### ✅ New Files Created
- `src/trading/orders/balance_validation.py` - Enhanced balance validation module

### ✅ Modified Files
- `src/trading/position_manager/exit.py` - Updated to use enhanced balance validation
- `src/trading/position_manager/stop_loss.py` - Updated to use enhanced balance validation  
- `src/trading/position_manager/monitor.py` - Updated to pass user_address parameter
- `src/bot.py` - Updated to pass user_address to position monitoring
- `src/config/settings.py` - Added configurable balance validation settings

## Key Features Implemented

### 1. Symbol-Specific Tolerance
- **XRP-specific configuration** with higher thresholds and more retries
- **Configurable trust factors** for different API reliability levels
- **Grace period handling** for API synchronization delays

### 2. Cross-Validation Logic
- **Position data validation** against balance API results
- **Weighted average calculations** for significant discrepancies
- **Fallback to position data** when balance shows zero for active positions

### 3. Enhanced Retry Mechanisms
- **Exponential backoff** for failed API calls
- **Symbol-specific retry counts** (XRP: 3 retries, others: 2)
- **Timeout handling** with longer timeouts for unreliable APIs

## Environment Variables

Add these to your `.env` file to configure the enhanced balance validation:

```bash
# Enhanced Balance Validation (default: YES)
ENABLE_ENHANCED_BALANCE_VALIDATION=YES

# Cross-validation timeout in seconds (default: 15)
BALANCE_CROSS_VALIDATION_TIMEOUT=15

# XRP-specific grace period in minutes (default: 15)
XRP_BALANCE_GRACE_PERIOD_MINUTES=15

# XRP balance API trust factor 0.0-1.0 (default: 0.3, lower = less trust)
XRP_BALANCE_TRUST_FACTOR=0.3
```

## Deployment Steps

### 1. Pre-Deployment Verification
```bash
# Run the verification script to check implementation
python verify_fix.py
```

### 2. Configuration
```bash
# Add the new environment variables to your .env file
echo "ENABLE_ENHANCED_BALANCE_VALIDATION=YES" >> .env
echo "XRP_BALANCE_GRACE_PERIOD_MINUTES=15" >> .env
echo "XRP_BALANCE_TRUST_FACTOR=0.3" >> .env
```

### 3. Database Backup (Recommended)
```bash
# Backup your database before deployment
cp trades.db trades.db.backup.$(date +%Y%m%d_%H%M%S)
```

### 4. Deployment
```bash
# Start the bot with the new balance validation
uv run polyflup.py
```

## Monitoring & Validation

### 1. Check Logs for Balance Validation Messages
Look for these specific log messages:
```
🔍 [XRP] Balance validation: {...}
⚠️ [XRP] Balance shows zero (0.0000) but position shows 5.23. Using position data
⚠️ [XRP] Position (5.23) >> Balance (0.0000). Using weighted average with position bias.
```

### 2. Monitor XRP Exit Plans
- Watch for XRP positions that previously couldn't place exit plans
- Verify that exit orders are now being placed despite zero balance readings
- Check that exit plan sizes match expected position sizes

### 3. Cross-Validation Health
- Monitor for balance/position discrepancy warnings
- Check that API response times remain reasonable (< 15s)
- Verify that fallback logic activates appropriately

## Troubleshooting

### Issue: XRP exit plans still not placing
**Solution:** 
1. Increase grace period: `XRP_BALANCE_GRACE_PERIOD_MINUTES=20`
2. Decrease balance trust: `XRP_BALANCE_TRUST_FACTOR=0.2`
3. Check logs for specific error messages

### Issue: Performance degradation
**Solution:**
1. Reduce cross-validation timeout: `BALANCE_CROSS_VALIDATION_TIMEOUT=10`
2. Disable for reliable symbols: Modify `balance_validation.py` to skip for non-XRP symbols
3. Monitor API response times in logs

### Issue: Too many false positives
**Solution:**
1. Increase zero balance threshold in XRP config
2. Adjust grace period based on typical API sync times
3. Fine-tune trust factors based on observed accuracy

## Rollback Plan

If issues arise, you can quickly rollback:

### 1. Disable Enhanced Validation
```bash
# Set environment variable to disable
export ENABLE_ENHANCED_BALANCE_VALIDATION=NO
# Restart bot
pkill -f polyflup.py
uv run polyflup.py
```

### 2. Revert Code Changes
```bash
# Restore from git (if using version control)
git checkout HEAD~1
# Or manually revert the key changes in:
# - src/trading/position_manager/exit.py
# - src/trading/position_manager/stop_loss.py
# - src/trading/position_manager/monitor.py
```

## Success Criteria

The fix is successful when:
1. ✅ XRP positions with active trades can place exit plans
2. ✅ Balance validation logs show proper cross-validation
3. ✅ No increase in false ghost trade settlements
4. ✅ Performance remains acceptable (< 1s additional latency)
5. ✅ Other symbols (BTC, ETH, SOL) continue to work normally

## Next Steps

After deployment:
1. **Monitor for 24-48 hours** to ensure stability
2. **Adjust configuration** based on observed behavior
3. **Consider expanding** to other symbols if they show similar issues
4. **Document learnings** for future API reliability improvements

The implementation is ready for deployment and should resolve the XRP balance API sync issues while maintaining compatibility with other trading symbols.
