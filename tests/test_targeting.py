from jobsearch import targeting as t

EXCL = ["Principal", "Staff", "Director", "Manager", "Intern", "Java full stack only"]


def test_titles_mid_level_engineering_pass():
    for title in ["Software Engineer", "Senior Backend Engineer (Go)", "SDE II", "AI Engineer", "Machine Learning Engineer",
                  "Platform Engineer, Infrastructure", "Full Stack Engineer", "Software Developer - Python"]:
        assert t.title_ok(title, EXCL)[0], title


def test_titles_rejected():
    assert t.title_ok("Staff Software Engineer", EXCL) == (False, "too_senior")
    assert t.title_ok("Engineering Manager", EXCL)[0] is False
    assert t.title_ok("Software Engineering Intern", EXCL) == (False, "too_junior")
    assert t.title_ok("Product Designer", EXCL) == (False, "not_engineering")
    assert t.title_ok("Sales Engineer", EXCL)[1] == "off_domain"
    assert t.title_ok("Principal Backend Engineer", EXCL)[0] is False


def test_locations():
    assert t.classify_location("Bengaluru, Karnataka, India", None).india_eligible
    assert t.classify_location("Hyderabad", False).country == "India"
    remote_any = t.classify_location("Remote", True)
    assert remote_any.eligible and remote_any.remote_policy == "REMOTE"
    us_remote = t.classify_location("Remote - US", True)
    assert not us_remote.eligible
    london = t.classify_location("London, United Kingdom", False)
    assert london.country == "UK" and london.eligible and not london.india_eligible
    berlin = t.classify_location("Berlin, Germany", False)
    assert berlin.country == "Europe" and berlin.eligible


def test_location_priority():
    order = ["Hyderabad, Telangana, India", "Bangalore, India", "Gurgaon, Haryana", "Pune, India", "Chennai, India",
             "Dubai, UAE", "Singapore", "London, UK", "Amsterdam, Netherlands", "Austin, Texas"]
    ranks = [t.location_rank(x, t.classify_location(x, False).country) for x in order]
    assert ranks == sorted(ranks) and ranks[0] == 1 and ranks[-1] == 10
    assert t.classify_location("Austin, Texas", False).eligible  # not searched, but applied to if it turns up


def test_decide_all_eligible_ignores_fit():
    loc = t.classify_location("Bengaluru, India", None)
    ok, why = t.decide("ALL_ELIGIBLE", "SKIP", 20, 70, loc, 4, (2, 8), "not_needed", True)
    assert ok and why == "eligible"
    ok, why = t.decide("FIT_THRESHOLD", "SKIP", 20, 70, loc, 4, (2, 8), "not_needed", True)
    assert not ok


def test_decide_eligibility_rules():
    loc = t.classify_location("Bengaluru, India", None)
    assert t.decide("ALL_ELIGIBLE", "GOOD", 75, 70, loc, 10, (2, 8), "not_needed", True)[1] == "years_required_10"
    assert t.decide("ALL_ELIGIBLE", "GOOD", 75, 70, loc, None, (2, 8), "not_needed", False)[1] == "shift_rule_SCH-12"
    london = t.classify_location("London, UK", False)
    assert t.decide("ALL_ELIGIBLE", "GOOD", 75, 70, london, 5, (2, 8), "no", True)[1] == "no_sponsorship"
    assert t.decide("ALL_ELIGIBLE", "GOOD", 75, 70, loc, 5, (2, 8), "x", True, "requires clearance")[0] is False


def test_dedupe_key():
    assert t.dedupe_key("acme", "greenhouse:123") == "acme|greenhouse:123"
    assert t.dedupe_key("acme", None, "Senior Engineer!", "Bengaluru") == "acme|senior engineer|bengaluru"
