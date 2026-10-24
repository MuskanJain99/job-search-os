"""Project application state into the Notion 'Applications' database (Supabase stays authoritative)."""

from __future__ import annotations

import httpx

from . import db
from .config import settings
from .log import get_logger

log = get_logger("notion")
API = "https://api.notion.com/v1"
HEADERS = lambda: {"Authorization": f"Bearer {settings.notion_token}", "Notion-Version": "2022-06-28",  # noqa: E731
                   "Content-Type": "application/json"}

STATUS = {  # Supabase status -> (Status, Submission State, Pipeline Stage)
    "PREPARING": ("Application In Progress", "Preparing", "Preparing"),
    "READY": ("Application In Progress", "Ready for Submission", "Ready for Submission"),
    "BLOCKED": ("Application In Progress", "Blocked", "Preparing"),
    "SKIPPED": ("Application In Progress", "Blocked", "Closed"),   # reason shown in the Blocker column
    "CLOSED": ("No Response", "Not Started", "Closed"),
    "SUBMITTED": ("In Process", "Submitted Unconfirmed", "Closed"),
    "SUBMITTED_CONFIRMED": ("In Process", "Submitted Confirmed", "Closed"),
    "INTERVIEWING": ("Interview Scheduled", "Submitted Confirmed", "Closed"),
    "OFFER": ("Selected", "Submitted Confirmed", "Closed"),
    "REJECTED": ("Rejected", "Submitted Confirmed", "Closed"),
    "NO_RESPONSE": ("No Response", "Submitted Confirmed", "Closed"),
}
FIT = {"STRONG": "Strong", "GOOD": "Moderate", "STRETCH": "Weak", "SKIP": "Weak"}


def _props(a: dict) -> dict:
    st, sub, stage = STATUS.get(a["status"], ("Application In Progress", "Not Started", "Discovered"))
    p = {
        "Company": {"title": [{"text": {"content": (a["company"] or "")[:200]}}]},
        "Position": {"rich_text": [{"text": {"content": (a["title"] or "")[:200]}}]},
        "Job URL": {"url": a["url"]},
        "Status": {"status": {"name": st}},
        "Submission State": {"select": {"name": sub}},
        "Pipeline Stage": {"select": {"name": stage}},
        "Fit": {"select": {"name": FIT.get(a["verdict"] or "", "Unknown")}},
        "Priority": {"select": {"name": {1: "High", 2: "High", 3: "Medium"}.get(a["priority"], "Low")}},
        "Application Submitted?": {"select": {"name": "Yes" if a["submitted_at"] else "No"}},
        "Next Action": {"rich_text": [{"text": {"content": (a["next_action"] or "")[:200]}}]},
        "Blocker": {"rich_text": [{"text": {"content": (a["blocker"] or a.get("skip_reason") or "")[:500]}}]},
    }
    if a["submitted_at"]:
        p["Date of Application"] = {"date": {"start": a["submitted_at"].date().isoformat()}}
    return p


def run() -> None:
    with db.run("notion_sync") as r:
        if not settings.notion_configured:
            r.partial("notion not configured")
            return
        if not settings.can_write_external():
            r.summary["skipped"] = "dry_run"
            return
        rows = r.conn.execute(
            """select a.id, a.status, a.skip_reason, a.priority, a.submitted_at, a.next_action, a.notion_page_id, j.title, j.url,
                      c.name as company, f.verdict,
                      (select coalesce(question_text, detail) from blockers b where b.application_id=a.id and b.resolved_at is null
                       order by created_at desc limit 1) as blocker
               from applications a join jobs j on j.id=a.job_id left join companies c on c.id=j.company_id
               left join lateral (select verdict from fit_scores where job_id=j.id order by created_at desc limit 1) f on true
               where a.updated_at > now() - interval '2 days' or a.notion_page_id is null
               order by a.updated_at desc limit 200""").fetchall()
        with httpx.Client(timeout=30, headers=HEADERS()) as c:
            for a in rows:
                props = _props(a)
                try:
                    res = None
                    if a["notion_page_id"]:
                        res = c.patch(f"{API}/pages/{a['notion_page_id']}", json={"properties": props})
                        if res.status_code == 400 and "archived" in res.text:  # page was deleted in Notion: recreate it
                            res = None
                        else:
                            res.raise_for_status()
                    if res is None:
                        res = c.post(f"{API}/pages", json={"parent": {"database_id": settings.notion_applications_db},
                                                           "properties": props})
                        res.raise_for_status()
                        r.conn.execute("update applications set notion_page_id=%s where id=%s", (res.json()["id"], a["id"]))
                    r.bump("synced")
                except httpx.HTTPError as exc:
                    r.bump("errors")
                    resp = getattr(exc, "response", None)
                    if resp is not None and "first_error" not in r.summary:
                        r.summary["first_error"] = f"{resp.status_code} {resp.text[:300]}"
                    elif resp is None and "first_error" not in r.summary:
                        r.summary["first_error"] = type(exc).__name__
