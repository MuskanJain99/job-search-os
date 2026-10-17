"""Weekly digest email (the only routine message Muskan gets)."""

from __future__ import annotations

from . import db
from .config import settings


def build(conn) -> str:
    q = lambda sql: conn.execute(sql).fetchone()  # noqa: E731
    week = q("""select
        (select count(*) from jobs where first_seen_at > now()-interval '7 days') discovered,
        (select count(*) from applications where created_at > now()-interval '7 days') prepared,
        (select count(*) from applications where submitted_at > now()-interval '7 days') submitted,
        (select count(*) from applications where status='SUBMITTED_CONFIRMED' and submitted_at > now()-interval '7 days') confirmed,
        (select count(*) from applications where status='REJECTED' and last_status_change_at > now()-interval '7 days') rejected,
        (select count(*) from applications where status in ('INTERVIEWING','OFFER')) interviewing,
        (select count(*) from outreach where sent_at > now()-interval '7 days') outreach_sent,
        (select count(*) from outreach where status='REPLIED' and created_at > now()-interval '30 days') replies""")
    blockers = conn.execute(
        """select b.type, coalesce(b.question_text, b.detail) q, c.name company, j.title, j.url
           from blockers b left join applications a on a.id=b.application_id left join jobs j on j.id=a.job_id
           left join companies c on c.id=j.company_id where b.resolved_at is null order by b.created_at desc limit 40""").fetchall()
    manual = conn.execute(
        """select c.name company, j.title, j.url, a.channel from applications a join jobs j on j.id=a.job_id
           left join companies c on c.id=j.company_id where a.status='READY' and a.next_action in ('laptop_submit','apply_manually')
           order by a.priority limit 30""").fetchall()
    failed = conn.execute("select workflow, count(*) n from runs where status='FAILED' and started_at > now()-interval '7 days' group by 1").fetchall()

    lines = ["Job search — weekly summary", "",
             f"This week: {week['discovered']} jobs found · {week['prepared']} prepared · {week['submitted']} submitted "
             f"({week['confirmed']} confirmed) · {week['rejected']} rejections · {week['outreach_sent']} outreach emails.",
             f"Active interviews/offers: {week['interviewing']} · replies to outreach (30 days): {week['replies']}", ""]
    if blockers:
        lines += ["NEEDS YOUR INPUT (reply to Claude with answers, or run `jobsearch laptop assist`):"]
        lines += [f"- [{b['type']}] {b['company'] or ''} {b['title'] or ''}: {b['q']} {b['url'] or ''}".strip() for b in blockers]
        lines.append("")
    if manual:
        lines += ["WAITING FOR THE LAPTOP HELPER (or apply yourself):"]
        lines += [f"- {m['company']} — {m['title']} ({m['channel']}): {m['url']}" for m in manual]
        lines.append("")
    if failed:
        lines += ["Runs that failed this week: " + ", ".join(f"{f['workflow']}×{f['n']}" for f in failed)]
    return "\n".join(lines)


def run() -> None:
    with db.run("weekly_digest") as r:
        text = build(r.conn)
        if settings.can_write_external() and settings.google_configured and settings.owner_email:
            from .google_api import send_email

            send_email(settings.owner_email, "[Job Search] Weekly summary", text)
            r.summary["sent"] = True
        else:
            print(text)
