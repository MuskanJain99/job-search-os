"""Paste-a-link: `jobsearch ingest <url>` (or the 'Apply to a link' GitHub workflow).

A pasted link is an explicit choice to apply: it skips eligibility filters except truthfulness rules,
goes to the front of the queue, and the next submit run (cloud or laptop) applies to it.
"""

from __future__ import annotations

from . import db, discover, scoring, targeting
from .log import get_logger
from .sources import aggregators, ats, search
from .sources.ats import Posting

log = get_logger("ingest")


def run(url: str) -> None:
    url = ats.clean_url(url)
    with db.run("ingest_link") as r:
        conn = r.conn
        existing = conn.execute(
            "select a.id, a.status from jobs j join applications a on a.job_id=j.id where j.url=%s", (url,)).fetchone()
        if existing:
            conn.execute("update applications set priority=1, discovered_via='user_link' where id=%s", (existing["id"],))
            r.summary["existing_status"] = existing["status"]
            print(f"Already tracked: {existing['status']}")
            return
        p = ats.find_posting(url)
        if not p:
            text = search.fetch_page_text(url)
            if len(text) < 200:
                raise RuntimeError("Could not read the job page; paste the JD text into a chat instead.")
            p = Posting(ats="link", token="", job_id="", title=_guess_title(text), url=url, apply_url=url,
                        location="", remote=None, description=text, posted_at=None, extra={"source": "user_link"})
        p.extra["source"] = "user_link"
        evidence, skeleton, profile = db.resume_evidence(conn), db.resume_skeleton(conn), db.profile_answers(conn)
        company = p.company_name or p.token or "Unknown"
        loc = targeting.classify_location(p.location, p.remote, p.description)
        company_id = discover._company(conn, company, p)
        key = targeting.dedupe_key(targeting.normalize(company), f"{p.ats}:{p.job_id}" if p.job_id else None, p.title, p.location)
        job = conn.execute(
            """insert into jobs (company_id, source, ats_job_id, url, title, normalized_title, location, country, remote_policy,
                   india_eligible, jd_text, jd_hash, status, last_verified_at, dedupe_key)
               values (%s,'user_link',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'OPEN',now(),%s)
               on conflict (dedupe_key) do update set last_seen_at=now() returning id""",
            (company_id, p.job_id or None, url, p.title[:300], targeting.normalize(p.title), p.location, loc.country,
             loc.remote_policy, loc.india_eligible, p.description, targeting.jd_hash(p.description), key)).fetchone()
        fit = scoring.score_job(p.title, company, p.location, p.description, evidence)
        app_id = discover.prepare(r, str(job["id"]), p, company, max(fit.score, 80), evidence, skeleton, profile)
        if app_id:
            conn.execute("update applications set priority=1 where id=%s", (app_id,))
        gaps = [g.requirement for g in fit.gaps if g.must_have][:3]
        print(f"{company} — {p.title}: fit {fit.score}. Channel: {aggregators.apply_channel(p.apply_url)}."
              + (f" Must-haves you don't have evidence for: {', '.join(gaps)} (won't be claimed)." if gaps else ""))


def _guess_title(text: str) -> str:
    for line in text.splitlines():
        line = line.strip("# ").strip()
        if 5 < len(line) < 120:
            return line
    return "Job"
