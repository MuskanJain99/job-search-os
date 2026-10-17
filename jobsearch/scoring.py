"""Fit scoring: compares a JD with the evidence bank. Under ALL_ELIGIBLE it only orders the queue."""

from __future__ import annotations

import json

from .llm import ask_json, untrusted
from .models import FitScore

PROMPT_VERSION = "v3-service"

TASK = """Score how well the candidate's evidence matches this job, and extract eligibility facts.
- score 0-100; verdict STRONG (>=80) / GOOD (70-79) / STRETCH (55-69) / SKIP (<55).
- matched_claims: claim_ids from <evidence> that directly support JD requirements.
- gaps: JD requirements without evidence (must_have=true if the JD marks them required).
- min_years_required: smallest number of years the JD demands (null if not stated).
- sponsorship_or_relocation: 'not_needed' for India-based or India-remote roles; else 'yes' / 'no' / 'unknown'
  from the JD text (e.g. 'no visa sponsorship' -> 'no').
- shift_or_hours: copy any stated shift/working-hours requirement, else "".
- skip_reason: non-empty ONLY for: requires security clearance, requires a non-English language,
  posting says it is closed, or the role is not hands-on software engineering."""


def score_job(title: str, company: str, location: str, jd_text: str, evidence: list[dict]) -> FitScore:
    ev = "\n".join(f"{e['claim_id']}: {e['claim']} [{', '.join(e.get('technologies') or [])}]" for e in evidence)
    payload = (
        f"<evidence>\n{ev}\n</evidence>\n"
        f"Job: {title} at {company} ({location})\n" + untrusted(jd_text, 14000)
    )
    fit = ask_json(TASK, payload, FitScore, fast=True, max_tokens=2000)
    valid = {e["claim_id"] for e in evidence}
    fit.matched_claims = [c for c in fit.matched_claims if c in valid]
    return fit


def shift_ok(shift_text: str, remote_policy: str, rule_answer: str) -> bool:
    """Apply profile rule SCH-12 deterministically where the shift is explicit.

    Remote: all hours must fall within 06:00-22:00 IST. Hybrid/onsite: normal daytime shifts only.
    Unparseable or absent shift text passes (a form question will still route through answers)."""
    s = (shift_text or "").lower()
    if not s:
        return True
    night_words = ("night", "rotational shift", "us shift", "graveyard")
    evening_words = ("evening", "afternoon", "2 pm", "2:00 pm", "14:00", "3 pm", "4 pm", "11 pm", "23:00", "midnight")
    if any(w in s for w in night_words):
        return False
    if remote_policy in ("HYBRID", "ONSITE") and any(w in s for w in evening_words):
        return False
    if remote_policy == "REMOTE" and any(w in s for w in ("11 pm", "23:00", "midnight", "1 am", "2 am")):
        return False
    return True


def gaps_json(fit: FitScore) -> str:
    return json.dumps([g.model_dump() for g in fit.gaps])
