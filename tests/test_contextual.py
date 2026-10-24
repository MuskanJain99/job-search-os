from jobsearch.answers import deterministic

PROFILE = [{"id": i, "answer_type": "VALUE", "field": f, "answer": a, "applies_when": ""} for i, f, a in [
    ("APQ-02", "hear", "Job board — LinkedIn"), ("APQ-03", "prev", "No"), ("EXP-01", "exp", "4.3"),
    ("EXP-05", "title", "Software Engineer"), ("EXP-06", "emp", "IBM"), ("AUT-01", "a", "Yes"), ("AUT-03", "b", "No"),
    ("AUT-04", "c", "Yes"), ("AUT-05", "d", "No"), ("LOC-02", "city", "Hyderabad, Telangana"), ("LOC-03", "st", "Telangana"),
    ("LOC-04", "zip", "500055"), ("CNS-01", "cns", "Yes / Yes / Yes"), ("COI-02", "nc", "No"), ("COI-03", "comp", "No"),
    ("COI-04", "conf", "Yes"), ("ID-04", "pf", "Muskan"), ("ID-06", "dob", "x"), ("EEO-01", "sex", "Female"),
    ("USR-AAAAAA", "What was your undergraduate GPA?", "7.2")]]


def ans(label, loc="", t="text"):
    out = deterministic([{"field_id": "f", "label": label, "type": t, "options": []}], PROFILE, loc)
    return out.get("f", (None,))[0]


def test_digest_questions_now_answered():
    assert ans("How did you hear about this opportunity?*") == "LinkedIn"
    assert ans("Current Company*") == "IBM" and ans("Current Title") == "Software Engineer"
    assert ans("What city do you reside in? *") == "Hyderabad"
    assert ans("In what city and state is your primary residence? (e.g. San Jose, CA)") == "Hyderabad, Telangana"
    assert ans("What was your undergraduate GPA?*") == "7.2"
    assert ans("Are you 18 years of age or older?*") == "Yes"
    assert ans("Are you authorized to work in the United States without Visa Sponsorship or EAD?*") == "No"
    assert ans("Are you authorized to work in Chile?*") == "No"
    assert ans("Are you currently eligible to work in your country of residence?*") == "Yes"
    assert ans("Do you now or in the future require visa sponsorship to continue working in your country of residence?*") == "No"
    assert ans("Will you now or in the future require sponsorship for work authorization?", "Dublin, Ireland") == "Yes"
    assert ans("Will you now or in the future require sponsorship for work authorization?", "Hyderabad") == "No"
    assert ans("Will you now or in the future require sponsorship for work authorization?") is None  # unknown country
    assert ans("Are you bound by any restrictive covenants (non-compete, non-solicitation)?*") == "No"
    assert ans("Are you currently employed by a client, partner, or competitor of Addepar?*") == "No"
    assert ans("Do you currently work for, or have you previously worked for Zscaler?*") == "No"
    assert ans("Do you have at least 9 years of full time relevant working experience?") == "No"
    assert ans("Do you have over 3 years over professional software engineering experience?*") == "Yes"
    assert ans("Do you have 4+ years of Golang experience?") == "Yes"  # any named skill = 4 years
    assert ans("Job Applicant Data Privacy Notice*") == "Yes"
    assert ans("Sex*") == "Female" and ans("Preferred First Name*") == "Muskan"
    assert ans("Postal/Zip Code") == "500055" and ans("Province/State") == "Telangana"


def test_unknowns_stay_unknown():
    assert ans("Website") is None and ans("Address Line 1") is None and ans("Home Address*") is None


def test_any_skill_is_four_years():
    assert ans("Do you have minimum 4 years of experience in Spark?") == "Yes"
    assert ans("Do you have a minimum of 5 years of experience in Cloud?") == "No"
    assert ans("Do you have 4+ years of experience designing and scaling ML pipelines?") == "Yes"
