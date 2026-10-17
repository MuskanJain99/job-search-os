"""Inbox sync: classify job-related Gmail threads, update state, book interviews, alert when needed."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from psycopg.types.json import Jsonb  # noqa: F401  (kept for parity with other modules)

from . import alerts, db
from .config import settings
from .llm import ask_json, untrusted
from .log import get_logger
from .models import ThreadClass

log = get_logger("inbox")

QUERY = ("newer_than:2d (from:(greenhouse.io OR lever.co OR ashbyhq.com OR myworkday.com OR smartrecruiters.com OR "
         "workable.com OR icims.com OR hackerrank.com OR codesignal.com OR codility.com OR linkedin.com OR naukri.com OR instahyre.com) "
         "OR subject:(application OR interview OR assessment OR offer OR \"thank you for applying\" OR referral))")

TASK = """Classify this job-search email thread for Muskan. Fill company/role if identifiable.
needs_owner=true when a human asks her a question, proposes or confirms interview times, sends an offer,
or sends a scheduling link. proposed_slots_iso: concrete interview slots offered (ISO 8601 with offset).
deadline_iso: assessment or response deadline if stated."""


def run() -> None:
    with db.run("inbox_sync") as r:
        if not settings.google_configured:
            r.partial("google not configured")
            return
        from .google_api import search_threads

        conn = r.conn
        threads = [t for t in search_threads(QUERY, 60)]
        own = {t["id"] for t in search_threads('subject:"[Job Search]" newer_than:7d', 50)}
        threads = [t for t in threads if t["id"] not in own]  # never classify our own alert/question emails
        r.summary["threads"] = len(threads)
        for t in threads:
            tid = t["id"]
            if conn.execute("select 1 from email_threads where thread_id=%s and processed_at > now() - interval '1 hour'", (tid,)).fetchone():
                continue
            try:
                handle(r, tid)
            except Exception as exc:
                r.bump("errors")
                r.audit("inbox.error", "email_threads", tid, kind=type(exc).__name__)
        from . import human

        human.apply_late_replies(r)  # answers she sent after the 2-minute window → re-queue those jobs


def _match_application(conn, company: str, role: str):
    if not company:
        return None
    return conn.execute(
        """select a.id, a.status, j.title from applications a join jobs j on j.id=a.job_id join companies c on c.id=j.company_id
           where c.normalized_name = regexp_replace(lower(%s), '[^a-z0-9]+', ' ', 'g')
              or lower(c.name) like lower(%s)
           order by (lower(j.title) like lower(%s)) desc, a.updated_at desc limit 1""",
        (company, f"%{company}%", f"%{role[:30]}%")).fetchone()


def handle(r: db.Run, tid: str) -> None:
    from .google_api import get_thread, thread_text

    conn = r.conn
    subject, sender, _, text = thread_text(get_thread(tid))
    c = ask_json(TASK, untrusted(f"Subject: {subject}\nFrom: {sender}\n\n{text}", 12000), ThreadClass, fast=True, max_tokens=800)
    app = _match_application(conn, c.company, c.role)
    app_id = str(app["id"]) if app else None
    conn.execute(
        """insert into email_threads (thread_id, application_id, subject, from_address, classification, last_message_at, processed_at)
           values (%s,%s,%s,%s,%s,now(),now())
           on conflict (thread_id) do update set classification=excluded.classification, application_id=coalesce(excluded.application_id, email_threads.application_id),
                 processed_at=now()""",
        (tid, app_id, subject[:300], sender[:200], c.classification))
    r.bump(f"class_{c.classification.lower()}")
    label = f"{c.company or 'A company'} — {c.role or 'role'}"[:90]

    if c.classification == "ACKNOWLEDGMENT" and app and app["status"] in ("SUBMITTED", "READY", "SUBMITTING"):
        db.set_application_status(r, app_id, "SUBMITTED_CONFIRMED", confirmation_evidence=f"ack email {tid}")
    elif c.classification == "REJECTION" and app:
        db.set_application_status(r, app_id, "REJECTED", next_action=None)
        conn.execute("update outreach set status='CLOSED', next_followup_at=null where application_id=%s and status='SENT'", (app_id,))
    elif c.classification == "OFFER":
        if app:
            db.set_application_status(r, app_id, "OFFER")
        alerts.alert(r, "offer", tid, f"Offer: {label}. Check Gmail — nothing was replied on your behalf.")
    elif c.classification == "ASSESSMENT":
        if app:
            db.open_blocker(r, app_id, "ASSESSMENT", f"Assessment for {label}", c.deadline_iso or "")
        alerts.alert(r, "assessment", tid, f"Assessment: {label}" + (f", due {c.deadline_iso[:16]}" if c.deadline_iso else ""))
    elif c.classification in ("INTERVIEW_REQUEST", "SCHEDULING"):
        if app:
            db.set_application_status(r, app_id, "INTERVIEWING")
        booked = book_slot(r, app_id, label, c.proposed_slots_iso) if c.proposed_slots_iso else None
        if booked:
            alerts.alert(r, "interview", tid, f"Interview: {label}, {booked} IST — slot is free on your calendar; confirm by replying in Gmail.")
        else:
            alerts.alert(r, "interview", tid, f"Interview request: {label}. Reply in Gmail"
                         + (" (scheduling link inside)" if c.scheduling_link else "") + ".")
    elif c.needs_owner:
        alerts.alert(r, "human_reply", tid, f"Reply needed: {label} — {c.summary[:90]}")


def book_slot(r: db.Run, app_id: str | None, label: str, slots: list[str]) -> str | None:
    """Pick the earliest offered slot free on her calendar (weekday 10:00-19:00 IST) and hold it."""
    from zoneinfo import ZoneInfo

    from .google_api import create_event, free_busy

    tz = ZoneInfo(settings.timezone)
    for s in sorted(slots):
        try:
            start = datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(tz)
        except ValueError:
            continue
        if start < datetime.now(tz) or start.weekday() > 4 or not (10 <= start.hour < 19):
            continue
        end = start + timedelta(hours=1)
        if free_busy(start.astimezone(timezone.utc).isoformat(), end.astimezone(timezone.utc).isoformat()):
            continue
        if settings.can_write_external():
            ev = create_event(f"HOLD — Interview: {label}", start.isoformat(), end.isoformat(),
                              "Held automatically; confirm the slot with the recruiter.")
            if app_id:
                r.conn.execute("insert into interviews (application_id, scheduled_at, duration_min, calendar_event_id, stage) values (%s,%s,60,%s,'TBD') on conflict do nothing",
                               (app_id, start, ev))
        return start.strftime("%a %d %b %H:%M")
    return None


def daily_needs_input(r: db.Run) -> None:
    rows = r.conn.execute(
        """select b.question_text, c.name from blockers b join applications a on a.id=b.application_id join jobs j on j.id=a.job_id
           join companies c on c.id=j.company_id
           left join lateral (select score from fit_scores where job_id=j.id order by created_at desc limit 1) f on true
           where b.resolved_at is null and b.created_at > now() - interval '24 hours'
             and b.type in ('ASK_ME','NEVER_ANSWER') and coalesce(f.score,0) >= 80""").fetchall()
    if rows:
        alerts.alert(r, "needs_input", datetime.now().strftime("%Y-%m-%d"),
                     f"{len(rows)} question(s) need your answer for strong-fit jobs — see the latest email or ask Claude 'show blockers'.")
