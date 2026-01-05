"""WebSocket Manager for real-time Polymarket data"""

import json
import asyncio
import threading
import time
import os
from typing import Dict, List, Optional, Callable, Any, Union
import websockets
from src.config.settings import CLOB_WSS_HOST, MARKETS
from src.utils.logger import log
from py_clob_client.signing.hmac import build_hmac_signature


class WebSocketManager:
    """
    Manages WebSocket connections to Polymarket CLOB.
    Handles real-time price updates and order notifications.
    """

    def __init__(self):
        self.wss_url = CLOB_WSS_HOST
        self.prices: Dict[str, float] = {}  # token_id -> midpoint_price
        self.token_to_symbol: Dict[str, str] = {}
        self.callbacks: Dict[str, List[Callable]] = {
            "price": [],
            "order": [],
        }
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._connected = False
        self.subscribed_tokens: List[str] = []
        self.subscription_queue: asyncio.Queue = asyncio.Queue()
        self._auth_done = False

    def start(self):
        """Start the WebSocket manager in a background thread"""
        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(target=self._run_event_loop, daemon=True)
        self._thread.start()
        log("🚀 WebSocket Manager started in background")

    def stop(self):
        """Stop the WebSocket manager"""
        self._running = False
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)

    def _run_event_loop(self):
        """Internal method to run the asyncio event loop"""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        # Re-initialize the queue in the correct loop
        self.subscription_queue = asyncio.Queue()
        try:
            self._loop.run_until_complete(self._main_loop())
        except Exception as e:
            log(f"❌ WebSocket event loop crashed: {e}")

    async def _main_loop(self):
        """Main connection and message loop"""
        while self._running:
            try:
                # Use longer timeout for connection
                async with websockets.connect(
                    self.wss_url, ping_interval=20, ping_timeout=20
                ) as websocket:
                    self._connected = True
                    self._auth_done = False
                    log(f"✅ WebSocket connected to {self.wss_url}")

                    # 1. Authenticate (required for user channel)
                    await self._authenticate(websocket)

                    # 2. Subscribe to user updates
                    await self._subscribe_user(websocket)

                    # 3. Re-subscribe to existing prices using the "market" channel
                    if self.subscribed_tokens:
                        await self._subscribe_market(websocket, self.subscribed_tokens)

                    # 4. Handle incoming messages and subscription queue
                    recv_task = asyncio.create_task(self._receive_messages(websocket))
                    sub_task = asyncio.create_task(
                        self._process_subscription_queue(websocket)
                    )

                    done, pending = await asyncio.wait(
                        [recv_task, sub_task], return_when=asyncio.FIRST_COMPLETED
                    )

                    for task in pending:
                        task.cancel()

                    for task in done:
                        exc = task.exception()
                        if exc:
                            raise exc

            except Exception as e:
                self._connected = False
                self._auth_done = False
                if self._running:
                    log(f"⚠️ WebSocket connection lost: {e}. Reconnecting in 5s...")
                    await asyncio.sleep(5)

    async def _authenticate(self, websocket):
        """Authenticate the WebSocket connection"""
        api_key = os.getenv("API_KEY")
        api_secret = os.getenv("API_SECRET")
        api_passphrase = os.getenv("API_PASSPHRASE")

        if not (api_key and api_secret and api_passphrase):
            log("⚠️ API Credentials missing, skipping WebSocket authentication")
            return

        timestamp = str(int(time.time()))
        nonce = 0

        # Build HMAC signature for WSS auth
        # Request path for WSS auth is usually "/ws"
        signature = build_hmac_signature(api_secret, timestamp, "GET", "/ws")

        auth_msg = {
            "type": "auth",
            "apiKey": api_key,
            "signature": signature,
            "nonce": nonce,
            "timestamp": timestamp,
            "passphrase": api_passphrase,
        }

        await websocket.send(json.dumps(auth_msg))

    async def _subscribe_user(self, websocket):
        """Subscribe to user specific updates (orders, fills)"""
        msg = {"type": "subscribe", "channel": "user"}
        await websocket.send(json.dumps(msg))
        log("📡 Subscribed to User Channel")

    async def _subscribe_market(self, websocket, token_ids: List[str]):
        """Subscribe to market channel for price updates"""
        if not token_ids:
            return

        msg = {"type": "subscribe", "channel": "market", "assets_ids": token_ids}
        await websocket.send(json.dumps(msg))
        log(f"📡 Subscribed to Market Channel for {len(token_ids)} tokens")

    async def _process_subscription_queue(self, websocket):
        """Handle new subscriptions while connection is active"""
        while self._running:
            token_ids = await self.subscription_queue.get()
            if self._connected:
                await self._subscribe_market(websocket, token_ids)
            self.subscription_queue.task_done()

    async def _receive_messages(self, websocket):
        """Continuous message reception loop"""
        async for message in websocket:
            if not self._running:
                break
            await self._handle_message(message)

    async def _handle_message(self, message: Union[str, bytes]):
        """Process incoming WSS messages"""
        try:
            if isinstance(message, bytes):
                message = message.decode("utf-8")

            data = json.loads(message)
            msg_type = data.get("event_type") or data.get("type")

            # Handle multiple message formats from Polymarket
            if msg_type in ["book", "price_change", "best_bid_ask", "last_trade_price"]:
                # Handle price update from market channel
                asset_id = data.get("asset_id")

                if msg_type == "best_bid_ask":
                    bid = data.get("best_bid")
                    ask = data.get("best_ask")
                    if bid and ask:
                        mid = (float(bid) + float(ask)) / 2.0
                        self.prices[asset_id] = mid
                elif msg_type == "price_change":
                    changes = data.get("price_changes", [])
                    for c in changes:
                        a_id = c.get("asset_id")
                        bid = c.get("best_bid")
                        ask = c.get("best_ask")
                        if bid and ask and float(bid) > 0 and float(ask) > 0:
                            self.prices[a_id] = (float(bid) + float(ask)) / 2.0
                elif msg_type == "last_trade_price":
                    price = data.get("price")
                    if asset_id and price:
                        self.prices[asset_id] = float(price)

                # Execute callbacks if price updated
                new_price = self.prices.get(asset_id)
                if new_price:
                    for cb in self.callbacks["price"]:
                        try:
                            cb(asset_id, new_price)
                        except Exception as e:
                            log(f"❌ Error in price callback: {e}")

            elif msg_type == "order":
                # Handle user order updates
                event = data.get("event")
                order = data.get("order", {})
                for cb in self.callbacks["order"]:
                    try:
                        cb(event, order)
                    except Exception as e:
                        log(f"❌ Error in order callback: {e}")

            elif msg_type == "auth":
                if data.get("success"):
                    self._auth_done = True
                    log("✅ WebSocket authenticated successfully")
                else:
                    log(f"❌ WebSocket authentication FAILED: {data.get('message')}")

            elif msg_type == "error":
                log(f"❌ WebSocket API Error: {data.get('message')}")

        except Exception as e:
            log(f"⚠️ Error handling WSS message: {e}")

    def subscribe_to_prices(
        self, token_ids: List[str], symbol_map: Optional[Dict[str, str]] = None
    ):
        """Public method to add tokens to subscription list"""
        new_tokens = [t for t in token_ids if t not in self.subscribed_tokens]
        if not new_tokens:
            return

        self.subscribed_tokens.extend(new_tokens)
        if symbol_map:
            self.token_to_symbol.update(symbol_map)

        if self._connected and self._loop:
            self._loop.call_soon_threadsafe(
                self.subscription_queue.put_nowait, new_tokens
            )

    def get_price(self, token_id: str) -> Optional[float]:
        """Get the latest cached price for a token"""
        return self.prices.get(token_id)

    def register_callback(self, event_type: str, callback: Callable):
        """Register a function to be called on WSS events"""
        if event_type in self.callbacks:
            self.callbacks[event_type].append(callback)


# Singleton instance
ws_manager = WebSocketManager()
