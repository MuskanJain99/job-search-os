from jobsearch import human


def test_parse_digest_reply_numbered_and_ordered():
    qs = ["Do you hold a security clearance?", "Preferred pronouns?"]
    out, skip, save = human.parse_reply("1: No\n2: -", qs)
    assert out[qs[0]] == "No" and out[qs[1]] == "-"
    out2, _, _ = human.parse_reply("No\nYes", qs)
    assert out2[qs[1]] == "Yes"


def test_wait_for_human_never_waits(monkeypatch):
    from jobsearch import laptop

    calls = []

    class P:
        url = "https://x"
        def is_closed(self): return False
        def close(self): calls.append("closed")
        def evaluate(self, js): return ["Visa sponsorship?"]

    class R:
        def bump(self, *_a, **_k): pass

    monkeypatch.setattr(laptop.db, "open_blocker", lambda *a, **k: calls.append(("blocker", a[2])))
    monkeypatch.setattr(laptop.db, "set_application_status", lambda *a, **k: calls.append(("status", a[2])))
    assert laptop.wait_for_human(R(), P(), "app1", "co", what="captcha") is False
    assert ("status", "SKIPPED") in calls and ("blocker", "CAPTCHA") in calls
    calls.clear()
    assert laptop.wait_for_human(R(), P(), "app1", "co", what="fields") is False
    assert ("blocker", "ASK_ME") in calls and ("status", "SKIPPED") not in calls
