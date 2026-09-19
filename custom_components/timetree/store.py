"""Persistent sync bookkeeping for the TimeTree integration."""

from __future__ import annotations

import logging
from copy import deepcopy
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .conflicts import Conflict, SyncRecord
from .const import DOMAIN, STORAGE_VERSION

_LOGGER = logging.getLogger(__name__)

SAVE_DELAY = 10


class TimeTreeSyncStore:
    """Store the last synchronised state plus pending conflicts."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Initialize the store."""
        self._hass = hass
        self._store = Store[dict[str, Any]](
            hass, STORAGE_VERSION, f"{DOMAIN}.sync.{entry_id}"
        )
        self._records: dict[str, SyncRecord] = {}
        self._conflicts: dict[str, Conflict] = {}
        self._meta: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # persistence
    # ------------------------------------------------------------------
    async def async_load(self) -> None:
        """Load the stored state."""
        stored = await self._store.async_load() or {}
        self._records = {
            uuid: SyncRecord.from_dict(data)
            for uuid, data in (stored.get("records") or {}).items()
            if isinstance(data, dict)
        }
        self._conflicts = {
            conflict_id: Conflict.from_dict(data)
            for conflict_id, data in (stored.get("conflicts") or {}).items()
            if isinstance(data, dict)
        }
        self._meta = dict(stored.get("meta") or {})
        _LOGGER.debug(
            "Loaded %s sync records and %s conflicts",
            len(self._records),
            len(self._conflicts),
        )

    def _as_dict(self) -> dict[str, Any]:
        """Return the serialisable state."""
        return {
            "records": {uuid: record.to_dict() for uuid, record in self._records.items()},
            "conflicts": {
                conflict_id: conflict.to_dict()
                for conflict_id, conflict in self._conflicts.items()
            },
            "meta": deepcopy(self._meta),
        }

    def async_save(self) -> None:
        """Schedule a debounced save."""
        self._store.async_delay_save(self._as_dict, SAVE_DELAY)

    async def async_save_now(self) -> None:
        """Write the state to disk immediately."""
        await self._store.async_save(self._as_dict())

    # ------------------------------------------------------------------
    # records
    # ------------------------------------------------------------------
    @property
    def records(self) -> dict[str, SyncRecord]:
        """Return the sync records."""
        return self._records

    def get_record(self, uuid: str) -> SyncRecord | None:
        """Return the record for an event."""
        return self._records.get(uuid)

    def set_record(self, record: SyncRecord) -> None:
        """Store a record."""
        self._records[record.uuid] = record

    def drop_record(self, uuid: str) -> None:
        """Remove a record."""
        self._records.pop(uuid, None)

    def records_for(self, calendar_id: str) -> dict[str, SyncRecord]:
        """Return the records of one calendar."""
        return {
            uuid: record
            for uuid, record in self._records.items()
            if record.calendar_id == calendar_id
        }

    def mark_ignored(self, uuid: str, fingerprint: str | None) -> None:
        """Remember that an event should not be synchronised anymore."""
        record = self._records.get(uuid)
        if record is None:
            return
        record.ignored = True
        record.ignored_fingerprint = fingerprint

    # ------------------------------------------------------------------
    # conflicts
    # ------------------------------------------------------------------
    @property
    def conflicts(self) -> dict[str, Conflict]:
        """Return the pending conflicts."""
        return self._conflicts

    def add_conflict(self, conflict: Conflict) -> bool:
        """Store a conflict, returning True when it is new."""
        if conflict.conflict_id in self._conflicts:
            return False
        self._conflicts[conflict.conflict_id] = conflict
        return True

    def upsert_conflict(self, conflict: Conflict) -> bool:
        """Store or refresh a conflict, returning True when it is new."""
        exists = conflict.conflict_id in self._conflicts
        self._conflicts[conflict.conflict_id] = conflict
        return not exists

    def pop_conflict(self, conflict_id: str) -> Conflict | None:
        """Remove and return a conflict."""
        return self._conflicts.pop(conflict_id, None)

    def clear_conflicts(self) -> None:
        """Remove all pending conflicts."""
        self._conflicts.clear()

    def conflicts_for(self, calendar_id: str) -> list[Conflict]:
        """Return the conflicts of one calendar."""
        return [
            conflict
            for conflict in self._conflicts.values()
            if conflict.calendar_id == calendar_id
        ]

    # ------------------------------------------------------------------
    # meta
    # ------------------------------------------------------------------
    def set_meta(self, key: str, value: Any) -> None:
        """Store a metadata value."""
        self._meta[key] = value

    def get_meta(self, key: str, default: Any = None) -> Any:
        """Return a metadata value."""
        return self._meta.get(key, default)
