"""Do-not-disturb: no Chrome windows, push notifications, SMS or waiting while Muskan is busy.

Busy = (a) a Google Calendar event is in progress right now (interviews included), (b) inside QUIET_HOURS
("HH:MM-HH:MM" in OWNER_TIMEZONE, may cross midnight), or (c) she turned focus on by hand
(`jobsearch focus 2h`, stored in ~/.job-search-os/focus on the laptop). While busy the laptop helper skips its
run, alerts go by silent email only, and human questions are recorded as blockers instead of waiting.
Anything skipped stays queued and is picked up on the next run after focus ends.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import settings
from .log import get_logger

log = get_logger("focus")
FOCUS_FILE = Path.home() / ".job-search-os" / "focus"


def set_manual(spec: str) -> str:
    """'2h', '45m', or 'off'."""
    FOCUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    if spec.strip().lower() in ("off", "0", "stop"):
        FOCUS_FILE.unlink(missing_ok=True)
        return "focus off"
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([hm])", spec.strip().lower())
    if not m:
        raise ValueError("use e.g. 2h, 45m or off")
    secs = float(m.group(1)) * (3600 if m.group(2) == "h" else 60)
    FOCUS_FILE.write_text(str(time.time() + secs))
    return f"focus on until {datetime.fromtimestamp(time.time() + secs):%H:%M}"


def _manual() -> bool:
    try:
        return float(FOCUS_FILE.read_text()) > time.time()
    except (OSError, ValueError):
        return False


def _quiet_hours(now: datetime | None = None) -> bool:
    spec = getattr(settings, "quiet_hours", "") or ""
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*", spec)
    if not m:
        return False
    now = now or datetime.now(ZoneInfo(settings.timezone))
    cur = now.hour * 60 + now.minute
    a, b = int(m[1]) * 60 + int(m[2]), int(m[3]) * 60 + int(m[4])
    return a <= cur < b if a <= b else (cur >= a or cur < b)


def _calendar_busy() -> bool:
    if not settings.google_configured:
        return False
    try:
        from .google_api import free_busy

        now = datetime.now(timezone.utc)
        busy = free_busy(now.isoformat(), (now + timedelta(minutes=1)).isoformat())
        return bool(busy)
    except Exception as exc:  # never let a calendar hiccup stop the pipeline
        log.warning("calendar check failed: %s", type(exc).__name__)
        return False


def busy() -> str:
    """Empty string when free, otherwise the reason."""
    if _manual():
        return "manual focus"
    if _quiet_hours():
        return "quiet hours"
    if _calendar_busy():
        return "calendar event in progress"
    return ""
