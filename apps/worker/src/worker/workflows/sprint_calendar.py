from datetime import date, datetime, timedelta, timezone

from worker.workflows.config_models import SprintCfg

QUARTER_CYCLE_DAYS = 91


def sprint_end_offsets(cfg: SprintCfg) -> list[int]:
    offsets: list[int] = []
    elapsed_weeks = 0
    remaining = cfg.quarter_weeks
    while remaining > 0:
        length = cfg.last_sprint_weeks if remaining <= cfg.last_sprint_weeks else cfg.normal_sprint_weeks
        offsets.append((elapsed_weeks + length - 1) * 7 + 6)
        elapsed_weeks += length
        remaining -= length
    return offsets


def is_sprint_end(day: date, anchor: date, cfg: SprintCfg) -> bool:
    if day.weekday() != 0:
        return False
    delta = (day - anchor).days
    if delta < 0:
        return False
    return delta % QUARTER_CYCLE_DAYS in sprint_end_offsets(cfg)


def anchor_for_quarter(year: int, quarter: int) -> date:
    first_of_quarter = date(year, (quarter - 1) * 3 + 1, 1)
    return first_of_quarter + timedelta(days=(1 - first_of_quarter.weekday()) % 7)


def quarter_of(day: date) -> tuple[int, int]:
    return day.year, (day.month - 1) // 3 + 1


def previous_quarter(year: int, quarter: int) -> tuple[int, int]:
    return (year - 1, 4) if quarter == 1 else (year, quarter - 1)


def resolve_anchor(cfg: SprintCfg, for_date: date) -> date:
    if cfg.anchor_tuesday is not None:
        return cfg.anchor_tuesday
    year, quarter = quarter_of(for_date)
    candidates = [anchor_for_quarter(year, quarter)]
    prev_year, prev_quarter = previous_quarter(year, quarter)
    candidates.append(anchor_for_quarter(prev_year, prev_quarter))
    for candidate in candidates:
        if is_sprint_end(for_date, candidate, cfg):
            return candidate
    return candidates[0]


def sprint_window_for_end(end: date, anchor: date, cfg: SprintCfg) -> tuple[date, date] | None:
    if not is_sprint_end(end, anchor, cfg):
        return None
    offset = (end - anchor).days % QUARTER_CYCLE_DAYS
    offsets = sprint_end_offsets(cfg)
    length_days = cfg.last_sprint_weeks * 7 if offset == offsets[-1] else cfg.normal_sprint_weeks * 7
    start = end - timedelta(days=length_days - 1)
    return start, end


def resolve_sprint_window(
    cfg: SprintCfg, run_date: date, explicit_start: date | None = None
) -> tuple[date, date] | None:
    if explicit_start is not None:
        if explicit_start.weekday() != 1:
            raise ValueError(f"sprint_start must be a Tuesday, got {explicit_start}")
        anchor = cfg.anchor_tuesday or anchor_for_quarter(*quarter_of(explicit_start))
        offset = (explicit_start - anchor).days % QUARTER_CYCLE_DAYS
        offsets = sprint_end_offsets(cfg)
        last_start_week = (offsets[-1] - 6) // 7 - (cfg.last_sprint_weeks - 1)
        length_days = (
            cfg.last_sprint_weeks * 7
            if (offset // 7) == last_start_week
            else cfg.normal_sprint_weeks * 7
        )
        end = explicit_start + timedelta(days=length_days - 1)
        return explicit_start, end
    anchor = resolve_anchor(cfg, run_date)
    return sprint_window_for_end(run_date, anchor, cfg)


def work_dates(start: date, end: date, work_days: list[int]) -> list[date]:
    days: list[date] = []
    current = start
    while current <= end:
        if current.weekday() in work_days:
            days.append(current)
        current += timedelta(days=1)
    return days


def ref_for(sprint_start: date) -> str:
    return f"SP-{sprint_start.isoformat()}"


def parse_ref(ref: str) -> date:
    return date.fromisoformat(ref.removeprefix("SP-"))


def next_reminder_time(
    now: datetime, weekdays: list[int], hour_utc: int
) -> datetime:
    candidate = now.astimezone(timezone.utc).replace(hour=hour_utc, minute=0, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    while candidate.weekday() not in weekdays:
        candidate += timedelta(days=1)
    return candidate
