"""Daily Job Radar runner.  It intentionally tolerates an unavailable ATS."""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

import fetchers
from digest import build_message
from filters import Profile, ProfileError

ROOT = Path(__file__).parent
STATE = ROOT / "state"
LOG = logging.getLogger("job_radar")


def _load(path: Path, default: Any) -> Any:
    try:
        with path.open() as handle:
            return json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _save(path: Path, value: Any) -> None:
    STATE.mkdir(exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temp.replace(path)


def fetch_source(source: dict[str, Any]) -> list[dict[str, Any]]:
    adapter = source["adapter"]
    if adapter == "manual":
        return []
    try:
        function = getattr(fetchers, adapter)
    except AttributeError as exc:
        raise fetchers.FetchError(f"unknown adapter {adapter!r}") from exc
    rows = function(**source.get("params", {}))
    # Board APIs know their slug, not necessarily the human company display name.
    for row in rows:
        row["company"] = source["name"]
    return rows


def _key(source_id: str, job: dict[str, Any]) -> str:
    return hashlib.sha1(f"{source_id}:{job['job_id']}".encode()).hexdigest()


def _telegram(messages: list[str]) -> None:
    token, chat_id = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set")
    for message in messages:
        response = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=20,
            json={"chat_id": chat_id, "text": message, "parse_mode": "HTML",
                  "disable_web_page_preview": True})
        if not response.ok:
            raise RuntimeError(f"Telegram send failed: {response.status_code} {response.text[:200]}")


def _status(source: dict[str, Any], successes: dict[str, list[dict[str, Any]]],
            failures: dict[str, str]) -> str:
    """Make an empty response visible without conflating it with a fetch error."""
    ident = source["id"]
    if source.get("adapter") == "manual":
        return "manual"
    if ident in failures:
        return f"hard failure: {failures[ident]}"
    if not successes.get(ident):
        return "soft failure: returned 0 jobs"
    return "ok"


def _print_audit(sources: list[dict[str, Any]], successes: dict[str, list[dict[str, Any]]],
                 failures: dict[str, str], profile: Profile) -> None:
    print("id\tadapter\ttotal_fetched\tin_scope_location\tmatching_title\tstatus")
    for source in sources:
        rows = successes.get(source["id"], [])
        local = [job for job in rows if profile.location_matches(job["location"], source.get("extra_location_ok"))]
        matches = [job for job in local if profile.matches(job["title"])]
        print(f"{source['id']}\t{source['adapter']}\t{len(rows)}\t{len(local)}\t{len(matches)}\t"
              f"{_status(source, successes, failures)}")


def run(dry_run: bool, only: set[str], force_sunday: bool, audit: bool = False,
        profile_path: str = "profiles/uxr-germany.json") -> int:
    profile = Profile.load(str(ROOT / profile_path) if not os.path.isabs(profile_path) else profile_path)

    sources_path = ROOT / "sources.json"
    if not sources_path.exists():
        raise SystemExit(
            "no sources.json in %s.\n"
            "Copy sources.example.json to sources.json and edit it — it holds the "
            "employers you want polled, which is deliberately not committed." % ROOT)
    document = _load(sources_path, {})
    sources = document.get("sources", [])
    if only:
        unknown = only - {s["id"] for s in sources}
        if unknown:
            raise SystemExit("unknown source id(s): " + ", ".join(sorted(unknown)))
        sources = [s for s in sources if s["id"] in only]
    manual_sources = [s["name"] for s in sources if s.get("adapter") == "manual"]
    active = [s for s in sources if s.get("adapter") != "manual"]
    successes: dict[str, list[dict[str, Any]]] = {}
    failures: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        tasks = {pool.submit(fetch_source, s): s for s in active}
        for task in as_completed(tasks):
            source = tasks[task]
            try:
                successes[source["id"]] = task.result()
            except Exception as exc:  # an adapter must never take down the daily run
                failures[source["id"]] = str(exc)
                LOG.warning("%s failed: %s", source["id"], exc)

    all_open: list[dict[str, Any]] = []
    for source in sources:
        for job in successes.get(source["id"], []):
            if profile.matches(job["title"]) and profile.location_matches(
                    job["location"], source.get("extra_location_ok")):
                job = {**job, "source_id": source["id"]}
                all_open.append(job)
    # Best-fitting categories lead the digest; a reader scans top-down and stops.
    all_open.sort(key=lambda job: (profile.rank(profile.category(job["title"])),
                                   job["company"].casefold(), job["title"].casefold()))

    if audit:
        _print_audit(sources, successes, failures, profile)
        return 0

    seen = _load(STATE / "seen.json", {})
    health = _load(STATE / "health.json", {})
    today = datetime.now(timezone.utc).date().isoformat()
    new_jobs = [job for job in all_open if _key(job["source_id"], job) not in seen]
    for source in sources:
        ident = source["id"]
        current = health.get(ident, {})
        if ident in failures:
            health[ident] = {"consecutive_failures": int(current.get("consecutive_failures", 0)) + 1,
                             "last_error": failures[ident], "last_status": "hard fetch failure",
                             "last_checked": today}
        elif source.get("adapter") != "manual" and not successes.get(ident):
            health[ident] = {"consecutive_failures": int(current.get("consecutive_failures", 0)) + 1,
                             "last_error": "returned 0 jobs", "last_status": "soft failure: returned 0 jobs",
                             "last_checked": today}
        else:
            health[ident] = {"consecutive_failures": 0, "last_error": None, "last_status": "ok",
                             "last_checked": today}
    warning = [f"{s['name']} {health[s['id']]['last_status']} "
               f"{health[s['id']]['consecutive_failures']} days" for s in sources
               if health[s['id']]["consecutive_failures"] >= 2]
    is_sunday = force_sunday or date.today().weekday() == 6
    rendered = build_message(new_jobs, len(sources), warning, manual_sources, is_sunday, all_open, profile)
    messages = rendered if isinstance(rendered, list) else [rendered]
    if dry_run:
        print("\n\n--- next Telegram message ---\n\n".join(messages))
        return 0

    # Health is diagnostic state and may safely record a bad Telegram day.
    _save(STATE / "health.json", health)
    try:
        _telegram(messages)
    except Exception as exc:
        # Deliberately do NOT save seen.json: a failed notification must be retried tomorrow.
        LOG.error("%s", exc)
        return 1

    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).date().isoformat()
    for job in all_open:
        key = _key(job["source_id"], job)
        previous = seen.get(key, {})
        seen[key] = {"first_seen": previous.get("first_seen", today), "last_seen": today,
                     "title": job["title"], "url": job["url"], "company": job["company"]}
    seen = {key: value for key, value in seen.items() if value.get("last_seen", "") >= cutoff}
    _save(STATE / "seen.json", seen)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Poll ATS feeds for matching roles and notify Telegram.")
    parser.add_argument("--dry-run", action="store_true", help="print digest; do not send or write state")
    parser.add_argument("--only", action="append", default=[], metavar="ID", help="poll one source (repeatable)")
    parser.add_argument("--force-sunday", action="store_true", help="include the weekly open-role recap")
    parser.add_argument("--audit", action="store_true", help="print per-source fetch and filter diagnostics; do not write state")
    parser.add_argument("--profile", default="profiles/uxr-germany.json", metavar="PATH",
                        help="search profile to use (default profiles/uxr-germany.json)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        return run(args.dry_run, set(args.only), args.force_sunday, args.audit, args.profile)
    except ProfileError as exc:
        print(exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
