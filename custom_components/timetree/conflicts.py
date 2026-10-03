"""Conflict detection and resolution logic.

The integration keeps a snapshot of the state it last synchronised for every
event (``SyncRecord``). Comparing that snapshot with the *current* state of the
TimeTree event and of the exported copy tells us which side changed. When both
sides changed, or when one side removed an event the other side edited, the
change cannot be applied automatically without losing data, so it is queued as
a conflict for a manual decision.

This module is free of Home Assistant imports so the decision table can be unit
tested.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any
from collections.abc import Mapping
from enum import StrEnum

from .const import MARKER_PREFIX, MARKER_SUFFIX
from .models import TimeTreeEvent

UTC = timezone.utc
SEPARATOR = "\n\n"


class Action(StrEnum):
    """Action to apply for one event pair."""

    NONE = "none"
    CREATE_TARGET = "create_target"
    UPDATE_TARGET = "update_target"
    CREATE_SOURCE = "create_source"
    UPDATE_SOURCE = "update_source"
    DELETE_TARGET = "delete_target"
    DELETE_SOURCE = "delete_source"
    RECREATE_TARGET = "recreate_target"
    CONFLICT = "conflict"
    IGNORE = "ignore"


class ConflictKind(StrEnum):
    """Reason why a conflict was raised."""

    BOTH_CHANGED = "both_changed"
    TARGET_CHANGED = "target_changed"
    TARGET_REMOVED = "target_removed"
    SOURCE_REMOVED = "source_removed"
    TARGET_ONLY = "target_only"
    UNSUPPORTED_TARGET = "unsupported_target"
    WRITE_FAILED = "write_failed"


def _now_iso() -> str:
    """Return the current time as ISO string."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def export_marker(uuid: str) -> str:
    """Return the marker line used to tag exported copies."""
    return f"{MARKER_PREFIX}{uuid}{MARKER_SUFFIX}"


def add_marker(text: str | None, uuid: str) -> str:
    """Append the TimeTree marker to a description."""
    marker = export_marker(uuid)
    body = (text or "").replace(marker, "").strip()
    if not body:
        return marker
    return f"{body}{SEPARATOR}{marker}"


def find_marker(text: str | None) -> str | None:
    """Return the TimeTree uuid stored in a description, if any."""
    if not text:
        return None
    start = text.find(MARKER_PREFIX)
    if start == -1:
        return None
    end = text.find(MARKER_SUFFIX, start + len(MARKER_PREFIX))
    if end == -1:
        return None
    return text[start + len(MARKER_PREFIX) : end].strip() or None


def strip_marker(text: str | None) -> str:
    """Remove the marker from a description."""
    if not text:
        return ""
    return text.replace(export_marker(find_marker(text) or ""), "").strip()


@dataclass(slots=True)
class EventState:
    """Comparable projection of an event (source or target side)."""

    summary: str
    description: str
    location: str
    start: str
    end: str
    all_day: bool
    rrule: str | None = None
    updated: int | None = None

    def fingerprint(self) -> str:
        """Return a stable hash of the synchronised fields."""
        payload = "|".join(
            [
                self.summary,
                self.description,
                self.location,
                self.start,
                self.end,
                str(self.all_day),
                self.rrule or "",
            ]
        )
        return sha256(payload.encode("utf-8")).hexdigest()[:32]

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON friendly representation."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> EventState | None:
        """Create a state from a stored dict."""
        if not data:
            return None
        return cls(
            summary=str(data.get("summary") or ""),
            description=str(data.get("description") or ""),
            location=str(data.get("location") or ""),
            start=str(data.get("start") or ""),
            end=str(data.get("end") or ""),
            all_day=bool(data.get("all_day")),
            rrule=data.get("rrule"),
            updated=data.get("updated"),
        )

    def diff(self, other: EventState | None) -> dict[str, list[str]]:
        """Return the changed fields compared to ``other``."""
        if other is None:
            return {}
        changes: dict[str, list[str]] = {}
        for name in ("summary", "description", "location", "start", "end"):
            if getattr(self, name) != getattr(other, name):
                changes[name] = [getattr(other, name), getattr(self, name)]
        return changes

    @classmethod
    def from_timetree(
        cls,
        event: TimeTreeEvent,
        *,
        description: str | None = None,
    ) -> EventState:
        """Create a state from a TimeTree event.

        ``description`` must be the exact text the exported copy will carry
        (without the marker) so that both fingerprints stay comparable.
        """
        return cls(
            summary=event.title,
            description=(event.note or "") if description is None else description,
            location=event.location or "",
            start=_iso(event.ha_start),
            end=_iso(event.ha_end),
            all_day=event.all_day,
            rrule=_bare_rrule(event.rrule_line),
            updated=event.updated_ms or None,
        )

    @classmethod
    def from_calendar_event(cls, event: Any) -> EventState:
        """Create a state from a Home Assistant ``CalendarEvent``."""
        marker_free = strip_marker(getattr(event, "description", None))
        return cls(
            summary=getattr(event, "summary", "") or "",
            description=marker_free,
            location=getattr(event, "location", None) or "",
            start=_iso(getattr(event, "start", None)),
            end=_iso(getattr(event, "end", None)),
            all_day=bool(getattr(event, "all_day", False)),
            rrule=getattr(event, "rrule", None),
        )


def _iso(value: Any) -> str:
    """Return an ISO formatted value."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _bare_rrule(rrule_line: str | None) -> str | None:
    """Return an RRULE without the ``RRULE:`` prefix."""
    if not rrule_line:
        return None
    _, _, value = rrule_line.partition(":")
    return value or None


@dataclass(slots=True)
class SyncRecord:
    """Last synchronised state of one event pair."""

    uuid: str
    calendar_id: str
    source_fingerprint: str | None = None
    target_fingerprint: str | None = None
    target_uid: str | None = None
    target_entity: str | None = None
    last_sync: str | None = None
    ignored: bool = False
    ignored_fingerprint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON friendly representation."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SyncRecord:
        """Create a record from a stored dict."""
        return cls(
            uuid=str(data.get("uuid") or ""),
            calendar_id=str(data.get("calendar_id") or ""),
            source_fingerprint=data.get("source_fingerprint"),
            target_fingerprint=data.get("target_fingerprint"),
            target_uid=data.get("target_uid"),
            target_entity=data.get("target_entity"),
            last_sync=data.get("last_sync"),
            ignored=bool(data.get("ignored")),
            ignored_fingerprint=data.get("ignored_fingerprint"),
        )

    @property
    def exported(self) -> bool:
        """Return True when a copy was exported to the target."""
        return self.target_uid is not None


@dataclass(slots=True)
class Conflict:
    """A change that needs a manual decision."""

    conflict_id: str
    uuid: str
    calendar_id: str
    kind: str
    summary: str
    message: str
    detected_at: str = field(default_factory=_now_iso)
    source: dict[str, Any] | None = None
    target: dict[str, Any] | None = None
    baseline: dict[str, Any] | None = None
    target_uid: str | None = None
    target_entity: str | None = None
    target_time: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON friendly representation."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Conflict:
        """Create a conflict from a stored dict."""
        return cls(
            conflict_id=str(data.get("conflict_id") or ""),
            uuid=str(data.get("uuid") or ""),
            calendar_id=str(data.get("calendar_id") or ""),
            kind=str(data.get("kind") or ConflictKind.BOTH_CHANGED),
            summary=str(data.get("summary") or ""),
            message=str(data.get("message") or ""),
            detected_at=str(data.get("detected_at") or _now_iso()),
            source=data.get("source"),
            target=data.get("target"),
            baseline=data.get("baseline"),
            target_uid=data.get("target_uid"),
            target_entity=data.get("target_entity"),
            target_time=data.get("target_time"),
        )

    def as_attribute(self) -> dict[str, Any]:
        """Return a compact representation for entity attributes."""
        return {
            "conflict_id": self.conflict_id,
            "kind": self.kind,
            "summary": self.summary,
            "calendar_id": self.calendar_id,
            "timetree_uuid": self.uuid,
            "detected_at": self.detected_at,
            "message": self.message,
            "source": self.source,
            "target": self.target,
            "target_uid": self.target_uid,
        }


def make_conflict_id(uuid: str, kind: str, fingerprint: str = "") -> str:
    """Return a stable id for a conflict."""
    raw = f"{uuid}:{kind}:{fingerprint}"
    return sha256(raw.encode("utf-8")).hexdigest()[:12]


@dataclass(slots=True)
class Decision:
    """Outcome of the decision table."""

    action: Action
    kind: str = ""
    manual: bool = False
    reason: str = ""

    @property
    def is_conflict(self) -> bool:
        """Return True when the decision must be queued for a human."""
        return self.manual or self.action is Action.CONFLICT


def decide(
    *,
    source: EventState | None,
    target: EventState | None,
    record: SyncRecord | None,
    policy: str,
    two_way: bool,
    delete_removed: bool,
    recreate_removed: bool,
) -> Decision:
    """Return what should happen for one event pair.

    ``source`` is the TimeTree event, ``target`` the exported copy. ``None``
    means "does not exist on that side".
    """
    source_fp = source.fingerprint() if source else None
    target_fp = target.fingerprint() if target else None

    if (
        record is not None
        and record.ignored
        and record.ignored_fingerprint is not None
        and record.ignored_fingerprint in (source_fp, target_fp)
    ):
        return Decision(Action.IGNORE, reason="event is marked as ignored")

    # ---- nothing on either side --------------------------------------
    if source is None and target is None:
        return Decision(Action.NONE)

    # ---- only the exported copy exists ------------------------------
    if source is None:
        if record is None or not record.exported:
            if two_way:
                return Decision(Action.CREATE_SOURCE, kind=ConflictKind.TARGET_ONLY)
            return Decision(Action.NONE, reason="event is not managed by TimeTree")
        return Decision(
            Action.CONFLICT,
            kind=ConflictKind.SOURCE_REMOVED,
            manual=True,
            reason="the TimeTree event was deleted while the exported copy changed",
        )

    # ---- only the TimeTree event exists -----------------------------
    if target is None:
        if record is not None and record.exported:
            if recreate_removed:
                return Decision(
                    Action.CREATE_TARGET,
                    kind=ConflictKind.TARGET_REMOVED,
                    reason="the exported copy was removed, recreating it",
                )
            return Decision(
                Action.CONFLICT,
                kind=ConflictKind.TARGET_REMOVED,
                manual=True,
                reason="the exported copy was deleted or moved out of the window",
            )
        return Decision(Action.CREATE_TARGET, reason="event is not exported yet")

    # ---- both sides exist -------------------------------------------
    source_changed = record is None or record.source_fingerprint != source_fp
    target_changed = (
        record is None or record.target_fingerprint != target_fp
    )

    if source_fp == target_fp or not source_changed and not target_changed:
        return Decision(Action.NONE, reason="both sides are in sync")

    if not source_changed and target_changed:
        if two_way:
            return Decision(
                Action.UPDATE_SOURCE,
                kind=ConflictKind.TARGET_CHANGED,
                reason="the exported copy was changed",
            )
        if policy in ("source_wins", "newest_wins"):
            return Decision(
                Action.UPDATE_TARGET,
                kind=ConflictKind.TARGET_CHANGED,
                reason="restoring the TimeTree version",
            )
        if policy == "target_wins":
            return Decision(
                Action.UPDATE_SOURCE,
                kind=ConflictKind.TARGET_CHANGED,
                reason="applying the exported version to TimeTree",
            )
        return Decision(
            Action.CONFLICT,
            kind=ConflictKind.TARGET_CHANGED,
            manual=True,
            reason="the exported copy was changed outside Home Assistant",
        )

    if source_changed and not target_changed:
        return Decision(
            Action.UPDATE_TARGET, reason="the TimeTree event was changed"
        )

    # both changed
    if policy == "source_wins":
        return Decision(
            Action.UPDATE_TARGET,
            kind=ConflictKind.BOTH_CHANGED,
            reason="both sides changed, TimeTree wins",
        )
    if policy == "target_wins":
        return Decision(
            Action.UPDATE_SOURCE,
            kind=ConflictKind.BOTH_CHANGED,
            reason="both sides changed, the exported copy wins",
        )
    return Decision(
        Action.CONFLICT,
        kind=ConflictKind.BOTH_CHANGED,
        manual=True,
        reason="both sides changed since the last sync",
    )


def resolution_to_action(
    resolution: str, conflict: Conflict
) -> Action | None:
    """Translate a user resolution into an action."""
    has_source = bool(conflict.source)
    has_target = bool(conflict.target)
    if resolution == "use_timetree":
        if has_source and has_target:
            return Action.UPDATE_TARGET
        if has_source:
            return Action.CREATE_TARGET
        if has_target:
            return Action.DELETE_TARGET
        return Action.NONE
    if resolution == "use_export_target":
        if has_source and has_target:
            return Action.UPDATE_SOURCE
        if has_target:
            return Action.CREATE_SOURCE
        if has_source:
            return Action.DELETE_SOURCE
        return Action.NONE
    if resolution == "use_newest":
        source_updated = (conflict.source or {}).get("updated") or 0
        target_updated = (conflict.target or {}).get("updated") or 0
        if source_updated >= target_updated:
            return resolution_to_action("use_timetree", conflict)
        return resolution_to_action("use_export_target", conflict)
    if resolution == "skip":
        return Action.NONE
    return None
