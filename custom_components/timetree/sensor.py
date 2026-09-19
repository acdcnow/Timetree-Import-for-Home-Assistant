"""Sensor platform for the TimeTree integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import SIGNAL_STORE_UPDATED
from .coordinator import TimeTreeCoordinator
from .entity import TimeTreeCalendarEntityBase, TimeTreeEntity
from .options import CalendarOption

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the TimeTree sensors."""
    runtime = entry.runtime_data
    coordinator: TimeTreeCoordinator = runtime.coordinator
    entities: list[SensorEntity] = [
        TimeTreeLastUpdatedSensor(coordinator, entry, option, runtime.hub_device_id)
        for option in coordinator.options.calendars.values()
    ]
    entities.extend(
        [
            TimeTreeConflictSensor(coordinator, entry, runtime.hub_device_id),
            TimeTreeLastExportSensor(coordinator, entry, runtime.hub_device_id),
            TimeTreeExportStatusSensor(coordinator, entry, runtime.hub_device_id),
        ]
    )
    async_add_entities(entities)


class TimeTreeLastUpdatedSensor(TimeTreeCalendarEntityBase, SensorEntity):
    """Timestamp of the last successful synchronisation."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:clock-check-outline"

    def __init__(
        self,
        coordinator: TimeTreeCoordinator,
        entry: ConfigEntry,
        option: CalendarOption,
        hub_device_id: str | None = None,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry, option, hub_device_id)
        self._attr_name = "Last updated"
        self._attr_unique_id = f"{entry.entry_id}_{option.calendar_id}_last_updated"

    @property
    def native_value(self):
        """Return the last successful update time."""
        return self.coordinator.last_update_success_time

    @property
    def available(self) -> bool:
        """Return True while the coordinator has data."""
        return self.coordinator.last_update_success_time is not None


class TimeTreeStoreSensor(TimeTreeEntity, SensorEntity):
    """Base class for sensors backed by the sync store."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: TimeTreeCoordinator,
        entry: ConfigEntry,
        hub_device_id: str | None = None,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry, hub_device_id)
        self._attr_device_info = self.hub_device_info

    @property
    def store(self):
        """Return the sync store of the config entry."""
        return self.entry.runtime_data.store

    @property
    def exporter(self):
        """Return the export manager of the config entry."""
        return self.entry.runtime_data.exporter

    async def async_added_to_hass(self) -> None:
        """Subscribe to store updates."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_STORE_UPDATED}_{self.entry.entry_id}",
                self._handle_store_update,
            )
        )

    @callback
    def _handle_store_update(self) -> None:
        """Write the new state when the store changed."""
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Ignore coordinator updates, the store drives this sensor."""
        self.async_write_ha_state()


class TimeTreeConflictSensor(TimeTreeStoreSensor):
    """Number of changes that need a manual decision."""

    _attr_icon = "mdi:alert-decagram-outline"

    def __init__(self, coordinator, entry, hub_device_id=None) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry, hub_device_id)
        self._attr_name = "Sync conflicts"
        self._attr_unique_id = f"{entry.entry_id}_conflicts"

    @property
    def native_value(self) -> int:
        """Return the number of pending conflicts."""
        return len(self.store.conflicts)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the pending conflicts."""
        conflicts = sorted(
            self.store.conflicts.values(), key=lambda item: item.detected_at
        )
        return {
            "conflicts": [conflict.as_attribute() for conflict in conflicts],
            "export_target": self.coordinator.options.export_target,
            "conflict_policy": self.coordinator.options.conflict_policy,
        }


class TimeTreeLastExportSensor(TimeTreeStoreSensor):
    """Timestamp of the last export run."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:calendar-sync-outline"

    def __init__(self, coordinator, entry, hub_device_id=None) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry, hub_device_id)
        self._attr_name = "Last export"
        self._attr_unique_id = f"{entry.entry_id}_last_export"

    @property
    def native_value(self):
        """Return the last export timestamp."""
        value = self.store.get_meta("last_export")
        return dt_util.parse_datetime(value) if value else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return export settings and the last report."""
        options = self.coordinator.options
        report = self.store.get_meta("last_report") or {}
        return {
            "export_enabled": options.export_enabled,
            "export_target": options.export_target,
            "export_interval": options.export_interval,
            "export_direction": options.export_direction,
            "dry_run": options.export_dry_run,
            "last_report": report,
        }


class TimeTreeExportStatusSensor(TimeTreeStoreSensor):
    """Result of the last export run."""

    _attr_icon = "mdi:calendar-check-outline"

    def __init__(self, coordinator, entry, hub_device_id=None) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry, hub_device_id)
        self._attr_name = "Export status"
        self._attr_unique_id = f"{entry.entry_id}_export_status"

    @property
    def native_value(self) -> str:
        """Return the state of the last export run."""
        report = self.exporter.last_report
        if report is None:
            return "not_run"
        if report.errors:
            return "error"
        return "ok"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the counters of the last export run."""
        report = self.exporter.last_report
        if report is None:
            return {"last_error": self.exporter.last_error}
        return {"last_error": self.exporter.last_error, **report.as_dict()}
