"""Data access for the terminal dashboard.

The dashboard is a separate, read-only process: it opens `trades.db` with
`mode=ro` + `query_only` (so it can never block or corrupt the bot's writes) and
only needs the database file, which lets it run against a database downloaded
from production without any `.env`. This is the one place where the
`db_connection()` write helper is deliberately not used.
"""

import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

import requests

from src import profiles
from src.markets import DEFAULT_MINUTES, parse_slug, window_bounds, window_minutes

CLOB_HOST = "https://clob.polymarket.com"
ET = ZoneInfo("America/New_York")


@dataclass
class OpenPosition:
    id: int
    symbol: str
    side: str
    size: float
    entry_price: float
    bet_usd: float
    token_id: str
    window_end: Optional[datetime]
    has_exit_order: bool
    scaled_in: bool
    is_reversal: bool
    is_hedged: bool = False
    hedge_price: Optional[float] = None
    entry_sold: bool = False  # entry leg exited early (stop loss / pre-settlement)
    hedge_sold: bool = False  # hedge leg exited early
    exit_price: Optional[float] = None  # entry leg sale price
    hedge_exit_price: Optional[float] = None  # hedge leg sale price
    slug: Optional[str] = None
    unpaired: bool = False  # a held leg with no trade row (other leg unfilled / pair not saved)

    @property
    def hedge_side(self) -> str:
        return {"UP": "DOWN", "DOWN": "UP"}.get(self.side, "?")

    @property
    def pair_cost(self) -> float:
        """Cash committed to this position (both legs when hedged)."""
        if self.is_hedged and self.hedge_price:
            return self.size * (self.entry_price + self.hedge_price)
        return self.bet_usd

    def realized(self, fee_rate: float = 0.07) -> Optional[float]:
        """P&L already booked by selling a leg early, net of the taker fee; None if nothing sold."""
        fee = lambda p: self.size * fee_rate * p * (1.0 - p)
        total, any_sold = 0.0, False
        if self.entry_sold and self.exit_price is not None:
            total += self.size * (self.exit_price - self.entry_price) - fee(self.exit_price)
            any_sold = True
        if self.hedge_sold and self.hedge_exit_price is not None and self.hedge_price:
            total += self.size * (self.hedge_exit_price - self.hedge_price) - fee(self.hedge_exit_price)
            any_sold = True
        return total if any_sold else None

    def pnl(self, mid: Optional[float]):
        """(pnl $, pnl % of cost, locked?) combining locked, realized and open-leg mark-to-market.

        Both legs held: the locked edge. A leg sold: its realized result (with fee) plus the
        other leg marked to the entry-token midpoint (hedge ~ 1 - mid). Unhedged: mark only.
        """
        locked = self.locked_edge
        if locked is not None:
            return locked * self.size, locked / (self.entry_price + self.hedge_price), True
        realized = self.realized()
        cost = self.pair_cost
        if realized is None:
            if mid is None:
                return None, None, False
            pnl = (mid - self.entry_price) * self.size
            return pnl, (mid / self.entry_price - 1) if self.entry_price else None, False
        open_mark = 0.0
        if not self.entry_sold:
            if mid is None:
                return None, None, False
            open_mark = (mid - self.entry_price) * self.size
        elif not self.hedge_sold and self.hedge_price:
            if mid is None:
                return None, None, False
            open_mark = ((1.0 - mid) - self.hedge_price) * self.size
        total = realized + open_mark
        return total, (total / cost if cost else None), False

    @property
    def locked_edge(self) -> Optional[float]:
        """Guaranteed profit per share if both legs are held to resolution."""
        if self.is_hedged and self.hedge_price and not (self.entry_sold or self.hedge_sold):
            return 1.0 - self.entry_price - self.hedge_price
        return None


@dataclass
class ClosedTrade:
    id: int
    symbol: str
    side: str
    entry_price: float
    exit_price: Optional[float]
    size: float
    pnl: Optional[float]  # None while a held leg-out is unresolved
    roi_pct: Optional[float]
    outcome: str
    exited_early: bool
    settled_at: Optional[datetime]


@dataclass
class PeriodStats:
    label: str
    trades: int = 0
    wins: int = 0
    pnl: float = 0.0
    staked: float = 0.0
    gross_win: float = 0.0
    gross_loss: float = 0.0

    @property
    def win_rate(self) -> Optional[float]:
        return self.wins / self.trades if self.trades else None

    @property
    def roi(self) -> Optional[float]:
        return self.pnl / self.staked if self.staked else None

    @property
    def profit_factor(self) -> Optional[float]:
        return self.gross_win / self.gross_loss if self.gross_loss else None


@dataclass
class SymbolStats:
    symbol: str
    trades: int
    wins: int
    pnl: float


@dataclass
class PaperOrder:
    side: str
    price: float
    size: float
    filled: float
    token_id: str
    label: str
    age_s: float


@dataclass
class PaperHolding:
    token_id: str
    size: float
    avg_price: float
    label: str
    slug: Optional[str] = None
    outcome: Optional[str] = None


@dataclass
class Snapshot:
    open_positions: List[OpenPosition] = field(default_factory=list)
    recent: List[ClosedTrade] = field(default_factory=list)
    periods: List[PeriodStats] = field(default_factory=list)
    by_symbol: List[SymbolStats] = field(default_factory=list)
    equity_curve: List[float] = field(default_factory=list)
    window_minutes: List[int] = field(default_factory=lambda: [DEFAULT_MINUTES])
    paper_cash: Optional[float] = None  # set when the DB belongs to a paper account
    paper_start: Optional[float] = None
    paper_orders: List[PaperOrder] = field(default_factory=list)
    paper_holdings: List[PaperHolding] = field(default_factory=list)
    profile: Optional[str] = None  # requested by the user
    profile_applied: Optional[str] = None  # what the running bot has applied
    note: Optional[str] = None  # non-fatal status, e.g. waiting for the bot to create tables
    error: Optional[str] = None


def _parse_ts(value) -> Optional[datetime]:
    """Parse the ISO timestamps stored in trades.db into aware datetimes."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def _period_stats(conn: sqlite3.Connection, label: str, since: Optional[str]) -> PeriodStats:
    where = "settled = 1" + (" AND settled_at >= ?" if since else "")
    row = conn.execute(
        f"""
        SELECT COUNT(*) n,
               COALESCE(SUM(COALESCE(pnl_usd, 0) > 0), 0) wins,
               COALESCE(SUM(COALESCE(pnl_usd, 0)), 0) pnl,
               COALESCE(SUM(COALESCE(bet_usd, 0)), 0) staked,
               COALESCE(SUM(CASE WHEN pnl_usd > 0 THEN pnl_usd END), 0) gross_win,
               COALESCE(SUM(CASE WHEN pnl_usd < 0 THEN -pnl_usd END), 0) gross_loss
        FROM trades WHERE {where}
        """,
        (since,) if since else (),
    ).fetchone()
    return PeriodStats(
        label=label,
        trades=row["n"],
        wins=row["wins"],
        pnl=row["pnl"],
        staked=row["staked"],
        gross_win=row["gross_win"],
        gross_loss=row["gross_loss"],
    )


def _label(slug: Optional[str], outcome: Optional[str], token_id: str) -> str:
    if slug:
        short = slug.split("-updown-")
        asset = short[0].upper()
        iv = short[1].split("-")[0].upper() if len(short) > 1 else ""
        return f"{asset}{'-' + iv if iv and iv != '15M' else ''} {(outcome or '').upper()}".strip()
    return "…" + token_id[-6:]


def _load_legouts(conn: sqlite3.Connection, snap: Snapshot, limit: int) -> None:
    """Pairs where only one leg filled (sold or held) belong in Recent trades with their real result."""
    try:
        rows = conn.execute(
            """SELECT id, symbol, entry_side, first_leg, final_entry_price, final_hedge_price, size,
                      entry_filled, hedge_filled, sell_price, sell_size, sell_fee, resolution,
                      filled_leg_won, realized_pnl, closed_at
               FROM pair_attempts WHERE outcome = 'timeout' AND first_leg IN ('entry', 'hedge')
               ORDER BY id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    except sqlite3.Error:
        return  # older database without the measurement tables
    for r in rows:
        entry_leg = r["first_leg"] == "entry"
        side = r["entry_side"] if entry_leg else {"UP": "DOWN", "DOWN": "UP"}.get(r["entry_side"], "?")
        paid = (r["final_entry_price"] if entry_leg else r["final_hedge_price"]) or 0.0
        qty = (r["entry_filled"] if entry_leg else r["hedge_filled"]) or r["size"] or 0.0
        held = (r["resolution"] or "").startswith("hold")
        if r["sell_price"] is not None and not held:
            qty = r["sell_size"] or qty
            pnl = (r["sell_price"] - paid) * qty - (r["sell_fee"] or 0.0)
            outcome = "LEG-OUT sold"
        elif r["filled_leg_won"] is not None:
            pnl = r["realized_pnl"] if r["realized_pnl"] is not None else (r["filled_leg_won"] - paid) * qty
            outcome = "LEG-OUT held " + ("✓ won" if r["filled_leg_won"] else "✗ lost")
        else:
            pnl, outcome = None, "LEG-OUT held…" if held else "LEG-OUT"
        cost = paid * qty
        snap.recent.append(
            ClosedTrade(
                id=-r["id"], symbol=r["symbol"] or "?", side=(side or "?").upper(), entry_price=paid,
                exit_price=None if held else r["sell_price"], size=qty, pnl=pnl,
                roi_pct=(pnl / cost * 100.0) if pnl is not None and cost else None,
                outcome=outcome, exited_early=False, settled_at=_parse_ts(r["closed_at"]),
            )
        )


def _add_unpaired(snap: Snapshot) -> None:
    """Show paper holdings that no open trade accounts for (e.g. one leg of a pair that has not
    filled its other side yet): a held leg is a position even without a trade row."""
    covered = set()
    for p in snap.open_positions:
        if p.slug:
            covered.add((p.slug, p.side))
            if p.is_hedged:
                covered.add((p.slug, p.hedge_side))
    for h in snap.paper_holdings:
        side = (h.outcome or "").upper()
        if not h.slug or (h.slug, side) in covered:
            continue
        parsed = parse_slug(h.slug)
        snap.open_positions.append(
            OpenPosition(
                id=0,
                symbol=parsed[0] if parsed else h.label,
                side=side or "?",
                size=h.size,
                entry_price=h.avg_price,
                bet_usd=h.size * h.avg_price,
                token_id=h.token_id,
                window_end=parsed[2] if parsed else None,
                has_exit_order=False,
                scaled_in=False,
                is_reversal=False,
                slug=h.slug,
                unpaired=True,
            )
        )


def _load_paper(conn: sqlite3.Connection, snap: Snapshot) -> None:
    """Paper account (cash, resting orders, holdings) - empty for real databases."""
    try:
        row = conn.execute("SELECT cash, start_cash FROM paper_account WHERE id=1").fetchone()
    except sqlite3.Error:
        return  # not a paper database
    if not row:
        return
    snap.paper_cash, snap.paper_start = row[0], row[1]
    now = datetime.now(timezone.utc)
    try:
        for r in conn.execute(
            "SELECT side, price, size, filled, token_id, slug, outcome, created_at FROM paper_orders WHERE status='LIVE' ORDER BY created_at DESC"
        ):
            created = _parse_ts(r["created_at"])
            snap.paper_orders.append(
                PaperOrder(r["side"], r["price"], r["size"], r["filled"], r["token_id"],
                           _label(r["slug"], r["outcome"], r["token_id"]),
                           (now - created).total_seconds() if created else 0.0)
            )
        for r in conn.execute("SELECT token_id, size, avg_price, slug, outcome FROM paper_positions WHERE size > 0.001"):
            snap.paper_holdings.append(
                PaperHolding(r["token_id"], r["size"], r["avg_price"], _label(r["slug"], r["outcome"], r["token_id"]), r["slug"], r["outcome"])
            )
    except sqlite3.Error:
        pass


def load_snapshot(db_path: Path, recent_limit: int = 12, curve_len: int = 40) -> Snapshot:
    """Read everything the dashboard needs in one short read-only transaction."""
    if not db_path.exists():
        return Snapshot(error=f"Database not found: {db_path}")
    try:
        conn = _connect(db_path)
    except sqlite3.Error as e:
        return Snapshot(error=f"Cannot open database: {e}")

    try:
        snap = Snapshot()
        _load_paper(conn, snap)
        snap.profile = profiles.get(conn, "risk_profile")
        snap.profile_applied = profiles.get(conn, "risk_profile_applied")
        has_trades = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='trades'"
        ).fetchone()
        if not has_trades:
            snap.note = "Waiting for the bot to create the trades table (start it with `uv run polyflup.py`)"
            return snap
        cols = {r[1] for r in conn.execute("PRAGMA table_info(trades)")}
        opt = lambda name, default="NULL": name if name in cols else f"{default} AS {name}"  # older DBs
        for r in conn.execute(
            f"""
            SELECT id, symbol, side, size, entry_price, bet_usd, token_id, window_end,
                   limit_sell_order_id, scaled_in, is_reversal,
                   {opt("is_hedged", "0")}, {opt("hedge_order_price")},
                   {opt("exited_early", "0")}, {opt("hedge_exited_early", "0")},
                   {opt("exit_price")}, {opt("hedge_exit_price")}, {opt("slug")}
            FROM trades WHERE settled = 0 ORDER BY id DESC
            """
        ):
            snap.open_positions.append(
                OpenPosition(
                    id=r["id"],
                    symbol=r["symbol"] or "?",
                    side=(r["side"] or "?").upper(),
                    size=r["size"] or 0.0,
                    entry_price=r["entry_price"] or 0.0,
                    bet_usd=r["bet_usd"] or 0.0,
                    token_id=r["token_id"] or "",
                    window_end=_parse_ts(r["window_end"]),
                    has_exit_order=bool(r["limit_sell_order_id"]),
                    scaled_in=bool(r["scaled_in"]),
                    is_reversal=bool(r["is_reversal"]),
                    is_hedged=bool(r["is_hedged"]),
                    hedge_price=r["hedge_order_price"],
                    entry_sold=bool(r["exited_early"]),
                    hedge_sold=bool(r["hedge_exited_early"]),
                    exit_price=r["exit_price"],
                    hedge_exit_price=r["hedge_exit_price"],
                    slug=r["slug"],
                )
            )
        _add_unpaired(snap)

        for r in conn.execute(
            """
            SELECT id, symbol, side, entry_price, exit_price, size, pnl_usd, roi_pct,
                   final_outcome, exited_early, settled_at
            FROM trades WHERE settled = 1
            ORDER BY COALESCE(settled_at, timestamp) DESC, id DESC LIMIT ?
            """,
            (recent_limit,),
        ):
            snap.recent.append(
                ClosedTrade(
                    id=r["id"],
                    symbol=r["symbol"] or "?",
                    side=(r["side"] or "?").upper(),
                    entry_price=r["entry_price"] or 0.0,
                    exit_price=r["exit_price"],
                    size=r["size"] or 0.0,
                    pnl=r["pnl_usd"] or 0.0,
                    roi_pct=r["roi_pct"],
                    outcome=r["final_outcome"] or "",
                    exited_early=bool(r["exited_early"]),
                    settled_at=_parse_ts(r["settled_at"]),
                )
            )

        _load_legouts(conn, snap, recent_limit)
        snap.recent.sort(key=lambda t: t.settled_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        del snap.recent[recent_limit:]

        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        snap.periods = [
            _period_stats(conn, "Today (UTC)", today.isoformat()),
            _period_stats(conn, "Last 7d", (today - timedelta(days=6)).isoformat()),
            _period_stats(conn, "All time", None),
        ]

        snap.by_symbol = [
            SymbolStats(r["symbol"] or "?", r["n"], r["wins"], r["pnl"])
            for r in conn.execute(
                """
                SELECT symbol, COUNT(*) n,
                       COALESCE(SUM(COALESCE(pnl_usd, 0) > 0), 0) wins,
                       COALESCE(SUM(COALESCE(pnl_usd, 0)), 0) pnl
                FROM trades WHERE settled = 1 GROUP BY symbol ORDER BY pnl DESC
                """
            )
        ]

        pnls = [
            r[0]
            for r in conn.execute(
                """
                SELECT COALESCE(pnl_usd, 0) FROM trades WHERE settled = 1
                ORDER BY COALESCE(settled_at, timestamp) DESC, id DESC LIMIT ?
                """,
                (curve_len,),
            )
        ][::-1]
        total = 0.0
        for p in pnls:
            total += p
            snap.equity_curve.append(total)
        # Window lengths in use (e.g. 15 and 5 once 'BTC-5M' style markets have traded)
        snap.window_minutes = sorted(
            {
                window_minutes(r[0])
                for r in conn.execute(
                    "SELECT DISTINCT symbol FROM (SELECT symbol FROM trades ORDER BY id DESC LIMIT 300)"
                )
                if r[0]
            }
            or {DEFAULT_MINUTES},
            reverse=True,
        )
        return snap
    except sqlite3.Error as e:
        return Snapshot(error=f"Database error: {e}")
    finally:
        conn.close()


def tail_lines(path: Path, count: int, max_bytes: int = 16384) -> List[str]:
    """Last `count` lines of a (possibly huge) log file without reading all of it."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            chunk = f.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    lines = [ln.rstrip("\r") for ln in chunk.splitlines() if ln.strip()]
    return lines[-count:]


def file_age_seconds(path: Path) -> Optional[float]:
    try:
        return max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        return None


def current_window_end(minutes: int = DEFAULT_MINUTES, now: Optional[datetime] = None) -> datetime:
    """End of the current `minutes`-long market window (same ET slotting as the bot)."""
    return window_bounds(f"X-{minutes}M", now)[1]


class LiveFeed:
    """Background poller for network data: midpoints of open positions + wallet balance.

    Network calls never run on the render thread, so a slow API only makes the
    numbers stale, never the UI.
    """

    def __init__(self, mid_interval: float = 2.0, balance_interval: float = 30.0):
        self.mid_interval = mid_interval
        self.balance_interval = balance_interval
        self.mids: Dict[str, float] = {}
        self.balance: Optional[float] = None
        self.wallet: Optional[str] = None
        self.token_ids: List[str] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._session = requests.Session()
        self._balance_fn = None
        self._threads: List[threading.Thread] = []

    def configure_wallet(self) -> None:
        """Best effort: resolve wallet + balance reader from the bot's settings/.env."""
        import contextlib
        import io

        try:
            # settings prints a FATAL line and raises SystemExit without PROXY_PK
            with contextlib.redirect_stdout(io.StringIO()):
                from eth_account import Account
                from src.config.settings import FUNDER_PROXY, PROXY_PK
                from src.utils.web3_utils import get_balance
            self.wallet = (
                FUNDER_PROXY
                if FUNDER_PROXY and FUNDER_PROXY.startswith("0x")
                else Account.from_key(PROXY_PK).address
            )
            self._balance_fn = get_balance
        except (Exception, SystemExit):
            self.wallet = None
            self._balance_fn = None

    def set_tokens(self, token_ids: List[str]) -> None:
        with self._lock:
            self.token_ids = sorted({t for t in token_ids if t})

    def mid(self, token_id: str) -> Optional[float]:
        with self._lock:
            return self.mids.get(token_id)

    def start(self) -> None:
        self._threads = [
            threading.Thread(target=self._poll_mids, daemon=True),
            threading.Thread(target=self._poll_balance, daemon=True),
        ]
        for t in self._threads:
            t.start()

    def stop(self) -> None:
        self._stop.set()

    def _poll_mids(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                tokens = list(self.token_ids)
            for tid in tokens:
                if self._stop.is_set():
                    return
                try:
                    r = self._session.get(
                        f"{CLOB_HOST}/midpoint", params={"token_id": tid}, timeout=5
                    )
                    if r.ok:
                        with self._lock:
                            self.mids[tid] = float(r.json()["mid"])
                except (requests.RequestException, KeyError, ValueError):
                    pass
            self._stop.wait(self.mid_interval)

    def _poll_balance(self) -> None:
        while not self._stop.is_set():
            if self._balance_fn and self.wallet:
                try:
                    bal = self._balance_fn(self.wallet)
                    with self._lock:
                        self.balance = bal
                except Exception:
                    pass
            self._stop.wait(self.balance_interval)
