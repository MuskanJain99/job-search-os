"""Map application-form fields to Muskan's approved profile answers.

The model proposes; code enforces the answer_type rules:
  VALUE / RULE           -> may be used
  ASK_ME / NEVER_ANSWER_AUTOMATICALLY -> never filled; becomes a blocker if required
  PREFER_NOT_TO_ANSWER / NOT_APPLICABLE -> only if the form offers an equivalent option
Free-text motivation questions may be GENERATED from evidence; factual questions may not.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date

from .llm import ask_json, untrusted
from .models import FormAnswers

BLOCKING_TYPES = {"ASK_ME", "NEVER_ANSWER_AUTOMATICALLY"}
HARD_STOP_WORDS = (
    "passport number", "aadhaar", "pan number", "social security", "ssn", "national id", "date of birth",
    "criminal", "convicted", "background check consent", "i certify", "i declare", "attest", "signature",
    "visa status", "immigration",
)

TASK = """For each form field, choose the answer from <profile> (cite its id in profile_id).
- VALUE: use the answer as written (pick the matching option label for selects/radios).
- RULE: apply the rule using today's date {today} (e.g. notice-period arithmetic).
- If the matching profile entry is ASK_ME or NEVER_ANSWER_AUTOMATICALLY: answer=null, basis=that type.
- PREFER_NOT_TO_ANSWER / NOT_APPLICABLE: use only if an equivalent option exists, else answer=null basis=UNKNOWN.
- Name/email/phone/links/location/education/experience/notice/salary/authorization: ONLY from <profile>.
- Open-ended motivation questions ("why this company", "tell us about a project"): basis=GENERATED, write
  2-4 sentences using ONLY <evidence> facts.
- Anything else with no profile entry: answer=null, basis=UNKNOWN. Never guess.
- Resume/cover-letter file fields: answer="__RESUME__" or "__COVER_LETTER__"."""


# Unambiguous contact/identity fields are filled straight from the approved profile (no model guess).
_RULES: list[tuple[str, str]] = [  # (regex on lowercased label, profile id)
    (r"\b(country code|dial(ing)? code|phone.{0,12}country|country.{0,12}(phone|code))\b", "CON-01"),
    (r"\b(phone|mobile|telephone|contact number|cell)\b", "CON-02"),
    (r"\be-?mail\b", "CON-03"),
    (r"\blinkedin\b", "CON-04"),
    (r"\bgit ?hub\b", "CON-05"),
    (r"^(legal |first |given )?(first )?name$|^first name|^given name", "ID-01"),
    (r"^last name|^surname|^family name|legal last name", "ID-02"),
    (r"^(full |legal full )?name$|^your name$", "ID-03"),
    (r"^country( of residence)?$|^country/region$", "LOC-01"),
]


# Yes/No questions with a standing answer (any input type: radio, select, checkbox, combobox).
_YES_NO_RULES: list[tuple[str, str]] = [
    (r"experience (in|with) backend|backend (development )?experience", "EXP-12"),
    (r"^(?!.*(assistance|package|support|bonus|expense|where|which|preferred|when)).*"
     r"((willing|open|able|prepared|ready|consider|happy).{0,40}relocat|relocat\w*\s*\?|^relocation$)", "REL-01"),
    (r"(consent|agree|allow).{0,80}(shar|transfer|disclos).{0,120}recruit|(shar|transfer).{0,60}application.{0,120}recruit", "CNS-06"),
]
# Generic total-experience questions offered as ranges ("Years of experience: 0-2 / 2-4 / 4-6 / 6+").
_TOTAL_EXP = re.compile(r"(years|yrs).{0,30}experience|experience.{0,30}(years|yrs|level|range)|how (many|much|long).{0,40}experience|total experience|work experience")
_SKILL_SPECIFIC = re.compile(r"experience (in|with|using|of) |\b(in|with) (\w+ ){0,3}(development|programming|language)")

YEARS_MARKER = "__YEARS__:"


def deterministic(fields: list[dict], profile: list[dict]) -> dict[str, tuple[str, str]]:
    """field_id -> (value, profile_id) for fields with one obvious answer in the approved profile.
    Only VALUE entries (and the EXP-13 years rule) are used; anything else goes to the model."""
    by_id = {p["id"]: p for p in profile if p["answer_type"] == "VALUE" and p.get("answer")}
    years_rule = next((p for p in profile if p["id"] == "EXP-13" and p.get("answer")), None)
    out: dict[str, tuple[str, str]] = {}
    for f in fields:
        t = f.get("type")
        if t in ("file", "textarea"):
            continue
        label = re.sub(r"[*:]", "", f.get("label", "")).strip().lower()
        hit = next((pid for pat, pid in _YES_NO_RULES if re.search(pat, label) and pid in by_id), None)
        if hit:
            out[f["field_id"]] = (str(by_id[hit]["answer"]).strip(), hit)
            continue
        if years_rule and t in ("select", "radio", "combobox") and _TOTAL_EXP.search(label) and not _SKILL_SPECIFIC.search(label):
            from . import ranges

            if t == "combobox" or ranges.looks_like_ranges(f.get("options") or []):
                out[f["field_id"]] = (YEARS_MARKER + str(years_rule["answer"]).strip(), "EXP-13")
                continue
        if t in ("radio", "checkbox"):
            continue
        for pattern, pid in _RULES:
            if re.search(pattern, label) and pid in by_id:
                out[f["field_id"]] = (str(by_id[pid]["answer"]).strip(), pid)
                break
    return out


@dataclass
class Resolved:
    values: dict[str, str]          # field_id -> answer
    provenance: list[dict]          # for app_answers rows
    blockers: list[tuple[str, str]]  # (type, question)


def resolve(fields: list[dict], profile: list[dict], evidence: list[dict], job_title: str, company: str,
            user_answers: dict[str, str] | None = None) -> Resolved:
    """user_answers: {question label: answer} that Muskan gave directly (email reply) — used verbatim."""
    user_answers = {k.strip().lower(): v for k, v in (user_answers or {}).items()}
    if user_answers and all(f.get("label", "").strip().lower() in user_answers or not f.get("required") for f in fields) \
            and not any(f.get("type") == "file" for f in fields):
        # Every required field is covered by her own answers: no model call needed.
        vals = {f["field_id"]: user_answers[f["label"].strip().lower()] for f in fields
                if f.get("label", "").strip().lower() in user_answers}
        return Resolved(vals, [], [])
    by_id = {p["id"]: p for p in profile}
    prof = "\n".join(f"{p['id']} [{p['answer_type']}] {p['field']}: {p['answer']}"
                     + (f" (when: {p['applies_when']})" if p.get("applies_when") else "") for p in profile)
    ev = "\n".join(f"{e['claim_id']}: {e['claim']}" for e in evidence[:60])
    payload = (f"<profile>\n{prof}\n</profile>\n<evidence>\n{ev}\n</evidence>\nJob: {job_title} at {company}\n"
               + untrusted(json.dumps(fields, ensure_ascii=False), 15000))
    out = ask_json(TASK.format(today=date.today().isoformat()), payload, FormAnswers, fast=True, max_tokens=6000)

    proposed = {a.field_id: a for a in out.answers}
    det = deterministic(fields, profile)
    values: dict[str, str] = {}
    prov: list[dict] = []
    blockers: list[tuple[str, str]] = []
    for f in fields:
        fid, label, required = f["field_id"], f.get("label", ""), bool(f.get("required"))
        a = proposed.get(fid)
        low = label.lower()
        if fid in det and low.strip() not in user_answers:
            values[fid] = det[fid][0]
            prov.append({"question_text": label[:500], "profile_id": det[fid][1], "answer": values[fid], "basis": "PROFILE"})
            continue
        if low.strip() in user_answers:  # her own answer always wins, including for hard-stop questions
            values[fid] = user_answers[low.strip()]
            prov.append({"question_text": label[:500], "profile_id": None, "answer": values[fid], "basis": "USER"})
            continue
        if any(w in low for w in HARD_STOP_WORDS):
            if required:
                blockers.append(("NEVER_ANSWER", label))
            continue
        if a is None or a.answer in (None, ""):
            kind = a.basis if a else "UNKNOWN"
            if required:
                blockers.append(("NEVER_ANSWER" if kind == "NEVER_ANSWER" else "ASK_ME", label))
            continue
        if a.profile_id:
            p = by_id.get(a.profile_id)
            if p is None or p["answer_type"] in BLOCKING_TYPES:
                if required:
                    blockers.append(("ASK_ME", label))
                continue
            basis = "PROFILE"
        elif a.basis == "GENERATED" and f.get("type") in ("textarea", "text"):
            basis = "GENERATED"
        elif a.answer in ("__RESUME__", "__COVER_LETTER__"):
            basis = "PROFILE"
        else:
            if required:
                blockers.append(("ASK_ME", label))
            continue
        values[fid] = a.answer
        prov.append({"question_text": label[:500], "profile_id": a.profile_id, "answer": a.answer[:2000], "basis": basis})
    return Resolved(values, prov, blockers)
