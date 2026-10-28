"""Cloud submission for public employer forms (Greenhouse, Lever, Ashby, SmartRecruiters, Workable).

Hard stops (become blockers, never workarounds): CAPTCHA, emailed/SMS verification codes, login or
account creation, assessments, legal declarations, ID/passport/visa fields, ASK_ME / NEVER_ANSWER
questions, required questions without an approved answer.

Two switches gate the final click: DRY_RUN=false AND SUBMIT_ENABLED=true. Otherwise the form is
filled and screenshotted, and the application stays READY.
"""

from __future__ import annotations

import os
import re
import tempfile
import time
from pathlib import Path

from . import answering, answers, db, otp, ranges, resume_pdf
from .config import settings
from .log import get_logger
from .sources.aggregators import CLOUD_TRY

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
    if (!t) { let a = el.parentElement;
      for (let i = 0; i < 5 && a && !t; i++, a = a.parentElement) {
        for (const l of a.querySelectorAll('label, legend, [class*="label"], [class*="Label"], h3, h4')) {
          if (!l.contains(el) && !l.querySelector('input, select, textarea') && (l.innerText || '').trim().length > 2) { t = l.innerText; break; }
        }
      } }
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

STOP_TEXT = ("verify you are human", "i'm not a robot", "captcha", "create an account", "create account", "sign in to apply", "log in to apply",
             "assessment", "coding challenge")
CONFIRM_TEXT = ("thank you for applying", "application has been submitted", "application submitted",
                "we have received your application", "thanks for applying", "application received",
                "successfully submitted")


def _apply_url(ats_name: str, url: str, token: str | None = None) -> str:
    if ats_name == "greenhouse" and token:
        m = re.search(r"gh_jid=(\d+)", url) or re.search(r"greenhouse\.io/[^/]+/jobs/(\d+)", url)
        if m and "greenhouse.io" not in url.split("/")[2]:
            # Company-hosted career pages embed the form (0 forms found, Apply button times out): use the board itself.
            return f"https://job-boards.greenhouse.io/{token}/jobs/{m.group(1)}"
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
        if not resume_pdf.ensure_default(r):
            return
        usage = db.daily_usage(conn)
        budget = max(0, cfg["max_applications_per_day"] - (usage["applications_today"] or 0))
        queue = conn.execute(
            """select a.id, a.channel, a.resume_version_id, a.cover_letter, j.url, j.title, j.jd_text, j.status as job_status,
                      c.name as company, c.ats as company_ats, c.ats_token, f.score
               from applications a join jobs j on j.id=a.job_id left join companies c on c.id=j.company_id
               left join lateral (select score from fit_scores where job_id=j.id order by created_at desc limit 1) f on true
               where a.status='READY' and a.next_action in ('submit','laptop_submit') and a.channel = any(%s)
               order by (a.discovered_via='user_link') desc,
                        row_number() over (partition by c.id order by a.priority, f.score desc nulls last),  -- one per company first
                        a.priority, f.score desc nulls last limit %s""",
            (list(CLOUD_TRY), budget)).fetchall()
        r.summary["queued"] = len(queue)
        if not queue:
            return
        header = db.contact_header(conn)
        profile = db.profile_answers(conn)
        evidence = db.resume_evidence(conn)
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw, tempfile.TemporaryDirectory() as tmp:
            browser = pw.chromium.launch()
            company_errors: dict[str, int] = {}
            stop_at = time.monotonic() + float(os.environ.get("SUBMIT_TIME_BUDGET_S", "2400"))  # finish cleanly before the 50-min job limit
            for app in queue:
                if time.monotonic() > stop_at:
                    r.summary["time_budget_hit"] = True
                    break
                if company_errors.get(app["company"] or "", 0) >= 2:  # this employer's form keeps failing: others get a turn
                    _skip(r, str(app["id"]), "this employer's form keeps failing")
                    r.bump("deferred_skipped")
                    continue
                ctx = browser.new_context(locale="en-IN", timezone_id="Asia/Kolkata", viewport={"width": 1366, "height": 900})
                try:
                    submit_one(r, ctx.new_page(), app, header, profile, evidence, Path(tmp))
                except answers.Deferred as d:
                    r.bump("deferred_llm")
                    r.audit("submit.deferred", "applications", str(app["id"]), needs=str(d)[:400])  # model rate-limited: stays READY for the next run, nobody is asked
                except Exception as exc:
                    r.bump("errors")
                    page_info = {}
                    try:
                        pg = ctx.pages[0]
                        page_info = {"url": pg.url[:150], "forms": pg.locator("form").count(),
                                     "buttons": [t.strip()[:30] for t in pg.locator("button, input[type=submit], a[role=button]").all_inner_texts()[:12]],
                                     "captcha": _captcha(pg)}
                    except Exception:
                        pass
                    r.audit("submit.error", "applications", str(app["id"]), kind=type(exc).__name__, msg=str(exc)[:300], page=page_info)
                    company_errors[app["company"] or ""] = company_errors.get(app["company"] or "", 0) + 1
                    tries = conn.execute("select count(*) n from audit_log where action='submit.error' and entity_id::text=%s", (str(app["id"]),)).fetchone()["n"]
                    if tries >= 2:  # failed twice: give up on this one, the queue moves on
                        _skip(r, str(app["id"]), f"form could not be completed ({type(exc).__name__})")
                finally:
                    ctx.close()
            browser.close()


def _skip(r: db.Run, app_id: str, why: str) -> None:
    db.set_application_status(r, app_id, "SKIPPED", skip_reason=why[:200], next_action=None)


def submit_one(r: db.Run, page, app: dict, header: dict, profile, evidence, tmp: Path) -> None:
    conn, app_id = r.conn, str(app["id"])
    pdf = resume_pdf.resume_for(conn, app, header, tmp / app_id)
    on_gh = app["channel"] == "greenhouse" or (app.get("company_ats") == "greenhouse" and "gh_jid=" in app["url"])
    url = _apply_url("greenhouse" if on_gh else app["channel"], app["url"], app.get("ats_token"))
    resp = page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2500)
    body = page.inner_text("body").lower()[:20000]
    if "error=true" in page.url:  # Greenhouse sends removed jobs to the board with ?error=true
        body += " job is closed"
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
    if needs_login:
        _skip(r, app_id, "account/sign-in required")
        r.bump("skip_login")
        return
    if _captcha(page):
        _skip(r, app_id, "CAPTCHA")
        r.bump("skip_captcha")
        return

    fields = page.evaluate(FIELDS_JS)
    stored = {row["question_text"]: row["answer"] for row in conn.execute(
        "select question_text, answer from app_answers where application_id=%s", (app_id,)).fetchall()}
    res = answering.resolve_with_owner(r, app_id, fields, profile, evidence, app["title"], app["company"] or "", jd=app.get("jd_text") or "")
    if res is None:
        return  # she was alerted; no answer in time → recorded, move on
    for a in res.provenance:
        if a["question_text"] not in stored:
            conn.execute("insert into app_answers (application_id, question_text, profile_id, answer, basis) values (%s,%s,%s,%s,%s)",
                         (app_id, a["question_text"], a["profile_id"], a["answer"], a["basis"]))

    by_id = {f["field_id"]: f for f in fields}
    failed = 0
    failed_fields: list[str] = []
    for fid, value in res.values.items():
        f = by_id.get(fid)
        if not f:
            continue
        if not try_fill(page, f, value, pdf, app.get("cover_letter")):
            failed += 1
            failed_fields.append(f"{f.get('type')}: {f.get('label', '')[:70]} = {value[:30]}")
    shot = tmp / f"{app_id}.png"
    page.screenshot(path=str(shot), full_page=True)

    if not (settings.can_write_external() and settings.submit_enabled):
        r.bump("would_submit")
        r.audit("submit.dry_run", "applications", app_id, filled=len(res.values))
        return
    if failed:
        # A field we meant to fill wasn't fillable: never submit a half-filled form from the cloud.
        r.audit("submit.fill_failed", "applications", app_id, fields=failed_fields[:6])
        _skip(r, app_id, "a form field could not be filled")
        r.bump("skip_fill_failed")
        return
    started_ms = int(time.time() * 1000)
    page.get_by_role("button", name="Submit").or_(page.locator("button[type=submit], input[type=submit]")).first.click(timeout=15000)
    page.wait_for_timeout(6000)
    after = page.inner_text("body").lower()[:20000]
    if _captcha(page):
        _skip(r, app_id, "CAPTCHA after submit")
        r.bump("skip_captcha")
        return
    if any(t in after for t in ("verification code", "security code", "enter the code")):
        code = otp.fetch_code(app["company"] or "", started_ms)
        if not code or not otp.enter_code(page, code):
            _skip(r, app_id, "verification code could not be read from email")
            r.bump("skip_verification")
            return
        r.audit("submit.otp", "applications", app_id)
        page.get_by_role("button", name="Submit").or_(page.locator("button[type=submit], input[type=submit]")).first.click()
        page.wait_for_timeout(6000)
        after = page.inner_text("body").lower()[:20000]
        if any(t in after for t in ("verification code", "security code", "enter the code")) or _captcha(page):
            _skip(r, app_id, "verification code rejected")
            r.bump("skip_verification")
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
    years = _years(value)
    mn = re.search(r"(\d+)\+?\s*(?:years|yrs)", f.get("label", "").lower())
    if years is not None and mn and t in ("select", "radio", "combobox") and \
            re.search(r"^(do|have|are|can)\b", f.get("label", "").strip().lower()):
        value, years = ("Yes" if years >= int(mn.group(1)) else "No"), None  # "Do you have 6+ years…?" is yes/no, not a range
    if t == "select":
        opts = loc.evaluate("el => [...el.options].map(o => o.text.trim())")
        if years is not None and ranges.looks_like_ranges(opts):
            i = ranges.pick(opts, years)
            if i is None:
                raise ValueError("no range contains the years")
            loc.select_option(index=i)
            return
        _select_fuzzy(loc, value)
        return
    if t in ("radio", "checkbox"):
        opts = f.get("options") or []
        idx = None
        if years is not None and ranges.looks_like_ranges(opts):
            idx = ranges.pick(opts, years)
        else:
            want = value.strip().lower()
            if t == "checkbox" and want in ("no", "false", "decline", "n/a"):
                return  # leave unticked (e.g. marketing opt-ins)
            idx = next((i for i, o in enumerate(opts) if o.strip().lower() == want), None)
            if idx is None and t == "checkbox" and want in ("yes", "true", "i agree", "agree"):
                idx = 0
        if idx is None:
            raise ValueError("no matching option")
        page.locator(f"[data-jb='{refs[idx]}']").check(force=True)
        return
    if t == "combobox":
        _combobox(page, loc, value, years)
        return
    label = f.get("label", "")
    if re.search(r"country code|dial", label, re.I) or (value.startswith("+") and value[1:].isdigit()):
        if _pick_phone_country(page, loc, value):
            return
    if re.search(r"phone|mobile|telephone", label, re.I) and _in_intl_widget(loc):
        # intl-tel-input reformats national numbers (India: "090000 52756"); typing the full
        # international number keeps it as "+91 90000 52756" and selects India in the flag dropdown.
        digits = re.sub(r"\D", "", value)
        loc.fill("")
        loc.press_sequentially(f"{PHONE_DIAL_CODE}{digits[-10:]}", delay=20)
        return
    loc.fill(value)


PHONE_DIAL_CODE = "+91"  # profile CON-01


def _years(value: str) -> float | None:
    from .answers import YEARS_MARKER

    v = value.strip()
    if v.startswith(YEARS_MARKER):
        v = v[len(YEARS_MARKER):]
    try:
        return float(v)
    except ValueError:
        return None


def _in_intl_widget(loc) -> bool:
    try:
        return loc.locator("xpath=ancestor::*[contains(concat(' ', normalize-space(@class), ' '), ' iti ')][1]").count() > 0
    except Exception:
        return False


def _combobox(page, loc, value: str, years: float | None) -> None:
    """React-select style dropdowns: type to filter, then click the best option (not just the first)."""
    loc.click()
    page.wait_for_timeout(400)
    typed = "" if years is not None else value
    if typed:
        loc.fill(typed)
        page.wait_for_timeout(800)
    options = page.locator("[role=option]")
    texts = [t.strip() for t in options.all_inner_texts()[:300]]
    idx = None
    if years is not None:
        idx = ranges.pick(texts, years) if ranges.looks_like_ranges(texts) else None
    else:
        want = value.strip().lower()
        idx = next((i for i, t in enumerate(texts) if t.lower() == want), None)
        if idx is None:  # "India" -> "India +91", not "British Indian Ocean Territory"
            idx = next((i for i, t in enumerate(texts) if re.match(rf"{re.escape(want)}\b", t.lower())), None)
        if idx is None:
            idx = next((i for i, t in enumerate(texts) if want and want in t.lower()), None)
    if idx is None:
        page.keyboard.press("Escape")
        raise ValueError("no matching option")
    options.nth(idx).click()


def _select_fuzzy(loc, value: str) -> None:
    """Pick an <option> by exact label, then by value, then by an option whose text contains the answer
    (e.g. '+91' -> 'India (+91)', 'India' -> 'India')."""
    try:
        loc.select_option(label=value)
        return
    except Exception:
        pass
    opts = loc.evaluate("el => [...el.options].map(o => [o.text.trim(), o.value])")
    want = value.strip().lower()
    for text, val in opts:
        if want == text.lower() or want == val.lower():
            loc.select_option(value=val)
            return
    for text, val in opts:
        if want and (want in text.lower() or (want.lstrip("+") and want in val.lower())):
            loc.select_option(value=val)
            return
    raise ValueError("no matching option")


def _pick_phone_country(page, number_or_select, value: str) -> bool:
    """Handle intl-tel-input style widgets: open the flag dropdown next to the field and choose the dial code."""
    code = value.lstrip("+")
    try:
        root = number_or_select.locator("xpath=ancestor::*[contains(@class,'iti')][1]")
        if root.count() == 0:
            return False
        root.locator(".iti__selected-country, .iti__selected-flag, button").first.click()
        page.locator(f"li.iti__country[data-dial-code='{code}']").first.click(timeout=3000)
        return True
    except Exception:
        return False


def try_fill(page, f: dict, value: str, pdf: Path, cover: str | None, timeout_ms: int = 6000) -> bool:
    """fill() that never raises: a field that vanished or isn't interactable is skipped, not fatal."""
    page.set_default_timeout(timeout_ms)
    try:
        fill(page, f, value, pdf, cover)
        return True
    except Exception as exc:  # noqa: BLE001
        log.info("could not fill a %s field: %s", f.get("type"), type(exc).__name__)
        return False


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)
