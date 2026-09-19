"""DataUpdateCoordinator for the TimeTree integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import TimeTreeApi, TimeTreeApiError, TimeTreeAuthError
from .const import DOMAIN
from .models import TimeTreeCalendar, TimeTreeEvent
from .options import TimeTreeOptions

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class CalendarData:
    """The current state of one TimeTree calendar."""

    calendar: TimeTreeCalendar
    events: dict[str, TimeTreeEvent] = field(default_factory=dict)
    fetched_at: datetime | None = None

    def event_list(self) -> list[TimeTreeEvent]:
        """Return the events as a list."""
        return list(self.events.values())

    def get(self, uuid: str) -> TimeTreeEvent | None:
        """Return an event by uuid."""
        return self.events.get(uuid)


@dataclass(slots=True)
class TimeTreeData:
    """The current state of all calendars of a config entry."""

    calendars: dict[str, CalendarData] = field(default_factory=dict)
    fetched_at: datetime | None = None

    def get(self, calendar_id: str) -> CalendarData | None:
        """Return the data of one calendar."""
        return self.calendars.get(str(calendar_id))

    def all_events(self) -> list[tuple[str, TimeTreeEvent]]:
        """Return every event of every calendar as (calendar_id, event) pairs."""
        return [
            (calendar_id, event)
            for calendar_id, data in self.calendars.items()
            for event in data.events.values()
        ]

    def find_event(self, uuid: str) -> tuple[str, TimeTreeEvent] | None:
        """Return the calendar id and event for a uuid."""
        for calendar_id, data in self.calendars.items():
            if (event := data.events.get(uuid)) is not None:
                return calendar_id, event
        return None


class TimeTreeCoordinator(DataUpdateCoordinator[TimeTreeData]):
    """Poll the TimeTree API for all selected calendars."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api: TimeTreeApi,
        options: TimeTreeOptions,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=max(1, options.scan_interval)),
        )
        self.entry = entry
        self.api = api
        self.options = options
        self.last_update_success_time: datetime | None = None
        self._user_names: dict[str, dict[int, str]] = {}
        self._alias_codes: dict[str, str | None] = {
            calendar_id: option.alias_code
            for calendar_id, option in options.calendars.items()
        }

    @property
    def calendar_ids(self) -> list[str]:
        """Return the selected calendar ids."""
        return list(self.options.calendars)

    def alias_code(self, calendar_id: str) -> str | None:
        """Return the alias code of a calendar (needed for write URLs)."""
        return self._alias_codes.get(str(calendar_id))

    def calendar_meta(self, calendar_id: str) -> TimeTreeCalendar:
        """Return a calendar object usable by the API client."""
        option = self.options.calendar(calendar_id)
        return TimeTreeCalendar(
            calendar_id=str(calendar_id),
            name=option.name if option else "TimeTree",
            alias_code=self._alias_codes.get(str(calendar_id)),
        )

    async def async_refresh_calendar_metadata(self) -> None:
        """Refresh names, members and alias codes from the API."""
        try:
            calendars = await self.api.async_get_calendars()
        except TimeTreeAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except TimeTreeApiError as err:
            _LOGGER.debug("Could not refresh TimeTree metadata: %s", err)
            return
        for calendar in calendars:
            self._user_names[calendar.calendar_id] = calendar.users
            self._alias_codes[calendar.calendar_id] = calendar.alias_code

    async def _async_update_data(self) -> TimeTreeData:
        """Fetch the events of every selected calendar."""
        await self.async_refresh_calendar_metadata()

        calendars: dict[str, CalendarData] = {}
        errors: list[str] = []
        for calendar_id in self.calendar_ids:
            calendar = self.calendar_meta(calendar_id)
            calendar.users = self._user_names.get(calendar_id, {})
            try:
                events = await self.api.async_fetch_events(
                    calendar,
                    include_comments=self.options.include_comments,
                    include_birthdays=self.options.include_birthdays,
                )
            except TimeTreeAuthError as err:
                raise ConfigEntryAuthFailed(str(err)) from err
            except TimeTreeApiError as err:
                errors.append(f"{calendar.name}: {err}")
                continue
            calendars[calendar_id] = CalendarData(
                calendar=calendar,
                events={event.uuid: event for event in events if event.uuid},
                fetched_at=dt_util.utcnow(),
            )

        if not calendars and errors:
            raise UpdateFailed("; ".join(errors))
        if errors:
            _LOGGER.warning("Some TimeTree calendars failed to update: %s", errors)

        self.last_update_success_time = dt_util.utcnow()
        return TimeTreeData(calendars=calendars, fetched_at=self.last_update_success_time)

    async def async_update_options(self, options: TimeTreeOptions) -> None:
        """Apply new options to the running coordinator."""
        self.options = options
        self.update_interval = timedelta(minutes=max(1, options.scan_interval))
        self._alias_codes = {
            calendar_id: option.alias_code
            for calendar_id, option in options.calendars.items()
        }
        await self.async_request_refresh()

    async def async_create_event(
        self, calendar_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Create an event and refresh the data."""
        calendar = self.calendar_meta(calendar_id)
        calendar.users = self._user_names.get(str(calendar_id), {})
        result = await self.api.async_create_event(calendar, payload)
        await self.async_request_refresh()
        return result if isinstance(result, dict) else {}

    async def async_update_event(
        self, calendar_id: str, uuid: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Update an event and refresh the data."""
        calendar = self.calendar_meta(calendar_id)
        calendar.users = self._user_names.get(str(calendar_id), {})
        result = await self.api.async_update_event(calendar, uuid, payload)
        await self.async_request_refresh()
        return result if isinstance(result, dict) else {}

    async def async_delete_event(self, calendar_id: str, uuid: str) -> None:
        """Delete an event and refresh the data."""
        calendar = self.calendar_meta(calendar_id)
        calendar.users = self._user_names.get(str(calendar_id), {})
        await self.api.async_delete_event(calendar, uuid)
        await self.async_request_refresh()
