"""Calendar export / synchronisation engine.

TimeTree is the source of truth. Every run:

1. reads the current TimeTree events of all selected calendars,
2. reads the events of the configured export target (e.g. a Google calendar)
   inside the export window and matches them through an invisible marker that
   is appended to the exported description,
3. compares both sides with the state stored from the previous run and either
   applies the change, or - when both sides changed - queues a conflict that a
   human resolves through the ``timetree.resolve_conflict`` service.
"""

from __future__ import annotations

import logging
import uuid as uuid_lib
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any
from collections.abc import Iterable, Mapping

from homeassistant.components.calendar import (
    CalendarEntity,
    CalendarEntityFeature,
    CalendarEvent,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util

from .api import TimeTreeError
from .conflicts import (
    Action,
    Conflict,
    Decision,
    EventState,
    SyncRecord,
    add_marker,
    decide,
    find_marker,
    make_conflict_id,
    resolution_to_action,
    strip_marker,
)
from .const import (
    DOMAIN,
    EVENT_CONFLICT_DETECTED,
    EVENT_EXPORT_COMPLETED,
    RESOLUTION_IGNORE,
    RESOLUTION_SKIP,
    SIGNAL_STORE_UPDATED,
)
from .coordinator import TimeTreeCoordinator
from .models import TimeTreeEvent, build_event_payload, describe_event
from .options import TimeTreeOptions
from .recurrence import expand_event
from .store import TimeTreeSyncStore

_LOGGER = logging.getLogger(__name__)

CALENDAR_COMPONENT_KEY = "calendar"
STARTUP_DELAY = timedelta(seconds=45)
NOTIFICATION_ID = f"{DOMAIN}_conflicts"


class ExportError(HomeAssistantError):
    """Raised when an export cannot be performed."""


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------
@dataclass
class ExportReport:
    """Result of one export run."""

    reason: str = "manual"
    target: str | None = None
    dry_run: bool = False
    started: datetime = field(default_factory=dt_util.utcnow)
    finished: datetime | None = None
    created: int = 0
    updated: int = 0
    deleted: int = 0
    imported: int = 0
    unchanged: int = 0
    conflicts: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Return True when nothing failed."""
        return not self.errors

    @property
    def changed(self) -> int:
        """Return the number of applied changes."""
        return self.created + self.updated + self.deleted + self.imported

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON friendly representation."""
        return {
            "reason": self.reason,
            "target": self.target,
            "dry_run": self.dry_run,
            "started": self.started.isoformat(),
            "finished": self.finished.isoformat() if self.finished else None,
            "created": self.created,
            "updated": self.updated,
            "deleted": self.deleted,
            "imported": self.imported,
            "unchanged": self.unchanged,
            "conflicts": self.conflicts,
            "skipped": self.skipped,
            "errors": self.errors,
            "ok": self.ok,
        }

    def summary(self) -> str:
        """Return a human readable one line summary."""
        return (
            f"created {self.created}, updated {self.updated}, "
            f"deleted {self.deleted}, imported {self.imported}, "
            f"conflicts {self.conflicts}, errors {len(self.errors)}"
        )


@dataclass(slots=True)
class TargetWrite:
    """Everything needed to write one event into the export target."""

    summary: str
    description: str
    location: str
    start: date | datetime
    end: date | datetime
    all_day: bool
    rrule: str | None = None

    @property
    def marker_uuid(self) -> str | None:
        """Return the TimeTree uuid stored in the description."""
        return find_marker(self.description)

    @classmethod
    def from_timetree(
        cls,
        event: TimeTreeEvent,
        *,
        marker_uuid: str | None = None,
        details: bool = True,
    ) -> TargetWrite:
        """Build the write payload for a TimeTree event."""
        description = describe_event(event, include_details=details)
        if marker_uuid:
            description = add_marker(description, marker_uuid)
        return cls(
            summary=event.title,
            description=description,
            location=event.location or "",
            start=event.ha_start,
            end=event.ha_end,
            all_day=event.all_day,
            rrule=bare_rrule(event.rrule_line),
        )

    @classmethod
    def from_state(
        cls, state: EventState, *, marker_uuid: str | None = None
    ) -> TargetWrite:
        """Build the write payload for a stored/observed state."""
        description = strip_marker(state.description)
        if marker_uuid:
            description = add_marker(description, marker_uuid)
        return cls(
            summary=state.summary,
            description=description,
            location=state.location,
            start=parse_iso_value(state.start, state.all_day),
            end=parse_iso_value(state.end, state.all_day),
            all_day=state.all_day,
            rrule=state.rrule,
        )


def bare_rrule(rrule_line: str | None) -> str | None:
    """Return an RRULE value without the ``RRULE:`` prefix."""
    if not rrule_line:
        return None
    _, _, value = rrule_line.partition(":")
    return value or None


def parse_iso_value(value: str, all_day: bool) -> date | datetime:
    """Parse a stored ISO value back into a date or datetime."""
    if all_day:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return dt_util.now().date()
    parsed = dt_util.parse_datetime(value)
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            parsed = dt_util.now()
    return dt_util.as_local(parsed)


# ---------------------------------------------------------------------------
# target calendar adapter
# ---------------------------------------------------------------------------
def async_get_calendar_entity(
    hass: HomeAssistant, entity_id: str
) -> CalendarEntity | None:
    """Return a calendar entity object by entity id."""
    component = hass.data.get(CALENDAR_COMPONENT_KEY)
    if component is None:
        return None
    entity = component.get_entity(entity_id)
    if entity is None or not isinstance(entity, CalendarEntity):
        return None
    return entity


class CalendarTarget:
    """Adapter around a calendar entity used as export target."""

    def __init__(self, hass: HomeAssistant, entity_id: str, entity: CalendarEntity) -> None:
        """Initialize the adapter."""
        self.hass = hass
        self.entity_id = entity_id
        self.entity = entity

    @property
    def features(self) -> CalendarEntityFeature:
        """Return the supported features of the target."""
        return self.entity.supported_features or CalendarEntityFeature(0)

    def supports(self, feature: CalendarEntityFeature) -> bool:
        """Return True when the target supports a feature."""
        return bool(self.features & feature)

    @property
    def name(self) -> str:
        """Return a friendly name for the target."""
        return self.entity.name or self.entity_id

    async def async_get_events(
        self, start: datetime, end: datetime
    ) -> list[CalendarEvent]:
        """Return the events of the target inside a window."""
        return await self.entity.async_get_events(self.hass, start, end)

    async def async_create(self, write: TargetWrite) -> None:
        """Create an event in the target calendar."""
        kwargs: dict[str, Any] = {
            "summary": write.summary,
            "description": write.description,
            "location": write.location,
            "dtstart": write.start,
            "dtend": write.end,
        }
        if write.rrule:
            kwargs["rrule"] = write.rrule
        await self._async_call("async_create_event", **kwargs)

    async def async_update(self, uid: str | None, write: TargetWrite) -> None:
        """Update an event in the target calendar.

        Targets that cannot update an event in place (for example Google, which
        only exposes create/delete) get a fresh copy instead. The new copy is
        created first so a failure never loses the existing event.
        """
        if not uid:
            await self.async_create(write)
            return
        event = {
            "summary": write.summary,
            "description": write.description,
            "location": write.location,
            "dtstart": write.start,
            "dtend": write.end,
            "rrule": write.rrule,
        }
        if self.supports(CalendarEntityFeature.UPDATE_EVENT):
            try:
                await self._async_call("async_update_event", uid, event)
                return
            except (ExportError, HomeAssistantError, NotImplementedError) as err:
                _LOGGER.debug("Update on %s failed (%s), recreating instead", self.entity_id, err)
        await self.async_create(write)
        try:
            await self.async_delete(uid)
        except (ExportError, HomeAssistantError, NotImplementedError) as err:
            _LOGGER.warning(
                "%s: could not remove the superseded copy %s: %s",
                self.entity_id,
                uid,
                err,
            )

    async def async_delete(self, uid: str) -> None:
        """Delete an event from the target calendar."""
        await self._async_call("async_delete_event", uid)

    async def _async_call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        """Call an entity method, converting unsupported calls to errors."""
        func = getattr(self.entity, method, None)
        if func is None:
            raise ExportError(
                f"{self.entity_id} does not support {method.removesuffix('_event')}"
            )
        try:
            return await func(*args, **kwargs)
        except NotImplementedError as err:
            raise ExportError(f"{self.entity_id} does not support {method}") from err
        except TypeError as err:
            # e.g. an entity that does not accept ``rrule``
            if "rrule" in kwargs:
                kwargs.pop("rrule")
                return await func(*args, **kwargs)
            raise ExportError(f"{self.entity_id}: {err}") from err
        except HomeAssistantError as err:
            raise ExportError(f"{self.entity_id}: {err}") from err


# ---------------------------------------------------------------------------
# engine
# ---------------------------------------------------------------------------
class ExportManager:
    """Run the export loop and resolve conflicts."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        coordinator: TimeTreeCoordinator,
        store: TimeTreeSyncStore,
    ) -> None:
        """Initialize the manager."""
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self.store = store
        self.last_report: ExportReport | None = None
        self.last_error: str | None = None
        self._unsub: Any = None
        self._running = False

    @property
    def options(self) -> TimeTreeOptions:
        """Return the current options."""
        return self.coordinator.options

    @property
    def enabled(self) -> bool:
        """Return True when the export loop is configured."""
        return bool(self.options.export_enabled and self.options.export_target)

    # -- lifecycle ---------------------------------------------------------
    @callback
    def async_start(self) -> None:
        """Start the export loop (and one initial run)."""
        self.async_stop()
        if not self.enabled:
            return
        interval = timedelta(minutes=max(1, self.options.export_interval))
        self._unsub = async_track_time_interval(
            self.hass, self._async_tick, interval, name=f"{DOMAIN} export loop"
        )
        self.hass.async_create_background_task(
            self._async_startup_run(),
            f"{DOMAIN} initial export",
        )
        _LOGGER.debug("Export loop started (every %s minutes)", interval)

    @callback
    def async_stop(self) -> None:
        """Stop the export loop."""
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    async def _async_startup_run(self) -> None:
        """Run the first export after startup."""
        await asyncio_sleep(STARTUP_DELAY.total_seconds())
        try:
            await self.async_run(reason="startup")
        except Exception as err:  # noqa: BLE001 - never break startup
            _LOGGER.debug("Initial export failed: %s", err)

    async def _async_tick(self, _now: datetime) -> None:
        """Timer callback."""
        try:
            await self.async_run(reason="scheduled")
        except Exception as err:  # noqa: BLE001 - keep the loop alive
            _LOGGER.error("Scheduled export failed: %s", err)

    # -- main run ----------------------------------------------------------
    async def async_run(
        self,
        *,
        reason: str = "manual",
        dry_run: bool | None = None,
    ) -> ExportReport:
        """Export/sync once."""
        options = self.options
        report = ExportReport(
            reason=reason,
            target=options.export_target,
            dry_run=options.export_dry_run if dry_run is None else dry_run,
        )
        if self._running:
            report.errors.append("an export run is already in progress")
            return report

        self._running = True
        touched: set[str] = set()
        try:
            if not options.export_target:
                raise ExportError("no export target is configured")
            target_entity = async_get_calendar_entity(self.hass, options.export_target)
            if target_entity is None:
                raise ExportError(
                    f"calendar entity {options.export_target} was not found"
                )
            target = CalendarTarget(self.hass, options.export_target, target_entity)
            if not target.supports(CalendarEntityFeature.CREATE_EVENT):
                raise ExportError(
                    f"{target.entity_id} does not support creating events"
                )

            now = dt_util.now()
            window_start = now - timedelta(days=options.export_past_days)
            window_end = now + timedelta(days=options.export_future_days)

            target_events = await target.async_get_events(window_start, window_end)
            target_by_marker, unmanaged = self._index_target_events(target_events, report)

            await self._async_process(
                target, window_start, window_end, target_by_marker, unmanaged, report, touched
            )
        except (ExportError, TimeTreeError, HomeAssistantError) as err:
            report.errors.append(str(err))
            self.last_error = str(err)
            _LOGGER.warning("TimeTree export failed: %s", err)
        except Exception as err:  # noqa: BLE001 - report instead of crashing
            report.errors.append(f"unexpected error: {err}")
            self.last_error = str(err)
            _LOGGER.exception("Unexpected error during TimeTree export")
        else:
            self.last_error = None
            if touched and not report.dry_run:
                await self._async_rebaseline(touched, window_start, window_end)
            self.store.set_meta("last_export", dt_util.utcnow().isoformat())
            self.store.set_meta("last_export_target", options.export_target)
            self.store.async_save()
        finally:
            report.finished = dt_util.utcnow()
            self._running = False
            self.last_report = report
            if not report.dry_run:
                self.store.set_meta("last_report", report.as_dict())
                self.store.async_save()
            self._notify_store()

        self._fire_export_event(report)
        return report

    def _notify_store(self) -> None:
        """Tell the entities that the sync store changed."""
        async_dispatcher_send(self.hass, f"{SIGNAL_STORE_UPDATED}_{self.entry.entry_id}")

    def _index_target_events(
        self, target_events: Iterable[CalendarEvent], report: ExportReport
    ) -> tuple[dict[str, CalendarEvent], list[CalendarEvent]]:
        """Split target events into managed (marker) and unmanaged ones."""
        by_marker: dict[str, CalendarEvent] = {}
        unmanaged: list[CalendarEvent] = []
        for event in target_events:
            marker = find_marker(event.description)
            if not marker:
                unmanaged.append(event)
                continue
            if marker in by_marker:
                continue
            by_marker[marker] = event
        return by_marker, unmanaged

    async def _async_process(
        self,
        target: CalendarTarget,
        window_start: datetime,
        window_end: datetime,
        target_by_marker: dict[str, CalendarEvent],
        unmanaged: list[CalendarEvent],
        report: ExportReport,
        touched: set[str],
    ) -> None:
        """Compare both sides and apply every decision."""
        data = self.coordinator.data
        options = self.options
        source_by_uuid: dict[str, tuple[str, TimeTreeEvent]] = {}
        if data is not None:
            for calendar_id, event in data.all_events():
                source_by_uuid[event.uuid] = (calendar_id, event)

        for uuid in sorted(set(source_by_uuid) | set(target_by_marker)):
            calendar_id, source_event = source_by_uuid.get(uuid, ("", None))  # type: ignore[assignment]
            target_event = target_by_marker.get(uuid)
            record = self.store.get_record(uuid)

            if record is not None and record.target_entity and record.target_entity != target.entity_id:
                record = None

            if source_event is not None and not self._in_window(
                source_event, window_start, window_end
            ):
                # Outside the export window: nothing to create, and an existing
                # copy must not be mistaken for a deletion.
                report.skipped += 1
                continue
            if source_event is not None and not self._matches_export_attendees(source_event):
                if record is not None and record.exported:
                    # Previously exported, but attendee unselected: treat as removed
                    source_event = None
                else:
                    report.skipped += 1
                    continue
            if source_event is None and record is None and target_event is not None:
                continue

            source_state = (
                self._source_state(source_event) if source_event is not None else None
            )
            target_state = (
                EventState.from_calendar_event(target_event)
                if target_event is not None
                else None
            )
            if (
                source_event is not None
                and source_event.is_recurring
                and target_state is not None
                and source_state is not None
                and target_state.summary == source_state.summary
                and target_state.description == source_state.description
                and target_state.location == source_state.location
            ):
                target_state = EventState(
                    summary=source_state.summary,
                    description=source_state.description,
                    location=source_state.location,
                    start=source_state.start,
                    end=source_state.end,
                    all_day=source_state.all_day,
                    rrule=source_state.rrule,
                )
            if source_state is None and target_state is None:
                self.store.drop_record(uuid)
                continue

            decision = decide(
                source=source_state,
                target=target_state,
                record=record,
                policy=options.conflict_policy,
                two_way=options.two_way,
                delete_removed=options.export_delete_removed,
                recreate_removed=options.export_recreate_removed,
            )
            if decision.is_conflict:
                self._raise_conflict(
                    uuid=uuid,
                    calendar_id=calendar_id or (record.calendar_id if record else ""),
                    decision=decision,
                    source_state=source_state,
                    target_state=target_state,
                    target_event=target_event,
                    baseline=record,
                )
                report.conflicts += 1
                continue

            try:
                applied = await self._async_apply(
                    decision=decision,
                    uuid=uuid,
                    calendar_id=calendar_id or (record.calendar_id if record else ""),
                    target=target,
                    source_event=source_event,
                    source_state=source_state,
                    target_state=target_state,
                    target_event=target_event,
                    record=record,
                    report=report,
                )
            except (ExportError, HomeAssistantError, TimeTreeError) as err:
                # one broken event must not abort the whole run
                _LOGGER.warning("Could not synchronise '%s': %s", source_state or uuid, err)
                report.errors.append(f"{source_state.summary if source_state else uuid}: {err}")
                continue
            if applied:
                touched.add(uuid)
        if options.import_unmanaged and options.two_way:
            for event in unmanaged:
                state = EventState.from_calendar_event(event)
                key = f"target:{event.uid or state.fingerprint()}"
                decision = decide(
                    source=None,
                    target=state,
                    record=None,
                    policy=options.conflict_policy,
                    two_way=True,
                    delete_removed=options.export_delete_removed,
                    recreate_removed=options.export_recreate_removed,
                )
                if decision.action is not Action.CREATE_SOURCE:
                    continue
                await self._async_import_unmanaged(target, key, state, event, report)
            return

        if unmanaged and not options.two_way:
            report.skipped += len(unmanaged)

    def _in_window(
        self, event: TimeTreeEvent, window_start: datetime, window_end: datetime
    ) -> bool:
        """Return True when an event is relevant for the export window."""
        if event.is_recurring:
            return bool(expand_event(event, window_start, window_end))
        if event.all_day:
            return (
                event.start_date < window_end.date()
                and event.end_date > window_start.date()
            )
        return event.start_datetime < window_end and event.end_datetime > window_start

    def _matches_export_attendees(self, event: TimeTreeEvent) -> bool:
        """Return True when an event matches the attendee export filter."""
        selected = self.options.export_attendees
        if not selected:
            return True
        if not event.attendee_ids and not event.attendee_names:
            return self.options.export_include_untagged
        selected_set = {str(item).strip().lower() for item in selected}
        event_ids = {str(uid).strip().lower() for uid in event.attendee_ids}
        event_names = {str(name).strip().lower() for name in event.attendee_names}
        return bool((event_ids | event_names) & selected_set)

    def _source_state(self, event: TimeTreeEvent) -> EventState:
        """Return the comparable state of a TimeTree event."""
        return EventState.from_timetree(
            event,
            description=describe_event(
                event, include_details=self.options.description_details
            ),
        )

    # -- applying decisions ------------------------------------------------
    async def _async_apply(
        self,
        *,
        decision: Decision,
        uuid: str,
        calendar_id: str,
        target: CalendarTarget,
        source_event: TimeTreeEvent | None,
        source_state: EventState | None,
        target_state: EventState | None,
        target_event: CalendarEvent | None,
        record: SyncRecord | None,
        report: ExportReport,
    ) -> bool:
        """Apply one decision, returning True when something changed."""
        action = decision.action
        target_uid = (target_event.uid if target_event else None) or (
            record.target_uid if record else None
        )

        if action is Action.NONE:
            report.unchanged += 1
            if record is not None and source_state is not None:
                self._refresh_record(
                    record,
                    source_state,
                    target_state if target_state is not None else source_state,
                    target_uid,
                    target.entity_id,
                )
                self.store.set_record(record)
            return False

        if action is Action.IGNORE:
            report.skipped += 1
            return False

        if action is Action.CREATE_TARGET:
            if source_event is None:
                report.skipped += 1
                return False
            write = TargetWrite.from_timetree(
                source_event,
                marker_uuid=uuid,
                details=self.options.description_details,
            )
            if report.dry_run:
                report.created += 1
                return False
            await target.async_create(write)
            report.created += 1
            self.store.set_record(
                SyncRecord(
                    uuid=uuid,
                    calendar_id=calendar_id,
                    source_fingerprint=source_state.fingerprint() if source_state else None,
                    target_fingerprint=source_state.fingerprint() if source_state else None,
                    target_entity=target.entity_id,
                    last_sync=dt_util.utcnow().isoformat(timespec="seconds"),
                )
            )
            return True

        if action is Action.UPDATE_TARGET:
            write = (
                TargetWrite.from_timetree(
                    source_event,
                    marker_uuid=uuid,
                    details=self.options.description_details,
                )
                if source_event is not None
                else TargetWrite.from_state(source_state, marker_uuid=uuid)
                if source_state is not None
                else None
            )
            if write is None:
                report.skipped += 1
                return False
            if report.dry_run:
                report.updated += 1
                return False
            await target.async_update(target_uid, write)
            report.updated += 1
            self._store_synced(uuid, calendar_id, source_state, target, target_uid)
            return True

        if action is Action.DELETE_TARGET:
            if target_uid is None:
                report.skipped += 1
                return False
            if report.dry_run:
                report.deleted += 1
                return False
            await target.async_delete(target_uid)
            report.deleted += 1
            if record is not None:
                record.target_uid = None
                record.target_fingerprint = None
                self.store.set_record(record)
            return True

        if action in (Action.UPDATE_SOURCE, Action.CREATE_SOURCE):
            state = target_state
            if state is None:
                report.skipped += 1
                return False
            if report.dry_run:
                if action is Action.UPDATE_SOURCE:
                    report.updated += 1
                else:
                    report.imported += 1
                return False
            if action is Action.UPDATE_SOURCE:
                if source_event is None:
                    report.skipped += 1
                    return False
                await self._async_write_source(calendar_id, uuid, state)
                report.updated += 1
                self._store_synced(uuid, calendar_id, target_state, target, target_uid)
                return True
            await self._async_create_source(uuid, state, target, target_uid, report)
            return True

        if action is Action.DELETE_SOURCE:
            if report.dry_run:
                report.deleted += 1
                return False
            await self.coordinator.async_delete_event(calendar_id, uuid)
            report.deleted += 1
            self.store.drop_record(uuid)
            return True

        report.skipped += 1
        return False

    def _refresh_record(
        self,
        record: SyncRecord,
        source_state: EventState,
        target_state: EventState,
        target_uid: str | None,
        target_entity: str,
    ) -> None:
        """Update the stored fingerprints of an in-sync event."""
        record.source_fingerprint = source_state.fingerprint()
        record.target_fingerprint = target_state.fingerprint()
        record.target_uid = target_uid or record.target_uid
        record.target_entity = target_entity
        record.ignored = False
        record.ignored_fingerprint = None

    def _store_synced(
        self,
        uuid: str,
        calendar_id: str,
        state: EventState | None,
        target: CalendarTarget,
        target_uid: str | None = None,
    ) -> None:
        """Store a record for a state that both sides are expected to have now."""
        fingerprint = state.fingerprint() if state is not None else None
        self.store.set_record(
            SyncRecord(
                uuid=uuid,
                calendar_id=calendar_id,
                source_fingerprint=fingerprint,
                target_fingerprint=fingerprint,
                target_uid=target_uid,
                target_entity=target.entity_id,
                last_sync=dt_util.utcnow().isoformat(timespec="seconds"),
            )
        )

    async def _async_write_source(
        self, calendar_id: str, uuid: str, state: EventState
    ) -> None:
        """Write the target state back into TimeTree."""
        payload = self._payload_from_state(state)
        await self.coordinator.async_update_event(calendar_id, uuid, payload)

    async def _async_create_source(
        self,
        uuid: str,
        state: EventState,
        target: CalendarTarget,
        target_uid: str | None,
        report: ExportReport,
    ) -> None:
        """Create a TimeTree event from a target event."""
        calendar_ids = self.coordinator.calendar_ids
        if not calendar_ids:
            raise ExportError("no TimeTree calendar is configured")
        calendar_id = calendar_ids[0]
        payload = self._payload_from_state(state)
        payload["uuid"] = uuid
        result = await self.coordinator.async_create_event(calendar_id, payload)
        created_uuid = extract_created_uuid(result) or uuid
        report.imported += 1
        self.store.set_record(
            SyncRecord(
                uuid=created_uuid,
                calendar_id=calendar_id,
                source_fingerprint=state.fingerprint(),
                target_fingerprint=state.fingerprint(),
                target_uid=target_uid,
                target_entity=target.entity_id,
                last_sync=dt_util.utcnow().isoformat(timespec="seconds"),
            )
        )
        if created_uuid != uuid and target_uid:
            # TimeTree assigned its own uuid: re-tag the exported copy so the
            # pair stays linked on the next run.
            await self._async_retag(target, target_uid, state, created_uuid)

    async def _async_import_unmanaged(
        self,
        target: CalendarTarget,
        key: str,
        state: EventState,
        event: CalendarEvent,
        report: ExportReport,
    ) -> None:
        """Import an unmanaged target event into TimeTree."""
        if report.dry_run:
            report.imported += 1
            return
        calendar_ids = self.coordinator.calendar_ids
        if not calendar_ids:
            report.skipped += 1
            return
        new_uuid = uuid_lib.uuid4().hex
        payload = self._payload_from_state(state)
        payload["uuid"] = new_uuid
        result = await self.coordinator.async_create_event(calendar_ids[0], payload)
        created_uuid = extract_created_uuid(result) or new_uuid
        report.imported += 1
        self.store.set_record(
            SyncRecord(
                uuid=created_uuid,
                calendar_id=calendar_ids[0],
                source_fingerprint=None,
                target_fingerprint=state.fingerprint(),
                target_uid=event.uid,
                target_entity=target.entity_id,
                last_sync=dt_util.utcnow().isoformat(timespec="seconds"),
            )
        )
        if event.uid:
            await self._async_retag(target, event.uid, state, created_uuid)

    async def _async_retag(
        self,
        target: CalendarTarget,
        target_uid: str,
        state: EventState,
        new_uuid: str,
    ) -> None:
        """Re-write an exported copy so it points at the TimeTree uuid."""
        write = TargetWrite.from_state(state, marker_uuid=new_uuid)
        try:
            await target.async_update(target_uid, write)
        except (ExportError, HomeAssistantError) as err:
            _LOGGER.warning("Could not re-tag the exported copy: %s", err)

    def _payload_from_state(self, state: EventState) -> dict[str, Any]:
        """Convert a stored state into a TimeTree write payload."""
        start = parse_iso_value(state.start, state.all_day)
        end = parse_iso_value(state.end, state.all_day)
        recurrences = [f"RRULE:{state.rrule}"] if state.rrule else []
        return build_event_payload(
            title=state.summary,
            note=state.description,
            location=state.location,
            start=start,
            end=end,
            all_day=state.all_day,
            recurrences=recurrences,
        )

    # -- conflicts ---------------------------------------------------------
    def _raise_conflict(
        self,
        *,
        uuid: str,
        calendar_id: str,
        decision: Decision,
        source_state: EventState | None,
        target_state: EventState | None,
        target_event: CalendarEvent | None,
        baseline: SyncRecord | None,
    ) -> None:
        """Queue a conflict for a manual decision."""
        conflict = Conflict(
            conflict_id=make_conflict_id(uuid, decision.kind),
            uuid=uuid,
            calendar_id=calendar_id,
            kind=decision.kind,
            summary=(source_state or target_state).summary if (source_state or target_state) else uuid,
            message=decision.reason,
            source=source_state.as_dict() if source_state else None,
            target=target_state.as_dict() if target_state else None,
            baseline=baseline.to_dict() if baseline else None,
            target_uid=target_event.uid if target_event else None,
            target_entity=self.options.export_target,
        )
        is_new = self.store.upsert_conflict(conflict)
        self.store.async_save()
        self._notify_store()
        if not is_new:
            return
        _LOGGER.warning(
            "TimeTree conflict (%s) for '%s': %s",
            conflict.kind,
            conflict.summary,
            conflict.message,
        )
        self.hass.bus.async_fire(
            EVENT_CONFLICT_DETECTED,
            {
                "conflict_id": conflict.conflict_id,
                "kind": conflict.kind,
                "summary": conflict.summary,
                "calendar_id": conflict.calendar_id,
                "timetree_uuid": conflict.uuid,
                "message": conflict.message,
            },
        )
        self.hass.async_create_background_task(
            self._async_notify_conflict(conflict),
            f"{DOMAIN} conflict notification",
        )

    async def _async_notify_conflict(self, conflict: Conflict) -> None:
        """Send a persistent notification for a new conflict."""
        if not self.options.notify_conflicts:
            return
        total = len(self.store.conflicts)
        message = (
            f"**{conflict.summary}**\n\n"
            f"{conflict.message}\n\n"
            f"- conflict id: `{conflict.conflict_id}`\n"
            f"- kind: `{conflict.kind}`\n"
            f"- pending conflicts: {total}\n\n"
            "Resolve it with the `timetree.resolve_conflict` service or ignore it "
            "with `timetree.resolve_all_conflicts`."
        )
        try:
            await self.hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "title": "TimeTree sync conflict",
                    "message": message,
                    "notification_id": NOTIFICATION_ID,
                },
                blocking=False,
            )
        except Exception as err:  # noqa: BLE001 - notifications are optional
            _LOGGER.debug("Could not create conflict notification: %s", err)

    async def async_resolve_conflict(
        self, conflict_id: str, resolution: str, *, dry_run: bool = False
    ) -> bool:
        """Resolve (or dismiss) a pending conflict."""
        conflict = self.store.conflicts.get(conflict_id)
        if conflict is None:
            raise ExportError(f"unknown conflict id {conflict_id}")

        if resolution == RESOLUTION_IGNORE:
            fingerprint = None
            if conflict.source:
                fingerprint = EventState.from_dict(conflict.source).fingerprint()
            elif conflict.target:
                fingerprint = EventState.from_dict(conflict.target).fingerprint()
            if self.store.get_record(conflict.uuid) is None:
                self.store.set_record(
                    SyncRecord(
                        uuid=conflict.uuid,
                        calendar_id=conflict.calendar_id,
                        target_uid=conflict.target_uid,
                    )
                )
            self.store.mark_ignored(conflict.uuid, fingerprint)
            self.store.pop_conflict(conflict_id)
            await self.store.async_save_now()
            self._notify_store()
            return True

        action = resolution_to_action(resolution, conflict)
        if action is None:
            raise ExportError(f"unsupported resolution {resolution}")

        if resolution == RESOLUTION_SKIP:
            self._rebaseline_from_conflict(conflict)
            self.store.pop_conflict(conflict_id)
            await self.store.async_save_now()
            self._notify_store()
            return True

        if action is Action.NONE:
            self.store.pop_conflict(conflict_id)
            await self.store.async_save_now()
            self._notify_store()
            return True

        target = self._async_target()
        source_event = self._find_source(conflict.uuid)
        source_state = EventState.from_dict(conflict.source)
        target_state = EventState.from_dict(conflict.target)
        if source_state is None and source_event is not None:
            source_state = self._source_state(source_event)

        decision = Decision(action=action, kind=conflict.kind, reason="manual resolution")
        record = self.store.get_record(conflict.uuid)
        report = ExportReport(reason="resolve", target=target.entity_id, dry_run=dry_run)
        await self._async_apply(
            decision=decision,
            uuid=conflict.uuid,
            calendar_id=conflict.calendar_id or (record.calendar_id if record else ""),
            target=target,
            source_event=source_event,
            source_state=source_state,
            target_state=target_state,
            target_event=None,
            record=record,
            report=report,
        )
        if not report.errors:
            self.store.pop_conflict(conflict_id)
            await self.store.async_save_now()
        self._notify_store()
        return not report.errors

    async def async_resolve_all(
        self, resolution: str, *, dry_run: bool = False
    ) -> tuple[int, list[str]]:
        """Resolve every pending conflict."""
        resolved = 0
        errors: list[str] = []
        for conflict_id in list(self.store.conflicts):
            try:
                await self.async_resolve_conflict(
                    conflict_id, resolution, dry_run=dry_run
                )
                resolved += 1
            except (ExportError, HomeAssistantError, TimeTreeError) as err:
                errors.append(f"{conflict_id}: {err}")
        return resolved, errors

    def _rebaseline_from_conflict(self, conflict: Conflict) -> None:
        """Accept the current divergence as the new baseline."""
        record = self.store.get_record(conflict.uuid)
        if record is None:
            record = SyncRecord(uuid=conflict.uuid, calendar_id=conflict.calendar_id)
        source = EventState.from_dict(conflict.source)
        target = EventState.from_dict(conflict.target)
        if source is not None:
            record.source_fingerprint = source.fingerprint()
        if target is not None:
            record.target_fingerprint = target.fingerprint()
        record.target_uid = conflict.target_uid or record.target_uid
        record.target_entity = conflict.target_entity or record.target_entity
        record.last_sync = dt_util.utcnow().isoformat(timespec="seconds")
        self.store.set_record(record)

    def _async_target(self) -> CalendarTarget:
        """Return the configured export target."""
        entity_id = self.options.export_target
        if not entity_id:
            raise ExportError("no export target is configured")
        entity = async_get_calendar_entity(self.hass, entity_id)
        if entity is None:
            raise ExportError(f"calendar entity {entity_id} was not found")
        return CalendarTarget(self.hass, entity_id, entity)

    def _find_source(self, uuid: str) -> TimeTreeEvent | None:
        """Return the current TimeTree event for a uuid."""
        data = self.coordinator.data
        if data is None:
            return None
        found = data.find_event(uuid)
        return found[1] if found else None

    # -- re-baseline -------------------------------------------------------
    async def _async_rebaseline(
        self,
        touched: set[str],
        window_start: datetime,
        window_end: datetime,
    ) -> None:
        """Refresh the stored fingerprints after writes.

        The target calendar may normalise what we wrote (line breaks, extra
        properties), and TimeTree may adjust timestamps, so the stored
        fingerprints are re-read instead of assumed. Without this the next run
        would report a conflict for every event we just wrote.
        """
        try:
            await self.coordinator.async_refresh()
        except Exception as err:  # noqa: BLE001 - baseline is best effort
            _LOGGER.debug("Could not refresh TimeTree after export: %s", err)
        try:
            target = self._async_target()
            events = await target.async_get_events(window_start, window_end)
        except (ExportError, HomeAssistantError) as err:
            _LOGGER.debug("Could not refresh the export target: %s", err)
            return

        target_by_marker = {find_marker(e.description): e for e in events}
        data = self.coordinator.data
        for uuid in touched:
            record = self.store.get_record(uuid)
            if record is None:
                continue
            if data is not None and (found := data.find_event(uuid)) is not None:
                record.source_fingerprint = self._source_state(found[1]).fingerprint()
            target_event = target_by_marker.get(uuid)
            if target_event is not None:
                record.target_uid = target_event.uid or record.target_uid
                record.target_fingerprint = EventState.from_calendar_event(
                    target_event
                ).fingerprint()
        self.store.async_save()

    # -- helpers -----------------------------------------------------------
    def _fire_export_event(self, report: ExportReport) -> None:
        """Fire an event so automations can react to the run."""
        self.hass.bus.async_fire(
            EVENT_EXPORT_COMPLETED,
            {
                "reason": report.reason,
                "target": report.target,
                "dry_run": report.dry_run,
                **{
                    key: value
                    for key, value in report.as_dict().items()
                    if key not in ("reason", "target", "dry_run")
                },
            },
        )


def extract_created_uuid(result: Mapping[str, Any] | None) -> str | None:
    """Return the uuid of an event from a write response."""
    if not isinstance(result, Mapping):
        return None
    for key in ("event", "data"):
        nested = result.get(key)
        if isinstance(nested, Mapping):
            uuid = nested.get("uuid")
            if uuid:
                return str(uuid)
    uuid = result.get("uuid")
    return str(uuid) if uuid else None


async def asyncio_sleep(seconds: float) -> None:
    """Sleep without importing asyncio at module level."""
    import asyncio  # noqa: PLC0415

    await asyncio.sleep(seconds)
