from jobsearch.discover import spread_by_company
from jobsearch.sources.ats import Posting


def _p(company, n):
    return Posting(ats="x", token=company, job_id=str(n), title=f"Engineer {n}", company_name=company, url=f"https://x/{company}/{n}", apply_url="", location="", remote=False, description="", posted_at=None)


def test_round_robin():
    posts = [_p("wise", i) for i in range(4)] + [_p("acme", 0), _p("zeta", 0)]
    order = [p.company_name for p in spread_by_company(posts)]
    assert order[:3] == ["wise", "acme", "zeta"]
