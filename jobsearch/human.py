"""Human-in-the-loop with a timeout: alert → wait a little → continue or move on.

Every point that needs Muskan follows the same rule:
  1. Alert her (email + push, SMS when configured).
  2. Wait HUMAN_WAIT_SECONDS (default 120 s) for her response.
  3. If she responded, continue from where it stopped. If not, record a blocker and move on.
  4. A late response is still used: inbox sync reads replies to question emails, stores the answers,
     resolves the blocker and puts the application back in the queue.

Questions go out as ONE email per application: "[Job Search] ANSWER NEEDED #<token>". She replies with
answers on numbered lines ("1: No", "2: 3 years") or one per line in order.
"""

from __future__ import annotations

import re
import secrets
import time

import httpx

from . import db
from .config import settings
from .log import get_logger

log = get_logger("human")
SUBJECT_PREFIX = "[Job Search] ANSWER NEEDED #"


def _push(text: str) -> None:
    if settings.ntfy_topic:
        try:
            httpx.post(f"https://ntfy.sh/{settings.ntfy_topic}", content=text[:250].encode(),
                       headers={"Title": "Job Search — answer needed", "Priority": "high", "Tags": "question"}, timeout=15)
        except httpx.HTTPError:
            pass


def ask(r: db.Run, app_id: str, company: str, title: str, questions: list[tuple[str, str]],
        wait_s: int | None = None) -> dict[str, str]:
    """Ask the owner the given (type, question) pairs. Returns {question: answer} for what she answered in time.
    Unanswered questions become blockers tagged with the token so a late reply still lands."""
    if not questions:
        return {}
    token = secrets.token_hex(3)
    if not settings.google_configured or not settings.owner_email:
        _block_all(r, app_id, company, questions, token)
        return {}
    from .google_api import get_thread, send_email, thread_text

    lines = [f"{i}. {q}" for i, (_, q) in enumerate(questions, 1)]
    body = (f"{company} — {title}\n\nThe application is waiting on {len(questions)} question(s) your answer sheet doesn't cover:\n\n"
            + "\n".join(lines)
            + "\n\nReply to this email with the answers, one per line in the same order (or '1: …'). "
              "Write 'skip' to drop this job. Add 'save' on its own line to remember these answers for future applications.\n")
    sent = send_email(settings.owner_email, f"{SUBJECT_PREFIX}{token}: {company[:40]} — {title[:40]}", body)
    _push(f"Answer needed: {company} — {title[:60]}. {len(questions)} question(s) — reply to the email.")
    r.audit("human.asked", "applications", app_id, token=token, n=len(questions))
    r.bump("questions_asked")

    deadline = time.time() + (wait_s if wait_s is not None else settings.captcha_wait_s)
    while time.time() < deadline:
        time.sleep(10)
        thread = get_thread(sent["threadId"])
        if len(thread.get("messages", [])) > 1:
            _, _, _, text = thread_text({"messages": thread["messages"][1:]})
            answers, skip, save = parse_reply(_strip_quoted(text), [q for _, q in questions])
            if skip:
                db.set_application_status(r, app_id, "SKIPPED", skip_reason="Skipped by Muskan (email reply)", next_action=None)
                return {}
            _store(r, app_id, answers, save)
            missing = [(t, q) for t, q in questions if q not in answers]
            _block_all(r, app_id, company, missing, token)
            return answers
    _block_all(r, app_id, company, questions, token)
    r.bump("questions_timed_out")
    return {}


def _block_all(r: db.Run, app_id: str, company: str, questions: list[tuple[str, str]], token: str) -> None:
    for t, q in questions:
        db.open_blocker(r, app_id, t, f"Waiting for your answer (#{token}) — {company}", q)


def _store(r: db.Run, app_id: str, answers: dict[str, str], save: bool) -> None:
    for q, a in answers.items():
        r.conn.execute("insert into app_answers (application_id, question_text, answer, basis) values (%s,%s,%s,'USER')",
                       (app_id, q[:500], a[:2000]))
        if save:
            pid = "USR-" + secrets.token_hex(3).upper()
            r.conn.execute(
                "insert into profile_answers (id, category, field, answer, answer_type, source) values (%s,'Application Questions',%s,%s,'VALUE','email reply')",
                (pid, q[:300], a[:2000]))
    r.audit("human.answered", "applications", app_id, n=len(answers), saved=save)


def _strip_quoted(text: str) -> str:
    keep = []
    for line in text.splitlines():
        if line.startswith(">") or re.match(r"^On .+ wrote:$", line.strip()):
            break
        if line.lower().startswith(("from:", "date:")):
            continue
        keep.append(line)
    return "\n".join(keep).strip()


def parse_reply(text: str, questions: list[str]) -> tuple[dict[str, str], bool, bool]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    low = [ln.lower() for ln in lines]
    skip = any(ln in ("skip", "skip this", "skip job") for ln in low)
    save = any(ln == "save" for ln in low)
    lines = [ln for ln in lines if ln.lower() not in ("skip", "save", "skip this", "skip job")]
    out: dict[str, str] = {}
    numbered = {}
    for ln in lines:
        m = re.match(r"^\s*(\d{1,2})\s*[\.:\)\-]\s*(.+)$", ln)
        if m:
            numbered[int(m.group(1))] = m.group(2).strip()
    if numbered:
        for i, q in enumerate(questions, 1):
            if i in numbered:
                out[q] = numbered[i]
    else:
        for q, ln in zip(questions, lines, strict=False):
            out[q] = ln
    return out, skip, save


def apply_late_replies(r: db.Run) -> None:
    """Inbox sync: answers that arrived after the wait window → store, resolve blockers, re-queue."""
    if not settings.google_configured:
        return
    from .google_api import get_thread, search_threads, thread_text

    for t in search_threads(f'subject:"{SUBJECT_PREFIX}" newer_than:7d', 20):
        thread = get_thread(t["id"])
        msgs = thread.get("messages", [])
        if len(msgs) < 2:
            continue
        subject, _, _, _ = thread_text({"messages": msgs[:1]})
        m = re.search(r"#([0-9a-f]{6})", subject)
        if not m:
            continue
        token = m.group(1)
        open_rows = r.conn.execute(
            "select id, application_id, question_text, created_at from blockers where resolved_at is null and detail like %s order by created_at",
            (f"%(#{token})%",)).fetchall()
        if not open_rows:
            continue
        app_id = str(open_rows[0]["application_id"])
        asked = r.conn.execute(
            "select question_text from blockers where detail like %s order by created_at", (f"%(#{token})%",)).fetchall()
        questions = [row["question_text"] for row in asked]
        _, _, _, text = thread_text({"messages": msgs[1:]})
        answers, skip, save = parse_reply(_strip_quoted(text), questions)
        if skip:
            db.set_application_status(r, app_id, "SKIPPED", skip_reason="Skipped by Muskan (email reply)", next_action=None)
            r.conn.execute("update blockers set resolved_at=now(), resolution='Skipped by Muskan' where detail like %s and resolved_at is null",
                           (f"%(#{token})%",))
            continue
        _store(r, app_id, {q: a for q, a in answers.items() if q in {o['question_text'] for o in open_rows}}, save)
        for row in open_rows:
            if row["question_text"] in answers:
                r.conn.execute("update blockers set resolved_at=now(), resolution='Answered by Muskan (email reply)' where id=%s", (row["id"],))
        still = r.conn.execute("select 1 from blockers where application_id=%s and resolved_at is null", (app_id,)).fetchone()
        if not still:
            channel = r.conn.execute("select channel from applications where id=%s", (app_id,)).fetchone()["channel"]
            from .sources.aggregators import CLOUD_SUBMIT

            db.set_application_status(r, app_id, "READY", next_action="submit" if channel in CLOUD_SUBMIT else "laptop_submit")
            r.bump("requeued_after_reply")
