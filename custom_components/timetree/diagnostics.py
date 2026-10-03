"""Diagnostics support for the TimeTree integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from .coordinator import TimeTreeCoordinator

TO_REDACT = {CONF_EMAIL, CONF_PASSWORD, "email", "password", "uid"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    runtime = entry.runtime_data
    coordinator: TimeTreeCoordinator = runtime.coordinator
    data = coordinator.data
    store = runtime.store

    calendars: dict[str, Any] = {}
    if data is not None:
        for calendar_id, calendar_data in data.calendars.items():
            events = calendar_data.events.values()
            calendars[calendar_id] = {
                "name": calendar_data.calendar.name,
                "alias_code": calendar_data.calendar.alias_code,
                "members": calendar_data.calendar.users,
                "labels": {
                    label_id: label.name
                    for label_id, label in calendar_data.calendar.labels.items()
                },
                "event_count": len(calendar_data.events),
                "recurring_events": sum(1 for event in events if event.is_recurring),
                "all_day_events": sum(1 for event in events if event.all_day),
                "memo_events": sum(1 for event in events if event.is_memo),
                "fetched_at": (
                    calendar_data.fetched_at.isoformat()
                    if calendar_data.fetched_at
                    else None
                ),
            }

    options = coordinator.options
    return {
        "entry": {
            "title": entry.title,
            "version": entry.version,
            "minor_version": entry.minor_version,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
        },
        "options": {
            "calendars": sorted(options.calendars),
            "scan_interval": options.scan_interval,
            "include_birthdays": options.include_birthdays,
            "include_comments": options.include_comments,
            "description_details": options.description_details,
            "export_enabled": options.export_enabled,
            "export_target": options.export_target,
            "export_interval": options.export_interval,
            "export_direction": options.export_direction,
            "export_past_days": options.export_past_days,
            "export_future_days": options.export_future_days,
            "export_delete_removed": options.export_delete_removed,
            "export_recreate_removed": options.export_recreate_removed,
            "export_dry_run": options.export_dry_run,
            "import_unmanaged": options.import_unmanaged,
            "conflict_policy": options.conflict_policy,
            "notify_conflicts": options.notify_conflicts,
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "last_update_success_time": coordinator.last_update_success_time,
            "calendars": calendars,
        },
        "export": {
            "browser_transport": runtime.api.browser_transport,
            "last_error": runtime.exporter.last_error,
            "last_report": (
                runtime.exporter.last_report.as_dict()
                if runtime.exporter.last_report
                else store.get_meta("last_report")
            ),
            "records": len(store.records),
            "pending_conflicts": len(store.conflicts),
            "conflicts": [
                conflict.as_attribute() for conflict in store.conflicts.values()
            ],
        },
        "store": {
            "version": store.get_meta("version"),
            "last_export": store.get_meta("last_export"),
            "last_export_target": store.get_meta("last_export_target"),
        },
    }
