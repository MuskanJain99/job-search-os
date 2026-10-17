"""Public applicant-tracking-system (ATS) job feeds. No login, no scraping of search pages.

Greenhouse, Lever, Ashby and SmartRecruiters publish job boards as JSON. This module turns
them into one Posting shape and can recognise a pasted job URL.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx

from ..log import get_logger

log = get_logger("ats")
UA = {"User-Agent": "job-search-os/0.1 (+https://github.com)"}
TIMEOUT = httpx.Timeout(30.0)


@dataclass
class Posting:
    ats: str
    token: str
    job_id: str
    title: str
    url: str
    apply_url: str
    location: str
    remote: bool | None
    description: str
    posted_at: datetime | None
    company_name: str = ""
    extra: dict = field(default_factory=dict)


_TAG = re.compile(r"<[^>]+>")


def html_to_text(raw: str) -> str:
    text = html.unescape(raw or "")
    text = re.sub(r"<(br|/p|/li|/h\d)\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<li[^>]*>", "\n• ", text, flags=re.I)
    text = _TAG.sub("", text)
    text = html.unescape(text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _ts(value) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000 if value > 1e11 else value, tz=timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _get(url: str, params: dict | None = None):
    with httpx.Client(headers=UA, timeout=TIMEOUT, follow_redirects=True) as c:
        resp = c.get(url, params=params)
        resp.raise_for_status()
        return resp.json()


# ---------- feeds ----------

def greenhouse(token: str) -> list[Posting]:
    data = _get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs", {"content": "true"})
    out = []
    for j in data.get("jobs", []):
        loc = (j.get("location") or {}).get("name", "")
        out.append(Posting(
            ats="greenhouse", token=token, job_id=str(j["id"]), title=j.get("title", ""),
            url=j.get("absolute_url", ""), apply_url=j.get("absolute_url", ""), location=loc,
            remote="remote" in loc.lower() if loc else None,
            description=html_to_text(j.get("content", "")),
            posted_at=_ts(j.get("first_published") or j.get("updated_at")),
            company_name=j.get("company_name", ""),
        ))
    return out


def greenhouse_questions(token: str, job_id: str) -> list[dict]:
    """Application form questions for one Greenhouse job (public endpoint)."""
    data = _get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{job_id}", {"questions": "true"})
    return data.get("questions", []) or []


def lever(token: str) -> list[Posting]:
    data = _get(f"https://api.lever.co/v0/postings/{token}", {"mode": "json"})
    out = []
    for j in data:
        cats = j.get("categories") or {}
        lists = "\n".join(
            f"{li.get('text', '')}\n{html_to_text(li.get('content', ''))}" for li in j.get("lists", []) or []
        )
        out.append(Posting(
            ats="lever", token=token, job_id=j["id"], title=j.get("text", ""),
            url=j.get("hostedUrl", ""), apply_url=j.get("applyUrl") or j.get("hostedUrl", ""),
            location=cats.get("location", "") or "", remote=(j.get("workplaceType") == "remote") or None,
            description=(j.get("descriptionPlain", "") + "\n" + lists + "\n" + (j.get("additionalPlain") or "")).strip(),
            posted_at=_ts(j.get("createdAt")),
        ))
    return out


def ashby(token: str) -> list[Posting]:
    data = _get(f"https://api.ashbyhq.com/posting-api/job-board/{token}", {"includeCompensation": "true"})
    out = []
    for j in data.get("jobs", []):
        locs = [j.get("location") or ""] + [s.get("location", "") for s in j.get("secondaryLocations", []) or []]
        out.append(Posting(
            ats="ashby", token=token, job_id=j["id"], title=j.get("title", ""),
            url=j.get("jobUrl", ""), apply_url=j.get("applyUrl") or j.get("jobUrl", ""),
            location=" / ".join(x for x in locs if x), remote=j.get("isRemote"),
            description=j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml", "")),
            posted_at=_ts(j.get("publishedAt")),
        ))
    return out


def smartrecruiters(token: str) -> list[Posting]:
    data = _get(f"https://api.smartrecruiters.com/v1/companies/{token}/postings", {"limit": 100})
    out = []
    for j in data.get("content", []):
        loc = j.get("location") or {}
        detail = {}
        try:
            detail = _get(f"https://api.smartrecruiters.com/v1/companies/{token}/postings/{j['id']}")
        except httpx.HTTPError:
            pass
        sections = ((detail.get("jobAd") or {}).get("sections") or {})
        desc = "\n\n".join(html_to_text((sections.get(k) or {}).get("text", ""))
                           for k in ("companyDescription", "jobDescription", "qualifications", "additionalInformation"))
        url = f"https://jobs.smartrecruiters.com/{token}/{j['id']}"
        out.append(Posting(
            ats="smartrecruiters", token=token, job_id=str(j["id"]), title=j.get("name", ""),
            url=url, apply_url=detail.get("applyUrl") or url,
            location=", ".join(x for x in (loc.get("city"), loc.get("region"), loc.get("country")) if x),
            remote=loc.get("remote"), description=desc.strip(), posted_at=_ts(j.get("releasedDate")),
        ))
    return out


FEEDS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby, "smartrecruiters": smartrecruiters}


def fetch_board(ats: str, token: str) -> list[Posting]:
    fn = FEEDS.get(ats)
    if not fn or not token:
        return []
    try:
        return fn(token)
    except httpx.HTTPError as exc:
        log.info("feed %s/%s failed: %s", ats, token, type(exc).__name__)
        return []


# ---------- URL recognition (for pasted links and search results) ----------

_PATTERNS = [
    ("greenhouse", re.compile(r"(?:job-boards|boards)(?:\.eu)?\.greenhouse\.io/([^/?#]+)/jobs/(\d+)")),
    ("greenhouse", re.compile(r"greenhouse\.io/embed/job_app\?.*?for=([^&]+).*?token=(\d+)")),
    ("lever", re.compile(r"jobs\.(?:eu\.)?lever\.co/([^/?#]+)/([0-9a-f-]{36})")),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([^/?#]+)/([0-9a-f-]{36})")),
    ("smartrecruiters", re.compile(r"jobs\.smartrecruiters\.com/([^/?#]+)/(\d+)")),
]


def parse_job_url(url: str) -> tuple[str, str, str] | None:
    """Return (ats, board_token, job_id) for a recognised ATS posting URL."""
    for ats, pat in _PATTERNS:
        m = pat.search(url)
        if m:
            return ats, m.group(1), m.group(2)
    return None


def clean_url(url: str) -> str:
    p = urlparse(url.strip())
    keep = "" if p.netloc.endswith(("linkedin.com", "naukri.com")) else p.query
    keep = "&".join(q for q in keep.split("&") if q and not q.lower().startswith(("utm_", "gh_src", "ref=", "source=")))
    return p._replace(query=keep, fragment="").geturl()


def find_posting(url: str) -> Posting | None:
    parsed = parse_job_url(url)
    if not parsed:
        return None
    ats, token, job_id = parsed
    for p in fetch_board(ats, token):
        if p.job_id == job_id:
            return p
    return None
