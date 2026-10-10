"""The TimeTree integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .api import TimeTreeApi
from .const import CONFIGURATION_URL, MANUFACTURER
from .coordinator import TimeTreeCoordinator
from .entity import hub_identifier
from .export import ExportManager
from .options import TimeTreeOptions
from .services import async_register_services, async_remove_services
from .store import TimeTreeSyncStore

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.CALENDAR, Platform.SENSOR]


@dataclass
class TimeTreeRuntimeData:
    """Runtime objects of a TimeTree config entry."""

    api: TimeTreeApi
    coordinator: TimeTreeCoordinator
    store: TimeTreeSyncStore
    exporter: ExportManager
    hub_device_id: str | None = None


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up TimeTree from a config entry."""
    options = TimeTreeOptions.from_entry(entry)

    api = await TimeTreeApi.async_create(
        hass, entry.data[CONF_EMAIL], entry.data[CONF_PASSWORD]
    )
    coordinator = TimeTreeCoordinator(hass, entry, api, options)
    await coordinator.async_config_entry_first_refresh()

    store = TimeTreeSyncStore(hass, entry.entry_id)
    await store.async_load()

    registry = dr.async_get(hass)
    device = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={hub_identifier(entry.entry_id)},
        name=f"TimeTree {entry.title}",
        manufacturer=MANUFACTURER,
        model="TimeTree account",
        entry_type=dr.DeviceEntryType.SERVICE,
        configuration_url=CONFIGURATION_URL,
    )

    exporter = ExportManager(hass, entry, coordinator, store)
    entry.runtime_data = TimeTreeRuntimeData(
        api=api,
        coordinator=coordinator,
        store=store,
        exporter=exporter,
        hub_device_id=device.id,
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    async_register_services(hass)
    exporter.async_start()

    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a TimeTree config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unloaded:
        return False

    runtime: TimeTreeRuntimeData = entry.runtime_data
    runtime.exporter.async_stop()
    await runtime.store.async_save_now()
    await runtime.api.async_close()
    async_remove_services(hass)
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate an old (single calendar) config entry."""
    if entry.version > 1:
        return False
    # ``TimeTreeOptions.from_entry`` understands the 1.x layout, so the entry
    # only has to be re-versioned and reloaded.
    hass.config_entries.async_update_entry(entry, version=1, minor_version=1)
    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)
