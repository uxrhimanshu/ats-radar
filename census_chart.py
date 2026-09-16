#!/usr/bin/env python3
"""Draw the market census that market_census.py has been collecting.

    python3 census_chart.py                     # → census.svg
    python3 census_chart.py --history state/market_history.jsonl --out census.svg

The census answers a question the daily digest cannot: not "did anything new
appear at the employers I chose", but "was that list ever the right list".
Until now it answered it into a JSONL file nobody looks at.

Two charts, because the census makes two different claims:

  1. roles per day, by category — a run of zeroes is a base rate, and a base
     rate is only legible as a shape over time.
  2. where the roles actually are, by region — which is the finding: the
     employers hiring may be nowhere near the ones a target list names.

Standard library only, like everything else here. Writes one SVG.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_HISTORY = ROOT / "state" / "market_history.jsonl"
EXAMPLE_HISTORY = ROOT / "state" / "market_history.example.jsonl"

INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
ACCENT = "#2a78d6"
ACCENT_LIGHT = "#9ec5f4"
PAPER = "#fcfcfb"
GRID = "#e1e0d9"


def esc(text) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def load(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def chart(rows: list[dict], illustrative: bool = False) -> str:
    if not rows:
        return ""

    by_day = defaultdict(int)
    by_cat = Counter()
    by_country = Counter()
    for r in rows:
        by_day[r.get("date", "?")] += int(r.get("count", 0))
        by_cat[r.get("category", "?")] += int(r.get("count", 0))
        by_country[r.get("country", "?")] += int(r.get("count", 0))

    days = sorted(by_day)
    width, pad_l, pad_r = 760, 44, 24
    top_h, gap = 150, 54
    plot_w = width - pad_l - pad_r
    peak = max(by_day.values()) or 1

    rows_n = min(len(by_country), 6)
    height = 74 + top_h + gap + rows_n * 26 + 78

    out = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d" '
           'font-family="system-ui,-apple-system,Segoe UI,sans-serif">' % (width, height, width, height),
           '<rect width="%d" height="%d" fill="%s"/>' % (width, height, PAPER),
           '<text x="%d" y="28" font-size="13" font-weight="650" fill="%s">'
           'What the market actually posted</text>' % (pad_l, INK),
           '<text x="%d" y="46" font-size="11" fill="%s">%d days · %s to %s · %s roles counted</text>'
           % (pad_l, MUTED, len(days), esc(days[0]), esc(days[-1]), format(sum(by_day.values()), ","))]

    # --- roles per day ----------------------------------------------------
    base = 74 + top_h
    out.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="%s"/>'
               % (pad_l, base, width - pad_r, base, GRID))
    bar_w = max(2.0, plot_w / max(len(days), 1) - 2)
    zero_run = 0
    longest_zero = 0
    for i, day in enumerate(days):
        v = by_day[day]
        zero_run = zero_run + 1 if v == 0 else 0
        longest_zero = max(longest_zero, zero_run)
        h = (v / peak) * top_h
        x = pad_l + i * (plot_w / max(len(days), 1))
        if v == 0:
            # A zero is the finding, so it gets a mark rather than blank paper.
            out.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="%s" stroke-width="1.5"/>'
                       % (x + bar_w / 2, base, x + bar_w / 2, base - 4, MUTED))
        else:
            out.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="%s"/>'
                       % (x, base - h, bar_w, h, ACCENT))
    out.append('<text x="%d" y="%d" font-size="11" fill="%s">%d</text>'
               % (pad_l - 8, 74 + 10, INK_2, peak))
    out.append('<text x="%d" y="%d" font-size="11" fill="%s">0</text>' % (pad_l - 8, base + 4, INK_2))
    out.append('<text x="%d" y="%d" font-size="11" fill="%s">roles matching the profile, per run</text>'
               % (pad_l, base + 20, INK_2))
    if longest_zero > 1:
        out.append('<text x="%d" y="%d" font-size="11" fill="%s">'
                   'longest run of zero: %d consecutive days — a base rate, not a broken feed</text>'
                   % (pad_l, base + 36, MUTED, longest_zero))

    # --- where they were --------------------------------------------------
    y = base + gap + 18
    out.append('<text x="%d" y="%d" font-size="11" font-weight="650" fill="%s">'
               'Where the roles were</text>' % (pad_l, y, INK_2))
    widest = max(by_country.values()) or 1
    for j, (country, n) in enumerate(by_country.most_common(rows_n)):
        ry = y + 14 + j * 26
        bw = (n / widest) * (plot_w - 150)
        out.append('<text x="%d" y="%d" font-size="11" fill="%s">%s</text>'
                   % (pad_l, ry + 12, INK_2, esc(country)[:14]))
        out.append('<rect x="%d" y="%d" width="%.1f" height="14" fill="%s"/>'
                   % (pad_l + 88, ry + 1, max(bw, 1), ACCENT_LIGHT if j else ACCENT))
        out.append('<text x="%.1f" y="%d" font-size="11" font-weight="650" fill="%s">%d</text>'
                   % (pad_l + 88 + max(bw, 1) + 7, ry + 12, INK, n))

    out.append('<text x="%d" y="%d" font-size="10" fill="%s">'
               'ats-radar census · categories: %s</text>'
               % (pad_l, height - 18, MUTED,
                  esc(", ".join(c for c, _ in by_cat.most_common(5)))))
    if illustrative:
        # A chart of invented numbers that does not say so is the worst thing
        # in this repository, so it says so on its face and not only in a README.
        out.append('<text x="%d" y="%d" font-size="10" font-weight="650" fill="#b4541f">'
                   'ILLUSTRATIVE DATA — shipped example, not an observed market</text>'
                   % (pad_l, height - 4))
    out.append('</svg>')
    return "\n".join(out) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Draw the collected market census.")
    parser.add_argument("--history", metavar="PATH", help="census history JSONL")
    parser.add_argument("--out", default="census.svg", metavar="PATH")
    args = parser.parse_args()

    path = Path(args.history) if args.history else DEFAULT_HISTORY
    if not path.exists():
        if args.history:
            print("no history at %s" % path, file=sys.stderr)
            return 2
        # state/ is gitignored, so a fresh clone has no history of its own.
        # Falling back to the example means the chart is inspectable before
        # anyone has run a census — and says which one it drew.
        path = EXAMPLE_HISTORY
        if not path.exists():
            print("no census history yet — run market_census.py first", file=sys.stderr)
            return 2
        print("no history yet; drawing the shipped example instead", file=sys.stderr)

    rows = load(path)
    illustrative = path.name == EXAMPLE_HISTORY.name
    svg = chart(rows, illustrative=illustrative)
    if not svg:
        print("history at %s is empty" % path, file=sys.stderr)
        return 2

    Path(args.out).write_text(svg, encoding="utf-8")
    print("%d rows from %s → %s" % (len(rows), path.name, args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
