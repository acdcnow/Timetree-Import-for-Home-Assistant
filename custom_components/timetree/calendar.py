"""Calendar platform for the TimeTree integration.

All TimeTree calendars of a config entry become regular Home Assistant calendar
entities, so they can be selected in dashboards, scripts, automations and as a
target of the standard ``calendar`` services. Recurring events are expanded
here (Home Assistant expects one event per occurrence) while the series itself
keeps its ``rrule`` so clients can still offer "this and future" actions.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from functools import partial
from typing import Any

from homeassistant.components.calendar import (
    CalendarEntity,
    CalendarEntityFeature,
    CalendarEvent,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .conflicts import strip_marker
from .entity import TimeTreeCalendarEntityBase
from .models import TimeTreeEvent, build_event_payload, describe_event
from .options import CalendarOption
from .recurrence import Occurrence, expand_events

_LOGGER = logging.getLogger(__name__)

# The "next event" state is computed from a bounded lookahead so the state can
# be served synchronously without walking recurrence rules in the event loop.
LOOKAHEAD_DAYS = 180
MAX_LOOKAHEAD_OCCURRENCES = 25


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one calendar entity per selected TimeTree calendar."""
    runtime = entry.runtime_data
    coordinator = runtime.coordinator
    async_add_entities(
        [
            TimeTreeCalendarEntity(coordinator, entry, option, runtime.hub_device_id)
            for option in coordinator.options.calendars.values()
        ]
    )


class TimeTreeCalendarEntity(CalendarEntity, TimeTreeCalendarEntityBase):
    """A TimeTree calendar."""

    _attr_supported_features = (
        CalendarEntityFeature.CREATE_EVENT
        | CalendarEntityFeature.UPDATE_EVENT
        | CalendarEntityFeature.DELETE_EVENT
    )

    def __init__(
        self,
        coordinator,
        entry: ConfigEntry,
        option: CalendarOption,
        hub_device_id: str | None = None,
    ) -> None:
        """Initialize the calendar entity."""
        super().__init__(coordinator, entry, option, hub_device_id)
        self._attr_unique_id = f"{entry.entry_id}_{option.calendar_id}"
        self._occurrences: list[tuple[TimeTreeEvent, Occurrence]] = []

    # ------------------------------------------------------------------
    # state
    # ------------------------------------------------------------------
    @property
    def event(self) -> CalendarEvent | None:
        """Return the next upcoming occurrence."""
        now = dt_util.now()
        for event, occurrence in self._occurrences:
            if _is_future(occurrence, now):
                return self._build_calendar_event(event, occurrence)
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose sync details of the calendar."""
        data = self.calendar_data
        return {
            "timetree_calendar_id": self.calendar_id,
            "event_count": len(data.events) if data else 0,
            "last_update_success": self.coordinator.last_update_success_time,
        }

    async def async_added_to_hass(self) -> None:
        """Register coordinator updates and build the lookahead."""
        await super().async_added_to_hass()
        await self._async_rebuild_occurrences()
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Rebuild the lookahead when new data arrives."""
        self.hass.async_create_task(self._async_rebuild_and_write())

    async def _async_rebuild_and_write(self) -> None:
        """Rebuild the lookahead and publish the new state."""
        try:
            await self._async_rebuild_occurrences()
        except Exception as err:  # noqa: BLE001 - never break the update loop
            _LOGGER.error("Could not expand TimeTree events: %s", err)
        self.async_write_ha_state()

    async def _async_rebuild_occurrences(self) -> None:
        """Expand the next occurrences of this calendar."""
        events = self.calendar_events
        if not events:
            self._occurrences = []
            return
        now = dt_util.now()
        end = now + timedelta(days=LOOKAHEAD_DAYS)
        pairs = await self.hass.async_add_executor_job(
            partial(
                expand_events,
                events,
                now,
                end,
                max_occurrences=MAX_LOOKAHEAD_OCCURRENCES,
            )
        )
        self._occurrences = pairs[:MAX_LOOKAHEAD_OCCURRENCES]

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        """Return every occurrence inside a range."""
        events = self.calendar_events
        if not events:
            return []
        pairs = await hass.async_add_executor_job(
            partial(expand_events, events, start_date, end_date)
        )
        return [self._build_calendar_event(event, occ) for event, occ in pairs]

    # ------------------------------------------------------------------
    # write support
    # ------------------------------------------------------------------
    async def async_create_event(self, **kwargs: Any) -> None:
        """Create an event in TimeTree."""
        dtstart, dtend = _resolve_times(kwargs)
        all_day = not isinstance(dtstart, datetime)
        payload = build_event_payload(
            title=kwargs.get("summary") or kwargs.get("title") or "New event",
            note=kwargs.get("description") or "",
            location=kwargs.get("location") or "",
            start=dtstart,
            end=dtend,
            all_day=all_day,
            label_id=None,
            attendees=None,
            recurrences=_payload_recurrences(kwargs.get("rrule")),
            timezone=_timezone_name(dtstart),
        )
        try:
            await self.coordinator.async_create_event(self.calendar_id, payload)
        except Exception as err:  # noqa: BLE001 - surface a readable error
            raise HomeAssistantError(f"TimeTree could not create the event: {err}") from err

    async def async_update_event(
        self,
        uid: str,
        event: dict[str, Any],
        recurrence_id: str | None = None,
        recurrence_range: str | None = None,
    ) -> None:
        """Update an event in TimeTree."""
        self._reject_single_occurrence(uid, recurrence_id, "update")
        current = None
        data = self.calendar_data
        if data is not None:
            current = data.get(uid)
        dtstart, dtend = _resolve_times(event)
        all_day = not isinstance(dtstart, datetime)
        payload = build_event_payload(
            title=event.get("summary") or (current.title if current else "Event"),
            note=strip_marker(
                event.get("description")
                if event.get("description") is not None
                else (current.note if current else "")
            ),
            location=event.get("location") or (current.location if current else ""),
            start=dtstart,
            end=dtend,
            all_day=all_day,
            label_id=current.label_id if current else None,
            attendees=current.attendee_ids if current else None,
            recurrences=(
                _payload_recurrences(event.get("rrule"))
                if event.get("rrule") is not None
                else (current.recurrences if current else None)
            ),
            alerts=current.alerts if current else None,
            category=current.category if current else 1,
            timezone=_timezone_name(dtstart),
        )
        try:
            await self.coordinator.async_update_event(self.calendar_id, uid, payload)
        except Exception as err:  # noqa: BLE001 - surface a readable error
            raise HomeAssistantError(f"TimeTree could not update the event: {err}") from err

    async def async_delete_event(
        self,
        uid: str,
        recurrence_id: str | None = None,
        recurrence_range: str | None = None,
    ) -> None:
        """Delete an event from TimeTree."""
        self._reject_single_occurrence(uid, recurrence_id, "delete")
        try:
            await self.coordinator.async_delete_event(self.calendar_id, uid)
        except Exception as err:  # noqa: BLE001 - surface a readable error
            raise HomeAssistantError(f"TimeTree could not delete the event: {err}") from err

    def _reject_single_occurrence(
        self, uid: str, recurrence_id: str | None, action: str
    ) -> None:
        """Refuse occurrence level writes for recurring events."""
        if not recurrence_id:
            return
        data = self.calendar_data
        event = data.get(uid) if data else None
        if event is None or not event.is_recurring:
            return
        if recurrence_id == event.start_datetime.strftime("%Y%m%dT%H%M%S"):
            # the first occurrence is the series itself
            return
        raise HomeAssistantError(
            "TimeTree cannot modify a single occurrence of a recurring event; "
            f"{action} the whole series in the TimeTree app instead."
        )

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _build_calendar_event(
        self, event: TimeTreeEvent, occurrence: Occurrence
    ) -> CalendarEvent:
        """Convert an occurrence into a Home Assistant calendar event."""
        return CalendarEvent(
            summary=event.title,
            start=occurrence.start,
            end=occurrence.end,
            location=event.location or None,
            description=describe_event(
                event,
                include_details=self.coordinator.options.description_details,
            )
            or None,
            uid=event.uuid,
            recurrence_id=occurrence.recurrence_id,
            rrule=_bare_rrule(event.rrule_line),
        )


def _is_future(occurrence: Occurrence, now: datetime) -> bool:
    """Return True when an occurrence has not ended yet."""
    if isinstance(occurrence.end, datetime):
        return occurrence.end > now
    return occurrence.end > now.date()


def _resolve_times(values: dict[str, Any]) -> tuple[date | datetime, date | datetime]:
    """Return the start/end values of a create/update call.

    Home Assistant core normalises the ``calendar.create_event`` service data
    into ``dtstart``/``dtend`` before calling the entity, while the WebSocket
    API passes them as is, so both spellings are accepted.
    """
    start = (
        values.get("dtstart")
        or values.get("start_date_time")
        or values.get("start_date")
    )
    end = values.get("dtend") or values.get("end_date_time") or values.get("end_date")
    if start is None:
        raise HomeAssistantError("TimeTree needs a start date or date time")
    if end is None:
        if isinstance(start, datetime):
            end = start + timedelta(hours=1)
        else:
            end = start + timedelta(days=1)
    return start, end


def _bare_rrule(rrule_line: str | None) -> str | None:
    """Return an RRULE value without the ``RRULE:`` prefix."""
    if not rrule_line:
        return None
    _, _, value = rrule_line.partition(":")
    return value or None


def _payload_recurrences(rrule: str | None) -> list[str]:
    """Return the TimeTree recurrence list for a rule value."""
    if not rrule:
        return []
    value = rrule.partition(":")[2] or rrule
    return [f"RRULE:{value}"]


def _timezone_name(value: date | datetime) -> str | None:
    """Return the IANA timezone name of a value (None for dates)."""
    if not isinstance(value, datetime):
        return None
    key = getattr(value.tzinfo, "key", None)
    return key or str(dt_util.DEFAULT_TIME_ZONE)
