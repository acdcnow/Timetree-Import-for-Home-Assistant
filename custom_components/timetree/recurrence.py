"""Recurrence expansion for TimeTree events.

TimeTree returns RFC 5545 style recurrence lines (``RRULE:``/``EXDATE``/
``RDATE``) with an inclusive end date, while Home Assistant expects an
*exclusive* end plus one event per occurrence. This module converts between the
two representations and is free of Home Assistant imports for testability.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from collections.abc import Iterable, Sequence

from dateutil.rrule import rruleset, rrulestr

from .models import (
    ALL_DAY_TIMEZONE,
    ONE_DAY,
    UTC,
    TimeTreeEvent,
    get_zone,
)

# Bounds that keep a single expansion cheap even for an ``RRULE:FREQ=DAILY``
# series that never ends.
MAX_OCCURRENCES = 1000
MAX_EXPANSION_DAYS = 366 * 3


@dataclass(slots=True, frozen=True)
class Occurrence:
    """A concrete occurrence of an event."""

    start: date | datetime
    end: date | datetime
    recurrence_id: str | None = None
    is_recurring: bool = False


def _naive(value: datetime) -> datetime:
    """Strip the tzinfo so recurrence math stays in wall clock time."""
    return value.replace(tzinfo=None)


def _to_comparable_date(value: date | datetime) -> date:
    """Return the calendar date of ``value``."""
    return value.date() if isinstance(value, datetime) else value


def _window_dates(
    window_start: datetime, window_end: datetime
) -> tuple[date, date]:
    """Return the date range covered by a datetime window."""
    return _to_comparable_date(window_start), _to_comparable_date(window_end)


def all_day_overlaps(
    start: date, end: date, window_start: datetime, window_end: datetime
) -> bool:
    """Return True when an all-day range overlaps the (exclusive) window."""
    window_start_date, window_end_date = _window_dates(window_start, window_end)
    return start < window_end_date and end > window_start_date


def timed_overlaps(
    start: datetime, end: datetime, window_start: datetime, window_end: datetime
) -> bool:
    """Return True when a timed range overlaps the window."""
    return start < window_end and end > window_start


def _parse_recurrence_datetime(
    raw: str, tz_name: str
) -> tuple[bool, datetime | date] | None:
    """Parse a single ``EXDATE``/``RDATE`` value.

    Returns ``(is_date_only, value)`` where the value of a timed entry is a
    naive datetime expressed in the event's own local time.
    """
    raw = raw.strip()
    if not raw:
        return None
    zone = get_zone(tz_name)
    try:
        if raw.endswith("Z"):
            parsed = datetime.strptime(raw, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
            return False, _naive(parsed.astimezone(zone))
        if "T" in raw:
            return False, datetime.strptime(raw.split("+", 1)[0], "%Y%m%dT%H%M%S")
        return True, datetime.strptime(raw, "%Y%m%d").date()
    except ValueError:
        return None


def split_recurrence_lines(
    recurrences: Sequence[str],
) -> tuple[str | None, list[str], list[str]]:
    """Split TimeTree recurrence lines into rule and exception value lists."""
    rule_line: str | None = None
    exdates: list[str] = []
    rdates: list[str] = []
    for line in recurrences or []:
        if not line:
            continue
        head, _, value = line.partition(":")
        name = head.split(";", 1)[0].strip().upper()
        if name == "RRULE" and value:
            rule_line = value
        elif name == "EXDATE":
            exdates.extend(part for part in value.split(",") if part.strip())
        elif name == "RDATE":
            rdates.extend(part for part in value.split(",") if part.strip())
    return rule_line, exdates, rdates


def is_valid_rule(rule_line: str) -> bool:
    """Return True when ``rule_line`` is an RFC 5545 rule with a FREQ."""
    parts = {}
    for chunk in rule_line.split(";"):
        key, _, value = chunk.partition("=")
        parts[key.strip().upper()] = value.strip()
    if not parts.get("FREQ"):
        return False
    return True


_DATE_UNTIL = re.compile(r"UNTIL=(\d{8})(?=;|$)")


def normalize_rule(rule_line: str, *, all_day: bool) -> str:
    """Return the rule with a date only UNTIL made inclusive.

    RFC 5545 treats ``UNTIL=20261008`` for a timed event as the end of that
    day in the DTSTART timezone, while dateutil would stop at 00:00 and drop
    the last occurrence.
    """
    if all_day:
        return rule_line
    return _DATE_UNTIL.sub(r"UNTIL=\1T235959", rule_line)


def _build_rruleset(
    event: TimeTreeEvent, dtstart: datetime
) -> rruleset | None:
    """Build an ``rruleset`` for an event, or None when it is not usable."""
    rule_line, exdate_values, rdate_values = split_recurrence_lines(event.recurrences)
    if not rule_line or not is_valid_rule(rule_line):
        return None
    try:
        parsed_rule = rrulestr(
            normalize_rule(rule_line, all_day=event.all_day), dtstart=dtstart
        )
    except Exception:  # noqa: BLE001 - dateutil raises various types
        return None

    result = rruleset()
    if isinstance(parsed_rule, rruleset):
        result = parsed_rule
    else:
        result.rrule(parsed_rule)
    zone_name = event.start_timezone or ALL_DAY_TIMEZONE
    for raw in rdate_values:
        parsed = _parse_recurrence_datetime(raw, zone_name)
        if parsed is None:
            continue
        is_date_only, value = parsed
        result.rdate(
            datetime.combine(value, time.min) if is_date_only else value  # type: ignore[arg-type]
        )
    for raw in exdate_values:
        parsed = _parse_recurrence_datetime(raw, zone_name)
        if parsed is None:
            continue
        is_date_only, value = parsed
        result.exdate(
            datetime.combine(value, time.min) if is_date_only else value  # type: ignore[arg-type]
        )
    return result


def _duration(event: TimeTreeEvent) -> timedelta:
    """Return the duration of one occurrence (at least one day for all-day)."""
    if event.all_day:
        duration = timedelta(days=(event.end_date - event.start_date).days)
        return duration if duration >= ONE_DAY else ONE_DAY
    duration = event.end_datetime - event.start_datetime
    if duration.total_seconds() <= 0:
        return timedelta(0)
    return duration


def _recurrence_id(start: date | datetime) -> str:
    """Return the ``recurrence_id`` value for an occurrence."""
    if isinstance(start, datetime):
        return start.strftime("%Y%m%dT%H%M%S")
    return start.strftime("%Y%m%d")


def single_occurrence(event: TimeTreeEvent) -> Occurrence:
    """Return the occurrence of a non recurring event."""
    return Occurrence(start=event.ha_start, end=event.ha_end)


def expand_event(
    event: TimeTreeEvent,
    window_start: datetime,
    window_end: datetime,
    *,
    max_occurrences: int = MAX_OCCURRENCES,
) -> list[Occurrence]:
    """Return every occurrence of ``event`` that overlaps the window."""
    if not event.is_recurring:
        if _overlaps(single_occurrence(event), window_start, window_end):
            return [single_occurrence(event)]
        return []

    duration = _duration(event)
    if event.all_day:
        dtstart = datetime.combine(event.start_date, time.min)
    else:
        dtstart = _naive(event.start_datetime)

    rules = _build_rruleset(event, dtstart)
    if rules is None:
        # Unsupported/lunar rule: fall back to the single stored occurrence so
        # the event is still visible instead of vanishing or breaking a view.
        if _overlaps(single_occurrence(event), window_start, window_end):
            return [single_occurrence(event)]
        return []

    zone = get_zone(event.start_timezone)
    window_start_naive = _naive(window_start.astimezone(zone))
    window_end_naive = _naive(window_end.astimezone(zone))

    occurrences: list[Occurrence] = []
    try:
        candidates = rules.xafter(
            window_start_naive - duration, count=max_occurrences, inc=True
        )
    except Exception:  # noqa: BLE001 - defensive, never break a whole view
        return []

    for naive_start in candidates:
        if naive_start > window_end_naive:
            break
        if event.all_day:
            start: date | datetime = naive_start.date()
            end: date | datetime = start + duration
        else:
            start = naive_start.replace(tzinfo=zone)
            end = start + duration
        occurrence = Occurrence(
            start=start,
            end=end,
            recurrence_id=_recurrence_id(start),
            is_recurring=True,
        )
        if _overlaps(occurrence, window_start, window_end):
            occurrences.append(occurrence)
    return occurrences


def _overlaps(
    occurrence: Occurrence, window_start: datetime, window_end: datetime
) -> bool:
    """Return True when an occurrence overlaps the window."""
    if isinstance(occurrence.start, datetime) and isinstance(occurrence.end, datetime):
        return timed_overlaps(occurrence.start, occurrence.end, window_start, window_end)
    return all_day_overlaps(
        _to_comparable_date(occurrence.start),
        _to_comparable_date(occurrence.end),
        window_start,
        window_end,
    )


def expand_events(
    events: Iterable[TimeTreeEvent],
    window_start: datetime,
    window_end: datetime,
    *,
    max_occurrences: int = MAX_OCCURRENCES,
) -> list[tuple[TimeTreeEvent, Occurrence]]:
    """Expand a collection of events into (event, occurrence) pairs."""
    if window_end <= window_start:
        return []
    span = window_end - window_start
    if span > timedelta(days=MAX_EXPANSION_DAYS):
        window_end = window_start + timedelta(days=MAX_EXPANSION_DAYS)

    result: list[tuple[TimeTreeEvent, Occurrence]] = []
    for event in events:
        for occurrence in expand_event(
            event,
            window_start,
            window_end,
            max_occurrences=max_occurrences,
        ):
            result.append((event, occurrence))
    result.sort(key=_sort_key)
    return result


def _sort_key(item: tuple[TimeTreeEvent, Occurrence]) -> datetime:
    """Return a sortable datetime for an occurrence start."""
    start = item[1].start
    if isinstance(start, datetime):
        return start.astimezone(UTC)
    return datetime(start.year, start.month, start.day, tzinfo=UTC)
