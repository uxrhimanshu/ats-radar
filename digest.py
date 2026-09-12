"""Telegram HTML digest rendering and safe 4096-character chunking.

Category labels and seniority come from the active search profile, so a digest
describes roles in the profile's own vocabulary rather than a hardcoded one.
"""
from __future__ import annotations
from datetime import datetime
from html import escape
from filters import Profile

LIMIT = 4096

def _entry(job: dict, profile: Profile) -> str:
    title = str(job["title"])
    category_id = profile.category(title)
    category = profile.label(category_id) if category_id else ""
    tags = " · ".join(t for t in (category, profile.seniority(title)) if t)
    return (f"<b>{escape(str(job['company']))}</b> — {escape(str(job['location']))} · "
            f"[{escape(tags)}]\n<a href=\"{escape(str(job['url']), quote=True)}\">"
            f"{escape(title)}</a>")

def _chunks(header: str, entries: list[str]) -> list[str]:
    messages, current = [], header
    for entry in entries:
        proposed = current + "\n\n" + entry
        if len(proposed) > LIMIT and current != header:
            messages.append(current); current = header + "\n\n" + entry
        else: current = proposed
    messages.append(current)
    return messages

def build_message(new_jobs, checked_count, failing_sources, manual_sources, is_sunday, all_open, profile):
    date = datetime.now().strftime("%-d %b")
    if not new_jobs and not is_sunday:
        msg = f"<b>{escape(profile.name)}</b> · {date} — nothing new · {checked_count} companies checked"
        if failing_sources: msg += "\n\n⚠️ " + escape(", ".join(failing_sources))
        return msg
    header = f"<b>{escape(profile.name)}</b> · {date}\n{len(new_jobs)} new · {checked_count} companies checked"
    entries = [_entry(job, profile) for job in new_jobs]
    if failing_sources: entries.append("⚠️ " + escape(", ".join(failing_sources)))
    if is_sunday:
        entries.append("<b>— currently open —</b>")
        entries.extend(f"{escape(str(j['company']))} · <a href=\"{escape(str(j['url']), quote=True)}\">{escape(str(j['title']))}</a>" for j in all_open)
        if manual_sources: entries.append("Manual check: " + escape(", ".join(manual_sources)))
    messages = _chunks(header, entries)
    return messages[0] if len(messages) == 1 else messages
