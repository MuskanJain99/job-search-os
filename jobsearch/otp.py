"""Read emailed security / verification codes from her Gmail so submissions that ask for one can finish on their own."""

from __future__ import annotations

import re
import time

from . import google_api
from .log import get_logger

log = get_logger("otp")

_QUERY = 'newer_than:1d (subject:"security code" OR subject:"verification code" OR subject:"verify your" OR "security code" OR "verification code")'
_CODE = [
    re.compile(r"(?:code|otp|passcode)\s*(?:is|:)?\s*[:\-]?\s*\n*\s*([A-Za-z0-9]{6,8})\b"),
    re.compile(r"\n\s*([A-Z0-9]{6,8})\s*\n"),
    re.compile(r"\b(\d{6})\b"),
]


def extract_code(text: str) -> str | None:
    for pat in _CODE:
        for m in pat.finditer(text):
            c = m.group(1)
            if c.lower() in {"verify", "account", "applic", "security", "company"} or not any(ch.isdigit() for ch in c) and len(c) < 8:
                continue
            return c
    return None


def fetch_code(company: str, started_ms: int, wait_s: int = 120) -> str | None:
    """Newest code mailed after `started_ms` (epoch ms). Prefers mail mentioning the company."""
    deadline = time.monotonic() + wait_s
    first = (company or "").split()[0].lower() if company else ""
    while time.monotonic() < deadline:
        try:
            best = None
            for t in google_api.search_threads(_QUERY, max_results=10):
                th = google_api.get_thread(t["id"])
                for m in th.get("messages", []):
                    if int(m.get("internalDate", 0)) < started_ms - 30000:
                        continue
                    subject, _sender, _mid, text = google_api.thread_text({"messages": [m]})
                    code = extract_code(subject + "\n" + text)
                    if not code:
                        continue
                    score = (first in (subject + text).lower() if first else False, int(m["internalDate"]))
                    if best is None or score > best[0]:
                        best = (score, code)
            if best:
                return best[1]
        except Exception as exc:  # noqa: BLE001
            log.info("code lookup failed: %s", type(exc).__name__)
        time.sleep(10)
    return None


def enter_code(page, code: str) -> bool:
    """Type the code into Greenhouse-style split boxes or a single input."""
    boxes = page.locator("input[id^='security-input'], input[maxlength='1']:visible")
    n = boxes.count()
    if n >= len(code):
        for i, ch in enumerate(code):
            boxes.nth(i).fill(ch)
        return True
    single = page.locator("input[name*='code' i]:visible, input[placeholder*='code' i]:visible, input[autocomplete='one-time-code']").first
    if single.count():
        single.fill(code)
        return True
    return False
