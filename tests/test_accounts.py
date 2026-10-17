from jobsearch import accounts


def test_password_is_strong_and_unique():
    a, b = accounts.new_password(), accounts.new_password()
    assert a != b and len(a) >= 20
    assert any(c.isdigit() for c in a) and any(c.isupper() for c in a) and any(c in "!@#$%" for c in a)


def test_portal_key_per_workday_tenant():
    k1 = accounts.portal_key("https://acme.wd1.myworkdayjobs.com/en-US/careers/job/123")
    k2 = accounts.portal_key("https://other.wd5.myworkdayjobs.com/x")
    assert k1 != k2 and k1.startswith("acme_wd1")


def test_only_required_consents_are_ticked():
    yes = ["I have read and agree to the Terms of Use", "I consent to the processing of my personal data per the Privacy Policy"]
    no = ["Send me job alerts and marketing emails", "Join our talent community newsletter", "I agree to receive SMS messages"]
    for label in yes:
        assert accounts.REQUIRED_CONSENT.search(label) and not accounts.MARKETING.search(label), label
    for label in no:
        assert accounts.MARKETING.search(label), label
