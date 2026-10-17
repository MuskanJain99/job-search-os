"""Laptop helper: runs on Muskan's Mac (launchd) using a dedicated, signed-in Chrome profile.

  jobsearch laptop auto    -> scheduled: apply on signed-in boards (LinkedIn Easy Apply, Naukri,
                              Instahyre, Indeed, Cutshort) within caps; skips quietly if signed out.
  jobsearch laptop assist  -> on demand: opens pre-filled forms that need a human step (CAPTCHA,
                              anti-bot codes, SMS); you finish and click Submit; it records it.

Employer portals that require an account (Workday, iCIMS, …) are signed up for automatically with
her permission — see accounts.py (Vault-stored unique passwords, Gmail verification).
  jobsearch laptop login   -> opens the profile so you can sign in to each board once.

Uses a separate Chrome profile directory, never the everyday profile.
"""

from __future__ import annotations

import random
import tempfile
import time
from pathlib import Path

from . import accounts, alerts, answering, db, resume_pdf
from . import answers as answers_mod
from .config import settings
from .log import get_logger
from .submit import CONFIRM_TEXT, FIELDS_JS, _captcha, try_fill

log = get_logger("laptop")
PROFILE_DIR = Path.home() / ".job-search-os" / "chrome-profile"

LOGIN_CHECK = {
    "linkedin": ("https://www.linkedin.com/feed/", "/login"),
    "naukri": ("https://www.naukri.com/mnjuser/homepage", "nlogin"),
    "instahyre": ("https://www.instahyre.com/candidate/opportunities/", "/login"),
    "indeed": ("https://profile.indeed.com/", "secure.indeed.com/auth"),
    "cutshort": ("https://cutshort.io/jobs", "/login"),
}
WARNING_TEXT = ("unusual activity", "we've restricted", "temporarily restricted", "verify your identity",
                "security verification", "weekly limit", "too many requests", "let's do a quick security check")
NEXT_BUTTONS = ("Submit application", "Submit", "Review", "Next", "Continue", "Apply now", "Easy Apply", "Apply",
                "I'm interested", "Interested")


def _browser(pw, headless: bool):
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    return pw.chromium.launch_persistent_context(
        str(PROFILE_DIR), channel="chrome", headless=headless, viewport={"width": 1366, "height": 900},
        locale="en-IN", timezone_id="Asia/Kolkata")


def _pause(a: float = 2.0, b: float = 6.0) -> None:
    time.sleep(random.uniform(a, b))


def login() -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        ctx = _browser(pw, headless=False)
        for url, _ in LOGIN_CHECK.values():
            ctx.new_page().goto(url)
        print("Sign in to each tab, then close the browser window.")
        ctx.pages[0].wait_for_event("close", timeout=0)
        ctx.close()


def signed_in(page, board: str) -> bool:
    url, login_marker = LOGIN_CHECK[board]
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    _pause(2, 4)
    return login_marker not in page.url


def auto() -> None:
    from playwright.sync_api import sync_playwright

    with db.run("laptop_boards") as r:
        conn = r.conn
        cfg = db.pipeline_config(conn)
        if cfg["paused"]:
            r.summary["skipped"] = "paused"
            return
        usage = db.daily_usage(conn)
        budget = max(0, cfg["max_applications_per_day"] - (usage["applications_today"] or 0))
        li_budget = max(0, min(5, cfg["max_linkedin_messages_per_day"]))  # LinkedIn applies kept low
        # Everything the cloud can't do: signed-in boards (LinkedIn — Easy Apply AND external "Apply" links,
        # Naukri, Instahyre, Indeed, Cutshort, Glassdoor, Wellfound) plus company sites, Workday, iCIMS, Taleo…
        # and cloud-channel jobs the cloud already tried and got blocked on (not human-step blockers).
        queue = conn.execute(
            """select a.id, a.channel, a.resume_version_id, j.url, j.title, c.name as company
               from applications a join jobs j on j.id=a.job_id left join companies c on c.id=j.company_id
               where a.status='READY' and a.next_action in ('laptop_submit','apply_manually')
               order by (a.discovered_via='user_link') desc, a.priority limit %s""",
            (budget,)).fetchall()
        r.summary["queued"] = len(queue)
        if not queue:
            return
        header, profile, evidence = db.contact_header(conn), db.profile_answers(conn), db.resume_evidence(conn)
        with sync_playwright() as pw, tempfile.TemporaryDirectory() as tmp:
            ctx = _browser(pw, headless=False)
            page = ctx.new_page()
            checked: dict[str, bool] = {}
            for app in queue:
                board = app["channel"]
                if board == "linkedin":
                    if li_budget <= 0:
                        continue
                    li_budget -= 1
                if board not in checked:
                    checked[board] = signed_in(page, board) if board in LOGIN_CHECK else True
                    if not checked[board]:
                        r.bump(f"signed_out_{board}")
                if not checked[board]:
                    continue
                if page.is_closed():  # a CAPTCHA timeout closes the tab — open a fresh one for the next job
                    page = ctx.new_page()
                try:
                    stop = apply_flow(r, page, app, header, profile, evidence, Path(tmp))
                except Exception as exc:
                    r.bump("errors")
                    r.audit("laptop.error", "applications", str(app["id"]), kind=type(exc).__name__)
                    continue
                if stop:  # a site warning: stop everything on that board for today
                    checked[board] = False
                _pause(20, 60)
            ctx.close()


def apply_flow(r: db.Run, page, app: dict, header, profile, evidence, tmp: Path) -> bool:
    """Multi-step apply loop. Returns True if a site warning was seen (caller stops that board)."""
    conn, app_id = r.conn, str(app["id"])
    content = conn.execute("select content from resume_versions where id=%s", (app["resume_version_id"],)).fetchone()["content"]
    pdf = resume_pdf.render_pdf(content, header, tmp / resume_pdf.safe_name("Muskan_Nahata", app["company"] or "", app["title"]))
    page.goto(app["url"], wait_until="domcontentloaded", timeout=60000)
    _pause()
    page = _follow_external_apply(page)
    for _step in range(14):
        text = page.inner_text("body").lower()[:30000]
        if any(t in text for t in ACCOUNT_TEXT) and (page.locator("input[type=password]").count()
                                                      or any(w in text for w in accounts.SIGNUP_WORDS)):
            first, _, last = (header.get("name") or "").partition(" ")
            if not accounts.handle_portal_auth(r, page, app_id, header["email"], first, last):
                return False  # a blocker was opened (CAPTCHA / SMS / sign-in failure)
            _pause()
            continue
        if any(w in text for w in WARNING_TEXT):
            if app["channel"] == "linkedin":
                conn.execute("update pipeline_config set max_linkedin_invites_per_day=0, max_linkedin_messages_per_day=0 where id=1")
            db.open_blocker(r, None, "OTHER", f"{app['channel']} showed a warning/verification — automation stopped for it")
            alerts.alert(r, "account", f"warning:{app['channel']}", f"{app['channel'].title()} showed a security warning — automation stopped for it. Check your account.")
            return True
        if _captcha(page):
            if not wait_for_human(r, page, app_id, f"{app['company']} — {app['title']}"):
                return False
            continue
        if _code_prompt(page):  # anti-bot email code (e.g. Greenhouse) — the human types it
            if not wait_for_human(r, page, app_id, f"{app['company']} — {app['title']}", what="code"):
                return False
            continue
        if any(t in text for t in CONFIRM_TEXT) or "application was sent" in text or "applied" in text[:400]:
            db.set_application_status(r, app_id, "SUBMITTED", submitted_at=_now(), confirmation_url=page.url, next_action="outreach")
            r.bump("submitted")
            return False
        fields = page.evaluate(FIELDS_JS)
        if fields:
            res = answering.resolve_with_owner(r, app_id, fields, profile, evidence, app["title"], app["company"] or "")
            if res is None:
                return False  # alerted + waited; recorded for a late reply — move on
            by_id = {f["field_id"]: f for f in fields}
            for fid, value in res.values.items():
                if fid in by_id and not _already_filled(page, by_id[fid]):
                    try_fill(page, by_id[fid], value, pdf, None)
                    _pause(0.5, 1.5)
        if not _click_next(page):
            # Unknown layout: hand the open window to her for 2 minutes, then continue from wherever she leaves it.
            if not wait_for_human(r, page, app_id, f"{app['company']} — {app['title']}", what="stuck"):
                return False
            continue
        if not (settings.can_write_external() and settings.submit_enabled):
            r.bump("would_submit")
            return False
        _pause()
    return False


MISSING_REQUIRED_JS = r"""
() => [...document.querySelectorAll('input[required], textarea[required], select[required], [aria-required="true"]')]
  .filter(el => {
    if (el.type === 'hidden' || el.getAttribute('aria-hidden') === 'true') return false;
    const s = getComputedStyle(el); const r = el.getBoundingClientRect();
    if (el.type !== 'file' && (s.display === 'none' || s.visibility === 'hidden' || r.width === 0)) return false;
    if (el.type === 'radio' || el.type === 'checkbox')
      return el.name ? !document.querySelector(`input[name="${CSS.escape(el.name)}"]:checked`) : !el.checked;
    const ctl = el.closest('.select__control, [class*="select__control"]');
    if (ctl) return !ctl.querySelector('[class*="single-value"], [class*="multi-value"]');
    if (el.type === 'file') return !(el.files && el.files.length);
    return !String(el.value || '').trim();
  }).length
"""


def _missing_required(page) -> bool:
    try:
        return page.evaluate(MISSING_REQUIRED_JS) > 0
    except Exception:
        return False


def _body(page) -> str:
    try:
        return page.inner_text("body").lower()[:30000]
    except Exception:
        return ""


def _state(page) -> str:
    try:
        return page.url + "|" + str(len(page.evaluate(FIELDS_JS))) + "|" + page.inner_text("body")[:500]
    except Exception:
        return ""


CODE_PROMPT = ("security code", "verification code", "enter the code", "we sent a code")


def _code_prompt(page) -> bool:
    try:
        text = page.inner_text("body").lower()[:20000]
    except Exception:
        return False
    return any(t in text for t in CODE_PROMPT) and page.locator(
        "input[autocomplete='one-time-code'], input[name*='code' i], input[id*='code' i], input[maxlength='1']").count() > 0


def wait_for_human(r: db.Run, page, app_id: str, label: str, what: str = "captcha") -> bool:
    """CAPTCHA hand-off: alert by email + push (+ SMS if configured), keep the page open and in front for
    CAPTCHA_WAIT_SECONDS (default 2 min). Returns True if it was solved; otherwise closes the tab, keeps the
    application queued for `jobsearch laptop assist`, and the caller moves on to the next job."""
    from .submit import _captcha

    try:
        page.bring_to_front()
    except Exception:
        pass
    msgs = {
        "captcha": f"CAPTCHA on your Mac: {label[:90]}. Solve it in the open Chrome window within 2 min — it continues by itself.",
        "code": f"Code needed on your Mac: {label[:80]}. Type the code (email/SMS) into the open Chrome window within 2 min.",
        "login": f"Sign-in needed on your Mac: {label[:80]}. Sign in in the open Chrome window within 2 min.",
        "stuck": f"Help needed on your Mac: {label[:80]}. Click to the next step in the open Chrome window within 2 min.",
        "fields": f"Answers needed on your Mac: {label[:80]}. Fill the empty required fields in the open Chrome window within 2 min.",
    }
    alerts.alert(r, "captcha", f"{app_id}:{int(time.time() // 60)}", msgs.get(what, msgs["stuck"]), live=True)
    start_state = _state(page)
    blocked = {"captcha": _captcha, "code": _code_prompt,
               "login": lambda p: p.locator("input[type=password]").count() > 0,
               "stuck": lambda p: _state(p) == start_state,
               "fields": _missing_required}.get(what, _captcha)
    deadline = time.time() + settings.captcha_wait_s
    while time.time() < deadline:
        time.sleep(3)
        try:
            if page.is_closed():
                break
            if not blocked(page):
                r.bump("captcha_solved_by_owner")
                r.audit("captcha.solved", "applications", app_id)
                _pause(1, 2)
                return True
        except Exception:
            break
    r.bump("captcha_timeout")
    db.open_blocker(r, app_id, "CAPTCHA", "CAPTCHA not solved within 2 minutes — run `jobsearch laptop assist` later", page.url)
    try:
        if not page.is_closed():
            page.close()
    except Exception:
        pass
    return False


ACCOUNT_TEXT = ("create account", "create an account", "sign in", "log in", "register", "already have an account")


def _follow_external_apply(page):
    """On a board page (LinkedIn, Indeed, Glassdoor, Naukri…) whose Apply goes to the employer's site,
    click it and continue in the tab/page it opens. Easy-apply style flows stay on the board."""
    url = page.url.lower()
    if not any(b in url for b in ("linkedin.com", "indeed.", "glassdoor.", "naukri.com", "instahyre.com", "cutshort.io", "wellfound.com")):
        return page
    if page.get_by_role("button", name="Easy Apply").count():
        return page
    candidates = [page.get_by_role("button", name=n) for n in ("Apply", "Apply now", "Apply on company site", "Apply on company website")]
    candidates += [page.get_by_role("link", name=n) for n in ("Apply", "Apply now", "Apply on company site")]
    for c in candidates:
        if c.count():
            try:
                with page.context.expect_page(timeout=8000) as new:
                    c.first.click()
                ext = new.value
                ext.wait_for_load_state("domcontentloaded")
                _pause(2, 4)
                return ext
            except Exception:
                _pause(2, 4)  # opened in the same tab
                return page
    return page


def _already_filled(page, f: dict) -> bool:
    if f["type"] in ("file", "radio", "checkbox"):
        return False
    try:
        return bool(page.locator(f"[data-jb='{f['refs'][0]}']").input_value())
    except Exception:
        return False


def _click_next(page) -> bool:
    for name in NEXT_BUTTONS:
        btn = page.get_by_role("button", name=name, exact=False)
        if btn.count() and btn.first.is_enabled():
            if name.lower().startswith("submit") and not (settings.can_write_external() and settings.submit_enabled):
                return True  # dry run: stop before the final click
            btn.first.click()
            return True
    return False


def assist() -> None:
    """Open pre-filled forms for blocked applications; you complete the human step and submit."""
    from playwright.sync_api import sync_playwright

    with db.run("laptop_assist") as r:
        conn = r.conn
        rows = conn.execute(
            """select distinct on (a.id) a.id, a.channel, a.resume_version_id, j.url, j.title, c.name as company, b.id as blocker_id, b.type
               from blockers b join applications a on a.id=b.application_id join jobs j on j.id=a.job_id
               left join companies c on c.id=j.company_id
               where b.resolved_at is null and b.type in ('CAPTCHA','MFA','ACCOUNT_CREATION','LOGIN','OTHER')
               order by a.id, b.created_at desc limit 10""").fetchall()
        if not rows:
            print("Nothing needs you right now.")
            return
        header, profile, evidence = db.contact_header(conn), db.profile_answers(conn), db.resume_evidence(conn)
        with sync_playwright() as pw, tempfile.TemporaryDirectory() as tmp:
            ctx = _browser(pw, headless=False)
            for row in rows:
                page = ctx.new_page()
                rv = (conn.execute("select content from resume_versions where id=%s", (row["resume_version_id"],)).fetchone()
                      if row["resume_version_id"] else None) or conn.execute(
                    "select content from resume_versions order by created_at desc limit 1").fetchone()
                if rv is None:
                    print(f"\n→ {row['company']} — {row['title']}: skipped (no tailored resume yet)")
                    page.close()
                    continue
                content = rv["content"]
                pdf = resume_pdf.render_pdf(content, header, Path(tmp) / resume_pdf.safe_name("Muskan_Nahata", row["company"] or "", row["title"]))
                page.goto(row["url"], wait_until="domcontentloaded")
                _pause(2, 3)
                fields = page.evaluate(FIELDS_JS)
                if fields:
                    res = answers_mod.resolve(fields, profile, evidence, row["title"], row["company"] or "")
                    by_id = {f["field_id"]: f for f in fields}
                    skipped = 0
                    for fid, value in res.values.items():
                        if fid in by_id and not try_fill(page, by_id[fid], value, pdf, None):
                            skipped += 1
                    page.set_default_timeout(30000)
                    if skipped:
                        print(f"  ({skipped} field(s) couldn't be auto-filled — please fill them yourself)")
                baseline = _body(page)
                label = f"{row['company']} — {row['title']}"
                app_id = str(row["id"])
                # Wait for her ONLY when something actually needs a human; otherwise carry on by itself.
                if _captcha(page) and not wait_for_human(r, page, app_id, label, what="captcha"):
                    continue
                if _missing_required(page):
                    print(f"\n→ {label}: some required fields are empty — fill them in the Chrome window (2 min)…")
                    if not wait_for_human(r, page, app_id, label, what="fields"):
                        continue
                if not (settings.can_write_external() and settings.submit_enabled):
                    print(f"\n✓ {label}: filled — dry run, not submitting.")
                    r.bump("would_submit")
                    page.wait_for_timeout(3000)
                    page.close()
                    continue
                page.get_by_role("button", name="Submit").or_(page.locator("button[type=submit], input[type=submit]")).first.click()
                page.wait_for_timeout(6000)
                if _code_prompt(page) and not wait_for_human(r, page, app_id, label, what="code"):
                    continue
                if _captcha(page) and not wait_for_human(r, page, app_id, label, what="captcha"):
                    continue
                page.wait_for_timeout(3000)
                now = _body(page)
                # Only a confirmation phrase that was NOT already on the page when we opened it counts.
                confirmed = any(t in now and t not in baseline for t in CONFIRM_TEXT)
                db.set_application_status(r, app_id, "SUBMITTED_CONFIRMED" if confirmed else "SUBMITTED", submitted_at=_now(),
                                          confirmation_url=page.url,
                                          confirmation_evidence="confirmation page (laptop)" if confirmed else None,
                                          next_action="outreach")
                conn.execute("update blockers set resolved_at=now(), resolution='Submitted from laptop helper' where id=%s", (row["blocker_id"],))
                r.bump("submitted_confirmed" if confirmed else "submitted_unconfirmed")
                print(f"\n✓ {label}: submitted{' (confirmed)' if confirmed else ' (no confirmation text seen — check later)'}.")
                if not page.is_closed():
                    page.close()
            ctx.close()


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)
