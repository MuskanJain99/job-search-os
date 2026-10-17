from jobsearch.log import redact
from jobsearch.scoring import shift_ok
from jobsearch.sources import aggregators, ats


def test_parse_job_url():
    assert ats.parse_job_url("https://job-boards.greenhouse.io/gitlab/jobs/8716143002?gh_src=x") == ("greenhouse", "gitlab", "8716143002")
    assert ats.parse_job_url("https://jobs.lever.co/hevodata/0b1c2d3e-1111-2222-3333-444455556666/apply")[0] == "lever"
    assert ats.parse_job_url("https://jobs.ashbyhq.com/acme/0b1c2d3e-1111-2222-3333-444455556666")[1] == "acme"
    assert ats.parse_job_url("https://www.linkedin.com/jobs/view/123") is None


def test_clean_url_strips_tracking():
    assert ats.clean_url("https://job-boards.greenhouse.io/x/jobs/1?gh_src=abc&utm_source=li#app") == "https://job-boards.greenhouse.io/x/jobs/1"


def test_apply_channel_routing():
    assert aggregators.apply_channel("https://boards.greenhouse.io/acme/jobs/1") == "greenhouse"
    assert aggregators.apply_channel("https://www.linkedin.com/jobs/view/1") == "linkedin"
    assert aggregators.apply_channel("https://acme.wd1.myworkdayjobs.com/x") == "workday"
    assert aggregators.apply_channel("https://careers.acme.com/job/1") == "company_site"
    assert "greenhouse" in aggregators.CLOUD_SUBMIT and "linkedin" in aggregators.LAPTOP_SUBMIT


def test_html_to_text():
    assert "• Go" in ats.html_to_text("&lt;ul&gt;&lt;li&gt;Go&lt;/li&gt;&lt;/ul&gt;")


def test_shift_rule():
    assert shift_ok("", "HYBRID", "")
    assert not shift_ok("2:00 PM to 11:00 PM IST", "HYBRID", "")
    assert shift_ok("2 PM to 10 PM IST", "REMOTE", "")
    assert not shift_ok("night shift", "REMOTE", "")


def test_redaction():
    out = redact("mail a.b@example.com key sk-abcdefghijklmnop phone +1 555 010 9999")
    assert "example.com" not in out and "sk-abc" not in out and "9999" not in out
