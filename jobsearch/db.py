"""Postgres access to the job-search-os database (Supabase).

The database is the system of record. Every state change is paired with an audit_log row.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .config import settings
from .log import get_logger

log = get_logger("db")
ACTOR = "ACTION"  # audit_log.actor value for this service (allowed: CLAUDE, ACTION, USER)


def connect() -> psycopg.Connection:
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is not set")
    return psycopg.connect(settings.database_url, row_factory=dict_row, autocommit=True, prepare_threshold=None)


@dataclass
class Run:
    conn: psycopg.Connection
    workflow: str
    id: str | None = None
    summary: dict[str, Any] = field(default_factory=dict)
    status: str = "OK"

    def audit(self, action: str, entity: str | None = None, entity_id: str | None = None, **details: Any) -> None:
        self.conn.execute(
            "insert into audit_log (actor, action, entity, entity_id, run_id, details) values (%s,%s,%s,%s,%s,%s)",
            (ACTOR, action, entity, entity_id, self.id, Jsonb(details)),
        )

    def bump(self, key: str, n: int = 1) -> None:
        self.summary[key] = self.summary.get(key, 0) + n

    def partial(self, reason: str) -> None:
        self.status = "PARTIAL"
        self.summary.setdefault("why_partial", []).append(reason)


@contextmanager
def run(workflow: str) -> Iterator[Run]:
    """Opens a connection and a runs row; closes the row with OK / PARTIAL / FAILED."""
    conn = connect()
    r = Run(conn=conn, workflow=workflow)
    row = conn.execute(
        "insert into runs (workflow, status, summary, actor) values (%s,'RUNNING',%s,%s) returning id",
        (workflow, Jsonb({"dry_run": settings.dry_run}), ACTOR),
    ).fetchone()
    r.id = str(row["id"])
    log.info("run %s started (%s, dry_run=%s)", workflow, r.id, settings.dry_run)
    try:
        yield r
    except Exception as exc:  # noqa: BLE001 — record and re-raise
        r.status = "FAILED"
        r.summary["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
        raise
    finally:
        r.summary["dry_run"] = settings.dry_run
        conn.execute(
            "update runs set status=%s, finished_at=now(), summary=%s where id=%s",
            (r.status, Jsonb(r.summary), r.id),
        )
        log.info("run %s finished: %s %s", workflow, r.status, json.dumps(_safe_summary(r.summary)))
        conn.close()


def _safe_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """Only numbers and short status words go to public logs."""
    return {k: v for k, v in summary.items() if isinstance(v, (int, float, bool)) or k in {"error"}}


# ---------- reads ----------

def pipeline_config(conn: psycopg.Connection) -> dict[str, Any]:
    return conn.execute("select * from pipeline_config where id=1").fetchone()


def daily_usage(conn: psycopg.Connection) -> dict[str, Any]:
    return conn.execute("select * from v_daily_usage").fetchone()


def profile_answers(conn: psycopg.Connection, on: datetime | None = None) -> list[dict[str, Any]]:
    """Answers valid today (Valid From / Valid Until respected)."""
    day = (on or datetime.now(timezone.utc)).date()
    return conn.execute(
        """select id, category, field, answer, answer_type, applies_when, validity_note, notes
           from profile_answers
           where (valid_from is null or valid_from <= %s) and (valid_until is null or valid_until >= %s)
           order by id""",
        (day, day),
    ).fetchall()


def resume_evidence(conn: psycopg.Connection) -> list[dict[str, Any]]:
    return conn.execute(
        """select claim_id, project_ref, role, claim, metrics, technologies
           from evidence_bank where status='IMPLEMENTED' and allowed_in in ('resume','both') order by claim_id"""
    ).fetchall()


def resume_skeleton(conn: psycopg.Connection) -> dict[str, Any]:
    return conn.execute("select experience, education from resume_skeleton where id=1").fetchone()


def contact_header(conn: psycopg.Connection) -> dict[str, str]:
    rows = conn.execute(
        "select id, answer from profile_answers where id in ('ID-03','CON-01','CON-02','CON-03','CON-04','CON-05','LOC-02')"
    ).fetchall()
    a = {r["id"]: r["answer"] for r in rows}
    return {
        "name": a.get("ID-03", ""),
        "phone": f"{a.get('CON-01', '')} {a.get('CON-02', '')}".strip(),
        "email": a.get("CON-03", ""),
        "linkedin": a.get("CON-04", ""),
        "github": a.get("CON-05", ""),
        "location": a.get("LOC-02", ""),
    }


def latest_runbook(conn: psycopg.Connection, workflow: str) -> str:
    row = conn.execute(
        "select body from runbooks where workflow=%s and active order by version desc limit 1", (workflow,)
    ).fetchone()
    return row["body"] if row else ""


# ---------- writes ----------

def open_blocker(r: Run, application_id: str | None, type_: str, detail: str, question: str | None = None) -> str:
    row = r.conn.execute(
        """insert into blockers (application_id, type, detail, question_text) values (%s,%s,%s,%s) returning id""",
        (application_id, type_, detail[:2000], (question or "")[:2000] or None),
    ).fetchone()
    if application_id:
        set_application_status(r, application_id, "BLOCKED", next_action="resolve_blocker")
    r.audit("blocker.opened", "blockers", str(row["id"]), type=type_)
    r.bump("blockers_opened")
    return str(row["id"])


def set_application_status(r: Run, application_id: str, status: str, **fields: Any) -> None:
    cols = ["status=%s", "updated_at=now()", "last_status_change_at=now()"]
    vals: list[Any] = [status]
    for k, v in fields.items():
        cols.append(f"{k}=%s")
        vals.append(v)
    vals.append(application_id)
    r.conn.execute(f"update applications set {', '.join(cols)} where id=%s", vals)
    r.audit("application.status", "applications", application_id, status=status)
