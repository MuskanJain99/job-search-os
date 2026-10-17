from jobsearch.answers import deterministic

PROFILE = [
    {"id": "CON-01", "answer_type": "VALUE", "answer": "+91", "field": "Phone country code"},
    {"id": "CON-02", "answer_type": "VALUE", "answer": "9000052756", "field": "Phone number"},
    {"id": "CON-03", "answer_type": "VALUE", "answer": "me@example.com", "field": "Email"},
    {"id": "ID-01", "answer_type": "VALUE", "answer": "Muskan", "field": "Legal first name"},
    {"id": "ID-02", "answer_type": "VALUE", "answer": "Nahata", "field": "Legal last name"},
    {"id": "LOC-01", "answer_type": "VALUE", "answer": "India", "field": "Country of residence"},
    {"id": "ID-06", "answer_type": "ASK_ME", "answer": "x", "field": "Date of birth"},
]


def _f(label, t="text"):
    return {"field_id": label, "label": label, "type": t, "required": True, "refs": [label]}


def test_contact_fields_filled_from_profile():
    fields = [_f("First Name *"), _f("Last Name"), _f("Email*"), _f("Phone"), _f("Country"), _f("Phone country code", "select"),
              _f("Date of birth"), _f("Why do you want to join?", "textarea")]
    out = deterministic(fields, PROFILE)
    assert out["First Name *"][0] == "Muskan"
    assert out["Last Name"][0] == "Nahata"
    assert out["Email*"][0] == "me@example.com"
    assert out["Phone"][0] == "9000052756"
    assert out["Country"][0] == "India"
    assert out["Phone country code"][0] == "+91"
    assert "Date of birth" not in out and "Why do you want to join?" not in out


def test_experience_ranges():
    from jobsearch import ranges

    assert ranges.pick(["0-2 years", "2-4 years", "4-6 years", "6+ years"], 4.3) == 2
    assert ranges.pick(["0-1", "1-3", "3-5", "5+"], 4.3) == 2
    assert ranges.pick(["Less than 2 years", "2-5 years", "More than 5 years"], 4.3) == 1
    assert ranges.pick(["1", "2", "3", "4", "5+"], 4.3) == 3
    assert ranges.pick(["Select...", "0 - 2", "3 - 5", "6 - 10", "10+"], 4.3) == 2


def test_standing_yes_answers_and_years_marker():
    prof = PROFILE + [
        {"id": "EXP-12", "answer_type": "VALUE", "answer": "Yes", "field": "backend"},
        {"id": "CNS-06", "answer_type": "VALUE", "answer": "Yes", "field": "consent"},
        {"id": "EXP-13", "answer_type": "RULE", "answer": "4.3", "field": "ranges"},
    ]
    fields = [
        {"field_id": "a", "label": "Do you have experience in Backend Development?", "type": "radio", "options": ["Yes", "No"], "refs": ["a1", "a2"]},
        {"field_id": "b", "label": "Do you consent to your application information being shared between Acme and organisations for recruitment purposes?", "type": "checkbox", "options": ["I agree"], "refs": ["b1"]},
        {"field_id": "c", "label": "Years of experience*", "type": "select", "options": ["0-2", "2-4", "4-6", "6+"], "refs": ["c"]},
        {"field_id": "d", "label": "Years of experience in Java", "type": "select", "options": ["0-2", "2-4", "4-6"], "refs": ["d"]},
    ]
    out = deterministic(fields, prof)
    assert out["a"][0] == "Yes" and out["b"][0] == "Yes"
    assert out["c"][0] == "__YEARS__:4.3"
    assert "d" not in out  # skill-specific years go to the model (and to ASK_ME if not on the resume)
