"""Referral outreach by email: find people with a public source, email only published addresses,
follow up twice at most, stop on any reply."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import alerts, db
from .config import settings
from .llm import ask_json, untrusted
from .log import get_logger
from .models import ContactList, Email
from .sources import search

log = get_logger("outreach")

FIND_TASK = """From these public pages, list up to 3 people relevant to a referral for this job: the recruiter
for the role, the hiring manager, or an engineer on the team (BITS Pilani / IBM alumni are a plus).
Include a person only if the page shows their name and current role at the company. source_url must be the
page where they appear. email: ONLY if that exact address is printed on the page; otherwise null."""

EMAIL_TASK = """Write a referral request email from Muskan (plain text, <= 120 words, subject <= 8 words).
One line on who she is; one concrete reason this role/team fits; two proof points from <evidence> (cite
claim_ids); the ask (referral or forwarding to the hiring manager); job link; thanks; sign 'Muskan'."""

FOLLOWUP = {
    1: "Hi {first},\n\nJust floating this back up in case it got buried — I'd really appreciate a referral or a pointer to the right person for the {title} role ({link}).\n\nThanks so much,\nMuskan",
    2: "Hi {first},\n\nOne last nudge on the {title} role — completely understand if now isn't a good time. Thank you either way!\n\nBest,\nMuskan",
}


def in_window(tz: str) -> bool:
    now = datetime.now(ZoneInfo(tz))
    start = now.replace(hour=9, minute=30, second=0, microsecond=0)
    return start <= now <= now.replace(hour=18, minute=30, second=0, microsecond=0)


def run() -> None:
    with db.run("outreach") as r:
        conn = r.conn
        cfg = db.pipeline_config(conn)
        if cfg["paused"]:
            r.summary["skipped"] = "paused"
            return
        find_contacts(r)
        if not in_window(settings.timezone):
            r.summary["send_window"] = False
            return
        budget = max(0, cfg["max_emails_per_day"] - (db.daily_usage(conn)["emails_today"] or 0))
        budget = check_replies_and_followups(r, cfg, budget)
        send_initials(r, budget)


def find_contacts(r: db.Run) -> None:
    conn = r.conn
    apps = conn.execute(
        """select a.id, j.title, c.id as company_id, c.name as company, c.domain from applications a join jobs j on j.id=a.job_id
           join companies c on c.id=j.company_id
           where a.status in ('SUBMITTED','SUBMITTED_CONFIRMED') and not exists (select 1 from contacts ct where ct.company_id=c.id)
           order by a.submitted_at desc limit 5""").fetchall()
    for a in apps:
        pages = []
        for q in (f'"{a["company"]}" recruiter "{a["title"]}" hiring', f'"{a["company"]}" engineering team blog OR talk "{a["title"].split()[0]}"'):
            for url in search.search_urls(q, limit=5, recency="qdr:y")[:3]:
                text = search.fetch_page_text(url)
                if text:
                    pages.append(f"SOURCE {url}\n{text[:6000]}")
        if not pages:
            continue
        found = ask_json(FIND_TASK, f"Company: {a['company']}\nRole: {a['title']}\n" + untrusted("\n\n".join(pages), 30000),
                         ContactList, fast=True)
        for c in found.contacts:
            if not c.source_url or c.source_url not in "\n".join(pages):
                continue
            email_ok = bool(c.email) and c.email in "\n".join(pages)
            conn.execute(
                """insert into contacts (company_id, name, role_title, contact_type, linkedin_url, email, email_status, source_url)
                   values (%s,%s,%s,%s,%s,%s,%s,%s) on conflict do nothing""",
                (a["company_id"], c.name[:200], c.role_title[:200], c.contact_type, c.linkedin_url,
                 c.email if email_ok else None, "SOURCED" if email_ok else "NONE", c.source_url))
            r.bump("contacts_found")


def send_initials(r: db.Run, budget: int) -> None:
    conn = r.conn
    if budget <= 0:
        return
    rows = conn.execute(
        """select ct.id as contact_id, ct.name, ct.email, a.id as app_id, j.title, j.url, j.jd_text, c.name as company
           from contacts ct join companies c on c.id=ct.company_id
           join jobs j on j.company_id=c.id join applications a on a.job_id=j.id
           where ct.email is not null and ct.email_status in ('SOURCED','VERIFIED') and not ct.do_not_contact
             and a.status in ('SUBMITTED','SUBMITTED_CONFIRMED')
             and not exists (select 1 from outreach o where o.contact_id=ct.id and o.application_id=a.id)
           limit %s""", (budget,)).fetchall()
    evidence = db.resume_evidence(conn)
    ev = "\n".join(f"{e['claim_id']}: {e['claim']}" for e in evidence)
    for row in rows:
        mail = ask_json(EMAIL_TASK, f"<evidence>\n{ev}\n</evidence>\nTo: {row['name'].split()[0]} at {row['company']}\n"
                        f"Role: {row['title']} — {row['url']}\n" + untrusted(row["jd_text"], 5000), Email, max_tokens=1200)
        thread_id = msg_id = None
        status = "DRAFT"
        if settings.can_write_external() and settings.email_enabled and settings.google_configured:
            from .google_api import send_email

            sent = send_email(row["email"], mail.subject, mail.body)
            thread_id, msg_id, status = sent.get("threadId"), sent.get("id"), "SENT"
        days = (db.pipeline_config(conn)["followup_days"] or [5, 10])[0]
        conn.execute(
            """insert into outreach (contact_id, application_id, channel, kind, step, subject, body, status, email_thread_id,
                   gmail_message_id, sent_at, next_followup_at)
               values (%s,%s,'EMAIL','INITIAL',0,%s,%s,%s,%s,%s,case when %s='SENT' then now() end,
                       case when %s='SENT' then now() + make_interval(days => %s) end)""",
            (row["contact_id"], row["app_id"], mail.subject, mail.body, status, thread_id, msg_id, status, status, days))
        r.bump("emails_sent" if status == "SENT" else "emails_drafted")


def check_replies_and_followups(r: db.Run, cfg, budget: int) -> int:
    conn = r.conn
    due = conn.execute("select * from v_followups_due").fetchall()
    for d in due:
        o = conn.execute("select * from outreach where id=%s", (d["outreach_id"],)).fetchone()
        if not (settings.google_configured and o["email_thread_id"]):
            continue
        from .google_api import get_thread, send_email, thread_text

        thread = get_thread(o["email_thread_id"])
        if len(thread.get("messages", [])) > o["step"] + 1:  # someone else wrote in the thread
            conn.execute("update outreach set status='REPLIED' where id=%s", (o["id"],))
            alerts.alert(r, "human_reply", f"outreach:{o['id']}", "A referral contact replied to your outreach — check Gmail")
            continue
        step = o["step"] + 1
        if step > 2 or budget <= 0:
            conn.execute("update outreach set status='CLOSED', next_followup_at=null where id=%s", (o["id"],))
            continue
        job = conn.execute("select j.title, j.url from applications a join jobs j on j.id=a.job_id where a.id=%s",
                           (o["application_id"],)).fetchone()
        body = FOLLOWUP[step].format(first=(d["name"] or "there").split()[0], title=job["title"], link=job["url"])
        if settings.can_write_external() and settings.email_enabled:
            _, _, msg_id, _ = thread_text(thread)
            send_email(d["email"], "Re: " + (o["subject"] or ""), body, thread_id=o["email_thread_id"], in_reply_to=msg_id)
            days = (cfg["followup_days"] or [5, 10])
            nxt = datetime.now(ZoneInfo("UTC")) + timedelta(days=(days[1] - days[0]) if step == 1 and len(days) > 1 else 5)
            conn.execute("update outreach set step=%s, next_followup_at=%s, status=%s where id=%s",
                         (step, nxt if step < 2 else None, "SENT" if step < 2 else "CLOSED", o["id"]))
            budget -= 1
            r.bump("followups_sent")
    return budget
