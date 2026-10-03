"""Shared entity base classes for the TimeTree integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import (
    DeviceEntryType,
    DeviceInfo,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONFIGURATION_URL, DOMAIN, MANUFACTURER
from .coordinator import CalendarData, TimeTreeCoordinator
from .models import TimeTreeEvent
from .options import CalendarOption


def hub_identifier(entry_id: str) -> tuple[str, str]:
    """Return the device identifier of the account."""
    return (DOMAIN, entry_id)


def calendar_identifier(entry_id: str, calendar_id: str) -> tuple[str, str]:
    """Return the device identifier of a calendar."""
    return (DOMAIN, f"{entry_id}:{calendar_id}")


class TimeTreeEntity(CoordinatorEntity[TimeTreeCoordinator]):
    """Base class for entities of a TimeTree config entry."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: TimeTreeCoordinator,
        entry: ConfigEntry,
        hub_device_id: str | None = None,
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self.entry = entry
        self.hub_device_id = hub_device_id

    @property
    def hub_device_info(self) -> DeviceInfo:
        """Return the device info of the account device."""
        return DeviceInfo(
            identifiers={hub_identifier(self.entry.entry_id)},
            name=f"TimeTree {self.coordinator.entry.title}",
            manufacturer=MANUFACTURER,
            model="TimeTree account",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=CONFIGURATION_URL,
        )


class TimeTreeCalendarEntityBase(TimeTreeEntity):
    """Base class for entities bound to one TimeTree calendar."""

    def __init__(
        self,
        coordinator: TimeTreeCoordinator,
        entry: ConfigEntry,
        option: CalendarOption,
        hub_device_id: str | None = None,
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator, entry, hub_device_id)
        self.calendar_id = option.calendar_id
        self._calendar_name = option.name
        self._attr_device_info = DeviceInfo(
            identifiers={calendar_identifier(entry.entry_id, option.calendar_id)},
            name=option.name,
            manufacturer=MANUFACTURER,
            model="TimeTree calendar",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=CONFIGURATION_URL,
            via_device_id=hub_device_id,
        )

    @property
    def calendar_name(self) -> str:
        """Return the calendar name."""
        return self._calendar_name

    @property
    def calendar_data(self) -> CalendarData | None:
        """Return the current data of this calendar."""
        data = self.coordinator.data
        if data is None:
            return None
        return data.get(self.calendar_id)

    @property
    def calendar_events(self) -> list[TimeTreeEvent]:
        """Return the current raw events of this calendar."""
        data = self.calendar_data
        if data is None:
            return []
        return data.event_list()

    @property
    def calendar_label(self) -> str:
        """Return a friendly name including the account."""
        return self._calendar_name
