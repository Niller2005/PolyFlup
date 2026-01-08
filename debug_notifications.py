#!/usr/bin/env python3
"""
Debug script to examine actual notification structure
"""

import json
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.config.settings import setup_env
setup_env()

from src.trading.orders import get_notifications
from src.utils.logger import log

def debug_notification_structure():
    """Get notifications and log their detailed structure"""
    try:
        notifications = get_notifications()
        
        if not notifications:
            log("🐛 DEBUG: No notifications found")
            return
            
        log(f"🐛 DEBUG: Found {len(notifications)} notifications")
        
        for i, notif in enumerate(notifications):
            log(f"🐛 DEBUG: Notification {i+1} structure:")
            
            if isinstance(notif, dict):
                log(f"🐛 DEBUG: Keys: {list(notif.keys())}")
                for key, value in notif.items():
                    if key == 'payload':
                        if isinstance(value, dict):
                            log(f"🐛 DEBUG: payload keys: {list(value.keys())}")
                            for k, v in value.items():
                                log(f"🐛 DEBUG: payload['{k}'] = {repr(v)}")
                        else:
                            log(f"🐛 DEBUG: payload = {repr(value)}")
                    else:
                        log(f"🐛 DEBUG: {key} = {repr(value)}")
            else:
                log(f"🐛 DEBUG: Notification is not a dict: {type(notif)}")
                log(f"🐛 DEBUG: Attributes: {[attr for attr in dir(notif) if not attr.startswith('_')]}")
                
                # Try to get common attributes
                for attr in ['id', 'type', 'timestamp', 'owner', 'payload']:
                    try:
                        value = getattr(notif, attr, None)
                        log(f"🐛 DEBUG: {attr} = {repr(value)}")
                    except:
                        log(f"🐛 DEBUG: {attr} = <error getting attribute>")
                        
            log("🐛 DEBUG: " + "="*50)
            
    except Exception as e:
        log(f"🐛 DEBUG ERROR: {e}")

if __name__ == "__main__":
    debug_notification_structure()
