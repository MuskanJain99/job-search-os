"""Cloud submission for public employer forms (Greenhouse, Lever, Ashby, SmartRecruiters, Workable).

Hard stops (become blockers, never workarounds): CAPTCHA, emailed/SMS verification codes, login or
account creation, assessments, legal declarations, ID/passport/visa fields, ASK_ME / NEVER_ANSWER
questions, required questions without an approved answer.

Two switches gate the final click: DRY_RUN=false AND SUBMIT_ENABLED=true. Otherwise the form is
filled and screenshotted, and the application stays READY.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from . import answering, db, resume_pdf
from .config import settings
from .log import get_logger
from .sources.aggregators import CLOUD_SUBMIT

log = get_logger("submit")

# JS: describe every visible form control with a stable ref.
FIELDS_JS = r"""
() => {
  const vis = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return el.type === 'file' || (r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'); };
  const labelOf = el => {
    let t = '';
    if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l) t = l.innerText; }
    if (!t) { const l = el.closest('label'); if (l) t = l.innerText; }
    if (!t) t = el.getAttribute('aria-label') || '';
    if (!t) { const lb = el.getAttribute('aria-labelledby'); if (lb) t = lb.split(' ').map(i => (document.getElementById(i)||{}).innerText||'').join(' '); }
    if (!t) { const fs = el.closest('fieldset'); if (fs) { const lg = fs.querySelector('legend'); if (lg) t = lg.innerText; } }
    if (!t) { const c = el.closest('.field, .application-question, [class*="question"], [class*="Field"]'); if (c) { const l = c.querySelector('label, legend, .text, [class*="label"]'); if (l) t = l.innerText; } }
    if (!t) t = el.getAttribute('placeholder') || el.getAttribute('name') || '';
    return t.replace(/\s+/g, ' ').trim().slice(0, 300);
  };
  const out = []; let n = 0; const radiosDone = new Set();
  for (const el of document.querySelectorAll('input, textarea, select, [role=combobox]')) {
    const type = (el.getAttribute('type') || el.tagName).toLowerCase();
    if (['hidden','submit','button','search'].includes(type) || !vis(el)) continue;
    if (type === 'radio' || type === 'checkbox') {
      const group = el.name || el.id; if (radiosDone.has(group)) continue; radiosDone.add(group);
      const opts = [...document.querySelectorAll(`input[name="${CSS.escape(el.name)}"]`)].map(o => ({ref: (o.dataset.jb = 'f' + (n++)), label: labelOf(o)}));
      const fs = el.closest('fieldset'); const q = fs && fs.querySelector('legend') ? fs.querySelector('legend').innerText : labelOf(el);
      out.push({field_id: el.name || opts[0].ref, label: q.trim().slice(0,300), type, required: el.required, options: opts.map(o=>o.label), refs: opts.map(o=>o.ref)});
      continue;
    }
    const ref = el.dataset.jb = 'f' + (n++);
    const item = {field_id: ref, label: labelOf(el), type: type === 'textarea' ? 'textarea' : type === 'select' ? 'select' : type === 'file' ? 'file' : el.getAttribute('role') === 'combobox' ? 'combobox' : 'text',
                  required: el.required || el.getAttribute('aria-required') === 'true' || /\*\s*$/.test(labelOf(el)), refs: [ref]};
    if (el.tagName === 'SELECT') item.options = [...el.options].map(o => o.text.trim()).filter(Boolean).slice(0, 60);
    out.push(item);
  }
  return out;
}
"""

STOP_TEXT = ("verify you are human", "i'm not a robot", "captcha", "security code", "verification code",
             "enter the code", "create an account", "create account", "sign in to apply", "log in to apply",
             "assessment", "coding challenge")
CONFIRM_TEXT = ("thank you for applying", "application has been submitted", "application submitted",
                "we have received your application", "thanks for applying", "application received",
                "successfully submitted")


def _apply_url(ats_name: str, url: str) -> str:
    if ats_name == "lever" and not url.rstrip("/").endswith("/apply"):
        return url.rstrip("/") + "/apply"
    if ats_name == "ashby" and "/application" not in url:
        return url.rstrip("/") + "/application"
    return url


def _captcha(page) -> bool:
    sel = "iframe[src*='recaptcha'], iframe[src*='hcaptcha'], iframe[src*='turnstile'], .g-recaptcha, .h-captcha, .cf-turnstile"
    return page.locator(sel).count() > 0 and page.locator("iframe[src*='recaptcha'][src*='invisible']").count() == 0


def run() -> None:
    with db.run("submit") as r:
        conn = r.conn
        cfg = db.pipeline_config(conn)
        if cfg["paused"]:
            r.summary["skipped"] = "paused"
            return
        usage = db.daily_usage(conn)
        budget = max(0, cfg["max_applications_per_day"] - (usage["applications_today"] or 0))
        queue = conn.execute(
            """select a.id, a.channel, a.resume_version_id, a.cover_letter, j.url, j.title, j.jd_text, j.status as job_status,
                      c.name as company, f.score
               from applications a join jobs j on j.id=a.job_id left join companies c on c.id=j.company_id
               left join lateral (select score from fit_scores where job_id=j.id order by created_at desc limit 1) f on true
               where a.status='READY' and a.next_action='submit' and a.channel = any(%s)
               order by (a.discovered_via='user_link') desc, a.priority, f.score desc nulls last limit %s""",
            (list(CLOUD_SUBMIT), budget)).fetchall()
        r.summary["queued"] = len(queue)
        if not queue:
            return
        header = db.contact_header(conn)
        profile = db.profile_answers(conn)
        evidence = db.resume_evidence(conn)
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw, tempfile.TemporaryDirectory() as tmp:
            browser = pw.chromium.launch()
            for app in queue:
                ctx = browser.new_context(locale="en-IN", timezone_id="Asia/Kolkata", viewport={"width": 1366, "height": 900})
                try:
                    submit_one(r, ctx.new_page(), app, header, profile, evidence, Path(tmp))
                except Exception as exc:
                    r.bump("errors")
                    r.audit("submit.error", "applications", str(app["id"]), kind=type(exc).__name__, msg=str(exc)[:300])
                finally:
                    ctx.close()
            browser.close()


def submit_one(r: db.Run, page, app: dict, header: dict, profile, evidence, tmp: Path) -> None:
    conn, app_id = r.conn, str(app["id"])
    content = conn.execute("select content from resume_versions where id=%s", (app["resume_version_id"],)).fetchone()["content"]
    pdf = resume_pdf.render_pdf(content, header, tmp / resume_pdf.safe_name("Muskan_Nahata", app["company"] or "", app["title"]))
    url = _apply_url(app["channel"], app["url"])
    resp = page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2500)
    body = page.inner_text("body").lower()[:20000]
    if (resp and resp.status == 404) or any(t in body for t in ("no longer accepting", "job is closed", "position has been filled", "no longer available")):
        db.set_application_status(r, app_id, "CLOSED", next_action=None)
        conn.execute("update jobs set status='CLOSED' where url=%s", (app["url"],))
        r.bump("closed")
        return
    if "apply" in body and page.locator("form").count() == 0:
        btn = page.get_by_role("button", name="Apply").or_(page.get_by_role("link", name="Apply")).first
        if btn.count():
            btn.click()
            page.wait_for_timeout(2000)
    needs_login = any(t in body for t in ("create an account", "create account", "sign in to apply", "log in to apply"))
    if needs_login:  # portal accounts are created by the laptop helper (accounts.py), not in the cloud
        db.set_application_status(r, app_id, "READY", next_action="laptop_submit")
        r.bump("handed_to_laptop")
        return
    if _captcha(page):  # the cloud has no screen: hand to the laptop helper, which alerts her and waits 2 min
        db.set_application_status(r, app_id, "READY", next_action="laptop_submit")
        r.bump("captcha_to_laptop")
        return

    fields = page.evaluate(FIELDS_JS)
    stored = {row["question_text"]: row["answer"] for row in conn.execute(
        "select question_text, answer from app_answers where application_id=%s", (app_id,)).fetchall()}
    res = answering.resolve_with_owner(r, app_id, fields, profile, evidence, app["title"], app["company"] or "")
    if res is None:
        return  # she was alerted; no answer in time → recorded, move on
    for a in res.provenance:
        if a["question_text"] not in stored:
            conn.execute("insert into app_answers (application_id, question_text, profile_id, answer, basis) values (%s,%s,%s,%s,%s)",
                         (app_id, a["question_text"], a["profile_id"], a["answer"], a["basis"]))

    by_id = {f["field_id"]: f for f in fields}
    for fid, value in res.values.items():
        f = by_id.get(fid)
        if not f:
            continue
        fill(page, f, value, pdf, app.get("cover_letter"))
    shot = tmp / f"{app_id}.png"
    page.screenshot(path=str(shot), full_page=True)

    if not (settings.can_write_external() and settings.submit_enabled):
        r.bump("would_submit")
        r.audit("submit.dry_run", "applications", app_id, filled=len(res.values))
        return
    page.get_by_role("button", name="Submit").or_(page.locator("button[type=submit], input[type=submit]")).first.click()
    page.wait_for_timeout(6000)
    after = page.inner_text("body").lower()[:20000]
    if any(t in after for t in ("verification code", "security code", "enter the code")) or _captcha(page):
        # The cloud can't hand this to a human; the laptop helper re-runs it with a live 2-minute hand-off.
        db.set_application_status(r, app_id, "READY", next_action="laptop_submit")
        r.bump("verification_to_laptop")
        return
    confirmed = any(t in after for t in CONFIRM_TEXT)
    db.set_application_status(r, app_id, "SUBMITTED_CONFIRMED" if confirmed else "SUBMITTED", submitted_at=_now(),
                              confirmation_url=page.url, confirmation_evidence=("confirmation page text" if confirmed else None),
                              next_action="outreach")
    r.bump("submitted_confirmed" if confirmed else "submitted")


def fill(page, f: dict, value: str, pdf: Path, cover: str | None) -> None:
    refs = f.get("refs") or [f["field_id"]]
    loc = page.locator(f"[data-jb='{refs[0]}']")
    t = f["type"]
    if t == "file":
        if value == "__RESUME__":
            loc.set_input_files(str(pdf))
        elif value == "__COVER_LETTER__" and cover:
            p = pdf.with_name(pdf.stem + "_cover.txt")
            p.write_text(cover)
            loc.set_input_files(str(p))
        return
    if t == "select":
        try:
            loc.select_option(label=value)
        except Exception:
            loc.select_option(value=value)
        return
    if t in ("radio", "checkbox"):
        for ref, label in zip(refs, f.get("options") or [], strict=False):
            if label.strip().lower() == value.strip().lower() or (t == "checkbox" and value.lower() in ("yes", "true")):
                page.locator(f"[data-jb='{ref}']").check(force=True)
                break
        return
    if t == "combobox":
        loc.click()
        loc.fill(value)
        page.wait_for_timeout(700)
        page.keyboard.press("Enter")
        return
    loc.fill(value)


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)
