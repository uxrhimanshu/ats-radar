"""EU-wide market census.

Counts every open role across a wide employer list, bucketed by country, role track and
seniority. Separate from main.py: that one alerts you about a curated target list; this one
measures the market those targets sit in. Appends a dated snapshot to
state/market_history.jsonl so a twelve-month question eventually gets a trend line rather
than one day's photograph.

The two answer different questions, and the difference is the point: a census can show that
the employers actually hiring for a role are not the ones on your list.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import fetchers
from filters import Profile, ProfileError
from regions import Regions

ROOT = Path(__file__).parent
HISTORY = ROOT / "state" / "market_history.jsonl"


def fetch(source: dict) -> tuple[dict, list[dict], str | None]:
    try:
        rows = getattr(fetchers, source["adapter"])(**source.get("params", {}))
        for row in rows:
            row["company"] = source["name"]
        return source, rows, None
    except Exception as exc:
        return source, [], f"{type(exc).__name__}: {exc}"


def collect(sources: list[dict], profile, regions, workers: int = 12):
    matched, failures, total = [], {}, 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(fetch, s) for s in sources]
        for future in as_completed(futures):
            source, rows, error = future.result()
            if error:
                failures[source["id"]] = error
                continue
            total += len(rows)
            for row in rows:
                category = profile.category(row["title"])
                if not category:
                    continue
                country = regions.country_of(row["location"])
                if not country:
                    continue
                matched.append({
                    "company": source["name"], "source_id": source["id"],
                    "title": row["title"], "location": row["location"], "url": row["url"],
                    "category": category, "country": country,
                    "seniority": profile.seniority(row["title"]),
                })
    return matched, failures, total


def main() -> int:
    parser = argparse.ArgumentParser(description="Count matching roles across a wide employer list, bucketed by region.")
    parser.add_argument("--dry-run", action="store_true", help="print only; append nothing to history")
    parser.add_argument("--out", metavar="PATH", help="also write the matched rows as JSON")
    parser.add_argument("--sources", default="census_sources.json")
    parser.add_argument("--profile", default="profiles/uxr-germany.json", metavar="PATH",
                        help="search profile (default profiles/uxr-germany.json)")
    parser.add_argument("--regions", default="profiles/regions-eu.json", metavar="PATH",
                        help="region map (default profiles/regions-eu.json)")
    args = parser.parse_args()

    try:
        profile = Profile.load(str(ROOT / args.profile))
        regions = Regions.load(str(ROOT / args.regions))
    except ProfileError as exc:
        print(exc, file=sys.stderr)
        return 2

    sources_path = ROOT / args.sources
    if not sources_path.exists():
        print("no %s — copy census_sources.example.json and edit it."
              % sources_path.name, file=sys.stderr)
        return 2

    sources = json.load(open(sources_path))["sources"]
    matched, failures, total = collect(sources, profile, regions)

    by_country = Counter(m["country"] for m in matched)
    by_category = Counter(m["category"] for m in matched)
    print(f"employers polled : {len(sources)}  ({len(failures)} failed)")
    print(f"postings scanned : {total}")
    print(f"EU matches       : {len(matched)}\n")
    print("by track:")
    for key, count in by_category.most_common():
        senior = sum(1 for m in matched if m["category"] == key and m["seniority"] == "Senior+")
        print(f"  {key:16} {count:5}   Senior+ {senior:4} ({senior / count * 100:.0f}%)")
    print("\nby country (top 12):")
    for key, count in by_country.most_common(12):
        print(f"  {key:10} {count:5}")
    if failures:
        print(f"\nfailed sources: {', '.join(sorted(failures))}")

    if args.out:
        Path(args.out).write_text(json.dumps(matched, indent=1))
        print(f"\nwrote {args.out}")

    if args.dry_run:
        print("\n[dry run] history not written")
        return 0

    today = datetime.now(timezone.utc).date().isoformat()
    cells = Counter((m["country"], m["category"], m["seniority"]) for m in matched)
    with HISTORY.open("a") as handle:
        for (country, category, seniority), count in sorted(cells.items()):
            handle.write(json.dumps({"date": today, "country": country, "category": category,
                                     "seniority": seniority, "count": count}) + "\n")
    print(f"\nappended {len(cells)} rows to {HISTORY.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
