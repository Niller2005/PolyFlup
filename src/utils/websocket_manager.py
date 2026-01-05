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


class WebSocketManager:
    """
    Manages WebSocket connections to Polymarket CLOB.
    Handles real-time price updates and order notifications.
    """

    def __init__(self):
        # Base URL from settings: wss://ws-subscriptions-clob.polymarket.com
        self.wss_base_url = CLOB_WSS_HOST.rstrip("/")
        self.prices: Dict[str, float] = {}  # token_id -> midpoint_price
        self.token_to_symbol: Dict[str, str] = {}
        self.callbacks: Dict[str, List[Callable]] = {
            "price": [],
            "order": [],
        }
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self.subscribed_tokens: List[str] = []
        self.subscription_queue: asyncio.Queue = asyncio.Queue()

    def start(self):
        """Start the WebSocket manager in background threads"""
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
        self.subscription_queue = asyncio.Queue()
        try:
            # We run both market and user connections in parallel tasks
            self._loop.run_until_complete(
                asyncio.gather(self._market_loop(), self._user_loop())
            )
        except Exception as e:
            if self._running:
                log(f"❌ WebSocket event loop crashed: {e}")

    async def _market_loop(self):
        """Handles connection to the public market data channel"""
        url = f"{self.wss_base_url}/ws/market"
        while self._running:
            try:
                async with websockets.connect(
                    url, ping_interval=10, ping_timeout=10
                ) as ws:
                    log(f"✅ WebSocket connected to Market Channel")
                    if self.subscribed_tokens:
                        await self._subscribe_market(ws, self.subscribed_tokens)

                    recv_task = asyncio.create_task(self._receive_messages(ws))
                    sub_task = asyncio.create_task(
                        self._process_market_subscription_queue(ws)
                    )

                    done, pending = await asyncio.wait(
                        [recv_task, sub_task], return_when=asyncio.FIRST_COMPLETED
                    )
                    for task in pending:
                        task.cancel()
                    for task in done:
                        exc = task.exception()
                        if exc is not None:
                            raise exc
            except Exception as e:
                if self._running:
                    log(f"⚠️ Market WebSocket lost: {e}. Reconnecting in 5s...")
                    await asyncio.sleep(5)

    async def _user_loop(self):
        """Handles connection to the private authenticated user channel"""
        url = f"{self.wss_base_url}/ws/user"
        while self._running:
            try:
                api_key = os.getenv("API_KEY")
                api_secret = os.getenv("API_SECRET")
                api_passphrase = os.getenv("API_PASSPHRASE")

                if not (api_key and api_secret and api_passphrase):
                    await asyncio.sleep(60)
                    continue

                async with websockets.connect(
                    url, ping_interval=10, ping_timeout=10
                ) as ws:
                    log(f"✅ WebSocket connected to User Channel")
                    auth_data = {
                        "apiKey": api_key,
                        "secret": api_secret,
                        "passphrase": api_passphrase,
                    }
                    msg = {"type": "user", "auth": auth_data, "markets": []}
                    await ws.send(json.dumps(msg))

                    ping_task = asyncio.create_task(self._ping_loop(ws))
                    recv_task = asyncio.create_task(self._receive_messages(ws))

                    done, pending = await asyncio.wait(
                        [ping_task, recv_task], return_when=asyncio.FIRST_COMPLETED
                    )
                    for task in pending:
                        task.cancel()
                    for task in done:
                        exc = task.exception()
                        if exc is not None:
                            raise exc
            except Exception as e:
                if self._running:
                    log(f"⚠️ User WebSocket lost: {e}. Reconnecting in 5s...")
                    await asyncio.sleep(5)

    async def _ping_loop(self, ws):
        """Send PING every 10 seconds as per Quickstart"""
        while self._running:
            await asyncio.sleep(10)
            await ws.send("PING")

    async def _subscribe_market(self, ws, token_ids: List[str]):
        """Subscribe to market channel"""
        if not token_ids:
            return
        msg = {"type": "market", "assets_ids": token_ids}
        await ws.send(json.dumps(msg))
        log(f"📡 Subscribed to {len(token_ids)} tokens on Market Channel")

    async def _process_market_subscription_queue(self, ws):
        """Handle new market subscriptions"""
        while self._running:
            token_ids = await self.subscription_queue.get()
            await self._subscribe_market(ws, token_ids)
            self.subscription_queue.task_done()

    async def _receive_messages(self, ws):
        """Continuous message reception loop"""
        async for message in ws:
            if not self._running:
                break
            if message == "PONG":
                continue
            await self._handle_message(message)

    async def _handle_message(self, message: Union[str, bytes]):
        """Process incoming WSS messages"""
        try:
            if isinstance(message, bytes):
                message = message.decode("utf-8")
            if message in ["PONG", "PING"]:
                return

            try:
                data = json.loads(message)
            except:
                return

            if isinstance(data, list):
                for item in data:
                    await self._process_single_message(item)
            else:
                await self._process_single_message(data)
        except Exception as e:
            log(f"⚠️ Error handling WSS message: {e}")

    async def _process_single_message(self, data: Any):
        """Process a single message object from WSS"""
        if not isinstance(data, dict):
            return
        try:
            event_type = data.get("event_type")
            if event_type in [
                "book",
                "price_change",
                "best_bid_ask",
                "last_trade_price",
            ]:
                asset_id = data.get("asset_id")
                if not asset_id:
                    return

                if event_type == "best_bid_ask":
                    b, a = data.get("best_bid"), data.get("best_ask")
                    if b and a:
                        self.prices[str(asset_id)] = (float(b) + float(a)) / 2.0
                elif event_type == "price_change":
                    for c in data.get("price_changes", []):
                        aid, b, a = (
                            c.get("asset_id"),
                            c.get("best_bid"),
                            c.get("best_ask"),
                        )
                        if aid and b and a and float(b) > 0:
                            self.prices[str(aid)] = (float(b) + float(a)) / 2.0
                            await self._trigger_price_callbacks(
                                str(aid), self.prices[str(aid)]
                            )
                elif event_type == "last_trade_price":
                    p = data.get("price")
                    if p:
                        self.prices[str(asset_id)] = float(p)

                new_p = self.prices.get(str(asset_id))
                if new_p and event_type != "price_change":
                    await self._trigger_price_callbacks(str(asset_id), new_p)

            elif data.get("type") == "order":
                ev, order = data.get("event"), data.get("order", {})
                for cb in self.callbacks["order"]:
                    try:
                        cb(ev, order)
                    except:
                        pass
            elif data.get("type") == "error":
                log(f"❌ WebSocket API Error: {data.get('message')}")
        except Exception as e:
            log(f"⚠️ Error processing single WSS message: {e}")

    async def _trigger_price_callbacks(self, asset_id: str, price: float):
        """Execute all registered price callbacks"""
        for cb in self.callbacks["price"]:
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb(asset_id, price)
                else:
                    cb(asset_id, price)
            except:
                pass

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
        if self._loop and self._running:
            self._loop.call_soon_threadsafe(
                self.subscription_queue.put_nowait, new_tokens
            )

    def get_price(self, token_id: str) -> Optional[float]:
        """Get the latest cached price for a token"""
        return self.prices.get(str(token_id))

    def register_callback(self, event_type: str, callback: Callable):
        """Register a function to be called on WSS events"""
        if event_type in self.callbacks:
            self.callbacks[event_type].append(callback)


# Singleton instance
ws_manager = WebSocketManager()
