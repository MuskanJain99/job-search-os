"""Gmail + Google Calendar via an OAuth refresh token for Muskan's own account.

Scopes: gmail.modify (read, send, label) and calendar.events. Create the refresh token once with
`python scripts/google_oauth.py` on your machine and store it as a GitHub secret.
"""

from __future__ import annotations

import base64
from email.mime.text import MIMEText
from functools import lru_cache

from .config import settings

SCOPES = ["https://www.googleapis.com/auth/gmail.modify", "https://www.googleapis.com/auth/calendar.events"]


@lru_cache(maxsize=1)
def _creds():
    from google.oauth2.credentials import Credentials

    if not settings.google_configured:
        raise RuntimeError("Google OAuth secrets not set (GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_REFRESH_TOKEN)")
    return Credentials(None, refresh_token=settings.google_refresh_token, client_id=settings.google_client_id,
                       client_secret=settings.google_client_secret, token_uri="https://oauth2.googleapis.com/token",
                       scopes=SCOPES)


@lru_cache(maxsize=1)
def gmail():
    from googleapiclient.discovery import build

    return build("gmail", "v1", credentials=_creds(), cache_discovery=False)


@lru_cache(maxsize=1)
def calendar():
    from googleapiclient.discovery import build

    return build("calendar", "v3", credentials=_creds(), cache_discovery=False)


def send_email(to: str, subject: str, body: str, thread_id: str | None = None, in_reply_to: str | None = None) -> dict:
    msg = MIMEText(body, "plain", "utf-8")
    msg["to"] = to
    msg["subject"] = subject
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    body_ = {"raw": raw}
    if thread_id:
        body_["threadId"] = thread_id
    return gmail().users().messages().send(userId="me", body=body_).execute()


def search_threads(q: str, max_results: int = 50) -> list[dict]:
    res = gmail().users().threads().list(userId="me", q=q, maxResults=max_results).execute()
    return res.get("threads", []) or []


def get_thread(thread_id: str) -> dict:
    return gmail().users().threads().get(userId="me", id=thread_id, format="full").execute()


def thread_text(thread: dict, limit: int = 12000) -> tuple[str, str, str, str]:
    """(subject, from, last_message_id_header, plain text of all messages)."""
    parts, subject, sender, msg_id = [], "", "", ""
    for m in thread.get("messages", []):
        headers = {h["name"].lower(): h["value"] for h in m.get("payload", {}).get("headers", [])}
        subject = subject or headers.get("subject", "")
        sender = headers.get("from", sender)
        msg_id = headers.get("message-id", msg_id)
        parts.append(f"From: {headers.get('from', '')}\nDate: {headers.get('date', '')}\n{_body(m.get('payload', {}))}")
    return subject, sender, msg_id, "\n---\n".join(parts)[-limit:]


def _body(payload: dict) -> str:
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", "ignore")
    for p in payload.get("parts", []) or []:
        t = _body(p)
        if t:
            return t
    return ""


def free_busy(start_iso: str, end_iso: str) -> list[dict]:
    res = calendar().freebusy().query(body={"timeMin": start_iso, "timeMax": end_iso, "items": [{"id": "primary"}]}).execute()
    return res["calendars"]["primary"]["busy"]


def create_event(summary: str, start_iso: str, end_iso: str, description: str = "") -> str:
    ev = calendar().events().insert(calendarId="primary", body={
        "summary": summary, "description": description,
        "start": {"dateTime": start_iso}, "end": {"dateTime": end_iso},
        "reminders": {"useDefault": True}}).execute()
    return ev["id"]
