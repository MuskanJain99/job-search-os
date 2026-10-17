from jobsearch import human

QS = ["Do you know anyone at Acme?", "Years of Java experience", "Willing to work from the Pune office?"]


def test_numbered_reply():
    ans, skip, save = human.parse_reply("1: No\n2) 0 years\n3. Yes\nsave", QS)
    assert ans == {QS[0]: "No", QS[1]: "0 years", QS[2]: "Yes"} and save and not skip


def test_in_order_reply_and_partial():
    ans, _, _ = human.parse_reply("No\n0", QS)
    assert ans == {QS[0]: "No", QS[1]: "0"}


def test_skip():
    _, skip, _ = human.parse_reply("skip", QS)
    assert skip


def test_quoted_history_removed():
    text = "1: No\n2: 1\n3: Yes\n\nOn Sat, 17 Oct 2026 someone wrote:\n> 1. Do you know anyone"
    assert "Do you know" not in human._strip_quoted(text)
