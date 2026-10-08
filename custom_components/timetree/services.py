"""Service handlers for the TimeTree integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .const import (
    ATTR_ACTION,
    ATTR_CALENDAR_ID,
    ATTR_CONFLICT_ID,
    ATTR_DRY_RUN,
    ATTR_ENTRY_ID,
    DOMAIN,
    RESOLUTIONS,
    SERVICE_EXPORT_NOW,
    SERVICE_GET_EVENT,
    SERVICE_LIST_CONFLICTS,
    SERVICE_REFRESH_NOW,
    SERVICE_RESOLVE_ALL_CONFLICTS,
    SERVICE_RESOLVE_CONFLICT,
)

_LOGGER = logging.getLogger(__name__)

EXPORT_NOW_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ENTRY_ID): cv.string,
        vol.Optional(ATTR_DRY_RUN): cv.boolean,
    }
)

REFRESH_NOW_SCHEMA = vol.Schema({vol.Optional(ATTR_ENTRY_ID): cv.string})

LIST_CONFLICTS_SCHEMA = vol.Schema({vol.Optional(ATTR_ENTRY_ID): cv.string})

RESOLVE_CONFLICT_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CONFLICT_ID): cv.string,
        vol.Required(ATTR_ACTION): vol.In(RESOLUTIONS),
        vol.Optional(ATTR_ENTRY_ID): cv.string,
    }
)

RESOLVE_ALL_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ACTION): vol.In(RESOLUTIONS),
        vol.Optional(ATTR_ENTRY_ID): cv.string,
    }
)

GET_EVENT_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_CALENDAR_ID): cv.string,
        vol.Required("uuid"): cv.string,
        vol.Optional(ATTR_ENTRY_ID): cv.string,
    }
)


def _entries(
    hass: HomeAssistant, config_entry_id: str | None = None
) -> list[ConfigEntry]:
    """Return the loaded TimeTree config entries."""
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if config_entry_id:
        entries = [entry for entry in entries if entry.entry_id == config_entry_id]
        if not entries:
            raise HomeAssistantError(f"unknown TimeTree config entry {config_entry_id}")
    return list(entries)


async def _async_export_now(call: ServiceCall) -> ServiceResponse:
    """Run the export loop now."""
    results: dict[str, Any] = {}
    for entry in _entries(call.hass, call.data.get(ATTR_ENTRY_ID)):
        runtime = entry.runtime_data
        report = await runtime.exporter.async_run(
            reason="service", dry_run=call.data.get(ATTR_DRY_RUN)
        )
        results[entry.entry_id] = report.as_dict()
    return {"reports": results}


async def _async_refresh_now(call: ServiceCall) -> ServiceResponse:
    """Refresh the TimeTree data now."""
    results: dict[str, Any] = {}
    for entry in _entries(call.hass, call.data.get(ATTR_ENTRY_ID)):
        coordinator = entry.runtime_data.coordinator
        await coordinator.async_refresh()
        results[entry.entry_id] = {
            "last_update_success": coordinator.last_update_success,
            "calendars": {
                calendar_id: len(data.events)
                for calendar_id, data in (coordinator.data.calendars.items() if coordinator.data else [])
            },
            "members": coordinator._user_names,
        }
    return {"entries": results}


async def _async_list_conflicts(call: ServiceCall) -> ServiceResponse:
    """Return the pending conflicts."""
    conflicts: list[dict[str, Any]] = []
    for entry in _entries(call.hass, call.data.get(ATTR_ENTRY_ID)):
        conflicts.extend(
            conflict.as_attribute()
            for conflict in entry.runtime_data.store.conflicts.values()
        )
    conflicts.sort(key=lambda item: item.get("detected_at") or "")
    return {"conflicts": conflicts, "count": len(conflicts)}


async def _async_resolve_conflict(call: ServiceCall) -> ServiceResponse:
    """Resolve one pending conflict."""
    conflict_id = call.data[ATTR_CONFLICT_ID]
    resolution = call.data[ATTR_ACTION]
    for entry in _entries(call.hass, call.data.get(ATTR_ENTRY_ID)):
        if conflict_id in entry.runtime_data.store.conflicts:
            resolved = await entry.runtime_data.exporter.async_resolve_conflict(
                conflict_id, resolution
            )
            return {"resolved": resolved, "conflict_id": conflict_id}
    raise HomeAssistantError(f"unknown conflict id {conflict_id}")


async def _async_resolve_all_conflicts(call: ServiceCall) -> ServiceResponse:
    """Resolve every pending conflict."""
    resolution = call.data[ATTR_ACTION]
    results: dict[str, Any] = {}
    total = 0
    for entry in _entries(call.hass, call.data.get(ATTR_ENTRY_ID)):
        resolved, errors = await entry.runtime_data.exporter.async_resolve_all(
            resolution
        )
        total += resolved
        results[entry.entry_id] = {"resolved": resolved, "errors": errors}
    return {"resolved": total, "entries": results}


async def _async_get_event(call: ServiceCall) -> ServiceResponse:
    """Return the raw details of one TimeTree event."""
    calendar_id = call.data.get(ATTR_CALENDAR_ID)
    uuid = call.data["uuid"]
    for entry in _entries(call.hass, call.data.get(ATTR_ENTRY_ID)):
        data = entry.runtime_data.coordinator.data
        if data is None:
            continue
        for current_id, calendar_data in data.calendars.items():
            if calendar_id and current_id != calendar_id:
                continue
            event = calendar_data.get(uuid)
            if event is None:
                continue
            return {
                "event": {
                    "uuid": event.uuid,
                    "calendar_id": current_id,
                    "title": event.title,
                    "note": event.note,
                    "location": event.location,
                    "all_day": event.all_day,
                    "start": event.ha_start.isoformat(),
                    "end": event.ha_end.isoformat(),
                    "rrule": event.rrule_line,
                    "recurrences": event.recurrences,
                    "label": event.label_name,
                    "attendees": event.attendee_names,
                    "updated_at": event.updated_at,
                    "fingerprint": event.fingerprint(),
                }
            }
    raise HomeAssistantError(f"event {uuid} was not found")


_REGISTERED = False


def async_register_services(hass: HomeAssistant) -> None:
    """Register the integration services once."""
    global _REGISTERED  # noqa: PLW0603 - module level flag mirrors core style
    if _REGISTERED:
        return
    hass.services.async_register(
        DOMAIN,
        SERVICE_EXPORT_NOW,
        _async_export_now,
        schema=EXPORT_NOW_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_REFRESH_NOW,
        _async_refresh_now,
        schema=REFRESH_NOW_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_LIST_CONFLICTS,
        _async_list_conflicts,
        schema=LIST_CONFLICTS_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_RESOLVE_CONFLICT,
        _async_resolve_conflict,
        schema=RESOLVE_CONFLICT_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_RESOLVE_ALL_CONFLICTS,
        _async_resolve_all_conflicts,
        schema=RESOLVE_ALL_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_EVENT,
        _async_get_event,
        schema=GET_EVENT_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    _REGISTERED = True


def async_remove_services(hass: HomeAssistant) -> None:
    """Remove the services when the last entry is unloaded."""
    global _REGISTERED  # noqa: PLW0603
    if hass.config_entries.async_loaded_entries(DOMAIN):
        return
    for service in (
        SERVICE_EXPORT_NOW,
        SERVICE_REFRESH_NOW,
        SERVICE_LIST_CONFLICTS,
        SERVICE_RESOLVE_CONFLICT,
        SERVICE_RESOLVE_ALL_CONFLICTS,
        SERVICE_GET_EVENT,
    ):
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)
    _REGISTERED = False
