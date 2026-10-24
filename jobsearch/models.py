"""Structured outputs requested from the LLM. Validated before anything is stored or used."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Gap(BaseModel):
    requirement: str
    must_have: bool
    covered: bool = False


class FitScore(BaseModel):
    score: int = Field(ge=0, le=100)
    verdict: Literal["STRONG", "GOOD", "STRETCH", "SKIP"]
    matched_claims: list[str] = Field(default_factory=list, description="claim_ids from <evidence> that match the JD")
    gaps: list[Gap] = Field(default_factory=list)
    rationale: str = Field(description="max 3 sentences")
    seniority: str = Field(default="", description="e.g. SDE II, Senior, Staff")
    min_years_required: int | None = None
    sponsorship_or_relocation: Literal["yes", "no", "unknown", "not_needed"] = "unknown"
    shift_or_hours: str = Field(default="", description="any stated shift / working hours, else empty")
    skip_reason: str = ""


class Bullet(BaseModel):
    text: str
    claim_ids: list[str] = Field(min_length=1)


class ExperienceBlock(BaseModel):
    key: str = Field(description="experience key from <skeleton>")
    bullets: list[Bullet]


class SkillGroup(BaseModel):
    group: str
    items: list[str]


class TailoredResume(BaseModel):
    headline: str
    summary: str
    skills: list[SkillGroup]
    experience: list[ExperienceBlock]
    emphasis: str = Field(description="one sentence: what was emphasised for this JD")


class FieldAnswer(BaseModel):
    field_id: str
    profile_id: str | None = Field(default=None, description="id from <profile> the answer comes from, or null")
    answer: str | None = Field(default=None, description="value to enter/select; null when it must not be answered")
    basis: Literal["PROFILE", "GENERATED", "ASK_ME", "NEVER_ANSWER", "UNKNOWN"]
    note: str = ""


class FormAnswers(BaseModel):
    answers: list[FieldAnswer]


class CoverLetter(BaseModel):
    text: str = Field(description="<= 250 words, plain text, signed 'Muskan'")
    claim_ids: list[str]


class Contact(BaseModel):
    name: str
    role_title: str
    contact_type: Literal["RECRUITER", "HIRING_MANAGER", "REFERRER", "ALUMNI", "OTHER"]
    linkedin_url: str | None = None
    email: str | None = Field(default=None, description="ONLY if published verbatim on the source page")
    source_url: str


class ContactList(BaseModel):
    contacts: list[Contact]


class Email(BaseModel):
    subject: str
    body: str
    claim_ids: list[str] = Field(default_factory=list)


class ThreadClass(BaseModel):
    classification: Literal["ACKNOWLEDGMENT", "REJECTION", "ASSESSMENT", "INTERVIEW_REQUEST", "SCHEDULING",
                            "OFFER", "RECRUITER_OUTREACH", "REPLY", "OTHER"]
    company: str = ""
    role: str = ""
    deadline_iso: str | None = None
    proposed_slots_iso: list[str] = Field(default_factory=list)
    scheduling_link: str | None = None
    needs_owner: bool = Field(description="true if Muskan must personally respond or decide")
    summary: str = Field(description="one line, no personal data beyond company/role")


class Draft(BaseModel):
    text: str = Field(description="the drafted answer, plain text, no markdown")
