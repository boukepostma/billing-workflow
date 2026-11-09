# ADR-0001: Sprint calendar model

Status: accepted (confirmed by owner 2026-11-09)

## Context
Sprints start on Tuesday; a Monday is always the last day of a sprint. Each quarter (13 weeks) is 3+3+3+4 weeks; the last week of the quarter makes the 4th sprint 4 weeks long.

## Decision
Anchor the calendar on one Tuesday (`sprint.anchor_tuesday: 2026-09-29`). Sprint ends are the Mondays at day offsets 20, 41, 62, 90 (mod 91) from the anchor. The `billing-sprint` schedule fires every Monday 08:00 Europe/Amsterdam and no-ops (`skipped_not_sprint_end`) on non-sprint-end Mondays.

Sprint ends from the anchor: 2026-10-19, 2026-11-09, 2026-11-30, 2026-12-28 (4-week), then the cycle repeats from 2026-12-29.

## Consequences
- One config value defines the whole calendar; changing the anchor shifts everything.
- Payment monitoring lives inside the same long-running execution (timers), so no-op Mondays are safe.
