"""Job aggregators that index the big boards (LinkedIn, Indeed, Glassdoor, Naukri, ZipRecruiter…).

We never scrape those sites directly (their terms forbid it and they block cloud IPs). Instead:
  * JSearch (RapidAPI) — Google for Jobs results, which index LinkedIn/Indeed/Glassdoor listings.
  * Adzuna — official jobs API (in, gb, us, sg, ae*).
Each listing is then resolved to the employer's own posting when the apply link points at a
public ATS, so job text and open/closed status come from the source.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

from ..log import get_logger
from .ats import Posting, _ts, find_posting, parse_job_url

log = get_logger("aggregators")

import os  # noqa: E402

JSEARCH_KEY = os.getenv("JSEARCH_API_KEY", "").strip()
ADZUNA_ID = os.getenv("ADZUNA_APP_ID", "").strip()
ADZUNA_KEY = os.getenv("ADZUNA_APP_KEY", "").strip()

# Country codes per target region
JSEARCH_COUNTRIES = {"India": "in", "Singapore": "sg", "UAE": "ae", "UK": "gb", "US": "us"}
ADZUNA_COUNTRIES = {"India": "in", "Singapore": "sg", "UK": "gb", "US": "us"}


def jsearch(query: str, country: str, pages: int = 1) -> list[Posting]:
    if not JSEARCH_KEY:
        return []
    out: list[Posting] = []
    try:
        with httpx.Client(timeout=60) as c:
            resp = c.get(
                "https://jsearch.p.rapidapi.com/search",
                headers={"X-RapidAPI-Key": JSEARCH_KEY, "X-RapidAPI-Host": "jsearch.p.rapidapi.com"},
                params={"query": query, "page": 1, "num_pages": pages, "date_posted": "week", "country": country},
            )
            resp.raise_for_status()
            for j in resp.json().get("data", []) or []:
                options = j.get("apply_options") or []
                links = [j.get("job_apply_link", "")] + [o.get("apply_link", "") for o in options]
                links = [x for x in links if x]
                direct = next((x for x in links if parse_job_url(x)), None)
                loc = ", ".join(x for x in (j.get("job_city"), j.get("job_state"), j.get("job_country")) if x)
                out.append(Posting(
                    ats="aggregator", token=(j.get("job_publisher") or "jsearch"), job_id=str(j.get("job_id")),
                    title=j.get("job_title", ""), url=direct or (links[0] if links else ""),
                    apply_url=direct or (links[0] if links else ""), location=loc,
                    remote=j.get("job_is_remote"), description=j.get("job_description", "") or "",
                    posted_at=_ts(j.get("job_posted_at_datetime_utc")), company_name=j.get("employer_name", ""),
                    extra={"source": "jsearch", "publisher": j.get("job_publisher"), "apply_links": links[:6]},
                ))
    except httpx.HTTPError as exc:
        log.info("jsearch failed: %s", type(exc).__name__)
    return out


def adzuna(what: str, country: str, where: str = "", max_days: int = 7) -> list[Posting]:
    if not (ADZUNA_ID and ADZUNA_KEY):
        return []
    out: list[Posting] = []
    try:
        with httpx.Client(timeout=60, follow_redirects=True) as c:
            resp = c.get(
                f"https://api.adzuna.com/v1/api/jobs/{country}/search/1",
                params={"app_id": ADZUNA_ID, "app_key": ADZUNA_KEY, "what": what, "where": where,
                        "max_days_old": max_days, "results_per_page": 50, "content-type": "application/json"},
            )
            resp.raise_for_status()
            for j in resp.json().get("results", []) or []:
                out.append(Posting(
                    ats="aggregator", token="adzuna", job_id=str(j.get("id")), title=j.get("title", ""),
                    url=j.get("redirect_url", ""), apply_url=j.get("redirect_url", ""),
                    location=(j.get("location") or {}).get("display_name", ""), remote=None,
                    description=j.get("description", "") or "",  # truncated by Adzuna; resolved later
                    posted_at=_ts(j.get("created")), company_name=(j.get("company") or {}).get("display_name", ""),
                    extra={"source": "adzuna", "truncated": True},
                ))
    except httpx.HTTPError as exc:
        log.info("adzuna failed: %s", type(exc).__name__)
    return out


def resolve(p: Posting) -> Posting:
    """If the apply link is a public ATS posting, replace the listing with the employer's own record."""
    if p.ats != "aggregator":
        return p
    candidates = [p.apply_url] + list(p.extra.get("apply_links", []))
    if p.extra.get("source") == "adzuna" and p.apply_url:
        try:  # Adzuna redirect → employer page
            with httpx.Client(timeout=30, follow_redirects=True) as c:
                candidates.insert(0, str(c.head(p.apply_url).url))
        except httpx.HTTPError:
            pass
    for link in candidates:
        if link and parse_job_url(link):
            found = find_posting(link)
            if found:
                found.company_name = found.company_name or p.company_name
                found.extra = {**p.extra, "resolved_from": p.extra.get("source")}
                return found
    return p


def apply_channel(url: str) -> str:
    """Where the apply button leads. Decides which workflow may submit it."""
    u = (url or "").lower()
    parsed = parse_job_url(u)
    if parsed:
        return parsed[0]  # greenhouse | lever | ashby | smartrecruiters  → cloud submit
    for needle, name in (
        ("apply.workable.com", "workable"), ("myworkdayjobs.com", "workday"), ("linkedin.com", "linkedin"),
        ("indeed.", "indeed"), ("naukri.com", "naukri"), ("instahyre.com", "instahyre"),
        ("glassdoor.", "glassdoor"), ("cutshort.io", "cutshort"), ("wellfound.com", "wellfound"),
        ("icims.com", "icims"), ("taleo.net", "taleo"), ("successfactors", "successfactors"),
    ):
        if needle in u:
            return name
    return "company_site"


CLOUD_SUBMIT = {"greenhouse", "lever", "ashby", "smartrecruiters", "workable"}
LAPTOP_SUBMIT = {"linkedin", "indeed", "naukri", "instahyre", "glassdoor", "cutshort", "wellfound"}
MANUAL = {"workday", "icims", "taleo", "successfactors", "company_site"}


def now() -> datetime:
    return datetime.now(timezone.utc)
