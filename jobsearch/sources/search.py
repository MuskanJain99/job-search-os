"""Optional web search for new postings (Firecrawl Search API, FIRECRAWL_API_KEY).

Search only finds URLs. Each URL is then resolved through the employer's public ATS feed when
possible (so the job text and open/closed status come from the source, not a search snippet).
"""

from __future__ import annotations

import httpx

from ..config import settings
from ..log import get_logger

log = get_logger("search")

ATS_SITES = (
    "site:job-boards.greenhouse.io OR site:boards.greenhouse.io OR site:jobs.lever.co "
    "OR site:jobs.ashbyhq.com OR site:jobs.smartrecruiters.com"
)


def search_urls(query: str, limit: int = 20, recency: str = "qdr:w") -> list[str]:
    if not settings.firecrawl_api_key:
        return []
    try:
        with httpx.Client(timeout=60) as c:
            resp = c.post(
                "https://api.firecrawl.dev/v2/search",
                headers={"Authorization": f"Bearer {settings.firecrawl_api_key}"},
                json={"query": query, "limit": limit, "tbs": recency, "sources": ["web"]},
            )
            resp.raise_for_status()
            data = resp.json().get("data") or {}
            items = data.get("web", []) if isinstance(data, dict) else data
            return [i.get("url", "") for i in items if i.get("url")]
    except httpx.HTTPError as exc:
        log.info("search failed: %s", type(exc).__name__)
        return []


def build_queries(titles: list[str], locations: list[str]) -> list[str]:
    """A small, rotating set of ATS-restricted queries (titles × locations)."""
    qs = []
    for t in titles[:4]:
        for loc in locations[:5]:
            loc_term = "remote India" if "Remote" in loc else loc.split(" (")[0]
            qs.append(f'"{t}" {loc_term} ({ATS_SITES})')
    return qs


def fetch_page_text(url: str) -> str:
    """Fetch any page as markdown via Firecrawl (used for pasted non-ATS links)."""
    if not settings.firecrawl_api_key:
        return ""
    try:
        with httpx.Client(timeout=90) as c:
            resp = c.post(
                "https://api.firecrawl.dev/v2/scrape",
                headers={"Authorization": f"Bearer {settings.firecrawl_api_key}"},
                json={"url": url, "formats": ["markdown"], "onlyMainContent": True},
            )
            resp.raise_for_status()
            return ((resp.json().get("data") or {}).get("markdown") or "")[:40000]
    except httpx.HTTPError as exc:
        log.info("scrape failed: %s", type(exc).__name__)
        return ""
