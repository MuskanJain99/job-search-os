"""Deterministic pre-filters applied before any LLM call (cheap, testable)."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

ENGINEERING = re.compile(r"\b(engineer|engineering|developer|sde|swe|programmer|architect)\b", re.I)
DOMAIN = re.compile(
    r"\b(software|backend|back[- ]end|platform|infrastructure|infra|distributed|systems?|data|cloud|ai|ml|"
    r"machine learning|llm|genai|full[- ]?stack|golang|go|python|api|sre|reliability|devops|sde|swe)\b", re.I)
TOO_JUNIOR = re.compile(r"\b(intern|internship|trainee|fresher|graduate|new grad|junior|jr\.?|entry[- ]level)\b", re.I)
TOO_SENIOR = re.compile(r"\b(staff|principal|distinguished|lead|head of|director|vp|vice president|manager|"
                        r"architect)\b", re.I)

COUNTRY_TERMS = {
    "India": ["india", "bengaluru", "bangalore", "hyderabad", "pune", "mumbai", "chennai", "gurgaon", "gurugram",
              "noida", "delhi", "kolkata", "ahmedabad", "kochi", "trivandrum", "jaipur", "coimbatore"],
    "Singapore": ["singapore"],
    "UAE": ["united arab emirates", "uae", "dubai", "abu dhabi"],
    "UK": ["united kingdom", "uk", "london", "england", "manchester", "edinburgh", "cambridge"],
    "US": ["united states", "usa", "u.s.", "us-", "new york", "san francisco", "seattle", "austin", "boston",
           "california", "texas", "washington", "chicago", "remote - us", "remote, us"],
}
GLOBAL_REMOTE = ["anywhere", "worldwide", "global", "apac", "asia", "emea & apac", "remote - india", "remote, india"]


@dataclass
class Location:
    country: str | None
    remote_policy: str  # ONSITE | HYBRID | REMOTE | UNKNOWN
    india_eligible: bool
    eligible: bool


def normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def title_ok(title: str, exclude: list[str]) -> tuple[bool, str]:
    t = title or ""
    if not ENGINEERING.search(t):
        return False, "not_engineering"
    if not DOMAIN.search(t):
        return False, "off_domain"
    if TOO_JUNIOR.search(t):
        return False, "too_junior"
    if TOO_SENIOR.search(t):
        return False, "too_senior"
    low = t.lower()
    for kw in exclude:
        k = kw.lower().strip()
        if not k or " only" in k:  # descriptive entries like "Java full stack only" are handled by scoring
            continue
        if re.search(rf"\b{re.escape(k)}\b", low):
            return False, f"excluded:{k}"
    return True, "ok"


def _has(text: str, term: str) -> bool:
    return re.search(rf"(?<![a-z]){re.escape(term)}(?![a-z])", text) is not None


def classify_location(location: str, remote: bool | None, description: str = "") -> Location:
    loc = (location or "").lower()
    countries = [c for c, terms in COUNTRY_TERMS.items() if any(_has(loc, t) for t in terms)]
    is_remote = bool(remote) or "remote" in loc
    is_hybrid = "hybrid" in loc or "hybrid" in (description or "")[:3000].lower()
    policy = "REMOTE" if is_remote and not is_hybrid else "HYBRID" if is_hybrid else ("ONSITE" if loc else "UNKNOWN")
    country = countries[0] if countries else None
    india = "India" in countries

    if india:
        return Location("India", policy, True, True)
    if is_remote and (any(g in loc for g in GLOBAL_REMOTE) or not countries):
        # Remote with no country restriction: possibly open to India, scoring verifies.
        return Location(country, "REMOTE", True, True)
    if is_remote and countries:
        # Remote but tied to another country: needs local work authorisation; not India-eligible.
        return Location(country, "REMOTE", False, False)
    if country in {"Singapore", "UAE", "UK", "US"}:
        return Location(country, policy, False, True)  # relocation targets; sponsorship checked in scoring
    return Location(country, policy, False, False)


def decide(policy: str, fit_verdict: str, fit_score: int, min_fit: int, loc: Location,
           min_years: int | None, years_ok: tuple[int, int], sponsorship: str, shift_ok: bool,
           hard_skip_reason: str = "") -> tuple[bool, str]:
    """Final go / no-go. Under ALL_ELIGIBLE the fit score never blocks — only eligibility does."""
    if hard_skip_reason:
        return False, hard_skip_reason
    if not loc.eligible:
        return False, "location_not_eligible"
    if loc.country not in (None, "India") and not loc.india_eligible and sponsorship == "no":
        return False, "no_sponsorship"
    if min_years is not None and not (years_ok[0] <= min_years <= years_ok[1]):
        return False, f"years_required_{min_years}"
    if not shift_ok:
        return False, "shift_rule_SCH-12"
    if policy == "FIT_THRESHOLD" and (fit_verdict == "SKIP" or fit_score < min_fit):
        return False, f"fit_{fit_score}"
    return True, "eligible"


def dedupe_key(company_normalized: str, ats_job_id: str | None, title: str = "", city: str = "") -> str:
    if ats_job_id:
        return f"{company_normalized}|{ats_job_id}"
    return f"{company_normalized}|{normalize(title)}|{normalize(city)}"


def jd_hash(text: str) -> str:
    return hashlib.sha256(normalize(text).encode()).hexdigest()[:32]
