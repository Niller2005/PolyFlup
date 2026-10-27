"""Rich-based live terminal dashboard for PolyFlup (read-only)."""

import re
import select
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional

from rich import box
from rich.align import Align
from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from src.tui.data import (
    ClosedTrade,
    LiveFeed,
    OpenPosition,
    Snapshot,
    ET,
    current_window_end,
    file_age_seconds,
    load_snapshot,
    tail_lines,
)

GREEN, RED, YELLOW, CYAN, DIM = "green3", "red1", "yellow1", "cyan", "grey50"
SPARK = "▁▂▃▄▅▆▇█"
_LOG_TS = re.compile(r"^\[(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}) UTC\]\s?")


def money(value: Optional[float], signed: bool = True) -> Text:
    if value is None:
        return Text("—", style=DIM)
    style = GREEN if value > 0 else RED if value < 0 else DIM
    return Text(f"{value:+,.2f}" if signed else f"{value:,.2f}", style=style)


def pct(value: Optional[float], digits: int = 1) -> Text:
    return Text("—", style=DIM) if value is None else Text(f"{value * 100:.{digits}f}%")


def side_text(side: str) -> Text:
    if side in ("UP", "YES"):
        return Text("▲ UP", style=f"bold {GREEN}")
    if side in ("DOWN", "NO"):
        return Text("▼ DOWN", style=f"bold {RED}")
    return Text(side or "?")


def fmt_left(seconds: Optional[float]) -> Text:
    if seconds is None:
        return Text("—", style=DIM)
    if seconds <= 0:
        return Text("settling", style=YELLOW)
    m, s = divmod(int(seconds), 60)
    style = RED if seconds < 60 else YELLOW if seconds < 180 else ""
    return Text(f"{m:02d}:{s:02d}", style=style)


def sparkline(values: List[float], width: int) -> Text:
    if not values:
        return Text("no settled trades yet", style=DIM)
    values = values[-width:]
    lo, hi = min(values), max(values)
    span = hi - lo
    chars = "".join(
        SPARK[int((v - lo) / span * (len(SPARK) - 1))] if span else SPARK[3]
        for v in values
    )
    return Text(chars, style=GREEN if values[-1] >= 0 else RED)


class Dashboard:
    def __init__(self, db_path: Path, log_path: Path, refresh: float, feed: LiveFeed):
        self.db_path = db_path
        self.log_path = log_path
        self.refresh = refresh
        self.feed = feed
        self.snap = Snapshot()
        self.paused = False
        self.loaded_at: Optional[datetime] = None
        self.compact = False

    # ---- data -------------------------------------------------------------
    def reload(self) -> None:
        self.snap = load_snapshot(self.db_path)
        self.loaded_at = datetime.now(timezone.utc)
        self.feed.set_tokens(
            [p.token_id for p in self.snap.open_positions]
            + [h.token_id for h in self.snap.paper_holdings]
            + [o.token_id for o in self.snap.paper_orders]
        )

    # ---- panels -----------------------------------------------------------
    def header(self) -> Panel:
        age = file_age_seconds(self.log_path)
        if age is None:
            status = Text("● NO LOG", style=f"bold {DIM}")
        elif age < 60:
            status = Text("● RUNNING", style=f"bold {GREEN}")
        elif age < 300:
            status = Text(f"● QUIET {int(age)}s", style=f"bold {YELLOW}")
        else:
            status = Text(f"● STALE {int(age // 60)}m", style=f"bold {RED}")

        now_et = datetime.now(tz=ET)
        windows = Text()
        for i, minutes in enumerate(self.snap.window_minutes):
            if i:
                windows.append("  ")
            windows.append(f"{minutes}m ", style=DIM)
            windows.append_text(fmt_left((current_window_end(minutes, now_et) - now_et).total_seconds()))
        bal = self.feed.balance
        paper = self.snap.paper_cash is not None
        if paper:
            bal = self.snap.paper_cash
        grid = Table.grid(expand=True)
        grid.add_column(justify="left", ratio=2)
        grid.add_column(justify="center", ratio=3)
        grid.add_column(justify="right", ratio=2)
        grid.add_row(
            Text.assemble(
                ("PolyFlup ", f"bold {CYAN}"),
                ("dashboard", DIM),
                *((("  PAPER ", "black on yellow1"),) if paper else ()),
            ),
            Text.assemble(
                ("window ends in ", DIM),
                windows,
                ("   paper cash " if paper else "   balance ", DIM),
                (f"{bal:,.2f} pUSD" if bal is not None else "—", "bold"),
            ),
            Text.assemble(status, ("  ", ""), (datetime.now(timezone.utc).strftime("%H:%M:%S UTC"), DIM)),
        )
        return Panel(grid, box=box.ROUNDED, border_style=CYAN, padding=(0, 1))

    def positions(self) -> Panel:
        positions: List[OpenPosition] = self.snap.open_positions
        table = Table(box=box.SIMPLE_HEAD, expand=True, header_style=f"bold {DIM}", pad_edge=False)
        columns = [
            ("#", "right"), ("Sym", "left"), ("Side", "left"), ("Size", "right"),
            ("Entry", "right"), ("Mid", "right"), ("PnL $", "right"), ("PnL %", "right"),
            ("Exit", "center"), ("Left", "right"),
        ]
        hidden = {"PnL %", "Exit"} if self.compact else set()
        keep = [name not in hidden for name, _ in columns]
        for (name, justify), shown in zip(columns, keep):
            if shown:
                table.add_column(name, justify=justify, no_wrap=True)

        now = datetime.now(timezone.utc)
        total_pnl, exposure, priced = 0.0, 0.0, 0
        for p in positions:
            mid = self.feed.mid(p.token_id)
            pnl = pnl_pct = None
            if mid is not None:
                pnl = (mid - p.entry_price) * p.size
                pnl_pct = (mid / p.entry_price - 1) if p.entry_price else None
                total_pnl += pnl
                priced += 1
            exposure += p.bet_usd
            left = (p.window_end - now).total_seconds() if p.window_end else None
            tags = ("⇄" if p.is_reversal else "") + ("+" if p.scaled_in else "")
            cells = [
                Text(str(p.id), style=DIM),
                Text(p.symbol, style="bold"),
                side_text(p.side),
                f"{p.size:,.2f}",
                f"{p.entry_price:.3f}",
                f"{mid:.3f}" if mid is not None else Text("…", style=DIM),
                money(pnl),
                Text("—", style=DIM) if pnl_pct is None else Text(f"{pnl_pct * 100:+.1f}%", style=GREEN if pnl_pct > 0 else RED if pnl_pct < 0 else DIM),
                Text("✓", style=GREEN) if p.has_exit_order else Text("·", style=DIM),
                Text.assemble(fmt_left(left), (f" {tags}" if tags else "", DIM)),
            ]
            table.add_row(*[c for c, shown in zip(cells, keep) if shown])
        empty = self.snap.note or (
            "No open trades yet. Paper orders resting on the book and legs not yet paired are listed in the panel on the right."
            if self.snap.paper_cash is not None
            else "No open positions"
        )
        body = table if positions else Align.center(Text("\n" + empty, style=DIM))
        subtitle = Text.assemble(
            (f"exposure ${exposure:,.2f}", DIM), ("  unrealized ", DIM), money(total_pnl if priced else None)
        )
        return Panel(body, title=f"[bold]Open positions[/] ({len(positions)})", subtitle=subtitle,
                     box=box.ROUNDED, border_style=GREEN if positions else DIM)

    def stats(self) -> Panel:
        table = Table(box=box.SIMPLE_HEAD, expand=True, header_style=f"bold {DIM}", pad_edge=False)
        table.add_column("", no_wrap=True)
        for name in ("Trades", "Win", "PnL $") + (() if self.compact else ("ROI",)) + ("PF",):
            table.add_column(name, justify="right", no_wrap=True)
        for s in self.snap.periods:
            pf = s.profit_factor
            cells = [
                Text(s.label, style="bold"),
                str(s.trades),
                pct(s.win_rate, 0),
                money(s.pnl),
            ]
            if not self.compact:
                cells.append(pct(s.roi))
            cells.append(
                Text("—", style=DIM) if pf is None else Text(f"{pf:.2f}", style=GREEN if pf >= 1 else RED)
            )
            table.add_row(*cells)

        syms = Table(box=box.SIMPLE_HEAD, expand=True, header_style=f"bold {DIM}", pad_edge=False)
        syms.add_column("Symbol", no_wrap=True)
        for name in ("Trades", "Win", "PnL $"):
            syms.add_column(name, justify="right", no_wrap=True)
        for s in self.snap.by_symbol:
            syms.add_row(Text(s.symbol, style="bold"), str(s.trades),
                         pct(s.wins / s.trades if s.trades else None, 0), money(s.pnl))

        curve = Text.assemble(("equity  ", DIM), sparkline(self.snap.equity_curve, 36))
        if self.snap.equity_curve:
            curve.append(f"  {self.snap.equity_curve[-1]:+,.2f}", style=GREEN if self.snap.equity_curve[-1] >= 0 else RED)
        return Panel(Group(table, syms, curve), title="[bold]Performance[/]", box=box.ROUNDED, border_style=CYAN)

    def paper_panel(self) -> Panel:
        orders = Table(box=box.SIMPLE_HEAD, expand=True, header_style=f"bold {DIM}", pad_edge=False)
        for name, justify in (("Order", "left"), ("Side", "left"), ("Price", "right"), ("Mkt", "right"), ("Size", "right"), ("Age", "right")):
            orders.add_column(name, justify=justify, no_wrap=True)
        for o in self.snap.paper_orders:
            mid = self.feed.mid(o.token_id)
            orders.add_row(
                Text(o.label, style="bold"),
                Text(o.side, style=GREEN if o.side == "BUY" else RED),
                f"{o.price:.2f}",
                f"{mid:.3f}" if mid is not None else Text("…", style=DIM),
                f"{o.size - o.filled:,.2f}",
                fmt_left(o.age_s).plain if o.age_s < 3600 else ">1h",
            )
        hold = Table(box=box.SIMPLE_HEAD, expand=True, header_style=f"bold {DIM}", pad_edge=False)
        for name, justify in (("Holding", "left"), ("Size", "right"), ("Avg", "right"), ("Mkt", "right"), ("PnL $", "right")):
            hold.add_column(name, justify=justify, no_wrap=True)
        for h in self.snap.paper_holdings:
            mid = self.feed.mid(h.token_id)
            hold.add_row(
                Text(h.label, style="bold"), f"{h.size:,.2f}", f"{h.avg_price:.3f}",
                f"{mid:.3f}" if mid is not None else Text("…", style=DIM),
                money((mid - h.avg_price) * h.size if mid is not None else None),
            )
        parts = []
        parts.append(orders if self.snap.paper_orders else Text("no resting orders", style=DIM))
        parts.append(hold if self.snap.paper_holdings else Text("no holdings", style=DIM))
        start = self.snap.paper_start or 0.0
        cash = self.snap.paper_cash or 0.0
        parts.append(Text.assemble(("cash ", DIM), f"{cash:,.2f}", ("  start ", DIM), f"{start:,.2f}"))
        return Panel(Group(*parts), title=f"[bold]Paper account[/] ({len(self.snap.paper_orders)} orders)", box=box.ROUNDED, border_style="yellow1")

    def recent(self) -> Panel:
        table = Table(box=box.SIMPLE_HEAD, expand=True, header_style=f"bold {DIM}", pad_edge=False)
        for name, justify in (
            ("When", "left"), ("#", "right"), ("Sym", "left"), ("Side", "left"),
            ("Entry", "right"), ("Exit", "right"), ("PnL $", "right"), ("ROI", "right"), ("Result", "left"),
        ):
            table.add_column(name, justify=justify, no_wrap=True)
        trade: ClosedTrade
        for trade in self.snap.recent:
            when = trade.settled_at.strftime("%m-%d %H:%M") if trade.settled_at else "—"
            result = trade.outcome or ("EARLY" if trade.exited_early else "")
            table.add_row(
                Text(when, style=DIM),
                Text(str(trade.id), style=DIM),
                Text(trade.symbol, style="bold"),
                side_text(trade.side),
                f"{trade.entry_price:.3f}",
                f"{trade.exit_price:.3f}" if trade.exit_price is not None else Text("—", style=DIM),
                money(trade.pnl),
                Text("—", style=DIM) if trade.roi_pct is None else Text(f"{trade.roi_pct:+.1f}%", style=GREEN if trade.roi_pct > 0 else RED if trade.roi_pct < 0 else DIM),
                Text(("⚡ " if trade.exited_early else "") + result, style=DIM),
            )
        body = table if self.snap.recent else Align.center(Text("\nNo settled trades yet", style=DIM))
        return Panel(body, title="[bold]Recent trades[/]", box=box.ROUNDED, border_style=DIM)

    def log_panel(self, height: int) -> Panel:
        lines = tail_lines(self.log_path, max(1, height))
        out = Text(no_wrap=True, overflow="ellipsis")
        if not lines:
            out.append("no log output", style=DIM)
        for i, raw in enumerate(lines):
            m = _LOG_TS.match(raw)
            stamp, msg = (m.group(2), raw[m.end():]) if m else ("", raw)
            style = (
                RED if ("❌" in msg or "ERROR" in msg or "FATAL" in msg)
                else YELLOW if "⚠" in msg
                else GREEN if "✅" in msg
                else ""
            )
            if stamp:
                out.append(stamp + " ", style=DIM)
            out.append(msg.strip(), style=style)
            if i < len(lines) - 1:
                out.append("\n")
        return Panel(out, title="[bold]Log[/]", box=box.ROUNDED, border_style=DIM)

    def footer(self) -> Text:
        loaded = self.loaded_at.strftime("%H:%M:%S") if self.loaded_at else "—"
        state = Text(" PAUSED ", style="black on yellow1") if self.paused else Text("")
        return Text.assemble(
            (" q", f"bold {CYAN}"), (" quit  ", DIM), ("r", f"bold {CYAN}"), (" refresh  ", DIM),
            ("p", f"bold {CYAN}"), (" pause  ", DIM), (f"│ db {self.db_path.name} @ {loaded}  │ every {self.refresh:g}s ", DIM), state,
        )

    # ---- layout -----------------------------------------------------------
    def render(self, height: int, width: int = 160) -> Layout:
        self.compact = width < 130  # drop low-value columns instead of truncating
        root = Layout()
        if self.snap.error:
            root.split_column(
                Layout(self.header(), size=3),
                Layout(Panel(Text(self.snap.error, style=RED), title="Error", border_style=RED)),
                Layout(self.footer(), size=1),
            )
            return root
        root.split_column(
            Layout(self.header(), size=3, name="header"),
            Layout(name="top", ratio=5),
            Layout(name="bottom", ratio=4),
            Layout(self.footer(), size=1, name="footer"),
        )
        right = Layout(ratio=2)
        if self.snap.paper_cash is not None:
            right.split_column(Layout(self.stats(), ratio=3), Layout(self.paper_panel(), ratio=3))
        else:
            right.update(self.stats())
        root["top"].split_row(Layout(self.positions(), ratio=3), right)
        bottom_h = max(3, (height - 4) * 4 // 9 - 2)
        root["bottom"].split_row(Layout(self.recent(), ratio=3), Layout(self.log_panel(bottom_h), ratio=2))
        return root


def _keys(on_key: Callable[[str], None], stop: threading.Event) -> None:
    """Single-keypress reader (POSIX). Silently disabled when stdin is not a TTY."""
    if not sys.stdin.isatty():
        return
    try:
        import termios
        import tty
    except ImportError:  # Windows: Ctrl-C still quits
        return
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        while not stop.is_set():
            if select.select([sys.stdin], [], [], 0.2)[0]:
                on_key(sys.stdin.read(1).lower())
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def run(db_path: Path, log_path: Path, refresh: float = 1.0, once: bool = False, wallet: bool = True) -> int:
    console = Console()
    feed = LiveFeed()
    if wallet:
        feed.configure_wallet()
    dash = Dashboard(db_path, log_path, refresh, feed)
    dash.reload()

    if once:
        feed.start()
        time.sleep(3.0 if (dash.snap.open_positions or dash.snap.paper_orders or dash.snap.paper_holdings) else 0.0)  # let first midpoints land
        console.print(dash.render(console.height or 40, console.width))
        feed.stop()
        return 1 if dash.snap.error else 0

    stop = threading.Event()
    force = threading.Event()

    def on_key(k: str) -> None:
        if k == "q":
            stop.set()
        elif k == "r":
            force.set()
        elif k == "p":
            dash.paused = not dash.paused

    feed.start()
    threading.Thread(target=_keys, args=(on_key, stop), daemon=True).start()
    last_load = time.monotonic()
    try:
        with Live(console=console, screen=True, auto_refresh=False) as live:
            while not stop.is_set():
                if not dash.paused and (force.is_set() or time.monotonic() - last_load >= dash.refresh):
                    dash.reload()
                    last_load = time.monotonic()
                    force.clear()
                live.update(dash.render(console.size.height, console.size.width), refresh=True)
                stop.wait(0.25)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        feed.stop()
    return 0
