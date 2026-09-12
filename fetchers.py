"""Small, dependency-light clients for the job boards used by Job Radar.

Each public function returns the same six fields.  Network failures are exposed
as FetchError so callers can record a per-source failure without stopping a run.
"""
from __future__ import annotations

import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from html import unescape
from typing import Any
from urllib.parse import urljoin, quote

import requests

TIMEOUT = 20
HEADERS = {"User-Agent": "Job-Radar/1.0 (+https://github.com/job-radar)"}


class FetchError(RuntimeError):
    """A public job feed could not be retrieved or decoded."""


def _request(method: str, url: str, **kwargs: Any) -> requests.Response:
    """Request with two retries for transient failures only."""
    kwargs.setdefault("headers", HEADERS)
    kwargs.setdefault("timeout", TIMEOUT)
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = requests.request(method, url, **kwargs)
            if response.status_code < 500:
                response.raise_for_status()
                return response
            last_error = FetchError(f"{url}: HTTP {response.status_code}")
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_error = exc
        except requests.RequestException as exc:
            raise FetchError(f"{url}: {exc}") from exc
        if attempt < 2:
            time.sleep(1.0 * (2 ** attempt))
    raise FetchError(f"{url}: {last_error}")


def _json(method: str, url: str, **kwargs: Any) -> Any:
    try:
        return _request(method, url, **kwargs).json()
    except (ValueError, requests.RequestException) as exc:
        raise FetchError(f"{url}: invalid JSON ({exc})") from exc


def _job(company: str, title: Any, location: Any, url: Any, job_id: Any,
         posted_at: Any = None) -> dict[str, str | None]:
    """Make the exact normalised object consumed by the filtering pipeline."""
    canonical_url = str(url or "")
    return {"company": company, "title": str(title or ""),
            "location": str(location or ""), "url": canonical_url,
            "job_id": str(job_id or canonical_url), "posted_at": _iso(posted_at)}


def _iso(value: Any) -> str | None:
    """Keep only ISO-8601 dates; convert epoch/RFC dates when safely possible."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()
    text = str(value)
    if re.match(r"^\d{4}-\d{2}-\d{2}T", text):
        return text
    try:
        return parsedate_to_datetime(text).astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError):
        return None


def greenhouse(board: str, company: str | None = None) -> list[dict]:
    data = _json("GET", f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs")
    return [_job(company or board, row.get("title"), row.get("location", {}).get("name"),
                 row.get("absolute_url"), row.get("id"), row.get("updated_at"))
            for row in data.get("jobs", [])]


def traderepublic_api(company: str | None = None) -> list[dict]:
    """Trade Republic's public careers API, used by its current careers page."""
    url = "https://api.traderepublic.com/api/v1/career/jobs"
    data = _json("GET", url, params={"content": "true"})
    rows = data.get("jobs", [])
    if not isinstance(rows, list):
        raise FetchError("Trade Republic careers: unexpected response")
    if not rows:
        raise FetchError("Trade Republic careers: zero postings")
    return [_job(company or "Trade Republic", row.get("title"),
                 (row.get("location") or {}).get("name"), row.get("absolute_url"), row.get("id"),
                 row.get("updated_at") or row.get("created_at")) for row in rows]


def smartrecruiters(company: str, display_name: str | None = None) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    while True:
        data = _json("GET", "https://api.smartrecruiters.com/v1/companies/"
                     f"{company}/postings?limit=100&offset={offset}")
        content = data.get("content", [])
        rows.extend(content)
        total = data.get("totalFound")
        # SmartRecruiters misleadingly returns 200/0 for unknown slugs.
        if total == 0:
            raise FetchError(f"SmartRecruiters {company}: zero postings (probable bad slug)")
        offset += len(content)
        if not content or offset >= int(total or 0):
            break
    result = []
    for row in rows:
        place = row.get("location") or {}
        location = ", ".join(str(x) for x in (place.get("city"), place.get("country")) if x)
        ident = row.get("id")
        result.append(_job(display_name or company, row.get("name"), location,
                           f"https://jobs.smartrecruiters.com/{company}/{ident}", ident,
                           row.get("releasedDate")))
    return result


def ashby(board: str, company: str | None = None) -> list[dict]:
    data = _json("GET", "https://api.ashbyhq.com/posting-api/job-board/"
                 f"{board}?includeCompensation=false")
    return [_job(company or board, row.get("title"), row.get("location"), row.get("jobUrl"),
                 row.get("id"), row.get("publishedAt")) for row in data.get("jobs", [])]


def lever(company: str, display_name: str | None = None) -> list[dict]:
    rows = _json("GET", f"https://api.lever.co/v0/postings/{company}?mode=json")
    if not isinstance(rows, list):
        raise FetchError(f"Lever {company}: unexpected response")
    return [_job(display_name or company, row.get("text"),
                 (row.get("categories") or {}).get("location"), row.get("hostedUrl"),
                 row.get("id"), row.get("createdAt")) for row in rows]


def personio_xml(subdomain: str, company: str | None = None) -> list[dict]:
    url = f"https://{subdomain}.jobs.personio.de/xml"
    try:
        root = ET.fromstring(_request("GET", url).content)
    except ET.ParseError as exc:
        raise FetchError(f"{url}: invalid XML") from exc
    rows = []
    for position in root.findall(".//position"):
        ident = position.findtext("id")
        rows.append(_job(company or subdomain, position.findtext("name"), position.findtext("office"),
                         f"https://{subdomain}.jobs.personio.de/job/{ident}", ident,
                         position.findtext("createdAt")))
    return rows


def recruitee(company: str, display_name: str | None = None) -> list[dict]:
    data = _json("GET", f"https://{company}.recruitee.com/api/offers/")
    return [_job(display_name or company, row.get("title"), row.get("location"),
                 row.get("careers_url"), row.get("id"), row.get("created_at"))
            for row in data.get("offers", [])]


def workday(tenant: str, site: str, host: str, company: str | None = None) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    url = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
    while True:
        data = _json("POST", url, json={"appliedFacets": {}, "limit": 20,
                     "offset": offset, "searchText": ""},
                     headers={**HEADERS, "Content-Type": "application/json"})
        batch = data.get("jobPostings", [])
        rows.extend(batch)
        offset += len(batch)
        if not batch or offset >= int(data.get("total", 0)):
            break
    return [_job(company or tenant, row.get("title"), row.get("locationsText"),
                 f"https://{host}/{site}{row.get('externalPath', '')}",
                 (row.get("bulletFields") or [None])[0], row.get("postedOn")) for row in rows]


def softgarden(slug: str, company: str | None = None) -> list[dict]:
    """Softgarden v3 currently returns an object with a `data` job list."""
    data = _json("GET", f"https://{slug}.softgarden.io/api/rest/v3/frontend/jobs?limit=100")
    candidates = data.get("data", data.get("jobs", data if isinstance(data, list) else []))
    if isinstance(candidates, dict):
        candidates = candidates.get("items", candidates.get("content", []))
    if not isinstance(candidates, list):
        raise FetchError(f"Softgarden {slug}: unexpected response")
    return [_job(company or slug, row.get("title") or row.get("name"),
                 row.get("location") or row.get("locationName"), row.get("url") or row.get("detailUrl"),
                 row.get("id") or row.get("jobId"), row.get("publishedAt") or row.get("createdAt"))
            for row in candidates]


def join_com(slug: str, company: str | None = None) -> list[dict]:
    """JOIN.com's confirmed public jobs endpoint (kept separate for easy removal)."""
    data = _json("GET", f"https://join.com/api/public/companies/{slug}/jobs")
    rows = data.get("jobs", data if isinstance(data, list) else [])
    if not isinstance(rows, list):
        raise FetchError(f"JOIN {slug}: unexpected response")
    return [_job(company or slug, row.get("title"), row.get("location"),
                 row.get("url") or row.get("jobUrl"), row.get("id"), row.get("publishedAt")) for row in rows]


def rss(url: str, company: str | None = None, location_contains: str | None = None) -> list[dict]:
    """RSS feeds used by Teamtailor and SAP SuccessFactors career sites.

    SAP embeds its location in the feed title; Teamtailor supplies namespaced
    ``tt:location`` fields.  This deliberately does not fetch individual job
    pages, keeping a daily feed poll lightweight.
    """
    try:
        root = ET.fromstring(_request("GET", url).content)
    except ET.ParseError as exc:
        raise FetchError(f"{url}: invalid RSS/XML") from exc
    rows = []
    for item in root.findall(".//item"):
        title = item.findtext("title") or ""
        locations = [node.text or "" for node in item.findall(".//{*}location/{*}name")]
        locations += [node.text or "" for node in item.findall(".//{*}location/{*}city")]
        location = ", ".join(dict.fromkeys(filter(None, locations)))
        if not location:
            # SAP's RSS title convention is "Title (Berlin, DE, 10115)".
            match = re.search(r"\(([^()]*(?:,\s*(?:DE|Germany))[^()]*)\)\s*$", title, re.I)
            location = match.group(1) if match else ""
        if location_contains and location_contains.casefold() not in location.casefold():
            continue
        link = item.findtext("link") or ""
        rows.append(_job(company or "RSS", title, location, link,
                         item.findtext("guid") or link, item.findtext("pubDate")))
    return rows


def siemens_html(company: str | None = None) -> list[dict]:
    """Siemens' server-rendered Oracle Career Site result page (no public JSON)."""
    url = "https://jobs.siemens.com/en_US/externaljobs/SearchJobs"
    page = _request("GET", url).text
    articles = re.findall(r'<article\b[^>]*class="[^"]*article--result[^"]*"[^>]*>(.*?)</article>',
                          page, flags=re.I | re.S)
    rows = []
    def clean(fragment: str) -> str:
        return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()
    for article in articles:
        match = re.search(r'<a\b[^>]*href="([^"]*JobDetail/(\d+))"[^>]*>(.*?)</a>', article,
                          flags=re.I | re.S)
        location = re.search(r'<span\b[^>]*class="list-item-location"[^>]*>(.*?)</span>', article,
                             flags=re.I | re.S)
        if match:
            rows.append(_job(company or "Siemens", clean(match.group(3)), clean(location.group(1)) if location else "",
                             unescape(match.group(1)), match.group(2)))
    if not rows:
        raise FetchError("Siemens careers: result markup changed or returned no postings")
    return rows


class _ListingParser(HTMLParser):
    """Very small CSS-class selector extractor for the deliberately fragile html adapter."""
    def __init__(self, item: str, title: str, link: str, location: str):
        super().__init__(convert_charrefs=True); self.want = (item, title, link, location)
        self.depth = 0; self.current: dict[str, str] | None = None; self.rows: list[dict[str, str]] = []
        self.field: str | None = None
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        values = dict(attrs); classes = values.get("class", "").split(); selector = "." + " ".join(classes)
        if self.current is None and self.want[0].lstrip(".") in classes:
            self.current = {}; self.depth = 1
        elif self.current is not None:
            self.depth += 1
        if self.current is not None:
            for key, wanted in zip(("title", "link", "location"), self.want[1:]):
                if wanted.lstrip(".") in classes:
                    self.field = key
                    if key == "link" and values.get("href"): self.current[key] = values["href"] or ""
    def handle_data(self, data: str):
        if self.current is not None and self.field:
            self.current[self.field] = (self.current.get(self.field, "") + data).strip()
    def handle_endtag(self, tag: str):
        if self.current is not None:
            self.depth -= 1
            if self.depth <= 0:
                self.rows.append(self.current); self.current = None
        self.field = None


def html(url: str, item_selector: str, title_selector: str, link_selector: str,
         location_selector: str, company: str | None = None) -> list[dict]:
    """Fragile fallback: selectors must be simple `.class` values; JS pages won't work."""
    parser = _ListingParser(item_selector, title_selector, link_selector, location_selector)
    parser.feed(_request("GET", url).text)
    return [_job(company or "unknown", row.get("title"), row.get("location"),
                 urljoin(url, row.get("link", "")), row.get("link")) for row in parser.rows if row.get("title")]


def manual(careers_url: str, **_: Any) -> list[dict]:
    """Intentional no-op for sites which require a human weekly check."""
    return []


_SAP_ROW = re.compile(
    r'<a href="(?P<url>/job/[^"]+)" class="jobTitle-link">(?P<title>[^<]+)</a>.*?'
    r'<span class="jobLocation">\s*(?P<loc>[^<]+?)\s*</span>',
    re.S,
)


def sap_search(terms: tuple[str, ...] = ("research", "user experience", "ux", "design"),
               location: str = "Germany", company: str | None = None) -> list[dict]:
    """Search jobs.sap.com, which is server-rendered HTML with no working RSS.

    SAP is the largest UX employer in Germany, so a category feed that returns a handful of
    roles badly understates it. Runs one search per term and dedupes by job id.
    """
    seen: dict[str, dict] = {}
    for term in terms:
        for startrow in (0, 25, 50):
            url = (f"https://jobs.sap.com/search/?q={quote(term)}"
                   f"&locationsearch={quote(location)}&startrow={startrow}")
            try:
                response = _request("GET", url)
            except FetchError:
                break
            rows = list(_SAP_ROW.finditer(response.text))
            if not rows:
                break
            for match in rows:
                path = match.group("url")
                job_id = path.rstrip("/").rsplit("/", 1)[-1]
                if job_id in seen:
                    continue
                seen[job_id] = _job(company or "SAP", unescape(match.group("title")),
                                    unescape(match.group("loc")),
                                    "https://jobs.sap.com" + path, job_id, None)
    return list(seen.values())
