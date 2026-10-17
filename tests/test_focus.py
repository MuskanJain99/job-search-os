from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from jobsearch import focus


def _at(h, m=0):
    return datetime(2026, 10, 17, h, m, tzinfo=ZoneInfo("Asia/Kolkata"))


def test_quiet_hours_same_day_and_overnight(monkeypatch):
    monkeypatch.setattr(focus, "settings", SimpleNamespace(quiet_hours="10:00-19:00", timezone="Asia/Kolkata"))
    assert focus._quiet_hours(_at(11)) and not focus._quiet_hours(_at(20))
    monkeypatch.setattr(focus, "settings", SimpleNamespace(quiet_hours="22:00-07:00", timezone="Asia/Kolkata"))
    assert focus._quiet_hours(_at(23)) and focus._quiet_hours(_at(3)) and not focus._quiet_hours(_at(12))
    monkeypatch.setattr(focus, "settings", SimpleNamespace(quiet_hours="", timezone="Asia/Kolkata"))
    assert not focus._quiet_hours(_at(12))


def test_manual_focus(tmp_path, monkeypatch):
    monkeypatch.setattr(focus, "FOCUS_FILE", tmp_path / "focus")
    assert not focus._manual()
    focus.set_manual("1h")
    assert focus._manual()
    focus.set_manual("off")
    assert not focus._manual()
