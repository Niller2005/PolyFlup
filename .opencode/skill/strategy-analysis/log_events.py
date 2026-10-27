"""Tally decision/event lines across PolyFlup logs (numbers, symbols and timestamps normalised).

Usage: uv run python .opencode/skill/strategy-analysis/log_events.py [-p REGEX] [-n 30] [FILES...]
Defaults: all logs/window_*.log + logs/trades_paper.log, pattern = the key decision events.
"""

import argparse
import glob
import re
import sys
from collections import Counter

DEFAULT = (
    r"emergency selling ALL|HOLDING .* despite|Confidence too low|Neither side clearly|"
    r"CRITICAL: .*timed out|Taker hedge not worth|PRE-SETTLEMENT EXIT OPPORTUNITY|"
    r"Trail capped|Maker pair skipped|No entry\+hedge pair|Hedge retry|SYNC_MISSING|"
    r"Errno 11|Both orders filled|TIMEOUT: Both orders"
)


def normalise(line: str) -> str:
    line = re.sub(r"^\[[^\]]*\]\s*", "", line)
    line = re.sub(r"\[[A-Z]+(-5M)?\]", "[SYM]", line)
    line = re.sub(r"#\d+", "#N", line)
    return re.sub(r"\d+(\.\d+)?", "N", line).strip()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("-p", "--pattern", default=DEFAULT)
    ap.add_argument("-n", type=int, default=30)
    ap.add_argument("files", nargs="*")
    a = ap.parse_args()
    files = a.files or sorted(glob.glob("logs/window_*.log")) + glob.glob("logs/trades_paper.log")
    rx, counts = re.compile(a.pattern), Counter()
    for f in files:
        with open(f, errors="replace") as fh:
            for line in fh:
                if rx.search(line):
                    counts[normalise(line)] += 1
    for text, n in counts.most_common(a.n):
        print(f"{n:6d}  {text[:150]}")
    print(f"-- {sum(counts.values())} matching lines in {len(files)} files", file=sys.stderr)


if __name__ == "__main__":
    main()
