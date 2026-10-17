"""Evidence-grounded resume tailoring.

The LLM chooses and rephrases; code enforces truth: every bullet must cite claim_ids that
exist in the evidence bank for the right role, and employer/title/dates come only from the
fixed resume skeleton — never from the model.
"""

from __future__ import annotations

from .llm import ask_json, untrusted
from .models import CoverLetter, TailoredResume

GENERATOR_VERSION = "service-v1"

TASK = """Build a one-page A4 resume tailored to this job from the candidate's evidence.
Rules:
- Use ONLY facts in <evidence>. Rephrase and reorder freely; never add employers, titles, dates, metrics,
  technologies or years that are not in a cited claim. If the JD needs a skill she lacks, do not mention it.
- Each bullet cites 1-3 claim_ids it is based on; numbers in a bullet must appear in those claims.
- experience[].key must be one of the keys in <skeleton>; a claim may only be used under the experience
  whose evidence_roles include that claim's role.
- 4-7 bullets for the current role, 1-3 for older roles; lead with what the JD asks for first.
- headline <= 12 words; summary <= 55 words; skills: 3-4 groups, only technologies present in cited claims."""


def tailor(job: dict, evidence: list[dict], skeleton: dict) -> tuple[dict, list[str], str]:
    ev = "\n".join(
        f"{e['claim_id']} (role: {e.get('role') or 'general'}): {e['claim']}"
        + (f" | metrics: {e['metrics']}" if e.get("metrics") else "")
        + f" | tech: {', '.join(e.get('technologies') or [])}"
        for e in evidence
    )
    sk = "\n".join(f"- key={x['key']}: {x['title']} @ {x['company']} ({x['dates']}); evidence_roles={x['evidence_roles']}"
                   for x in skeleton["experience"])
    payload = (f"<skeleton>\n{sk}\n</skeleton>\n<evidence>\n{ev}\n</evidence>\n"
               f"Job: {job['title']} at {job['company']}\n" + untrusted(job["jd_text"], 12000))
    draft = ask_json(TASK, payload, TailoredResume, max_tokens=5000)
    content, used = enforce(draft, evidence, skeleton)
    return content, used, draft.emphasis


def enforce(draft: TailoredResume, evidence: list[dict], skeleton: dict) -> tuple[dict, list[str]]:
    """Drop anything not backed by evidence; attach fixed facts from the skeleton."""
    by_id = {e["claim_id"]: e for e in evidence}
    used: list[str] = []
    blocks_by_key = {b.key: b for b in draft.experience}
    experience = []
    general_roles = {None, ""}
    for sk in skeleton["experience"]:
        block = blocks_by_key.get(sk["key"])
        bullets = []
        for b in (block.bullets if block else []):
            ids = [c for c in b.claim_ids if c in by_id and (by_id[c].get("role") in sk["evidence_roles"]
                                                              or by_id[c].get("role") in general_roles)]
            if not ids or not _numbers_backed(b.text, [by_id[c] for c in ids]):
                continue
            bullets.append({"text": b.text.strip(), "claim_ids": ids})
            used.extend(ids)
        if bullets:
            experience.append({k: sk[k] for k in ("company", "title", "dates", "location")} | {"bullets": bullets})
    allowed_tech = {t.lower() for c in set(used) for t in (by_id[c].get("technologies") or [])}
    skills = []
    for g in draft.skills:
        items = [i for i in g.items if i.lower() in allowed_tech or any(i.lower() in t for t in allowed_tech)]
        if items:
            skills.append({"group": g.group, "items": items})
    content = {
        "headline": draft.headline.strip(), "summary": draft.summary.strip(), "skills": skills,
        "experience": experience, "education": skeleton["education"], "format": "A4, one page",
    }
    return content, sorted(set(used))


def _numbers_backed(text: str, claims: list[dict]) -> bool:
    import re

    nums = set(re.findall(r"\d+(?:\.\d+)?", text))
    if not nums:
        return True
    source = " ".join(f"{c['claim']} {c.get('metrics') or ''}" for c in claims)
    return all(n in source for n in nums)


COVER_TASK = """Write a cover letter (<= 250 words, plain text, signed 'Muskan') for this job using ONLY facts
from <evidence>. Specific to the company and role; no claims about skills not in evidence; cite claim_ids used."""


def cover_letter(job: dict, evidence: list[dict]) -> str:
    ev = "\n".join(f"{e['claim_id']}: {e['claim']}" for e in evidence)
    out = ask_json(COVER_TASK, f"<evidence>\n{ev}\n</evidence>\nJob: {job['title']} at {job['company']}\n"
                   + untrusted(job["jd_text"], 8000), CoverLetter, max_tokens=1500)
    valid = {e["claim_id"] for e in evidence}
    if not [c for c in out.claim_ids if c in valid]:
        raise ValueError("cover letter cited no valid evidence")
    return out.text.strip()
