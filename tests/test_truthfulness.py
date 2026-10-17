"""The code — not the LLM — enforces that resumes and form answers only use approved facts."""

from jobsearch import answers, tailor
from jobsearch.models import Bullet, ExperienceBlock, FieldAnswer, FormAnswers, SkillGroup, TailoredResume

EVIDENCE = [
    {"claim_id": "P21-01", "role": "IBM – Cloud Security & Compliance", "claim": "Migrated 8M+ documents across 12 services",
     "metrics": "0 data-integrity incidents", "technologies": ["PostgreSQL", "Go"]},
    {"claim_id": "E1-01", "role": "Petasense – Full-Stack Engineering Intern", "claim": "Shipped features in React.js and Flask",
     "metrics": None, "technologies": ["React.js", "Flask"]},
]
SKELETON = {
    "experience": [
        {"key": "ibm", "company": "IBM", "title": "Software Engineer II", "dates": "2022 – 2026", "location": "Bengaluru",
         "evidence_roles": ["IBM – Cloud Security & Compliance"]},
        {"key": "petasense", "company": "Petasense", "title": "Intern", "dates": "2022", "location": "Hyderabad",
         "evidence_roles": ["Petasense – Full-Stack Engineering Intern"]},
    ],
    "education": [{"institution": "BITS Pilani", "degree": "M.Sc. + B.E.", "dates": "2017 – 2022"}],
}


def _draft(bullets_ibm, skills):
    return TailoredResume(headline="Backend engineer", summary="x", emphasis="x",
                          skills=[SkillGroup(group="Lang", items=skills)],
                          experience=[ExperienceBlock(key="ibm", bullets=bullets_ibm)])


def test_unknown_claim_ids_and_wrong_role_are_dropped():
    draft = _draft([Bullet(text="Migrated 8M+ documents across 12 services", claim_ids=["P21-01"]),
                    Bullet(text="Built Java microservices", claim_ids=["FAKE-1"]),
                    Bullet(text="Shipped React features at IBM", claim_ids=["E1-01"])], ["Go"])
    content, used = tailor.enforce(draft, EVIDENCE, SKELETON)
    texts = [b["text"] for e in content["experience"] for b in e["bullets"]]
    assert texts == ["Migrated 8M+ documents across 12 services"]
    assert used == ["P21-01"]


def test_invented_numbers_are_dropped():
    draft = _draft([Bullet(text="Migrated 50M documents across 12 services", claim_ids=["P21-01"])], ["Go"])
    content, _ = tailor.enforce(draft, EVIDENCE, SKELETON)
    assert content["experience"] == []


def test_skills_limited_to_cited_evidence():
    draft = _draft([Bullet(text="Migrated 8M+ documents across 12 services", claim_ids=["P21-01"])], ["Go", "Java", "Kubernetes"])
    content, _ = tailor.enforce(draft, EVIDENCE, SKELETON)
    assert content["skills"] == [{"group": "Lang", "items": ["Go"]}]


def test_employer_facts_come_from_skeleton():
    draft = _draft([Bullet(text="Migrated 8M+ documents across 12 services", claim_ids=["P21-01"])], ["Go"])
    content, _ = tailor.enforce(draft, EVIDENCE, SKELETON)
    assert content["experience"][0]["title"] == "Software Engineer II"
    assert content["education"] == SKELETON["education"]


PROFILE = [
    {"id": "ID-03", "field": "Full name", "answer": "Test Person", "answer_type": "VALUE", "applies_when": None},
    {"id": "APQ-05", "field": "Friends at company", "answer": "—", "answer_type": "ASK_ME", "applies_when": None},
    {"id": "DEC-01", "field": "Criminal record", "answer": "No", "answer_type": "NEVER_ANSWER_AUTOMATICALLY", "applies_when": None},
]


def test_answer_rules(monkeypatch):
    fields = [
        {"field_id": "name", "label": "Full name", "type": "text", "required": True},
        {"field_id": "friends", "label": "Do you know anyone here?", "type": "text", "required": True},
        {"field_id": "crime", "label": "Have you ever been convicted of a crime?", "type": "select", "required": True},
        {"field_id": "java", "label": "Years of Java", "type": "text", "required": True},
        {"field_id": "opt", "label": "Optional: favourite colour", "type": "text", "required": False},
    ]
    fake = FormAnswers(answers=[
        FieldAnswer(field_id="name", profile_id="ID-03", answer="Test Person", basis="PROFILE"),
        FieldAnswer(field_id="friends", profile_id="APQ-05", answer="No", basis="PROFILE"),   # model tried to answer an ASK_ME
        FieldAnswer(field_id="crime", profile_id="DEC-01", answer="No", basis="PROFILE"),
        FieldAnswer(field_id="java", profile_id=None, answer="5", basis="UNKNOWN"),          # invented
    ])
    monkeypatch.setattr(answers, "ask_json", lambda *a, **k: fake)
    res = answers.resolve(fields, PROFILE, EVIDENCE, "SWE", "Acme")
    assert res.values == {"name": "Test Person"}
    kinds = {q for _, q in res.blockers}
    assert {"Do you know anyone here?", "Have you ever been convicted of a crime?", "Years of Java"} <= kinds
    assert "Optional: favourite colour" not in kinds
