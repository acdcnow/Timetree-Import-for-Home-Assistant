"""Data models for the TimeTree integration.

This module is intentionally free of Home Assistant imports so that the
parsing/formatting logic can be unit tested without a running Home Assistant.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from typing import Any
from collections.abc import Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

UTC = timezone.utc
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
ALL_DAY_TIMEZONE = "UTC"
ONE_DAY = timedelta(days=1)
MILLISECOND = timedelta(milliseconds=1)

# TimeTree event ``type`` values
EVENT_TYPE_NORMAL = 0
EVENT_TYPE_BIRTHDAY = 1
# TimeTree event ``category`` values
CATEGORY_NORMAL = 1
CATEGORY_MEMO = 2


def get_zone(tz_name: str | None) -> timezone | ZoneInfo:
    """Return a tzinfo for ``tz_name`` falling back to UTC.

    ``ZoneInfo`` raises when the name is unknown or when the host has no
    time zone database (e.g. plain Windows), so never let that break parsing.
    """
    if not tz_name:
        return UTC
    try:
        return ZoneInfo(tz_name)
    except Exception:  # noqa: BLE001 - KeyError, ZoneInfoNotFoundError, ...
        return UTC


def to_int(value: Any) -> int | None:
    """Best effort conversion of an API value to ``int``."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def ms_to_datetime(value: Any, tz_name: str | None = None) -> datetime:
    """Convert a TimeTree millisecond timestamp to an aware datetime.

    TimeTree uses negative timestamps for events before 1970, which
    ``datetime.fromtimestamp`` refuses on some platforms, so the offset is
    always computed manually against the epoch.
    """
    return (EPOCH + timedelta(milliseconds=to_int(value) or 0)).astimezone(get_zone(tz_name))


def ms_to_date(value: Any, tz_name: str | None = None) -> date:
    """Convert a TimeTree millisecond timestamp to a (local) calendar date."""
    return ms_to_datetime(value, tz_name).date()


def datetime_to_ms(value: datetime) -> int:
    """Convert an aware datetime to a TimeTree millisecond timestamp."""
    return (value - EPOCH) // MILLISECOND


def date_to_utc_ms(value: date) -> int:
    """Convert a date to a UTC-midnight millisecond timestamp.

    TimeTree stores all-day events as UTC midnights, so using the local
    timezone here silently shifts events by a day on non-UTC hosts.
    """
    return (datetime(value.year, value.month, value.day, tzinfo=UTC) - EPOCH) // MILLISECOND


def parse_relations_label_id(event_data: Mapping[str, Any]) -> int | None:
    """Return the label id from either the flat or the JSON:API field."""
    label_id = to_int(event_data.get("label_id"))
    if label_id is not None:
        return label_id
    relationships = event_data.get("relationships")
    if isinstance(relationships, Mapping):
        label = relationships.get("label")
        if isinstance(label, Mapping):
            data = label.get("data")
            if isinstance(data, Mapping):
                raw = data.get("id")
                if isinstance(raw, str) and "," in raw:
                    return to_int(raw.rsplit(",", 1)[-1])
                return to_int(raw)
    return None


@dataclass(slots=True)
class TimeTreeLabel:
    """A TimeTree label (colour + name, optionally bound to a member)."""

    label_id: int
    name: str
    color: str | None = None

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> TimeTreeLabel:
        """Create a label from a ``calendar_labels`` entry."""
        color = data.get("color")
        if isinstance(color, int):
            color = f"#{color:06x}"
        return cls(
            label_id=to_int(data.get("label_id") or data.get("id")) or 0,
            name=str(data.get("name") or ""),
            color=color if isinstance(color, str) else None,
        )


@dataclass(slots=True)
class TimeTreeCalendar:
    """A TimeTree calendar plus its members."""

    calendar_id: str
    name: str
    alias_code: str | None = None
    users: dict[int, str] = field(default_factory=dict)
    labels: dict[int, TimeTreeLabel] = field(default_factory=dict)

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> TimeTreeCalendar:
        """Create a calendar from the ``/calendars`` response."""
        users: dict[int, str] = {}
        for user in data.get("calendar_users") or []:
            if not isinstance(user, Mapping):
                continue
            user_id = to_int(user.get("user_id") or user.get("id"))
            name = user.get("name")
            if user_id is not None and name:
                users[user_id] = str(name)
        return cls(
            calendar_id=str(data.get("id")),
            name=str(data.get("name") or "TimeTree"),
            alias_code=data.get("alias_code"),
            users=users,
        )


@dataclass(slots=True)
class TimeTreeEvent:
    """A TimeTree event in Home Assistant friendly form."""

    uuid: str
    calendar_id: str
    title: str = ""
    note: str = ""
    location: str = ""
    start_at: int = 0
    end_at: int = 0
    start_timezone: str = ALL_DAY_TIMEZONE
    end_timezone: str = ALL_DAY_TIMEZONE
    all_day: bool = False
    recurrences: list[str] = field(default_factory=list)
    alerts: list[int] = field(default_factory=list)
    label_id: int | None = None
    attendee_ids: list[int] = field(default_factory=list)
    event_type: int = EVENT_TYPE_NORMAL
    category: int = CATEGORY_NORMAL
    created_at: int | None = None
    updated_at: int | None = None
    parent_id: str | None = None
    recurring_uuid: str | None = None
    url: str | None = None
    location_lat: float | None = None
    location_lon: float | None = None
    deleted: bool = False
    # resolved metadata (not part of the TimeTree payload)
    label_name: str | None = None
    label_color: str | None = None
    attendee_names: list[str] = field(default_factory=list)
    comments: list[str] = field(default_factory=list)

    # -- lifecycle ---------------------------------------------------------
    @classmethod
    def from_api(
        cls,
        data: Mapping[str, Any],
        calendar_id: str,
        *,
        calendar: TimeTreeCalendar | None = None,
    ) -> TimeTreeEvent:
        """Create an event from a ``events/sync`` entry."""
        uuid = str(data.get("uuid") or data.get("id") or "")
        event = cls(
            uuid=uuid,
            calendar_id=calendar_id,
            title=str(data.get("title") or ""),
            note=str(data.get("note") or ""),
            location=str(data.get("location") or ""),
            start_at=to_int(data.get("start_at")) or 0,
            end_at=to_int(data.get("end_at")) or 0,
            start_timezone=str(data.get("start_timezone") or ALL_DAY_TIMEZONE),
            end_timezone=str(data.get("end_timezone") or ALL_DAY_TIMEZONE),
            all_day=bool(data.get("all_day")),
            recurrences=[str(item) for item in data.get("recurrences") or [] if item],
            alerts=[value for value in data.get("alerts") or [] if isinstance(value, int)],
            label_id=parse_relations_label_id(data),
            attendee_ids=[
                user_id
                for user_id in (to_int(value) for value in data.get("attendees") or [])
                if user_id is not None
            ],
            event_type=to_int(data.get("type")) or EVENT_TYPE_NORMAL,
            category=to_int(data.get("category")) or CATEGORY_NORMAL,
            created_at=to_int(data.get("created_at")),
            updated_at=to_int(data.get("updated_at")),
            parent_id=data.get("parent_id") or None,
            recurring_uuid=data.get("recurring_uuid") or None,
            url=data.get("url") or None,
            location_lat=data.get("location_lat"),
            location_lon=data.get("location_lon"),
            deleted=bool(data.get("deleted_at") or data.get("deleted")),
        )
        if calendar is not None:
            event.apply_calendar_metadata(calendar)
        return event

    def apply_calendar_metadata(self, calendar: TimeTreeCalendar) -> None:
        """Resolve label and attendee ids into human readable names."""
        if self.label_id is not None:
            label = calendar.labels.get(self.label_id)
            if label is not None:
                self.label_name = label.name
                self.label_color = label.color
        self.attendee_names = [
            calendar.users[user_id]
            for user_id in self.attendee_ids
            if user_id in calendar.users
        ]

    def copy(self) -> TimeTreeEvent:
        """Return a copy of the event."""
        return replace(self)

    # -- time helpers ------------------------------------------------------
    @property
    def is_birthday(self) -> bool:
        """Return True for TimeTree generated birthday entries."""
        return self.event_type == EVENT_TYPE_BIRTHDAY

    @property
    def is_memo(self) -> bool:
        """Return True for TimeTree memos."""
        return self.category == CATEGORY_MEMO

    @property
    def start_datetime(self) -> datetime:
        """Return the event start as aware datetime (all-day: event timezone)."""
        return ms_to_datetime(self.start_at, self.start_timezone)

    @property
    def end_datetime(self) -> datetime:
        """Return the event end as aware datetime (all-day: event timezone)."""
        return ms_to_datetime(self.end_at, self.end_timezone)

    @property
    def start_date(self) -> date:
        """Return the first day of an all-day event."""
        return ms_to_date(self.start_at, self.start_timezone)

    @property
    def end_date_inclusive(self) -> date:
        """Return the last day of an all-day event (TimeTree semantics)."""
        return ms_to_date(self.end_at, self.end_timezone)

    @property
    def end_date(self) -> date:
        """Return the exclusive end date used by Home Assistant (RFC 5545)."""
        end = self.end_date_inclusive
        if end < self.start_date:
            end = self.start_date
        return end + ONE_DAY

    @property
    def ha_start(self) -> date | datetime:
        """Return the start in the format Home Assistant expects."""
        return self.start_date if self.all_day else self.start_datetime

    @property
    def ha_end(self) -> date | datetime:
        """Return the end in the format Home Assistant expects."""
        return self.end_date if self.all_day else self.end_datetime

    @property
    def updated_ms(self) -> int:
        """Return the last modification timestamp in milliseconds."""
        return self.updated_at or self.created_at or 0

    # -- recurrence --------------------------------------------------------
    @property
    def rrule_line(self) -> str | None:
        """Return the raw RRULE line (with the ``RRULE:`` prefix) if any."""
        for line in self.recurrences:
            head = line.split(":", 1)[0].split(";", 1)[0].strip().upper()
            if head == "RRULE":
                return line
        return None

    @property
    def is_recurring(self) -> bool:
        """Return True when the event repeats."""
        return self.rrule_line is not None

    @property
    def description(self) -> str:
        """Return the event note including optional label/attendee details."""
        return describe_event(self)

    def details_lines(self) -> list[str]:
        """Return human readable details used to enrich the description."""
        lines: list[str] = []
        if self.label_name:
            lines.append(f"TimeTree label: {self.label_name}")
        if self.attendee_names:
            lines.append(f"TimeTree attendees: {', '.join(self.attendee_names)}")
        return lines

    # -- sync helpers ------------------------------------------------------
    def fingerprint(self) -> str:
        """Return a stable hash of the fields that are synchronised."""
        payload = "|".join(
            [
                self.title,
                self.note,
                self.location,
                str(self.start_at),
                str(self.end_at),
                str(self.all_day),
                ";".join(sorted(self.recurrences)),
            ]
        )
        return sha256(payload.encode("utf-8")).hexdigest()[:32]


def event_from_api(
    data: Mapping[str, Any],
    calendar_id: str,
    *,
    calendar: TimeTreeCalendar | None = None,
) -> TimeTreeEvent:
    """Module level convenience wrapper around :meth:`TimeTreeEvent.from_api`."""
    return TimeTreeEvent.from_api(data, calendar_id, calendar=calendar)


def describe_event(event: TimeTreeEvent, *, include_details: bool = True) -> str:
    """Return the description used for Home Assistant and for exports.

    Both the calendar entity and the exporter use this helper so that the
    description of the TimeTree event and the description of the exported copy
    stay byte identical (the exporter only appends its marker).
    """
    parts: list[str] = []
    if event.note:
        parts.append(event.note)
    if include_details:
        parts.extend(event.details_lines())
    if event.url:
        parts.append(event.url)
    if event.comments:
        parts.append("\n".join(event.comments))
    return "\n\n".join(part for part in parts if part)


def parse_event_list(
    payload: Mapping[str, Any],
    calendar_id: str,
    *,
    calendar: TimeTreeCalendar | None = None,
) -> list[TimeTreeEvent]:
    """Parse an ``events/sync`` payload into events."""
    events = payload.get("events") or payload.get("public_events") or []
    return [
        TimeTreeEvent.from_api(item, calendar_id, calendar=calendar)
        for item in events
        if isinstance(item, Mapping) and (item.get("uuid") or item.get("id"))
    ]


def filter_events(
    events: Iterable[TimeTreeEvent],
    *,
    include_birthdays: bool = False,
) -> list[TimeTreeEvent]:
    """Drop deleted (and optionally birthday) events."""
    return [
        event
        for event in events
        if not event.deleted and (include_birthdays or not event.is_birthday)
    ]


def build_event_payload(
    *,
    title: str,
    note: str = "",
    location: str = "",
    start: date | datetime,
    end: date | datetime,
    all_day: bool,
    label_id: int | None = None,
    attendees: Sequence[int] | None = None,
    recurrences: Sequence[str] | None = None,
    alerts: Sequence[int] | None = None,
    category: int = CATEGORY_NORMAL,
    event_type: int | None = None,
    timezone: str | None = None,
) -> dict[str, Any]:
    """Build the payload the TimeTree web client sends for a write.

    Home Assistant hands out an *exclusive* end (RFC 5545), while TimeTree
    stores an *inclusive* end, so all-day events are converted here. TimeTree
    also stores all-day events as UTC midnights.
    """
    if all_day:
        start_date = start if isinstance(start, date) and not isinstance(start, datetime) else start.date()
        end_date = end if isinstance(end, date) and not isinstance(end, datetime) else end.date()
        # HA end is exclusive -> TimeTree end is inclusive and may equal start.
        inclusive_end = max(end_date - ONE_DAY, start_date)
        start_at = date_to_utc_ms(start_date)
        end_at = date_to_utc_ms(inclusive_end)
        timezone_name = ALL_DAY_TIMEZONE
    else:
        if not isinstance(start, datetime) or not isinstance(end, datetime):
            raise ValueError("Timed events require datetime values")
        start_at = datetime_to_ms(start)
        end_at = datetime_to_ms(end)
        timezone_name = timezone or str(start.tzinfo or ALL_DAY_TIMEZONE)
        if timezone_name in ("UTC", "utc"):
            timezone_name = ALL_DAY_TIMEZONE

    payload: dict[str, Any] = {
        "title": title,
        "all_day": bool(all_day),
        "start_at": start_at,
        "start_timezone": timezone_name,
        "end_at": end_at,
        "end_timezone": timezone_name,
        "label_id": label_id if label_id is not None else 1,
        "note": note or "",
        "location": location or "",
        "attendees": list(attendees or []),
        "recurrences": list(recurrences or []),
        "alerts": list(alerts or []),
        "attachment": {"virtual_user_attendees": []},
        "category": category,
    }
    if event_type is not None:
        payload["type"] = event_type
    return payload
