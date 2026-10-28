"""One call used by every stage: resolve form answers, ask Muskan for the gaps (alert + short wait),
then resolve again with her answers. Returns the final Resolved; remaining gaps are already blockers."""

from __future__ import annotations

from . import answers as answers_mod
from . import db, human


def stored_user_answers(conn, app_id: str) -> dict[str, str]:
    rows = conn.execute("select question_text, answer from app_answers where application_id=%s and basis='USER'", (app_id,)).fetchall()
    return {r["question_text"]: r["answer"] for r in rows}


_JOB_DEPENDENT = ("visa", "sponsor", "authori", "relocat", "location", "salary", "compensation", "notice", "start", "city", "country", "why ", "company")


def reusable_answers(conn) -> dict[str, str]:
    """Profile-based answers already given to other applications for the exact same question: reused, so the model isn't asked again.
    Questions that depend on the job (country, salary, company) are never reused."""
    rows = conn.execute(
        """select distinct on (lower(question_text)) question_text, answer from app_answers
           where basis='PROFILE' and profile_id is not null and answer <> '' and length(question_text) > 12
           order by lower(question_text), created_at desc limit 2000""").fetchall()
    return {r["question_text"]: r["answer"] for r in rows if not any(w in r["question_text"].lower() for w in _JOB_DEPENDENT)}


def resolve_with_owner(r: db.Run, app_id: str, fields: list[dict], profile, evidence, title: str, company: str, jd: str = ""
                       ) -> answers_mod.Resolved | None:
    """None means: stopped (blockers recorded or job skipped by her) — move on to the next application."""
    known = {**reusable_answers(r.conn), **stored_user_answers(r.conn, app_id)}
    row = r.conn.execute("select j.location from applications a join jobs j on j.id=a.job_id where a.id=%s", (app_id,)).fetchone()
    where = (row or {}).get("location") or ""
    res = answers_mod.resolve(fields, profile, evidence, title, company, user_answers=known, job_location=where, jd=jd)
    if not res.blockers:
        return res
    got = human.ask(r, app_id, company, title, res.blockers)
    if not got or len(got) < len(res.blockers):
        return None
    res = answers_mod.resolve(fields, profile, evidence, title, company, user_answers={**known, **got}, job_location=where, jd=jd)
    if res.blockers:  # still something open (shouldn't happen) — record and move on
        for t, q in res.blockers:
            db.open_blocker(r, app_id, t, f"Still unanswered — {company}", q)
        return None
    r.conn.execute("update blockers set resolved_at=now(), resolution='Answered live by Muskan' "
                   "where application_id=%s and resolved_at is null and type in ('ASK_ME','NEVER_ANSWER')", (app_id,))
    return res
