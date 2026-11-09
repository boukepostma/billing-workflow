from datetime import date

from worker.workflows.config_models import SprintCfg
from worker.workflows import sprint_calendar as cal

CFG = SprintCfg(anchor_tuesday=date(2026, 10, 6))


def test_sprint_end_offsets():
    assert cal.sprint_end_offsets(CFG) == [20, 41, 62, 90]


def test_sprint_ends_with_anchor():
    assert cal.is_sprint_end(date(2026, 10, 26), CFG.anchor_tuesday, CFG)
    assert cal.is_sprint_end(date(2026, 11, 16), CFG.anchor_tuesday, CFG)
    assert cal.is_sprint_end(date(2026, 12, 7), CFG.anchor_tuesday, CFG)
    assert cal.is_sprint_end(date(2027, 1, 4), CFG.anchor_tuesday, CFG)
    assert not cal.is_sprint_end(date(2026, 11, 9), CFG.anchor_tuesday, CFG)
    assert not cal.is_sprint_end(date(2026, 11, 2), CFG.anchor_tuesday, CFG)
    assert not cal.is_sprint_end(date(2026, 11, 17), CFG.anchor_tuesday, CFG)


def test_sprint_windows():
    assert cal.sprint_window_for_end(date(2026, 11, 16), CFG.anchor_tuesday, CFG) == (
        date(2026, 10, 27),
        date(2026, 11, 16),
    )
    assert cal.sprint_window_for_end(date(2027, 1, 4), CFG.anchor_tuesday, CFG) == (
        date(2026, 12, 8),
        date(2027, 1, 4),
    )
    assert cal.sprint_window_for_end(date(2026, 11, 9), CFG.anchor_tuesday, CFG) is None


def test_auto_anchor_first_tuesday_of_quarter():
    auto = SprintCfg(anchor_tuesday=None)
    assert cal.anchor_for_quarter(2026, 4) == date(2026, 10, 6)
    assert cal.resolve_sprint_window(auto, date(2026, 11, 16)) == (date(2026, 10, 27), date(2026, 11, 16))
    assert cal.resolve_sprint_window(auto, date(2026, 11, 9)) is None
    assert cal.resolve_sprint_window(auto, date(2027, 1, 4)) == (date(2026, 12, 8), date(2027, 1, 4))


def test_explicit_sprint_start():
    window = cal.resolve_sprint_window(CFG, date(2026, 11, 16), explicit_start=date(2026, 12, 8))
    assert window == (date(2026, 12, 8), date(2027, 1, 4))


def test_work_dates():
    days = cal.work_dates(date(2026, 10, 27), date(2026, 11, 16), CFG.work_days)
    assert days == [
        date(2026, 10, 27),
        date(2026, 10, 28),
        date(2026, 11, 2),
        date(2026, 11, 3),
        date(2026, 11, 4),
        date(2026, 11, 9),
        date(2026, 11, 10),
        date(2026, 11, 11),
        date(2026, 11, 16),
    ]


def test_ref_roundtrip():
    ref = cal.ref_for(date(2026, 10, 27))
    assert ref == "SP-2026-10-27"
    assert cal.parse_ref(ref) == date(2026, 10, 27)


def test_next_reminder_time():
    from datetime import datetime, timezone

    monday_10am = datetime(2026, 11, 16, 10, 0, tzinfo=timezone.utc)
    nxt = cal.next_reminder_time(monday_10am, [2, 4], 16)
    assert nxt == datetime(2026, 11, 18, 16, 0, tzinfo=timezone.utc)
    wednesday_17 = datetime(2026, 11, 18, 17, 0, tzinfo=timezone.utc)
    nxt = cal.next_reminder_time(wednesday_17, [2, 4], 16)
    assert nxt == datetime(2026, 11, 20, 16, 0, tzinfo=timezone.utc)
