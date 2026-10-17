"""Employer-portal accounts (Workday, iCIMS, Taleo, SuccessFactors, company career sites).

With Muskan's permission, the laptop helper may create an account on a careers portal that requires one:
  * username = her email; password = a new random password unique to that portal,
    stored ONLY in Supabase Vault (secret name: portal_<host>) — never in tables, logs or code;
  * only REQUIRED terms/privacy consents are ticked; marketing opt-ins stay unticked;
  * the verification link/code is read from her Gmail and used for that portal only.
Never automated: CAPTCHA / bot-detection challenges, SMS/phone codes.
"""

from __future__ import annotations

import re
import secrets
import string
import time
from urllib.parse import urlparse

from . import db
from .log import get_logger

log = get_logger("accounts")

SIGNUP_WORDS = ("create account", "create an account", "sign up", "register", "new user")
SIGNIN_WORDS = ("sign in", "log in", "login")
REQUIRED_CONSENT = re.compile(r"(terms|privacy|data (protection|processing)|consent to (the )?processing|i (have read|agree|accept))", re.I)
MARKETING = re.compile(r"(marketing|newsletter|job alerts?|promotional|similar (jobs|roles)|talent community|sms|text messages)", re.I)


def portal_key(url: str) -> str:
    host = urlparse(url).netloc.lower()
    # Workday tenants live at <tenant>.wdN.myworkdayjobs.com — one account per tenant.
    return re.sub(r"[^a-z0-9]+", "_", host).strip("_")[:120]


def new_password() -> str:
    alphabet = string.ascii_letters + string.digits
    core = "".join(secrets.choice(alphabet) for _ in range(18))
    return core[:6] + secrets.choice("!@#$%") + core[6:12] + secrets.choice("23456789") + core[12:] + "Aa"


def get_password(conn, key: str) -> str | None:
    row = conn.execute("select decrypted_secret from vault.decrypted_secrets where name=%s", (f"portal_{key}",)).fetchone()
    return row["decrypted_secret"] if row else None


def save_password(r: db.Run, key: str, password: str, url: str) -> None:
    r.conn.execute("select vault.create_secret(%s, %s, %s)", (password, f"portal_{key}", f"Job portal account for {urlparse(url).netloc}"))
    r.audit("portal.account_created", "vault", f"portal_{key}", host=urlparse(url).netloc)  # never the password


def wait_for_verification(email_host_hint: str, since_epoch: float, timeout_s: int = 600) -> tuple[str | None, str | None]:
    """Poll Gmail for the portal's verification email. Returns (link, code)."""
    from .google_api import get_thread, search_threads, thread_text

    deadline = time.time() + timeout_s
    q = f"newer_than:1h (verify OR verification OR activate OR confirm OR \"one-time\" OR code) {email_host_hint}"
    while time.time() < deadline:
        for t in search_threads(q, 5):
            thread = get_thread(t["id"])
            msgs = thread.get("messages", [])
            if not msgs or int(msgs[-1].get("internalDate", "0")) / 1000 < since_epoch - 30:
                continue
            _, _, _, text = thread_text(thread, 8000)
            link = next((u for u in re.findall(r"https?://[^\s\"'<>)]+", text)
                         if re.search(r"(verif|activat|confirm|validate|token)", u, re.I)), None)
            code = None
            m = re.search(r"(?:code|otp|pin)[^0-9]{0,40}(\d{4,8})", text, re.I)
            if m:
                code = m.group(1)
            if link or code:
                return link, code
        time.sleep(15)
    return None, None


def handle_portal_auth(r: db.Run, page, app_id: str, email: str, first: str, last: str) -> bool:
    """Sign in (or sign up, then verify) on an employer portal. Returns True when past the auth wall."""
    from .laptop import wait_for_human
    from .submit import _captcha

    key = portal_key(page.url)
    pwd = get_password(r.conn, key)
    body = page.inner_text("body").lower()
    if _captcha(page) and not wait_for_human(r, page, app_id, f"portal sign-in ({urlparse(page.url).netloc})"):
        return False

    if pwd is None and any(w in body for w in SIGNUP_WORDS):
        _click_text(page, ("Create Account", "Create an account", "Sign Up", "Register"))
        time.sleep(2)
        pwd = new_password()
        started = time.time()
        _fill_auth_form(page, email, pwd, first, last)
        _tick_required_consents(page)
        if _captcha(page) and not wait_for_human(r, page, app_id, f"portal sign-up ({urlparse(page.url).netloc})"):
            return False
        _click_text(page, ("Create Account", "Sign Up", "Register", "Submit", "Continue"))
        time.sleep(4)
        if any(w in page.inner_text("body").lower() for w in ("sms", "text message code", "code sent to your phone")):
            if not wait_for_human(r, page, app_id, f"portal SMS code ({urlparse(page.url).netloc})", what="code"):
                return False
        save_password(r, key, pwd, page.url)
        link, code = wait_for_verification(urlparse(page.url).netloc.split(".")[-2], started)
        if link:
            page.goto(link, wait_until="domcontentloaded")
            time.sleep(3)
        elif code:
            box = page.locator("input[autocomplete='one-time-code'], input[name*='code' i], input[id*='code' i]").first
            if box.count():
                box.fill(code)
                _click_text(page, ("Verify", "Submit", "Continue"))
                time.sleep(3)
        r.bump("portal_accounts_created")

    body = page.inner_text("body").lower()
    if pwd and page.locator("input[type=password]").count():
        _fill_auth_form(page, email, pwd, first, last, signin=True)
        _click_text(page, ("Sign In", "Log In", "Login", "Continue", "Submit"))
        time.sleep(4)
    if page.locator("input[type=password]").count() and any(w in page.inner_text("body").lower() for w in SIGNIN_WORDS):
        return wait_for_human(r, page, app_id, f"portal sign-in ({urlparse(page.url).netloc})", what="login")
    return True


def _fill_auth_form(page, email: str, pwd: str, first: str, last: str, signin: bool = False) -> None:
    for sel, val in (("input[type=email], input[name*='email' i], input[id*='email' i], input[autocomplete=username]", email),
                     ("input[name*='first' i]", first), ("input[name*='last' i]", last)):
        loc = page.locator(sel)
        if loc.count() and (not signin or "email" in sel):
            loc.first.fill(val)
    for i in range(page.locator("input[type=password]").count()):  # password + confirm password
        page.locator("input[type=password]").nth(i).fill(pwd)


def _tick_required_consents(page) -> None:
    boxes = page.locator("input[type=checkbox]")
    for i in range(boxes.count()):
        b = boxes.nth(i)
        label = b.evaluate("el => (el.closest('label') || el.parentElement || el).innerText || el.getAttribute('aria-label') || ''")
        if REQUIRED_CONSENT.search(label or "") and not MARKETING.search(label or ""):
            b.check(force=True)


def _click_text(page, names: tuple[str, ...]) -> bool:
    for n in names:
        for role in ("button", "link"):
            loc = page.get_by_role(role, name=n, exact=False)
            if loc.count():
                loc.first.click()
                return True
    return False
