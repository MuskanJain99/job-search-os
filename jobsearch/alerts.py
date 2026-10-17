"""Alert policy: silence by default; interrupt Muskan only when it is necessary.

Alert-worthy kinds (anything else goes to the weekly digest):
  interview, offer, assessment, human_reply, account, pipeline_broken, needs_input (max 1/day).
Delivery: email to OWNER_EMAIL with subject "[Job Search] ACTION: …" and, if NTFY_TOPIC is set,
a phone push via ntfy.sh. Each alert key is sent at most once per 24 hours.
"""

from __future__ import annotations

import httpx

from . import db
from .config import settings
from .log import get_logger

log = get_logger("alerts")

ALLOWED = {"interview", "offer", "assessment", "human_reply", "account", "pipeline_broken", "needs_input", "captcha"}
URGENT = {"captcha", "interview", "offer"}  # also go by SMS when Twilio is configured


def should_send(kind: str) -> bool:
    return kind in ALLOWED


def already_sent(conn, key: str, hours: int = 24) -> bool:
    return conn.execute(
        "select 1 from audit_log where action='alert.sent' and details->>'key'=%s and at > now() - make_interval(hours => %s)",
        (key, hours)).fetchone() is not None


def alert(r: db.Run, kind: str, key: str, text: str, *, live: bool = False) -> bool:
    """Send one alert (<200 chars). live=True (CAPTCHA hand-off) skips the 24h dedupe and the dry-run gate,
    because a human is needed right now to continue."""
    if not should_send(kind):
        return False
    full_key = f"{kind}:{key}"
    if not live and already_sent(r.conn, full_key, 24):
        return False
    text = text.strip().replace("\n", " ")[:200]
    delivered = []
    if settings.can_write_external() or live:
        if settings.google_configured and settings.owner_email:
            from .google_api import send_email

            send_email(settings.owner_email, f"[Job Search] ACTION: {text[:80]}", text + "\n\n— job-search-os")
            delivered.append("email")
        if settings.ntfy_topic:  # iPhone/Android push via the free ntfy app
            try:
                httpx.post(f"https://ntfy.sh/{settings.ntfy_topic}", content=text.encode(),
                           headers={"Title": "Job Search", "Priority": "urgent" if kind == "captcha" else
                                    "high" if kind in URGENT else "default", "Tags": "rotating_light" if kind == "captcha" else ""},
                           timeout=15)
                delivered.append("push")
            except httpx.HTTPError:
                pass
        if kind in URGENT and settings.twilio_sid and settings.owner_phone:
            try:
                httpx.post(f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_sid}/Messages.json",
                           auth=(settings.twilio_sid, settings.twilio_token),
                           data={"From": settings.twilio_from, "To": settings.owner_phone, "Body": text[:300]}, timeout=15)
                delivered.append("sms")
            except httpx.HTTPError:
                pass
    r.audit("alert.sent", "alerts", None, key=full_key, text=text, channels=delivered, dry_run=settings.dry_run)
    r.bump("alerts")
    return True
