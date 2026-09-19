"""Minimal Home Assistant stubs so the integration logic can be tested locally.

The integration is not installed in a Home Assistant runtime here, so this
module injects lightweight stand-ins for the ``homeassistant`` modules the
integration imports. Only the behaviour that the integration actually uses is
implemented.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import types
from datetime import datetime, timedelta, timezone, tzinfo
from enum import StrEnum
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parent.parent
INTEGRATION_DIR = REPO_ROOT / "custom_components" / "timetree"
_INSTALLED = False


def _extra_lib_folders() -> list[Path]:
    """Return folders that contain the helper packages (dateutil, tzdata)."""
    folders: list[Path] = []
    env = os.environ.get("TT_LIBS")
    if env:
        folders.extend(Path(part) for part in env.split(os.pathsep) if part)
    temp = Path(os.environ.get("TEMP", "/tmp"))
    folders.extend([temp / "tt-libs", temp / "fd-libs"])
    return folders


# Helper packages must be importable before the timezone database is probed.
for _folder in _extra_lib_folders():
    if _folder.is_dir() and str(_folder) not in sys.path:
        sys.path.insert(0, str(_folder))


def _tz_exists() -> bool:
    """Return True when the host has a time zone database."""
    try:
        ZoneInfo("Europe/Vienna")
    except Exception:  # noqa: BLE001
        return False
    return True


# Without a tz database a fixed offset keeps tests deterministic.
TEST_TZ: tzinfo = (
    ZoneInfo("Europe/Vienna") if _tz_exists() else timezone(timedelta(hours=2))
)


class _SupportResponse(StrEnum):
    """Mirror of ``homeassistant.core.SupportsResponse``."""

    NONE = "none"
    ONLY = "only"
    OPTIONAL = "optional"


# ---------------------------------------------------------------------------
# module helpers
# ---------------------------------------------------------------------------
def _module(name: str) -> types.ModuleType:
    module = sys.modules.get(name)
    if module is None:
        module = types.ModuleType(name)
        sys.modules[name] = module
    return module


def _package(name: str) -> types.ModuleType:
    module = _module(name)
    module.__path__ = []  # type: ignore[attr-defined]
    return module


# ---------------------------------------------------------------------------
# hass core
# ---------------------------------------------------------------------------
class HomeAssistant:
    """Stand-in for the Home Assistant core object."""

    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self.bus = FakeBus()
        self.bus.hass = self
        self.services = FakeServices()
        self.config = types.SimpleNamespace(time_zone="Europe/Vienna")
        self.executor_calls = 0
        self.background_tasks: list[asyncio.Task] = []
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def async_add_executor_job(self, target, *args):
        """Run the blocking function inline."""
        self.executor_calls += 1
        return target(*args)

    def async_create_background_task(self, target, name=None, eager_start=True):
        """Schedule a coroutine."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # pragma: no cover - no loop in this thread
            loop = asyncio.new_event_loop()
        task = loop.create_task(target)
        self.background_tasks.append(task)
        return task

    def async_create_task(self, target, name=None, eager_start=True):
        """Schedule a coroutine."""
        return self.async_create_background_task(target, name)


class FakeBus:
    """Minimal event bus."""

    def __init__(self) -> None:
        self.hass: HomeAssistant | None = None
        self.fired: list[tuple[str, dict[str, Any]]] = []

    def async_fire(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        """Record a fired event."""
        self.fired.append((event_type, data or {}))
        if self.hass is not None:
            self.hass.events.append((event_type, data or {}))


class FakeServices:
    """Minimal service registry."""

    def __init__(self) -> None:
        self.registered: dict[tuple[str, str], Any] = {}
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def async_register(self, domain, service, handler, **kwargs):
        """Register a service."""
        self.registered[(domain, service)] = handler

    def async_remove(self, domain, service):
        """Remove a service."""
        self.registered.pop((domain, service), None)

    def has_service(self, domain, service) -> bool:
        """Return True when a service is registered."""
        return (domain, service) in self.registered

    async def async_call(self, domain, service, data=None, blocking=False, target=None):
        """Record a service call."""
        self.calls.append((domain, service, data or {}))
        return None


def callback(func):
    """Return the function unchanged (HA decorator)."""
    return func


def _install_core() -> None:
    const = _module("homeassistant.const")
    const.CONF_EMAIL = "email"
    const.CONF_PASSWORD = "password"
    const.CONF_NAME = "name"
    const.ATTR_ENTITY_ID = "entity_id"

    class Platform(StrEnum):
        CALENDAR = "calendar"
        SENSOR = "sensor"

    class EntityCategory(StrEnum):
        DIAGNOSTIC = "diagnostic"
        CONFIG = "config"

    const.Platform = Platform
    const.EntityCategory = EntityCategory

    core = _module("homeassistant.core")
    core.HomeAssistant = HomeAssistant
    core.callback = callback
    core.SupportsResponse = _SupportResponse
    core.ServiceResponse = dict

    class ServiceCall:
        """Stand-in for a service call."""

        def __init__(self, hass, data=None):
            self.hass = hass
            self.data = data or {}

    core.ServiceCall = ServiceCall

    exceptions = _module("homeassistant.exceptions")

    class HomeAssistantError(Exception):
        """Base HA error."""

    class ConfigEntryAuthFailed(HomeAssistantError):
        """Auth error."""

    class ServiceValidationError(HomeAssistantError):
        """Validation error."""

    exceptions.HomeAssistantError = HomeAssistantError
    exceptions.ConfigEntryAuthFailed = ConfigEntryAuthFailed
    exceptions.ServiceValidationError = ServiceValidationError

    # -- util ---------------------------------------------------------------
    util = _package("homeassistant.util")
    hass_dict = _module("homeassistant.util.hass_dict")

    class HassKey(str):
        """String subclass used as a HassDict key."""

        __slots__ = ()

    hass_dict.HassKey = HassKey
    util.hass_dict = hass_dict

    dt = _module("homeassistant.util.dt")
    dt.DEFAULT_TIME_ZONE = TEST_TZ

    def utcnow() -> datetime:
        return datetime.now(timezone.utc)

    def now() -> datetime:
        return datetime.now(timezone.utc).astimezone(TEST_TZ)

    def as_local(value: datetime) -> datetime:
        return value.astimezone(TEST_TZ)

    def parse_datetime(value: str | None) -> datetime | None:
        if not value:
            return None
        text = value.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return None

    def start_of_local_day(value: datetime) -> datetime:
        return value.astimezone(TEST_TZ).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

    def as_utc(value: datetime) -> datetime:
        return value.astimezone(timezone.utc)

    dt.utcnow = utcnow
    dt.now = now
    dt.as_local = as_local
    dt.parse_datetime = parse_datetime
    dt.start_of_local_day = start_of_local_day
    dt.as_utc = as_utc
    util.dt = dt

    helpers = _package("homeassistant.helpers")

    entity_module = _module("homeassistant.helpers.entity")

    class Entity:
        """Minimal entity."""

        _attr_has_entity_name = False
        _attr_should_poll = True
        _attr_device_info: Any = None
        _attr_unique_id: str | None = None
        _attr_name: str | None = None

        def __init__(self) -> None:
            self.hass: Any = None
            self.entity_id: str | None = None
            self._removers: list[Any] = []
            self.written_state = 0

        def async_write_ha_state(self) -> None:
            self.written_state += 1

        def async_on_remove(self, func) -> None:
            self._removers.append(func)

        async def async_added_to_hass(self) -> None:
            return None

        @property
        def available(self) -> bool:
            return True

    entity_module.Entity = Entity

    def _simple_decorator(func):
        return func

    entity_module.callback = _simple_decorator

    entity_platform = _module("homeassistant.helpers.entity_platform")
    entity_platform.AddConfigEntryEntitiesCallback = Any
    entity_platform.AddEntitiesCallback = Any

    event = _module("homeassistant.helpers.event")
    event.tracked = []

    def async_track_time_interval(hass, action, interval, name=None, cancel_on_shutdown=None):
        event.tracked.append((action, interval, name))
        return lambda: event.tracked.remove((action, interval, name)) if (action, interval, name) in event.tracked else None

    event.async_track_time_interval = async_track_time_interval
    event.async_call_later = lambda hass, delay, action: (lambda: None)

    dispatcher = _module("homeassistant.helpers.dispatcher")
    dispatcher.signals: dict[str, list[Any]] = {}

    def async_dispatcher_send(hass, signal, *args):
        for target in list(dispatcher.signals.get(signal, [])):
            target(*args)

    def async_dispatcher_connect(hass, signal, target):
        dispatcher.signals.setdefault(signal, []).append(target)

        def _unsub():
            dispatcher.signals[signal].remove(target)

        return _unsub

    dispatcher.async_dispatcher_send = async_dispatcher_send
    dispatcher.async_dispatcher_connect = async_dispatcher_connect

    storage = _module("homeassistant.helpers.storage")
    storage.STORAGE_DIR = ".storage"

    class Store:
        """In-memory stand-in for HA's Store."""

        saved: dict[str, Any] = {}

        def __class_getitem__(cls, item):
            return cls

        def __init__(self, hass, version, key):
            self.hass = hass
            self.version = version
            self.key = key

        async def async_load(self):
            return Store.saved.get(self.key)

        async def async_save(self, data):
            Store.saved[self.key] = json.loads(json.dumps(data))

        def async_delay_save(self, data_func, delay):
            Store.saved[self.key] = json.loads(json.dumps(data_func()))

    storage.Store = Store

    update_coordinator = _module("homeassistant.helpers.update_coordinator")

    class UpdateFailed(Exception):
        """Update failure."""

    class DataUpdateCoordinator:
        """Minimal coordinator."""

        def __class_getitem__(cls, item):
            return cls

        def __init__(self, hass, logger, name=None, update_interval=None, **kwargs):
            self.hass = hass
            self.logger = logger
            self.name = name
            self.update_interval = update_interval
            self.data = None
            self.last_update_success = True
            self.last_update_success_time = None
            self.update_calls = 0

        async def async_config_entry_first_refresh(self):
            await self.async_refresh()

        async def async_refresh(self):
            self.update_calls += 1
            self.data = await self._async_update_data()
            self.last_update_success = True
            return self.data

        async def async_request_refresh(self):
            return await self.async_refresh()

        def async_add_listener(self, update_callback, context=None):
            return lambda: None

        def async_set_updated_data(self, data):
            self.data = data

        async def _async_update_data(self):  # pragma: no cover - overridden
            return None

    class CoordinatorEntity(Entity):
        """Minimal coordinator entity."""

        _attr_should_poll = False

        def __class_getitem__(cls, item):
            return cls

        def __init__(self, coordinator):
            super().__init__()
            self.coordinator = coordinator
            self.hass = coordinator.hass

        @property
        def available(self) -> bool:
            return self.coordinator.last_update_success

        def _handle_coordinator_update(self) -> None:  # pragma: no cover
            self.async_write_ha_state()

        async def async_added_to_hass(self) -> None:
            return None

    update_coordinator.DataUpdateCoordinator = DataUpdateCoordinator
    update_coordinator.CoordinatorEntity = CoordinatorEntity
    update_coordinator.UpdateFailed = UpdateFailed

    selector = _module("homeassistant.helpers.selector")

    class _Selector:
        def __init__(self, config=None, **kwargs):
            self.config = config if config is not None else kwargs

        def __call__(self, value):
            """Selectors double as voluptuous validators in HA."""
            return value

    class TextSelectorType(StrEnum):
        EMAIL = "email"
        PASSWORD = "password"
        TEXT = "text"

    class SelectSelectorMode(StrEnum):
        DROPDOWN = "dropdown"
        LIST = "list"

    class NumberSelectorMode(StrEnum):
        SLIDER = "slider"
        BOX = "box"

    selector.TextSelector = _Selector
    selector.TextSelectorConfig = _Selector
    selector.TextSelectorType = TextSelectorType
    selector.SelectSelector = _Selector
    selector.SelectSelectorConfig = _Selector
    selector.SelectSelectorMode = SelectSelectorMode
    selector.NumberSelector = _Selector
    selector.NumberSelectorConfig = _Selector
    selector.NumberSelectorMode = NumberSelectorMode
    selector.BooleanSelector = _Selector
    selector.EntitySelector = _Selector
    selector.EntitySelectorConfig = _Selector

    config_validation = _module("homeassistant.helpers.config_validation")
    config_validation.string = str
    config_validation.boolean = bool
    config_validation.entity_id = str
    config_validation.date = str

    device_registry = _module("homeassistant.helpers.device_registry")

    class DeviceEntryType(StrEnum):
        SERVICE = "service"

    class DeviceInfo(dict):
        """TypedDict stand-in."""

        def __init__(self, **kwargs):
            super().__init__(**kwargs)

    class DeviceEntry:
        def __init__(self, device_id):
            self.id = device_id

    class DeviceRegistry:
        def __init__(self):
            self.created: dict[str, DeviceEntry] = {}

        def async_get_or_create(self, **kwargs):
            key = str(sorted(list(kwargs.get("identifiers") or [])))
            entry = self.created.get(key) or DeviceEntry(f"dev-{len(self.created) + 1}")
            self.created[key] = entry
            return entry

        def async_get(self, device_id):
            return None

    device_registry.DeviceInfo = DeviceInfo
    device_registry.DeviceEntryType = DeviceEntryType
    device_registry.DeviceEntry = DeviceEntry
    device_registry.DeviceRegistry = DeviceRegistry
    device_registry.async_get = lambda hass: hass.data.setdefault(
        "device_registry", DeviceRegistry()
    )

    components = _package("homeassistant.components")
    calendar = _module("homeassistant.components.calendar")

    class CalendarEntityFeature(int):
        CREATE_EVENT = 1
        DELETE_EVENT = 2
        UPDATE_EVENT = 4

    class CalendarEntity(Entity):
        """Minimal calendar entity."""

        _attr_supported_features = 0

        @property
        def supported_features(self):
            return self._attr_supported_features

        async def async_get_events(self, hass, start_date, end_date):  # pragma: no cover
            raise NotImplementedError

        async def async_create_event(self, **kwargs):  # pragma: no cover
            raise NotImplementedError

        async def async_delete_event(self, uid, recurrence_id=None, recurrence_range=None):
            raise NotImplementedError

        async def async_update_event(self, uid, event, recurrence_id=None, recurrence_range=None):
            raise NotImplementedError

    class CalendarEvent:
        """Calendar event with the same validation surface as core."""

        def __init__(
            self,
            start,
            end,
            summary,
            description=None,
            location=None,
            uid=None,
            recurrence_id=None,
            rrule=None,
        ):
            if isinstance(start, datetime) != isinstance(end, datetime):
                raise ValueError("start and end must have the same type")
            for value in (start, end):
                if isinstance(value, datetime) and value.tzinfo is None:
                    raise ValueError("Expected all values to have a timezone")
            if not isinstance(start, datetime) and start == end:
                end = start + timedelta(days=1)
            if rrule is not None:
                from dateutil.rrule import rrulestr

                rrulestr(rrule)
                if "FREQ" not in rrule:
                    raise ValueError("rrule did not contain FREQ")
            self.start = start
            self.end = end
            self.summary = summary
            self.description = description
            self.location = location
            self.uid = uid
            self.recurrence_id = recurrence_id
            self.rrule = rrule

        @property
        def all_day(self) -> bool:
            return not isinstance(self.start, datetime)

        def as_dict(self) -> dict[str, Any]:
            return {
                "start": self.start.isoformat()
                if isinstance(self.start, datetime)
                else self.start.isoformat(),
                "end": self.end.isoformat(),
                "summary": self.summary,
                "description": self.description,
                "location": self.location,
                "uid": self.uid,
                "rrule": self.rrule,
                "all_day": self.all_day,
            }

    calendar.CalendarEntity = CalendarEntity
    calendar.CalendarEvent = CalendarEvent
    calendar.CalendarEntityFeature = CalendarEntityFeature
    calendar.DATA_COMPONENT = HassKey("calendar")
    components.calendar = calendar

    sensor = _module("homeassistant.components.sensor")

    class SensorEntity(Entity):
        """Minimal sensor entity."""

    class SensorDeviceClass(StrEnum):
        TIMESTAMP = "timestamp"

    sensor.SensorEntity = SensorEntity
    sensor.SensorDeviceClass = SensorDeviceClass
    components.sensor = sensor

    diagnostics = _module("homeassistant.components.diagnostics")

    def async_redact_data(data, to_redact):
        if isinstance(data, dict):
            return {
                key: "**REDACTED**" if key in to_redact else value
                for key, value in data.items()
            }
        return data

    diagnostics.async_redact_data = async_redact_data
    components.diagnostics = diagnostics

    config_entries = _define_config_entries()
    core.ServiceCall = ServiceCall


def _define_config_entries() -> types.ModuleType:
    module = _module("homeassistant.config_entries")
    ConfigFlowResult = dict

    class ConfigEntry:
        """Minimal config entry."""

        def __init__(self, entry_id="entry1", title="TimeTree", data=None, options=None):
            self.entry_id = entry_id
            self.title = title
            self.data = data or {}
            self.options = options or {}
            self.version = 1
            self.minor_version = 1
            self.runtime_data = None
            self._unloads: list[Any] = []

        def async_on_unload(self, func):
            self._unloads.append(func)

        def add_update_listener(self, listener):
            return lambda: None

        def async_create_background_task(self, hass, target, name=None):
            return hass.async_create_background_task(target, name)

    class ConfigFlow:
        """Minimal config flow."""

        def __init__(self):
            self.hass = None
            self.unique_id = None
            self.forms: list[dict[str, Any]] = []

        async def async_set_unique_id(self, value, raise_on_progress=True):
            self.unique_id = value

        def _abort_if_unique_id_configured(self):
            return None

        def async_show_form(self, step_id, data_schema=None, errors=None, **kwargs):
            self.forms.append({"step_id": step_id, "schema": data_schema, "errors": errors})
            return {"type": "form", "step_id": step_id, "errors": errors or {}}

        def async_show_menu(self, step_id, menu_options, **kwargs):
            self.forms.append({"step_id": step_id, "menu": menu_options})
            return {"type": "menu", "step_id": step_id, "menu_options": menu_options}

        def async_create_entry(self, title, data, options=None):
            return {"type": "create_entry", "title": title, "data": data, "options": options}

        def async_abort(self, reason):
            return {"type": "abort", "reason": reason}

        def async_update_reload_and_abort(self, entry, data_updates=None, **kwargs):
            return {"type": "abort", "reason": "reauth_successful", "updates": data_updates}

        def _get_reauth_entry(self):
            return self._reauth_entry

        def __init_subclass__(cls, domain=None, **kwargs):
            super().__init_subclass__(**kwargs)
            cls.domain = domain

    class OptionsFlow:
        """Minimal options flow."""

        def __init__(self):
            self.hass = None
            self._config_entry = ConfigEntry()
            self.forms: list[dict[str, Any]] = []

        @property
        def config_entry(self):
            return self._config_entry

        def async_show_menu(self, step_id, menu_options, **kwargs):
            self.forms.append({"step_id": step_id, "menu": menu_options})
            return {"type": "menu", "step_id": step_id}

        def async_show_form(self, step_id, data_schema=None, errors=None, **kwargs):
            self.forms.append({"step_id": step_id, "schema": data_schema, "errors": errors})
            return {"type": "form", "step_id": step_id, "errors": errors or {}}

        def async_create_entry(self, title, data):
            return {"type": "create_entry", "title": title, "data": data}

    module.ConfigEntry = ConfigEntry
    module.ConfigFlow = ConfigFlow
    module.OptionsFlow = OptionsFlow
    module.ConfigFlowResult = ConfigFlowResult
    return module


# ---------------------------------------------------------------------------
# loader
# ---------------------------------------------------------------------------
def install_home_assistant_stubs() -> None:
    """Install every stub module (idempotent)."""
    global _INSTALLED  # noqa: PLW0603
    if _INSTALLED:
        return
    _INSTALLED = True
    _package("homeassistant")
    _install_core()
    _install_requests()


def _install_requests() -> None:
    """Provide a tiny ``requests`` stand-in for the local test run."""
    try:
        import requests  # noqa: F401
    except ImportError:
        pass
    else:
        return

    module = _module("requests")

    class Session:
        """Dummy session; tests replace it with their own fake."""

        def __init__(self, *args, **kwargs):
            self.cookies: dict[str, Any] = {}

        def request(self, *args, **kwargs):
            raise RuntimeError("the local test run has no HTTP backend")

        def get(self, *args, **kwargs):
            return self.request(*args, **kwargs)

        def put(self, *args, **kwargs):
            return self.request(*args, **kwargs)

        def post(self, *args, **kwargs):
            return self.request(*args, **kwargs)

        def delete(self, *args, **kwargs):
            return self.request(*args, **kwargs)

        def close(self):
            return None

    module.Session = Session
    module.RequestException = type("RequestException", (Exception,), {})
    module.exceptions = types.SimpleNamespace(RequestException=module.RequestException)


def load_integration() -> types.ModuleType:
    """Import ``custom_components.timetree`` without running its ``__init__``."""
    install_home_assistant_stubs()
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    for folder in _extra_lib_folders():
        if folder.is_dir() and str(folder) not in sys.path:
            sys.path.insert(0, str(folder))

    existing = sys.modules.get("custom_components.timetree")
    if isinstance(existing, types.ModuleType) and getattr(existing, "__path__", None) == [
        str(INTEGRATION_DIR)
    ]:
        return existing

    package = types.ModuleType("custom_components.timetree")
    package.__path__ = [str(INTEGRATION_DIR)]  # type: ignore[attr-defined]
    sys.modules.setdefault("custom_components", _package("custom_components"))
    sys.modules["custom_components.timetree"] = package
    return package


def import_module(name: str):
    """Import a submodule of the integration."""
    package = load_integration()
    return __import__(f"custom_components.timetree.{name}", fromlist=["_"])


def run(coro):
    """Run a coroutine in a fresh event loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
