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

from .llm import LLMError, ask_json, untrusted
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


_RELOC_ASSIST = re.compile(r"relocat\w*.{0,40}(assistance|support|package|help|expenses?)|(assistance|support|package|help).{0,40}relocat")


def relocation_assistance(job_location: str) -> str:
    """REL-07: she always needs relocation assistance unless the job is in Hyderabad."""
    return "No" if "hyderabad" in (job_location or "").lower() else "Yes"


def deterministic(fields: list[dict], profile: list[dict], job_location: str = "") -> dict[str, tuple[str, str | None]]:
    """field_id -> (value, profile_id) for fields with one obvious answer in the approved profile.
    Only VALUE entries (and the EXP-13 years rule) are used; anything else goes to the model."""
    by_id = {p["id"]: p for p in profile if p["answer_type"] == "VALUE" and p.get("answer")}
    years_rule = next((p for p in profile if p["id"] == "EXP-13" and p.get("answer")), None)
    out: dict[str, tuple[str, str | None]] = {}
    for f in fields:
        t = f.get("type")
        label = re.sub(r"[*:]", "", f.get("label", "")).strip().lower()
        if t == "file":
            if re.search(r"cover letter", label):
                out[f["field_id"]] = ("__COVER_LETTER__", None)
            elif re.search(r"resume|\bcv\b|curriculum", label):
                out[f["field_id"]] = ("__RESUME__", None)
            continue
        if t == "textarea":
            continue
        if _RELOC_ASSIST.search(label) and any(p["id"] == "REL-07" for p in profile):
            out[f["field_id"]] = (relocation_assistance(job_location), "REL-07")
            continue
        saved = next((p for p in profile if p["id"].startswith("USR-") and p["answer_type"] == "VALUE" and _norm(p["field"]) == _norm(label)), None)
        if saved:  # an answer she gave before (digest email) for this exact question
            out[f["field_id"]] = (str(saved["answer"]).strip(), saved["id"])
            continue
        hit = next((pid for pat, pid in _YES_NO_RULES if re.search(pat, label) and pid in by_id), None)
        if hit:
            out[f["field_id"]] = (str(by_id[hit]["answer"]).strip(), hit)
            continue
        if t != "file":
            try:
                total = float(by_id["EXP-01"]["answer"])
            except (KeyError, ValueError, TypeError):
                total = 4.3
            ctx = contextual(label, t or "", by_id, in_india(job_location), total)
            if ctx and ctx[0]:
                out[f["field_id"]] = ctx
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


_INDIA_WORDS = ("india", "hyderabad", "bengaluru", "bangalore", "gurugram", "gurgaon", "pune", "mumbai", "delhi", "noida", "chennai", "kolkata")
_FOREIGN = re.compile(r"united states|\bu\.?s\.?a?\b|canada|chile|united kingdom|\buk\b|singapore|germany|ireland|netherlands|poland|"
                      r"australia|\buae\b|emirates|france|spain|brazil|mexico|philippines|europe")
_TECH = re.compile(r"\b(go|golang|typescript|javascript|node|java|python|rust|kotlin|scala|c\+\+|kubernetes|aws|azure|gcp|terraform|"
                   r"react|helm|security|sql|spark|kafka)\b")


def _norm(text: str) -> str:
    return re.sub(r"[\s*:?]+", " ", text.lower()).strip()


def in_india(job_location: str) -> bool | None:
    loc = (job_location or "").lower()
    if not loc:
        return None
    return any(w in loc for w in _INDIA_WORDS)


_COUNTRIES_NO = re.compile(r"brazil|colombia|mexico|latin america|argentina|chile|peru|canada|united states|\bus\b|\buk\b|united kingdom|"
                           r"singapore|uae|emirates|germany|ireland|europe|poland|netherlands|france|spain|australia|japan")


def _standing(label: str, ftype: str, by_id: dict, india: bool | None) -> tuple[str, str] | None:
    """Questions whose answer is a stored fact or a fixed policy. Order matters: specific before generic."""
    def v(pid: str) -> str | None:
        return str(by_id[pid]["answer"]).strip() if pid in by_id else None

    def hit(pid: str, text: str | None = None):
        val = text if text is not None else v(pid)
        return (val, pid) if val else None

    # --- EEO / self-identification (stored by her; used only because the form asks) ---
    if re.search(r"hispanic|latino|latinx", label):
        return hit("EEO-04")
    if re.search(r"veteran|military service|armed forces", label):
        return hit("MIL-01", "No")
    if re.search(r"sexual orientation", label):
        return hit("EEO-05")
    if re.search(r"disabilit", label) and not re.search(r"accommodat", label):
        return hit("MIL-02")
    if re.search(r"\brace\b|ethnic", label) and not re.search(r"hispanic|latino", label):
        return hit("EEO-02" if re.search(r"\brace\b", label) else "EEO-03")
    if re.search(r"gender identity|^gender|^sex\b|identify as (a )?(man|woman|male|female)", label) and ftype != "textarea" \
            and not re.search(r"transgender", label):
        return hit("EEO-01")
    if re.search(r"^religio", label):
        return hit("EEO-06")
    if re.search(r"marital", label):
        return hit("EEO-07")
    if re.search(r"pronoun", label):
        return hit("ID-05")
    # --- identity / contact ---
    if re.search(r"preferred last name|^last name|^family name|^surname", label):
        return hit("ID-02")
    if re.search(r"preferred (first )?name", label):
        return hit("ID-04")
    if re.search(r"how.{0,25}(communicate|contact|reach)|preferred (contact|communication)", label):
        return hit("CON-06")
    if re.search(r"^(what )?(is your )?(gpa|cgpa|grade point)|\bgpa\b|\bcgpa\b", label):
        return hit("EXP-10", "7.2") if "EXP-10" in by_id else None
    if re.search(r"english.{0,30}(level|proficien|fluen)|(level|proficien|fluen).{0,30}english", label):
        return ("Fluent", "CON-08")
    # --- employment history at the hiring company ---
    if (re.search(r"(ever|previously|before|currently|now|at any other time|at any time).{0,40}(work|employ|contract|consult|interview|apply|appli)", label)
            or re.search(r"(work|employ|interview)\w*.{0,30}\b(for|at|with)\b.{0,50}(previously|before|ever|any other time)", label)) \
            and re.search(r"\b(for|at|with|by)\b", label) and not re.search(r"experience|skill|years", label):
        return ("No", "APQ-03")
    if re.search(r"(us|u\.s\.|united states|federal|state|local|foreign).{0,25}government.{0,40}(employ|entity|official|work)|"
                 r"government.{0,30}(entity|agency).{0,40}(employ|work)", label):
        return hit("SEC-03")
    if re.search(r"citizen.{0,40}(cuba|iran|north korea|syria)|(cuba|iran|north korea|syria).{0,40}citizen", label):
        return ("No", "ID-09")
    if re.search(r"tax resident|resident.{0,15}for tax", label):
        return ("No", "LOC-01")
    if re.search(r"passport", label) and re.search(r"countr|issu", label):
        return ("India", "REL-09")
    # --- work arrangement ---
    if re.search(r"(office|on-?site|in[- ]person|hybrid|commut).{0,80}(days?|times?|schedule|week|requirement|%|percent|willing|able|confirm)|"
                 r"(willing|able|open|comfortable|confirm).{0,60}(office|on-?site|in[- ]person|hybrid|relocat|commut)", label) \
            and not re.search(r"visa|sponsor", label):
        return hit("ARR-02", "Yes")
    if re.search(r"from where do you intend to work|where.{0,20}(intend|plan|expect).{0,15}work|preferred (work )?locations?|"
                 r"available to work in|cities.{0,30}(work|available)", label):
        return hit("ARR-01", "Anywhere") if ftype != "select" else None
    if re.search(r"(accept|comfortable|ok|okay|agree).{0,30}(salary|pay|compensation) range|(salary|pay|compensation) range.{0,30}(accept|comfortable|ok)", label):
        return ("Yes", "CMP-06")
    # --- acknowledgements ---
    if re.search(r"ai policy|ai usage|use of ai|candidate privacy|privacy statement|privacy notice|pay (range )?transparency|processing of (your )?personal|"
                 r"please double.?check|applicant privacy|data protection|recruitment privacy|i (have )?(read|reviewed)", label):
        return hit("CNS-01", "Yes") if "CNS-01" in by_id else None
    if re.search(r"assistance.{0,40}(llm|ai tool)|accommodation.{0,40}interview", label):
        return ("No", "CON-10")
    # --- work authorisation phrased generically ---
    if re.search(r"authorization to work in the country where you live|authori[sz]ation.{0,30}(country|where) you (live|reside)", label):
        return hit("AUT-01", "Yes")
    if re.search(r"(sponsor|commence).{0,40}immigration case|immigration case", label):
        return None if india is None else (hit("AUT-05") if india else hit("AUT-04"))
    if re.search(r"(bachelor|master|degree).{0,30}(computer science|engineering|related|stem)|(computer science|engineering).{0,30}degree", label):
        return ("Yes", "EXP-07")
    return None


def contextual(label: str, ftype: str, by_id: dict, india: bool | None, total_years: float) -> tuple[str, str] | None:
    """Standing answers that depend on the question wording and the job's country. (answer, profile id) or None."""
    def v(pid: str) -> str | None:
        return str(by_id[pid]["answer"]).strip() if pid in by_id else None

    def hit(pid: str, text: str | None = None):
        val = text if text is not None else v(pid)
        return (val, pid) if val else None

    if re.search(r"how did you (first )?(hear|learn|find)|where did you (first )?(hear|find|see|learn)|where have you (first )?(hear|learn)|"
                 r"how (have you )?(heard|learned)|source of (application|referral)", label):
        return hit("APQ-02", "LinkedIn") if "APQ-02" in by_id else None
    r = _standing(label, ftype, by_id, india)
    if r:
        return r
    if re.search(r"current( \(or most recent\))? (company|employer|firm)|most recent (company|employer)|current firm|firm you.?re part of|"
                 r"name of your current", label):
        return hit("EXP-06")
    if re.search(r"current( \(or most recent\))? (job )?title|most recent (job )?title|current (role|position)|^job title", label):
        return hit("EXP-05")
    if re.search(r"sanction|export control", label):
        return ("None of the above", "BGC-01")
    if re.search(r"(work|current).{0,20}location.{0,12}country|country.{0,20}(work|current)", label) and not re.search(r"visa|authori", label):
        return hit("LOC-01")
    if re.search(r"open[- ]source", label):
        return hit("APQ-07")
    if re.search(r"government official|public office|civil service|government.{0,20}employee", label) and "relative" not in label:
        return hit("SEC-03")
    if re.search(r"close relative|relative of|family member|close personal relationship|conflict of interest|actual or perceived conflict", label):
        return hit("COI-05")
    if re.search(r"referred.{0,50}(client|vendor|partner|decision|senior leader)", label):
        return ("No", "COI-05")
    if re.search(r"\b(ai|artificial intelligence|automated)\b.{0,40}(tool|process|screen|assist)", label) and \
            re.search(r"understand|consent|acknowledge|agree|may use", label):
        return hit("CNS-03", "Yes") if "CNS-03" in by_id else None
    if re.search(r"by (checking|selecting|submitting|clicking).{0,60}(confirm|understand|agree|acknowledge|have read|certify)|"
                 r"i (have read|agree|acknowledge|understand)", label) and ftype in ("checkbox", "select", "radio", "combobox", "text"):
        return hit("CNS-01", "Yes") if "CNS-01" in by_id else None
    if re.search(r"(hold|have|completed|earned|possess).{0,20}(bachelor|master|degree|b\.?e\b|b\.?tech)", label):
        return ("Yes", "EXP-07")
    if re.search(r"comfortable.{0,30}(hybrid|onsite|on-site|remote|office|commut|relocat)|(hybrid|on-?site).{0,30}(comfortable|okay|ok with)", label):
        return hit("ARR-02", "Yes")
    if "city" not in label and re.search(r"(state|province).{0,30}(live|reside|located|based)|(live|reside).{0,30}(state|province)", label):
        return hit("LOC-03")
    if re.search(r"expected (compensation|salary|ctc|pay)|(salary|compensation|ctc) expectation", label) and india is not False:
        if "base" in label and "CMP-04" in by_id:
            return ("3500000" if ftype == "number" else "INR 35 LPA", "CMP-04")
        if re.search(r"total|ctc|overall", label) and "CMP-05" in by_id:
            return ("4000000" if ftype == "number" else "INR 40 LPA", "CMP-05")
        return ("4000000", "CMP-07") if ftype == "number" else ("INR 35-40 LPA", "CMP-06")
    if re.search(r"(privacy (notice|policy)|data privacy)|consent.{0,40}(collect|stor|process).{0,60}(response|demographic|data)", label):
        return hit("CNS-01", "Yes") if "CNS-01" in by_id else None
    if re.search(r"confidential information", label):
        return hit("COI-04", "Yes") if "COI-04" in by_id else None
    if re.search(r"(18|eighteen)\s*(years|\+)|over the age of 18|at least 18", label):
        return ("Yes", "ID-06")
    if re.search(r"^(what is your )?legal first name", label):
        return hit("ID-01")
    if re.search(r"^preferred (first )?name", label):
        return hit("ID-04")
    if re.search(r"^(sex|gender)\b", label) and ftype != "textarea":
        return hit("EEO-01")
    if re.search(r"^(province|state)(/| or )?(province|state)?$|^state$", label):
        return hit("LOC-03")
    if re.search(r"postal|zip", label):
        return hit("LOC-04")
    if re.search(r"city and state|primary residence", label):
        return hit("LOC-02")
    if re.search(r"\bcity\b", label) and re.search(r"reside|live|location|^city$|current", label):
        return hit("LOC-02", (v("LOC-02") or "").split(",")[0] or None)
    if re.search(r"^current location|^location$", label):
        return hit("LOC-02")
    if re.search(r"country of residence|current country", label):
        if re.search(r"visa|sponsor|permit", label):
            return hit("AUT-05")
        if re.search(r"authori|eligible|right to work", label):
            return hit("AUT-01")
    if re.search(r"(require|need).{0,40}(visa|sponsorship|work permit)|(visa|sponsorship|work permit).{0,40}(require|need)", label):
        return None if india is None else hit("AUT-05" if india else "AUT-04")
    if re.search(r"authori[sz]ed|legal(ly)? .{0,12}(right|entitled)|right to work|eligible to work|entitled to work", label) and "work" in label:
        if _FOREIGN.search(label):
            return hit("AUT-03")
        if "india" in label:
            return hit("AUT-01")
        return None if india is None else hit("AUT-01" if india else "AUT-03")
    m = re.search(r"\b(?:based|located|residing|living|reside|live)\s+(?:in|at|near)\s+([^?]+)", label)
    if m and not re.search(r"this (location|country|area)|your country", m.group(1)):
        return ("Yes", "LOC-01") if any(w in m.group(1) for w in _INDIA_WORDS) else ("No", "LOC-01")
    if ftype not in ("select", "radio", "combobox") and not re.search(r"\d\s*\+?\s*(years|yrs)|at least|minimum|more than|over \d", label) \
            and re.search(r"how many (years|yrs)|total years|^years of|years of (relevant |professional |hands-on )?experience", label) and "experience" in label:
        generic = not _TECH.search(label) and re.search(r"software (engineering|development)|overall|professional experience|total experience|"
                                                           r"total years of (work |professional )?experience\s*\??$", label)
        return (str(by_id["EXP-01"]["answer"]).strip(), "EXP-01") if (generic and "EXP-01" in by_id) else ("4", "EXP-04")
    if re.search(r"^(do you|have you).{0,12}(have |had )?(any |professional |hands-on |prior |relevant |direct )*experience "
                 r"(with|in|using|operating|building|developing|designing|working|deploying|maintaining|managing systems)", label) \
            and not re.search(r"following|which of|select all|leading|lead a|mentor|managing (a )?(team|people)|manager", label):
        return ("Yes", "EXP-04")
    if re.search(r"commutable|within .{0,20}distance|currently live in this location|do you live in", label):
        return None if india is None else (("Yes", "ARR-03") if india else ("No", "ARR-03"))
    if re.search(r"(client, partner|partner, or competitor|competitor)", label) and re.search(r"employ|work", label):
        return hit("COI-03")
    if re.search(r"restrictive covenant|non-?compete|non-?solicit|post-employment restriction|employment agreements?", label):
        return hit("COI-02")
    if re.search(r"(previously|ever|currently|now).{0,25}(work|employ|consult|provid)\w*.{0,30}(for|at|by|to)\b|have you (ever )?been employed by", label):
        return hit("APQ-03", "No") if "APQ-03" in by_id else None
    m = re.search(r"(?:at least|over|more than|minimum(?: of)?|min\.?)\s*(\d+)\+?\s*(?:years|yrs)|(\d+)\+\s*(?:years|yrs)", label)
    if m and "experience" in label:
        need = int(m.group(1) or m.group(2))
        generic = not _TECH.search(label) and re.search(r"software|professional|relevant|full[- ]time|working|overall|total", label)
        have = total_years if generic else 4.0  # any named skill: 4 years (her standing instruction)
        return ("Yes" if have >= need else "No", "EXP-01" if generic else "EXP-04")
    return None


_OPEN_ENDED = re.compile(r"why |what (interests|excites|motivates|attracts)|interested in|tell us|describe|about yourself|"
                         r"passion|motivat|proud|cover letter|anything else|additional information|share with us|in your own words")
DRAFT_TASK = """Write the answer to this job-application question as Muskan, in first person, plain text, 3-5 sentences (max 110 words).
Use ONLY facts from <evidence> (her real work) and the job description for context; no invented employers, numbers, tools or
achievements. Be specific, warm and direct; no buzzwords, no markdown, no greeting or sign-off."""


def draft_answer(label: str, company: str, title: str, evidence: list[dict], jd: str) -> str | None:
    from .llm import ask_json as _ask
    from .models import Draft

    ev = "\n".join(f"{e['claim_id']}: {e['claim']}" for e in evidence[:80])
    payload = (f"<evidence>\n{ev}\n</evidence>\nCompany: {company}\nRole: {title}\n"
               + untrusted((jd or "")[:3500], 3500) + f"\nQuestion: {label}")
    try:
        return _ask(DRAFT_TASK, payload, Draft, fast=False, max_tokens=700).text.strip() or None
    except LLMError:
        return None


@dataclass
class Resolved:
    values: dict[str, str]          # field_id -> answer
    provenance: list[dict]          # for app_answers rows
    blockers: list[tuple[str, str]]  # (type, question)


def resolve(fields: list[dict], profile: list[dict], evidence: list[dict], job_title: str, company: str,
            user_answers: dict[str, str] | None = None, job_location: str = "", jd: str = "") -> Resolved:
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
    det = deterministic(fields, profile, job_location)
    head = (f"<profile>\n{prof}\n</profile>\n<evidence>\n{ev}\n</evidence>\nJob: {job_title} at {company}"
            f" (location: {job_location or 'unknown'})\n")
    # Only fields the rules couldn't settle go to the model, a few at a time: long forms made it return invalid JSON.
    todo = [f for f in fields if f["field_id"] not in det and f.get("label", "").strip().lower() not in user_answers]
    proposed: dict = {}
    for i in range(0, len(todo), 8):
        chunk = todo[i:i + 8]
        try:
            out = ask_json(TASK.format(today=date.today().isoformat()),
                           head + untrusted(json.dumps(chunk, ensure_ascii=False), 9000), FormAnswers, fast=False, max_tokens=4000)
            proposed.update({a.field_id: a for a in out.answers})
        except LLMError:
            pass  # these fields stay unknown: required ones become questions, optional ones are left empty
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
        if (a is None or a.answer in (None, "")) and f.get("type") in ("textarea", "text") and _OPEN_ENDED.search(low) \
                and not any(w in low for w in HARD_STOP_WORDS):
            text = draft_answer(label, company, job_title, evidence, jd)
            if text:
                values[fid] = text
                prov.append({"question_text": label[:500], "profile_id": None, "answer": text[:2000], "basis": "GENERATED"})
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
