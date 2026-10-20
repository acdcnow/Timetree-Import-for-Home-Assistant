"""Normalised runtime options for the TimeTree integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from homeassistant.config_entries import ConfigEntry

from .const import (
    CONF_ALIAS_CODE,
    CONF_CALENDAR_ID,
    CONF_CALENDAR_NAME,
    CONF_CALENDARS,
    CONF_CONFLICT_POLICY,
    CONF_DESCRIPTION_DETAILS,
    CONF_EXPORT_ATTENDEES,
    CONF_EXPORT_DELETE_REMOVED,
    CONF_EXPORT_DIRECTION,
    CONF_EXPORT_DRY_RUN,
    CONF_EXPORT_ENABLED,
    CONF_EXPORT_FILTER_MODE,
    CONF_EXPORT_FUTURE_DAYS,
    CONF_EXPORT_INCLUDE_UNTAGGED,
    CONF_EXPORT_INCLUDE_UNTAGGED_TAGS,
    CONF_EXPORT_INTERVAL,
    CONF_EXPORT_PAST_DAYS,
    CONF_EXPORT_RECREATE_REMOVED,
    CONF_EXPORT_TAGS,
    CONF_EXPORT_TARGET,
    CONF_IMPORT_UNMANAGED,
    CONF_INCLUDE_BIRTHDAYS,
    CONF_INCLUDE_COMMENTS,
    CONF_NOTIFY_CONFLICTS,
    CONF_SCAN_INTERVAL,
    DEFAULT_EXPORT_FUTURE_DAYS,
    DEFAULT_EXPORT_INTERVAL,
    DEFAULT_EXPORT_PAST_DAYS,
    DEFAULT_SCAN_INTERVAL,
    DIRECTION_EXPORT_ONLY,
    FILTER_MODE_ALL,
    POLICY_MANUAL,
)


@dataclass(slots=True)
class CalendarOption:
    """A selected TimeTree calendar."""

    calendar_id: str
    name: str
    alias_code: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return a storable representation."""
        return {
            CONF_CALENDAR_ID: self.calendar_id,
            CONF_CALENDAR_NAME: self.name,
            CONF_ALIAS_CODE: self.alias_code,
        }


@dataclass(slots=True)
class TimeTreeOptions:
    """All tunables of a TimeTree config entry."""

    calendars: dict[str, CalendarOption] = field(default_factory=dict)
    scan_interval: int = DEFAULT_SCAN_INTERVAL
    include_birthdays: bool = False
    include_comments: bool = False
    description_details: bool = True

    export_enabled: bool = False
    export_target: str | None = None
    export_interval: int = DEFAULT_EXPORT_INTERVAL
    export_direction: str = DIRECTION_EXPORT_ONLY
    export_past_days: int = DEFAULT_EXPORT_PAST_DAYS
    export_future_days: int = DEFAULT_EXPORT_FUTURE_DAYS
    export_delete_removed: bool = False
    export_recreate_removed: bool = False
    export_dry_run: bool = False
    import_unmanaged: bool = False
    export_attendees: list[str] = field(default_factory=list)
    export_include_untagged: bool = True
    export_tags: list[str] = field(default_factory=list)
    export_include_untagged_tags: bool = True
    export_filter_mode: str = FILTER_MODE_ALL

    conflict_policy: str = POLICY_MANUAL
    notify_conflicts: bool = True

    @property
    def two_way(self) -> bool:
        """Return True when changes are also mirrored back into TimeTree."""
        return self.export_direction != DIRECTION_EXPORT_ONLY

    @classmethod
    def from_entry(cls, entry: ConfigEntry) -> TimeTreeOptions:
        """Build the options from a config entry (options win over data)."""
        data: dict[str, Any] = dict(entry.data)
        options: dict[str, Any] = dict(entry.options)

        def get(key: str, default: Any) -> Any:
            value = options.get(key, data.get(key, default))
            return default if value is None else value

        calendars: dict[str, CalendarOption] = {}
        for item in get(CONF_CALENDARS, []) or []:
            if not isinstance(item, dict):
                continue
            calendar_id = item.get(CONF_CALENDAR_ID)
            if not calendar_id:
                continue
            calendars[str(calendar_id)] = CalendarOption(
                calendar_id=str(calendar_id),
                name=str(item.get(CONF_CALENDAR_NAME) or "TimeTree"),
                alias_code=item.get(CONF_ALIAS_CODE),
            )
        # Migration from the single calendar layout of 1.x
        legacy_id = data.get(CONF_CALENDAR_ID)
        if legacy_id and str(legacy_id) not in calendars:
            calendars[str(legacy_id)] = CalendarOption(
                calendar_id=str(legacy_id),
                name=str(data.get(CONF_CALENDAR_NAME) or "TimeTree"),
                alias_code=data.get(CONF_ALIAS_CODE),
            )

        return cls(
            calendars=calendars,
            scan_interval=int(get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)),
            include_birthdays=bool(get(CONF_INCLUDE_BIRTHDAYS, False)),
            include_comments=bool(get(CONF_INCLUDE_COMMENTS, False)),
            description_details=bool(get(CONF_DESCRIPTION_DETAILS, True)),
            export_enabled=bool(get(CONF_EXPORT_ENABLED, False)),
            export_target=get(CONF_EXPORT_TARGET, None),
            export_interval=int(get(CONF_EXPORT_INTERVAL, DEFAULT_EXPORT_INTERVAL)),
            export_direction=str(get(CONF_EXPORT_DIRECTION, DIRECTION_EXPORT_ONLY)),
            export_past_days=int(get(CONF_EXPORT_PAST_DAYS, DEFAULT_EXPORT_PAST_DAYS)),
            export_future_days=int(
                get(CONF_EXPORT_FUTURE_DAYS, DEFAULT_EXPORT_FUTURE_DAYS)
            ),
            export_delete_removed=bool(get(CONF_EXPORT_DELETE_REMOVED, False)),
            export_recreate_removed=bool(get(CONF_EXPORT_RECREATE_REMOVED, False)),
            export_dry_run=bool(get(CONF_EXPORT_DRY_RUN, False)),
            import_unmanaged=bool(get(CONF_IMPORT_UNMANAGED, False)),
            export_attendees=[
                str(item) for item in get(CONF_EXPORT_ATTENDEES, []) or [] if item
            ],
            export_include_untagged=bool(get(CONF_EXPORT_INCLUDE_UNTAGGED, True)),
            export_tags=[
                str(item) for item in get(CONF_EXPORT_TAGS, []) or [] if item
            ],
            export_include_untagged_tags=bool(
                get(CONF_EXPORT_INCLUDE_UNTAGGED_TAGS, True)
            ),
            export_filter_mode=str(get(CONF_EXPORT_FILTER_MODE, FILTER_MODE_ALL)),
            conflict_policy=str(get(CONF_CONFLICT_POLICY, POLICY_MANUAL)),
            notify_conflicts=bool(get(CONF_NOTIFY_CONFLICTS, True)),
        )

    def calendar(self, calendar_id: str) -> CalendarOption | None:
        """Return a selected calendar."""
        return self.calendars.get(str(calendar_id))
