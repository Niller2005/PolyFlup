"""WebSocket Manager for real-time Polymarket data"""

import json
import asyncio
import threading
import time
import os
from typing import Dict, List, Optional, Callable, Any, Union
import websockets
from src.config.settings import CLOB_WSS_HOST, MARKETS
from src.utils.logger import log, log_error


class WebSocketManager:
    """
    Manages WebSocket connections to Polymarket CLOB.
    Handles real-time price updates and order notifications.
    """

    def __init__(self):
        # Base URL from settings: wss://ws-subscriptions-clob.polymarket.com
        self.wss_base_url = CLOB_WSS_HOST.rstrip("/")
        self.prices: Dict[str, float] = {}  # token_id -> midpoint_price
        self.bids: Dict[str, float] = {}  # token_id -> best_bid
        self.asks: Dict[str, float] = {}  # token_id -> best_ask
        self.price_timestamps: Dict[
            str, float
        ] = {}  # token_id -> last update timestamp
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

        # Order fill tracking for async waiting (Phase 2 WebSocket integration)
        self.order_fill_events: Dict[str, asyncio.Event] = {}  # order_id -> Event
        self.order_fill_data: Dict[str, dict] = {}  # order_id -> order data
        self._order_fill_lock = threading.Lock()  # Thread-safe access from sync code

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
                log_error(f"WebSocket event loop crashed: {e}")

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
                    log_error(
                        f"Market WebSocket lost: {e}. Reconnecting in 5s...",
                        include_traceback=False,
                    )
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
                    log_error(
                        f"User WebSocket lost: {e}. Reconnecting in 5s...",
                        include_traceback=False,
                    )
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
            log_error(f"Error handling WSS message: {e}")

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
                        self.bids[str(asset_id)] = float(b)
                        self.asks[str(asset_id)] = float(a)
                        self.price_timestamps[str(asset_id)] = time.time()
                elif event_type == "price_change":
                    for c in data.get("price_changes", []):
                        aid, b, a = (
                            c.get("asset_id"),
                            c.get("best_bid"),
                            c.get("best_ask"),
                        )
                        if aid and b and a and float(b) > 0:
                            self.prices[str(aid)] = (float(b) + float(a)) / 2.0
                            self.bids[str(aid)] = float(b)
                            self.asks[str(aid)] = float(a)
                            self.price_timestamps[str(aid)] = time.time()
                            await self._trigger_price_callbacks(
                                str(aid), self.prices[str(aid)]
                            )
                elif event_type == "last_trade_price":
                    p = data.get("price")
                    if p:
                        self.prices[str(asset_id)] = float(p)
                        self.price_timestamps[str(asset_id)] = time.time()

                new_p = self.prices.get(str(asset_id))
                if new_p and event_type != "price_change":
                    await self._trigger_price_callbacks(str(asset_id), new_p)

            elif data.get("type") == "order":
                ev, order = data.get("event"), data.get("order", {})
                order_id = order.get("id", "")
                order_id_short = (
                    order_id[:10] if order_id and len(order_id) > 10 else order_id
                )

                # Log order events for debugging
                log(f"📡 WebSocket ORDER event: {ev} | Order ID: {order_id_short}")

                # Phase 2: Trigger waiting coroutines for order fills
                if ev and ev.lower() == "fill" and order_id:
                    with self._order_fill_lock:
                        # Check if anyone is waiting for this order
                        if order_id in self.order_fill_events:
                            # Store order data
                            self.order_fill_data[order_id] = order
                            # Trigger the event
                            event = self.order_fill_events[order_id]
                            # Use call_soon_threadsafe since we might be in a different thread
                            if self._loop:
                                self._loop.call_soon_threadsafe(event.set)
                            log(
                                f"   🎯 WebSocket fill event triggered for {order_id_short}"
                            )

                # Call registered callbacks (for notifications.py integration)
                for cb in self.callbacks["order"]:
                    try:
                        cb(ev, order)
                    except Exception as cb_error:
                        log_error(f"Error in order callback for {ev}: {cb_error}")
            elif data.get("type") == "error":
                log_error(
                    f"WebSocket API Error: {data.get('message')}",
                    include_traceback=False,
                )
        except Exception as e:
            log_error(f"Error processing single WSS message: {e}")

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

    def get_price_with_age(
        self, token_id: str, max_age_seconds: float = None
    ) -> tuple[Optional[float], Optional[float]]:
        """
        Get the latest cached price for a token with its age in seconds.

        Args:
            token_id: Token ID to query
            max_age_seconds: If specified, return None if price is older than this

        Returns:
            (price, age_seconds) tuple, where age_seconds is None if no timestamp available
        """
        price = self.prices.get(str(token_id))
        if not price:
            return None, None

        timestamp = self.price_timestamps.get(str(token_id))
        if not timestamp:
            return price, None

        age_seconds = time.time() - timestamp

        # If max_age specified and price is too old, return None
        if max_age_seconds is not None and age_seconds > max_age_seconds:
            return None, age_seconds

        return price, age_seconds

    def get_bid_ask(self, token_id: str) -> tuple[Optional[float], Optional[float]]:
        """Get the latest cached bid and ask for a token"""
        return self.bids.get(str(token_id)), self.asks.get(str(token_id))

    def is_winning_side(
        self, token_id: str, side: str, target_price: float = None
    ) -> Optional[bool]:
        """
        Determine if a position side is winning based on Polymarket outcome prices.

        UP and DOWN tokens have SEPARATE midpoint prices.
        Uses get_outcome_prices() which caches bestBid/bestAsk from market API.

        Args:
            token_id: The token ID to check (UP or DOWN token)
            side: "UP" or "DOWN" (must match to token type)
            target_price: Target price from spot (for logging only)

        Returns:
            True if winning side, False if losing, None if price unavailable
        """
        from src.data.market_data import get_outcome_prices
        from src.utils.logger import log

        # Get outcome prices (caches bestBid/bestAsk from market API)
        symbol = self.token_to_symbol.get(str(token_id))
        if not symbol:
            return None

        try:
            outcome_data = get_outcome_prices(symbol)
        except:
            return None

        # Use specific winning status for token we're checking
        if side == "UP":
            return outcome_data.get("up_wins")
        elif side == "DOWN":
            return outcome_data.get("down_wins")

        return None

        try:
            outcome_data = get_outcome_prices(symbol)
        except:
            return None

        # Use the specific price for the token we're checking
        if side == "UP":
            return outcome_data.get("up_wins")
        elif side == "DOWN":
            return outcome_data.get("down_wins")

        return None

    async def wait_for_order_fill(
        self, order_id: str, timeout: float = 120
    ) -> Optional[dict]:
        """
        Wait for a specific order to fill via WebSocket notification.

        This is a Phase 2 optimization to eliminate polling delays.
        Instead of checking get_order() every 5 seconds, we wait for
        the WebSocket "fill" event to arrive and immediately return.

        Args:
            order_id: The order ID to wait for
            timeout: Maximum seconds to wait (default 120)

        Returns:
            Order data dict when filled, or None on timeout

        Example:
            # Wait for order to fill with 30s timeout
            order_data = await ws_manager.wait_for_order_fill(order_id, timeout=30)
            if order_data:
                print(f"Order filled: {order_data['size_matched']}")
            else:
                print("Timeout - order not filled")
        """
        if not self._loop or not self._running:
            log(f"⚠️  WebSocket not running, cannot wait for order {order_id[:10]} fill")
            return None

        # Create event for this order
        event = asyncio.Event()
        with self._order_fill_lock:
            self.order_fill_events[order_id] = event

        try:
            # Wait for event to be set (by _process_single_message when fill arrives)
            await asyncio.wait_for(event.wait(), timeout=timeout)

            # Event was set, retrieve the order data
            with self._order_fill_lock:
                return self.order_fill_data.pop(order_id, None)

        except asyncio.TimeoutError:
            # Timeout reached, order not filled
            return None

        finally:
            # Cleanup event registration
            with self._order_fill_lock:
                self.order_fill_events.pop(order_id, None)
                self.order_fill_data.pop(order_id, None)

    async def wait_for_orders_fill(
        self, order_ids: List[str], timeout: float = 120, require_all: bool = True
    ) -> Dict[str, Optional[dict]]:
        """
        Wait for multiple orders to fill via WebSocket notifications.

        This is optimized for atomic hedging where we place entry + hedge
        simultaneously and need to wait for both to fill.

        Args:
            order_ids: List of order IDs to wait for
            timeout: Maximum seconds to wait (default 120)
            require_all: If True, wait for ALL orders to fill (default True)
                        If False, return as soon as ANY order fills

        Returns:
            Dict mapping order_id -> order_data (or None if not filled)

        Example:
            # Wait for both entry and hedge to fill
            results = await ws_manager.wait_for_orders_fill(
                [entry_order_id, hedge_order_id],
                timeout=120,
                require_all=True
            )

            entry_data = results.get(entry_order_id)
            hedge_data = results.get(hedge_order_id)

            if entry_data and hedge_data:
                print("Both orders filled!")
            else:
                print("Timeout or partial fill")
        """
        if not self._loop or not self._running:
            log("⚠️  WebSocket not running, cannot wait for order fills")
            return {order_id: None for order_id in order_ids}

        # Create events for all orders
        events = {}
        with self._order_fill_lock:
            for order_id in order_ids:
                event = asyncio.Event()
                self.order_fill_events[order_id] = event
                events[order_id] = event

        try:
            start_time = time.time()
            filled_orders = {}

            if require_all:
                # Wait for ALL orders to fill
                while len(filled_orders) < len(order_ids):
                    elapsed = time.time() - start_time
                    remaining_timeout = timeout - elapsed

                    if remaining_timeout <= 0:
                        # Timeout reached
                        break

                    # Wait for any remaining order to fill
                    pending_ids = [oid for oid in order_ids if oid not in filled_orders]
                    pending_events = [events[oid] for oid in pending_ids]

                    try:
                        # Wait for first event to trigger
                        done, pending = await asyncio.wait(
                            [asyncio.create_task(e.wait()) for e in pending_events],
                            timeout=remaining_timeout,
                            return_when=asyncio.FIRST_COMPLETED,
                        )

                        # Check which orders filled
                        with self._order_fill_lock:
                            for order_id in pending_ids:
                                if events[order_id].is_set():
                                    filled_orders[order_id] = self.order_fill_data.get(
                                        order_id
                                    )

                    except asyncio.TimeoutError:
                        break

            else:
                # Wait for ANY order to fill (return_when=FIRST_COMPLETED)
                try:
                    done, pending = await asyncio.wait(
                        [asyncio.create_task(e.wait()) for e in events.values()],
                        timeout=timeout,
                        return_when=asyncio.FIRST_COMPLETED,
                    )

                    # Collect all filled orders
                    with self._order_fill_lock:
                        for order_id in order_ids:
                            if events[order_id].is_set():
                                filled_orders[order_id] = self.order_fill_data.get(
                                    order_id
                                )

                except asyncio.TimeoutError:
                    pass

            # Build result dict (None for unfilled orders)
            result = {}
            for order_id in order_ids:
                result[order_id] = filled_orders.get(order_id)

            return result

        finally:
            # Cleanup all event registrations
            with self._order_fill_lock:
                for order_id in order_ids:
                    self.order_fill_events.pop(order_id, None)
                    self.order_fill_data.pop(order_id, None)

    def register_callback(self, event_type: str, callback: Callable):
        """Register a function to be called on WSS events"""
        if event_type in self.callbacks:
            self.callbacks[event_type].append(callback)


# Singleton instance
ws_manager = WebSocketManager()
