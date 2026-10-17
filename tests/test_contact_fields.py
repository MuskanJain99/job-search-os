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
