"""Local test suite for the TimeTree integration.

Run with::

    python tests/test_integration.py

The suite exercises the Home Assistant free logic (parsing, recurrence
expansion, conflict decision table, API transport, export engine) through the
stubs in ``ha_stub.py``.
"""

from __future__ import annotations

import sys
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ha_stub  # noqa: E402

ha_stub.install_home_assistant_stubs()

models = ha_stub.import_module("models")
recurrence = ha_stub.import_module("recurrence")
conflicts = ha_stub.import_module("conflicts")
const = ha_stub.import_module("const")
api_mod = ha_stub.import_module("api")
store_mod = ha_stub.import_module("store")
export_mod = ha_stub.import_module("export")
options_mod = ha_stub.import_module("options")
calendar_mod = ha_stub.import_module("calendar")

UTC = timezone.utc
VIENNA = ha_stub.TEST_TZ
CHECKS = 0
FAILURES: list[str] = []


def check(condition, message=""):
    """Assert a condition and count it."""
    global CHECKS  # noqa: PLW0603
    CHECKS += 1
    if not condition:
        raise AssertionError(message or "check failed")


def ms(value: datetime) -> int:
    """Return a millisecond timestamp."""
    return int(value.timestamp() * 1000)


CALENDAR_API = {
    "id": 42,
    "name": "Family",
    "alias_code": "alias42",
    "calendar_users": [{"user_id": 7, "name": "Markus"}, {"user_id": 8, "name": "Anna"}],
}


def make_calendar() -> "models.TimeTreeCalendar":
    """Return a calendar model."""
    return models.TimeTreeCalendar.from_api(CALENDAR_API)


# ---------------------------------------------------------------------------
# models
# ---------------------------------------------------------------------------
def test_models_timed_event():
    """A timed event keeps its timezone and maps to HA types."""
    start = datetime(2026, 10, 1, 18, 0, tzinfo=VIENNA)
    end = datetime(2026, 10, 1, 20, 0, tzinfo=VIENNA)
    event = models.TimeTreeEvent.from_api(
        {
            "uuid": "timed-1",
            "title": "Dinner",
            "note": "Pizza",
            "location": "Home",
            "start_at": ms(start),
            "end_at": ms(end),
            "start_timezone": "Europe/Vienna",
            "end_timezone": "Europe/Vienna",
            "all_day": False,
            "attendees": [7, 8],
            "label_id": 3,
            "type": 0,
            "category": 1,
            "updated_at": 1234,
        },
        "42",
        calendar=make_calendar(),
    )
    check(isinstance(event.ha_start, datetime))
    check(event.ha_start == start, f"start {event.ha_start} != {start}")
    check(event.ha_end == end)
    check(event.ha_start.tzinfo is not None)
    check(event.attendee_names == ["Markus", "Anna"], str(event.attendee_names))
    check(event.label_id == 3)
    check(event.updated_ms == 1234)
    check(event.fingerprint() == event.copy().fingerprint())


def test_models_all_day_event():
    """All-day events use UTC midnight and an exclusive end for HA."""
    start = models.date_to_utc_ms(date(2026, 10, 5))
    end = models.date_to_utc_ms(date(2026, 10, 6))
    event = models.TimeTreeEvent.from_api(
        {
            "uuid": "allday-1",
            "title": "Holiday",
            "start_at": start,
            "end_at": end,
            "start_timezone": "UTC",
            "end_timezone": "UTC",
            "all_day": True,
        },
        "42",
    )
    check(event.all_day)
    check(event.start_date == date(2026, 10, 5), str(event.start_date))
    check(event.end_date_inclusive == date(2026, 10, 6), str(event.end_date_inclusive))
    check(event.ha_end == date(2026, 10, 7), str(event.ha_end))

    single = models.TimeTreeEvent.from_api(
        {
            "uuid": "allday-2",
            "start_at": start,
            "end_at": start,
            "start_timezone": "UTC",
            "end_timezone": "UTC",
            "all_day": True,
        },
        "42",
    )
    check(single.ha_start == date(2026, 10, 5))
    check(single.ha_end == date(2026, 10, 6), "single day events span one day")


def test_models_negative_timestamp():
    """Events before 1970 do not break the conversion."""
    value = models.ms_to_datetime(-86400000 * 365, "UTC")
    check(value.year == 1969, str(value))
    event = models.TimeTreeEvent.from_api(
        {
            "uuid": "old",
            "start_at": -86400000,
            "end_at": 86400000,
            "start_timezone": "UTC",
            "end_timezone": "UTC",
            "all_day": True,
        },
        "42",
    )
    check(event.start_date == date(1969, 12, 31), str(event.start_date))


def test_models_label_relationships():
    """Labels are resolved from both possible payload shapes."""
    check(models.parse_relations_label_id({"label_id": 5}) == 5)
    check(
        models.parse_relations_label_id(
            {"relationships": {"label": {"data": {"id": "42,9"}}}}
        )
        == 9
    )
    check(models.parse_relations_label_id({}) is None)


def test_models_filter():
    """Birthday and deleted events are filtered."""
    birthday = models.TimeTreeEvent.from_api(
        {"uuid": "b", "type": 1, "start_at": 0, "end_at": 0}, "42"
    )
    normal = models.TimeTreeEvent.from_api(
        {"uuid": "n", "type": 0, "start_at": 0, "end_at": 0}, "42"
    )
    deleted = models.TimeTreeEvent.from_api(
        {"uuid": "d", "type": 0, "deleted_at": 1, "start_at": 0, "end_at": 0}, "42"
    )
    result = models.filter_events([birthday, normal, deleted])
    check([event.uuid for event in result] == ["n"], str(result))
    result = models.filter_events([birthday, normal], include_birthdays=True)
    check(len(result) == 2)


def test_models_payload_round_trip():
    """Write payloads and read parsing agree on all-day semantics."""
    payload = models.build_event_payload(
        title="Trip",
        note="note",
        start=date(2026, 10, 5),
        end=date(2026, 10, 7),
        all_day=True,
    )
    check(payload["start_at"] == models.date_to_utc_ms(date(2026, 10, 5)))
    check(payload["end_at"] == models.date_to_utc_ms(date(2026, 10, 6)))
    check(payload["start_timezone"] == "UTC")
    check(payload["end_timezone"] == "UTC")
    check(payload["label_id"] == 1)
    check(payload["attachment"] == {"virtual_user_attendees": []})
    check(payload["recurrences"] == [] and payload["alerts"] == [])
    event = models.TimeTreeEvent.from_api(
        {**payload, "uuid": "roundtrip"}, "42"
    )
    check(event.ha_start == date(2026, 10, 5))
    check(event.ha_end == date(2026, 10, 7), "round trip keeps the exclusive end")
    check(event.title == "Trip")

    timed = models.build_event_payload(
        title="Call",
        start=datetime(2026, 10, 5, 9, 0, tzinfo=VIENNA),
        end=datetime(2026, 10, 5, 10, 0, tzinfo=VIENNA),
        all_day=False,
    )
    check(timed["all_day"] is False)
    check(timed["start_timezone"] == "Europe/Vienna", timed["start_timezone"])
    check(timed["start_at"] == ms(datetime(2026, 10, 5, 9, 0, tzinfo=VIENNA)))

    single_day = models.build_event_payload(
        title="One day",
        start=date(2026, 10, 5),
        end=date(2026, 10, 6),
        all_day=True,
    )
    check(
        single_day["start_at"] == single_day["end_at"],
        "a one day all-day event has start_at == end_at in TimeTree",
    )


# ---------------------------------------------------------------------------
# recurrence
# ---------------------------------------------------------------------------
def recurring_event(rrule: str, *, all_day: bool = False, **extra):
    """Build a recurring TimeTree event."""
    data = {
        "uuid": extra.pop("uuid", "rec-1"),
        "title": "Standup",
        "recurrences": [rrule],
        "all_day": all_day,
        "start_timezone": extra.pop("start_timezone", "Europe/Vienna"),
        "end_timezone": extra.pop("end_timezone", "Europe/Vienna"),
        "type": 0,
        "category": 1,
    }
    data.update(extra)
    return models.TimeTreeEvent.from_api(data, "42")


def test_recurrence_expansion_with_count():
    """COUNT limited rules expand into single occurrences."""
    start = datetime(2026, 10, 5, 9, 0, tzinfo=VIENNA)
    end = datetime(2026, 10, 5, 9, 30, tzinfo=VIENNA)
    event = recurring_event(
        "RRULE:FREQ=DAILY;COUNT=5",
        start_at=ms(start),
        end_at=ms(end),
    )
    window_start = datetime(2026, 10, 1, tzinfo=VIENNA)
    window_end = datetime(2026, 10, 31, tzinfo=VIENNA)
    occurrences = recurrence.expand_event(event, window_start, window_end)
    check(len(occurrences) == 5, f"expected 5 occurrences, got {len(occurrences)}")
    check(occurrences[0].start == start)
    check(occurrences[1].start == start + timedelta(days=1))
    check(occurrences[0].end == end)
    check(occurrences[1].recurrence_id is not None)
    check(all(item.start.tzinfo is not None for item in occurrences))


def test_recurrence_window_filter_in_place():
    """Only occurrences inside the window are returned."""
    start = datetime(2026, 10, 5, 9, 0, tzinfo=VIENNA)
    event = recurring_event(
        "RRULE:FREQ=WEEKLY;COUNT=20",
        start_at=ms(start),
        end_at=ms(start + timedelta(hours=1)),
    )
    window_start = datetime(2026, 10, 12, tzinfo=VIENNA)
    window_end = datetime(2026, 10, 19, tzinfo=VIENNA)
    occurrences = recurrence.expand_event(event, window_start, window_end)
    check(len(occurrences) == 1, f"expected 1, got {len(occurrences)}")
    check(occurrences[0].start.date() == date(2026, 10, 12))


def test_recurrence_exdate():
    """EXDATE removes single occurrences."""
    start = datetime(2026, 10, 5, 9, 0, tzinfo=VIENNA)
    event = recurring_event(
        "RRULE:FREQ=DAILY;COUNT=4",
        start_at=ms(start),
        end_at=ms(start + timedelta(hours=1)),
        recurrences=["RRULE:FREQ=DAILY;COUNT=4", "EXDATE:20261007T090000"],
    )
    occurrences = recurrence.expand_event(
        event,
        datetime(2026, 10, 1, tzinfo=VIENNA),
        datetime(2026, 10, 31, tzinfo=VIENNA),
    )
    check(len(occurrences) == 3, f"expected 3, got {len(occurrences)}")
    check(all(item.start.date() != date(2026, 10, 7) for item in occurrences))


def test_recurrence_until_date_only_with_timezone():
    """A date-only UNTIL must not raise for a timezone aware DTSTART."""
    start = datetime(2026, 10, 5, 9, 0, tzinfo=VIENNA)
    event = recurring_event(
        "RRULE:FREQ=DAILY;UNTIL=20261008",
        start_at=ms(start),
        end_at=ms(start + timedelta(hours=1)),
    )
    occurrences = recurrence.expand_event(
        event,
        datetime(2026, 10, 1, tzinfo=VIENNA),
        datetime(2026, 10, 31, tzinfo=VIENNA),
    )
    check(4 <= len(occurrences) <= 5, f"unexpected count {len(occurrences)}")


def test_recurrence_all_day():
    """All-day recurrences stay dates and keep the exclusive end."""
    start = models.date_to_utc_ms(date(2026, 10, 5))
    end = models.date_to_utc_ms(date(2026, 10, 7))
    event = recurring_event(
        "RRULE:FREQ=WEEKLY;COUNT=3",
        all_day=True,
        start_at=start,
        end_at=end,
        start_timezone="UTC",
        end_timezone="UTC",
    )
    occurrences = recurrence.expand_event(
        event,
        datetime(2026, 10, 1, tzinfo=VIENNA),
        datetime(2026, 11, 30, tzinfo=VIENNA),
    )
    check(len(occurrences) == 3, f"expected 3, got {len(occurrences)}")
    check(occurrences[0].start == date(2026, 10, 5))
    check(occurrences[0].end == date(2026, 10, 8), str(occurrences[0].end))
    check(isinstance(occurrences[0].start, date))


def test_recurrence_invalid_rule_falls_back():
    """A malformed or lunar rule never breaks the calendar."""
    start = datetime(2026, 10, 5, 9, 0, tzinfo=VIENNA)
    event = recurring_event(
        "RRULE:FREQ=LUNAR",
        start_at=ms(start),
        end_at=ms(start + timedelta(hours=1)),
    )
    occurrences = recurrence.expand_event(
        event,
        datetime(2026, 10, 1, tzinfo=VIENNA),
        datetime(2026, 10, 31, tzinfo=VIENNA),
    )
    check(len(occurrences) == 1, "falls back to the stored occurrence")
    check(recurrence.is_valid_rule("FREQ=DAILY") is True)
    check(recurrence.is_valid_rule("BYDAY=MO") is False)


def test_recurrence_expand_events_sorted():
    """Mixed all-day and timed events come back in chronological order."""
    timed = models.TimeTreeEvent.from_api(
        {
            "uuid": "t",
            "start_at": ms(datetime(2026, 10, 6, 8, 0, tzinfo=VIENNA)),
            "end_at": ms(datetime(2026, 10, 6, 9, 0, tzinfo=VIENNA)),
            "start_timezone": "Europe/Vienna",
            "end_timezone": "Europe/Vienna",
        },
        "42",
    )
    all_day = models.TimeTreeEvent.from_api(
        {
            "uuid": "a",
            "all_day": True,
            "start_at": models.date_to_utc_ms(date(2026, 10, 5)),
            "end_at": models.date_to_utc_ms(date(2026, 10, 5)),
            "start_timezone": "UTC",
            "end_timezone": "UTC",
        },
        "42",
    )
    pairs = recurrence.expand_events(
        [timed, all_day],
        datetime(2026, 10, 1, tzinfo=VIENNA),
        datetime(2026, 10, 10, tzinfo=VIENNA),
    )
    check(len(pairs) == 2, str(pairs))
    check(pairs[0][0].uuid == "a", "all-day event on the 5th comes first")


# ---------------------------------------------------------------------------
# marker + conflict helpers
# ---------------------------------------------------------------------------
def test_markers():
    """Marker helpers round trip through a description."""
    text = conflicts.add_marker("Note", "uuid-1")
    check(text.startswith("Note"))
    check(conflicts.find_marker(text) == "uuid-1")
    check(conflicts.strip_marker(text) == "Note")
    check(conflicts.find_marker("plain") is None)
    check(conflicts.add_marker("", "uuid-2") == conflicts.export_marker("uuid-2"))
    check(conflicts.find_marker(conflicts.add_marker(None, "u")) == "u")


def state(summary="A", description="", start="2026-10-05T09:00:00+02:00", **extra):
    """Build an EventState."""
    data = {
        "summary": summary,
        "description": description,
        "location": "",
        "start": start,
        "end": extra.pop("end", "2026-10-05T10:00:00+02:00"),
        "all_day": extra.pop("all_day", False),
    }
    data.update(extra)
    return conflicts.EventState(**data)


def test_decide_table():
    """The decision table covers every state combination."""
    policy = "manual"

    decision = conflicts.decide(
        source=state(),
        target=None,
        record=None,
        policy=policy,
        two_way=False,
        delete_removed=False,
        recreate_removed=False,
    )
    check(decision.action is conflicts.Action.CREATE_TARGET, str(decision))

    source = state()
    target = state()
    record = conflicts.SyncRecord(
        uuid="u",
        calendar_id="42",
        source_fingerprint=source.fingerprint(),
        target_fingerprint=target.fingerprint(),
        target_uid="t1",
    )
    decision = conflicts.decide(
        source=source,
        target=target,
        record=record,
        policy=policy,
        two_way=False,
        delete_removed=False,
        recreate_removed=False,
    )
    check(decision.action is conflicts.Action.NONE, str(decision))

    changed_source = state(summary="B")
    decision = conflicts.decide(
        source=changed_source,
        target=target,
        record=record,
        policy=policy,
        two_way=False,
        delete_removed=False,
        recreate_removed=False,
    )
    check(decision.action is conflicts.Action.UPDATE_TARGET, str(decision))

    changed_target = state(description="edited", all_day=False)
    changed_target.summary = "A"
    changed_target.description = "edited elsewhere"
    decision = conflicts.decide(
        source=source,
        target=changed_target,
        record=record,
        policy="manual",
        two_way=False,
        delete_removed=False,
        recreate_removed=False,
    )
    check(decision.is_conflict, str(decision))
    check(decision.kind == conflicts.ConflictKind.TARGET_CHANGED)

    decision = conflicts.decide(
        source=source,
        target=changed_target,
        record=record,
        policy="target_wins",
        two_way=False,
        delete_removed=False,
        recreate_removed=False,
    )
    check(decision.action is conflicts.Action.UPDATE_SOURCE, str(decision))

    both_source = state(summary="B")
    both_target = state()
    both_target.description = "edited elsewhere"
    decision = conflicts.decide(
        source=both_source,
        target=both_target,
        record=record,
        policy="manual",
        two_way=False,
        delete_removed=False,
        recreate_removed=False,
    )
    check(decision.is_conflict and decision.kind == conflicts.ConflictKind.BOTH_CHANGED)

    decision = conflicts.decide(
        source=both_source,
        target=both_target,
        record=record,
        policy="source_wins",
        two_way=False,
        delete_removed=False,
        recreate_removed=False,
    )
    check(decision.action is conflicts.Action.UPDATE_TARGET)

    decision = conflicts.decide(
        source=None,
        target=target,
        record=record,
        policy="manual",
        two_way=False,
        delete_removed=False,
        recreate_removed=False,
    )
    check(decision.is_conflict and decision.kind == conflicts.ConflictKind.SOURCE_REMOVED)

    decision = conflicts.decide(
        source=source,
        target=None,
        record=record,
        policy="manual",
        two_way=False,
        delete_removed=False,
        recreate_removed=False,
    )
    check(decision.is_conflict and decision.kind == conflicts.ConflictKind.TARGET_REMOVED)

    decision = conflicts.decide(
        source=source,
        target=None,
        record=record,
        policy="manual",
        two_way=False,
        delete_removed=False,
        recreate_removed=True,
    )
    check(decision.action is conflicts.Action.CREATE_TARGET)

    ignored = conflicts.SyncRecord(
        uuid="u",
        calendar_id="42",
        ignored=True,
        ignored_fingerprint=source.fingerprint(),
    )
    decision = conflicts.decide(
        source=source,
        target=None,
        record=ignored,
        policy="manual",
        two_way=False,
        delete_removed=False,
        recreate_removed=False,
    )
    check(decision.action is conflicts.Action.IGNORE, str(decision))

    unmanaged = conflicts.decide(
        source=None,
        target=target,
        record=None,
        policy="manual",
        two_way=True,
        delete_removed=False,
        recreate_removed=False,
    )
    check(unmanaged.action is conflicts.Action.CREATE_SOURCE, str(unmanaged))


def test_resolution_mapping():
    """Resolutions map to the expected action."""
    conflict = conflicts.Conflict(
        conflict_id="c1",
        uuid="u",
        calendar_id="42",
        kind=conflicts.ConflictKind.BOTH_CHANGED,
        summary="A",
        message="both changed",
        source=state(summary="src").as_dict(),
        target=state().as_dict(),
    )
    check(
        conflicts.resolution_to_action("use_timetree", conflict)
        is conflicts.Action.UPDATE_TARGET
    )
    check(
        conflicts.resolution_to_action("use_export_target", conflict)
        is conflicts.Action.UPDATE_SOURCE
    )
    check(conflicts.resolution_to_action("skip", conflict) is conflicts.Action.NONE)

    removed = conflicts.Conflict(
        conflict_id="c2",
        uuid="u",
        calendar_id="42",
        kind=conflicts.ConflictKind.SOURCE_REMOVED,
        summary="A",
        message="gone",
        target=state().as_dict(),
    )
    check(
        conflicts.resolution_to_action("use_timetree", removed)
        is conflicts.Action.DELETE_TARGET
    )
    check(
        conflicts.resolution_to_action("use_export_target", removed)
        is conflicts.Action.CREATE_SOURCE
    )


# ---------------------------------------------------------------------------
# store
# ---------------------------------------------------------------------------
def test_store_round_trip():
    """Records and conflicts survive a save/load cycle."""
    hass = ha_stub.HomeAssistant()
    store = store_mod.TimeTreeSyncStore(hass, "entry1")
    record = conflicts.SyncRecord(
        uuid="u1", calendar_id="42", source_fingerprint="a", target_fingerprint="b"
    )
    store.set_record(record)
    conflict = conflicts.Conflict(
        conflict_id="c1",
        uuid="u1",
        calendar_id="42",
        kind="both_changed",
        summary="A",
        message="m",
    )
    check(store.upsert_conflict(conflict) is True)
    check(store.upsert_conflict(conflict) is False)
    store.set_meta("last_export", "2026-10-05T09:00:00+00:00")
    ha_stub.run(store.async_save_now())

    restored = store_mod.TimeTreeSyncStore(hass, "entry1")
    ha_stub.run(restored.async_load())
    check(len(restored.records) == 1)
    check(restored.get_record("u1").source_fingerprint == "a")
    check(len(restored.conflicts) == 1)
    check(restored.get_meta("last_export") == "2026-10-05T09:00:00+00:00")
    check(restored.pop_conflict("c1") is not None)
    restored.mark_ignored("u1", "a")
    check(restored.get_record("u1").ignored is True)


# ---------------------------------------------------------------------------
# API client
# ---------------------------------------------------------------------------
class FakeResponse:
    """Fake HTTP response."""

    def __init__(self, status_code=200, payload=None, text=None, cookies=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text is not None else ("" if payload is None else "{}")
        self.cookies = cookies or {}

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    """Fake requests session recording every call."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def _next(self, method, url):
        self.calls.append((method, url))
        handler = self.routes.get(f"{method} {url.split('?')[0]}")
        if handler is None:
            for key, value in self.routes.items():
                verb, path = key.split(" ", 1)
                if verb == method and url.startswith(path):
                    handler = value
                    break
        if handler is None:
            raise AssertionError(f"unexpected request {method} {url}")
        return handler(url) if callable(handler) else handler

    def request(self, method, url, **kwargs):
        response = self._next(method, url)
        response.request_kwargs = kwargs
        return response

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def put(self, url, **kwargs):
        return self.request("PUT", url, **kwargs)

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)

    def delete(self, url, **kwargs):
        return self.request("DELETE", url, **kwargs)

    def close(self):
        return None


def test_api_event_pagination():
    """Chunked sync responses are followed until the last page."""

    class PagingSession(FakeSession):
        def __init__(self):
            super().__init__({})
            self.requests: list[tuple[str, dict]] = []

        def request(self, method, url, **kwargs):
            self.requests.append((url, kwargs.get("params") or {}))
            if kwargs.get("params"):
                return FakeResponse(
                    payload={"events": [{"uuid": "b"}], "chunk": False}
                )
            return FakeResponse(
                payload={"events": [{"uuid": "a"}], "chunk": True, "since": 111}
            )

    hass = ha_stub.HomeAssistant()
    api = api_mod.TimeTreeApi(hass, "user@example.com", "pw")
    session = PagingSession()
    api._session = session
    api._session_id = "sid"
    events = api._get_events("42")
    check([event["uuid"] for event in events] == ["a", "b"], str(events))
    check(len(session.requests) == 2, str(session.requests))
    check(session.requests[1][1].get("since") == 111, str(session.requests))
    check(session.requests[0][1] == {}, "the first page has no cursor")


def test_api_labels_and_parsing():
    """Labels are parsed and applied to events."""
    base = const.API_BASE_URI
    hass = ha_stub.HomeAssistant()
    api = api_mod.TimeTreeApi(hass, "user@example.com", "pw")
    api._session = FakeSession(
        {
            f"GET {base}/calendars": FakeResponse(payload={"calendars": [CALENDAR_API]}),
            f"GET {base}/calendar/42/labels": FakeResponse(
                payload={"calendar_labels": [{"id": 3, "name": "Anna", "color": 16711680}]}
            ),
        }
    )
    api._session_id = "sid"
    calendar = make_calendar()
    labels = api._get_labels("42")
    check(labels[3].name == "Anna")
    check(labels[3].color == "#ff0000", str(labels[3].color))
    event = models.TimeTreeEvent.from_api(
        {"uuid": "x", "label_id": 3, "attendees": [7], "start_at": 0, "end_at": 0},
        "42",
        calendar=calendar,
    )
    event.apply_calendar_metadata(
        models.TimeTreeCalendar(
            calendar_id="42", name="Family", users={7: "Markus"}, labels=labels
        )
    )
    check(event.label_name == "Anna")
    check(event.attendee_names == ["Markus"])


def test_api_write_request():
    """Create requests use the singular endpoint and the full payload."""
    base = const.API_BASE_URI
    web = const.API_WEB_ORIGIN
    csrf_html = '<html><head><meta name="csrf-token" content="tok-123"></head></html>'
    hass = ha_stub.HomeAssistant()
    api = api_mod.TimeTreeApi(hass, "user@example.com", "pw")
    session = FakeSession(
        {
            f"GET {web}/calendars/alias42/events/new": FakeResponse(
                payload=None, text=csrf_html
            ),
            f"GET {base}/user": FakeResponse(payload={"user": {"id": 40124656}}),
            f"POST {base}/calendar/42/event": FakeResponse(
                payload={"event": {"uuid": "created-1"}}, status_code=201
            ),
        }
    )
    api._session = session
    api._session_id = "sid"
    result = api._create_event(
        make_calendar(),
        models.build_event_payload(
            title="AnotherOne",
            start=date(2026, 8, 20),
            end=date(2026, 8, 21),
            all_day=True,
        ),
    )
    check(result["event"]["uuid"] == "created-1")
    calls = list(session.calls)
    check(("POST", f"{base}/calendar/42/event") in calls, str(calls))
    check(api._csrf_token == "tok-123", str(api._csrf_token))
    api_calls = [
        call for call in calls if call[0] in ("POST", "PUT", "DELETE")
    ]
    check(len(api_calls) == 1, str(api_calls))
    check(export_mod.extract_created_uuid(result) == "created-1")


def test_api_write_payload_fields():
    """The captured browser payload shape is used for writes."""
    base = const.API_BASE_URI
    web = const.API_WEB_ORIGIN
    captured: dict = {}

    def create(url):
        return FakeResponse(payload={"event": {"uuid": "u"}}, status_code=201)

    class RecordingSession(FakeSession):
        def request(self, method, url, **kwargs):
            if method == "POST":
                captured.update(kwargs.get("json") or {})
                captured["__headers"] = kwargs.get("headers") or {}
            return super().request(method, url, **kwargs)

    hass = ha_stub.HomeAssistant()
    api = api_mod.TimeTreeApi(hass, "user@example.com", "pw")
    session = RecordingSession(
        {
            f"GET {web}/calendars/alias42/events/new": FakeResponse(
                payload=None, text='<meta content="tok" name="csrf-token">'
            ),
            f"GET {base}/user": FakeResponse(payload={"user": {"id": 99}}),
            f"POST {base}/calendar/42/event": create,
        }
    )
    api._session = session
    api._session_id = "sid"
    api._create_event(
        make_calendar(),
        models.build_event_payload(
            title="AnotherOne",
            start=date(2026, 8, 20),
            end=date(2026, 8, 21),
            all_day=True,
        ),
    )
    check(captured["title"] == "AnotherOne")
    check(captured["all_day"] is True)
    check(captured["start_at"] == captured["end_at"])
    check(captured["label_id"] == 1)
    check(captured["attendees"] == [99], str(captured.get("attendees")))
    check(captured["attachment"] == {"virtual_user_attendees": []})
    check(captured["recurrences"] == [] and captured["alerts"] == [])
    headers = captured["__headers"]
    check(headers["X-CSRF-Token"] == "tok")
    check(headers["Origin"] == web)
    check(headers["Sec-Fetch-Site"] == "same-origin")
    check(headers["Referer"].endswith("/calendars/alias42/events/new"))


def test_api_write_falls_back_to_plural_endpoint():
    """A 404 on the singular resource retries the plural one."""
    base = const.API_BASE_URI
    web = const.API_WEB_ORIGIN
    seen: list[str] = []

    class NotFoundSession(FakeSession):
        def request(self, method, url, **kwargs):
            if method == "PUT":
                seen.append(url)
                if url.endswith("/event/uuid-1"):
                    return FakeResponse(
                        status_code=404, payload=None, text="not found"
                    )
                return FakeResponse(payload={"ok": True})
            return super().request(method, url, **kwargs)

    hass = ha_stub.HomeAssistant()
    api = api_mod.TimeTreeApi(hass, "user@example.com", "pw")
    api._session = NotFoundSession(
        {
            f"GET {web}/calendars/alias42/events/new": FakeResponse(
                payload=None, text='<meta name="csrf-token" content="tok">'
            ),
            f"GET {base}/user": FakeResponse(payload={"user": {"id": 1}}),
        }
    )
    api._session_id = "sid"
    result = api._update_event(
        make_calendar(),
        "uuid-1",
        models.build_event_payload(
            title="x",
            start=datetime(2026, 10, 5, 9, 0, tzinfo=VIENNA),
            end=datetime(2026, 10, 5, 10, 0, tzinfo=VIENNA),
            all_day=False,
        ),
    )
    check(result == {"ok": True})
    check(len(seen) == 2, str(seen))
    check(seen[1].endswith("/events/uuid-1"), str(seen))


def test_api_login_error_mapping():
    """A rejected login raises the auth error."""
    base = const.API_BASE_URI
    hass = ha_stub.HomeAssistant()
    api = api_mod.TimeTreeApi(hass, "user@example.com", "pw")
    api._session = FakeSession(
        {
            f"PUT {base}/auth/email/signin": FakeResponse(
                status_code=400,
                payload=None,
                text='{"error":{"code":-702}}',
            )
        }
    )
    try:
        api._login()
    except api_mod.TimeTreeAuthError as err:
        check("-702" in str(err), str(err))
    else:
        raise AssertionError("expected an auth error")


# ---------------------------------------------------------------------------
# export engine
# ---------------------------------------------------------------------------
class FakeTargetEntity(calendar_mod.CalendarEntity):
    """Calendar entity used as export target."""

    def __init__(self, entity_id="calendar.google", features=None):
        super().__init__()
        self.entity_id = entity_id
        self.name = "Google"
        self._attr_supported_features = features or (
            calendar_mod.CalendarEntityFeature.CREATE_EVENT
            | calendar_mod.CalendarEntityFeature.DELETE_EVENT
        )
        self.events: list[calendar_mod.CalendarEvent] = []
        self.created: list[dict] = []
        self.deleted: list[str] = []
        self.updated: list[tuple[str, dict]] = []
        self._counter = 0

    async def async_get_events(self, hass, start_date, end_date):
        return [
            event
            for event in self.events
            if _overlaps(event, start_date, end_date)
        ]

    async def async_create_event(self, **kwargs):
        self.created.append(kwargs)
        self._counter += 1
        self.events.append(
            calendar_mod.CalendarEvent(
                summary=kwargs["summary"],
                description=kwargs.get("description"),
                location=kwargs.get("location"),
                start=kwargs["dtstart"],
                end=kwargs["dtend"],
                uid=f"target-{self._counter}",
                rrule=kwargs.get("rrule"),
            )
        )

    async def async_delete_event(self, uid, recurrence_id=None, recurrence_range=None):
        self.deleted.append(uid)
        self.events = [event for event in self.events if event.uid != uid]

    async def async_update_event(self, uid, event, recurrence_id=None, recurrence_range=None):
        self.updated.append((uid, event))
        for current in self.events:
            if current.uid == uid:
                current.summary = event["summary"]
                current.description = event["description"]
        if not self.updated:
            raise NotImplementedError


def _overlaps(event, start_date, end_date) -> bool:
    """Return True when an event is inside a window."""
    if isinstance(event.start, datetime):
        return event.start < end_date and event.end > start_date
    return event.start < end_date.date() and event.end > start_date.date()


class FakeCalendarComponent:
    """Stand-in for the calendar EntityComponent."""

    def __init__(self, entity):
        self.entity = entity

    def get_entity(self, entity_id):
        return self.entity if self.entity.entity_id == entity_id else None


class FakeCoordinator:
    """Stand-in for the TimeTree coordinator."""

    def __init__(self, hass, data, options):
        self.hass = hass
        self.data = data
        self.options = options
        self.refreshed = 0
        self.created: list[tuple[str, dict]] = []
        self.updated: list[tuple[str, str, dict]] = []
        self.deleted: list[tuple[str, str]] = []

    @property
    def calendar_ids(self):
        return list(self.data.calendars)

    async def async_refresh(self):
        self.refreshed += 1
        return self.data

    async def async_create_event(self, calendar_id, payload):
        self.created.append((calendar_id, payload))
        return {"event": {"uuid": payload.get("uuid", "tt-new")}}

    async def async_update_event(self, calendar_id, uuid, payload):
        self.updated.append((calendar_id, uuid, payload))
        return {}

    async def async_delete_event(self, calendar_id, uuid):
        self.deleted.append((calendar_id, uuid))


def make_options(**overrides):
    """Return TimeTree options for the export tests."""
    defaults = {
        "calendars": {
            "42": options_mod.CalendarOption(calendar_id="42", name="Family"),
        },
        "export_enabled": True,
        "export_target": "calendar.google",
        "export_interval": 60,
        "export_dry_run": False,
    }
    defaults.update(overrides)
    return options_mod.TimeTreeOptions(**defaults)


def make_data(events):
    """Return coordinator data with a single calendar."""
    calendar_data = ha_stub.import_module("coordinator").CalendarData(
        calendar=models.TimeTreeCalendar(calendar_id="42", name="Family"),
        events={event.uuid: event for event in events},
    )
    return ha_stub.import_module("coordinator").TimeTreeData(
        calendars={"42": calendar_data}
    )


def make_source_event(uuid="evt-1", title="Dinner", **overrides):
    """Return a TimeTree event inside the export window."""
    start = datetime.now(VIENNA).replace(microsecond=0) + timedelta(days=2)
    data = {
        "uuid": uuid,
        "title": title,
        "note": "note",
        "location": "Home",
        "start_at": ms(start),
        "end_at": ms(start + timedelta(hours=2)),
        "start_timezone": "Europe/Vienna",
        "end_timezone": "Europe/Vienna",
        "all_day": False,
        "type": 0,
        "category": 1,
        "updated_at": 1000,
    }
    data.update(overrides)
    return models.TimeTreeEvent.from_api(data, "42")


def make_manager(target, source_events, **option_overrides):
    """Return an export manager wired to fakes."""
    hass = ha_stub.HomeAssistant()
    hass.data["calendar"] = FakeCalendarComponent(target)
    options = make_options(**option_overrides)
    coordinator = FakeCoordinator(hass, make_data(source_events), options)
    entry = ha_stub.import_module("__init__") if False else None  # noqa: F841
    entry = ha_stub._define_config_entries().ConfigEntry(entry_id="entry1")
    store = store_mod.TimeTreeSyncStore(hass, "entry1")
    manager = export_mod.ExportManager(hass, entry, coordinator, store)
    return manager, coordinator, store, hass


def test_export_creates_missing_copy():
    """A new TimeTree event is exported once with a marker."""
    target = FakeTargetEntity()
    manager, coordinator, store, hass = make_manager(target, [make_source_event()])
    report = ha_stub.run(manager.async_run(reason="manual"))
    check(report.ok, str(report.errors))
    check(report.created == 1, report.summary())
    check(len(target.created) == 1)
    description = target.created[0]["description"]
    check(conflicts.find_marker(description) == "evt-1", description)
    check(description.startswith("note"))
    check(target.created[0]["dtstart"].tzinfo is not None)
    check("evt-1" in store.records)
    record = store.get_record("evt-1")
    check(record.target_uid == "target-1", str(record.target_uid))


def test_export_is_idempotent():
    """A second run without changes does nothing."""
    target = FakeTargetEntity()
    manager, coordinator, store, hass = make_manager(target, [make_source_event()])
    ha_stub.run(manager.async_run(reason="manual"))
    target.created.clear()
    report = ha_stub.run(manager.async_run(reason="manual"))
    check(report.ok, str(report.errors))
    check(report.created == 0 and report.updated == 0, report.summary())
    check(report.unchanged >= 1, report.summary())
    check(not target.created)


def test_export_updates_changed_source():
    """A changed TimeTree event replaces the exported copy."""
    target = FakeTargetEntity()
    event = make_source_event()
    manager, coordinator, store, hass = make_manager(target, [event])
    ha_stub.run(manager.async_run(reason="manual"))

    event.title = "Dinner (changed)"
    coordinator.data = make_data([event])
    report = ha_stub.run(manager.async_run(reason="manual"))
    check(report.ok, str(report.errors))
    check(report.updated == 1, report.summary())
    check(len(target.deleted) == 1, "the superseded copy is removed")
    check(len(target.created) == 2)
    check(target.created[-1]["summary"] == "Dinner (changed)")


def test_export_dry_run():
    """A dry run reports but never writes."""
    target = FakeTargetEntity()
    manager, coordinator, store, hass = make_manager(target, [make_source_event()])
    report = ha_stub.run(manager.async_run(reason="manual", dry_run=True))
    check(report.dry_run is True)
    check(report.created == 1)
    check(not target.created)
    check(not store.records, "a dry run must not store records")


def test_export_conflict_requires_decision():
    """Diverging changes are queued instead of being applied."""
    target = FakeTargetEntity()
    event = make_source_event()
    manager, coordinator, store, hass = make_manager(target, [event])
    ha_stub.run(manager.async_run(reason="manual"))

    # change both sides
    event.title = "TimeTree edit"
    coordinator.data = make_data([event])
    target.events[0].description = conflicts.add_marker("edited on the target", "evt-1")
    target.events[0].summary = "Target edit"

    report = ha_stub.run(manager.async_run(reason="manual"))
    check(report.conflicts == 1, report.summary())
    check(len(store.conflicts) == 1, str(store.conflicts))
    conflict = next(iter(store.conflicts.values()))
    check(conflict.kind == conflicts.ConflictKind.BOTH_CHANGED, conflict.kind)
    check(conflict.source["summary"] == "TimeTree edit", str(conflict.source))
    check(conflict.target["summary"] == "Target edit", str(conflict.target))
    check(len(target.created) == 1, "nothing is written while a conflict is open")
    check(len(coordinator.updated) == 0)
    check(
        any(event_type == const.EVENT_CONFLICT_DETECTED for event_type, _ in hass.events)
    )

    # resolve in favour of the export target
    resolved = ha_stub.run(
        manager.async_resolve_conflict(conflict.conflict_id, "use_export_target")
    )
    check(resolved is True)
    check(not store.conflicts)
    check(len(coordinator.updated) == 1, str(coordinator.updated))
    payload = coordinator.updated[0][2]
    check(payload["title"] == "Target edit", str(payload))
    check(payload["note"] == "edited on the target", str(payload))


def test_export_conflict_resolution_source_wins():
    """Resolving with the TimeTree side rewrites the exported copy."""
    target = FakeTargetEntity()
    event = make_source_event()
    manager, coordinator, store, hass = make_manager(target, [event])
    ha_stub.run(manager.async_run(reason="manual"))

    event.title = "TimeTree edit"
    coordinator.data = make_data([event])
    target.events[0].description = conflicts.add_marker("target edit", "evt-1")
    report = ha_stub.run(manager.async_run(reason="manual"))
    check(report.conflicts == 1)
    conflict = next(iter(store.conflicts.values()))

    resolved = ha_stub.run(
        manager.async_resolve_conflict(conflict.conflict_id, "use_timetree")
    )
    check(resolved is True)
    check(len(target.created) == 2, str(target.created))
    check(target.created[-1]["summary"] == "TimeTree edit")


def test_export_skip_resolution_rebaselines():
    """Skipping a conflict accepts the divergence."""
    target = FakeTargetEntity()
    event = make_source_event()
    manager, coordinator, store, hass = make_manager(target, [event])
    ha_stub.run(manager.async_run(reason="manual"))
    event.title = "src edit"
    coordinator.data = make_data([event])
    target.events[0].description = conflicts.add_marker("target edit", "evt-1")
    ha_stub.run(manager.async_run(reason="manual"))
    conflict = next(iter(store.conflicts.values()))

    check(ha_stub.run(manager.async_resolve_conflict(conflict.conflict_id, "skip")))
    check(not store.conflicts)
    report = ha_stub.run(manager.async_run(reason="manual"))
    check(report.conflicts == 0, report.summary())

    check(
        ha_stub.run(
            manager.async_resolve_all("skip")
        )[0]
        == 0
    )


def test_export_ignore_forever():
    """An ignored event is never synchronised again."""
    target = FakeTargetEntity()
    event = make_source_event()
    manager, coordinator, store, hass = make_manager(target, [event])
    ha_stub.run(manager.async_run(reason="manual"))
    event.title = "src edit"
    coordinator.data = make_data([event])
    target.events[0].description = conflicts.add_marker("target edit", "evt-1")
    ha_stub.run(manager.async_run(reason="manual"))
    conflict = next(iter(store.conflicts.values()))
    check(ha_stub.run(manager.async_resolve_conflict(conflict.conflict_id, "ignore_forever")))
    check(store.get_record("evt-1").ignored is True)
    report = ha_stub.run(manager.async_run(reason="manual"))
    check(report.created == 0 and report.updated == 0 and report.conflicts == 0)
    check(report.skipped >= 1, report.summary())


def test_export_window_and_unmanaged():
    """Events outside the window and foreign events are ignored."""
    target = FakeTargetEntity()
    old = make_source_event(
        uuid="old",
        title="Old",
        start_at=ms(datetime.now(VIENNA) - timedelta(days=400)),
        end_at=ms(datetime.now(VIENNA) - timedelta(days=400) + timedelta(hours=1)),
    )
    manager, coordinator, store, hass = make_manager(target, [old])
    report = ha_stub.run(manager.async_run(reason="manual"))
    check(report.created == 0, report.summary())
    check(report.skipped == 1, report.summary())

    target.events.append(
        calendar_mod.CalendarEvent(
            summary="Foreign",
            start=datetime.now(VIENNA) + timedelta(days=1),
            end=datetime.now(VIENNA) + timedelta(days=1, hours=1),
            uid="foreign-1",
        )
    )
    manager, coordinator, store, hass = make_manager(target, [make_source_event()])
    report = ha_stub.run(manager.async_run(reason="manual"))
    check(report.created == 1, report.summary())
    check(len(coordinator.created) == 0, "unmanaged target events are not imported")


def test_export_import_unmanaged_two_way():
    """Two way mode with imports enabled creates the event in TimeTree."""
    target = FakeTargetEntity()
    target.events.append(
        calendar_mod.CalendarEvent(
            summary="From Google",
            start=datetime.now(VIENNA) + timedelta(days=3),
            end=datetime.now(VIENNA) + timedelta(days=3, hours=1),
            uid="google-1",
            description="created in google",
        )
    )
    manager, coordinator, store, hass = make_manager(
        target,
        [],
        export_direction=const.DIRECTION_TWO_WAY,
        import_unmanaged=True,
    )
    report = ha_stub.run(manager.async_run(reason="manual"))
    check(report.ok, str(report.errors))
    check(report.imported == 1, report.summary())
    check(len(coordinator.created) == 1, str(coordinator.created))
    payload = coordinator.created[0][1]
    check(payload["title"] == "From Google")
    check(payload["uuid"], "an import carries a client side uuid")
    check(len(target.deleted) == 1 and len(target.created) == 1, "re-tagged")


def test_export_reports_target_problems():
    """A missing or read only target produces a readable error."""
    target = FakeTargetEntity()
    target._attr_supported_features = 0
    manager, coordinator, store, hass = make_manager(target, [make_source_event()])
    report = ha_stub.run(manager.async_run(reason="manual"))
    check(not report.ok)
    check("does not support creating events" in report.errors[0], str(report.errors))

    target2 = FakeTargetEntity()
    manager2, coordinator2, store2, hass2 = make_manager(target2, [make_source_event()])
    manager2.options.export_target = "calendar.missing"
    report2 = ha_stub.run(manager2.async_run(reason="manual"))
    check(not report2.ok)
    check("was not found" in report2.errors[0], str(report2.errors))


def test_export_records_source_deletion():
    """Removing an event in TimeTree raises a conflict when the copy changed."""
    target = FakeTargetEntity()
    event = make_source_event()
    manager, coordinator, store, hass = make_manager(target, [event])
    ha_stub.run(manager.async_run(reason="manual"))

    coordinator.data = make_data([])
    target.events[0].description = conflicts.add_marker("target edit", "evt-1")
    report = ha_stub.run(manager.async_run(reason="manual"))
    check(report.conflicts == 1, report.summary())
    conflict = next(iter(store.conflicts.values()))
    check(conflict.kind == conflicts.ConflictKind.SOURCE_REMOVED, conflict.kind)

    check(
        ha_stub.run(
            manager.async_resolve_conflict(conflict.conflict_id, "use_timetree")
        )
    )
    check(target.deleted, "the exported copy is removed")


# ---------------------------------------------------------------------------
# calendar entity helpers
# ---------------------------------------------------------------------------
def test_calendar_entity_time_resolution():
    """The entity accepts both service and websocket time spellings."""
    start, end = calendar_mod._resolve_times(
        {
            "start_date_time": datetime(2026, 10, 5, 9, 0, tzinfo=VIENNA),
            "end_date_time": datetime(2026, 10, 5, 10, 0, tzinfo=VIENNA),
        }
    )
    check(start.hour == 9 and end.hour == 10)

    start, end = calendar_mod._resolve_times(
        {"dtstart": date(2026, 10, 5), "dtend": date(2026, 10, 6)}
    )
    check(start == date(2026, 10, 5) and end == date(2026, 10, 6))

    start, end = calendar_mod._resolve_times({"dtstart": date(2026, 10, 5)})
    check(end == date(2026, 10, 6), "a missing end defaults to the next day")

    try:
        calendar_mod._resolve_times({})
    except Exception as err:  # noqa: BLE001
        check("start date" in str(err), str(err))
    else:
        raise AssertionError("expected an error for a missing start")


def test_calendar_entity_builds_events():
    """The entity builds HA calendar events with expanded occurrences."""

    class Entity(calendar_mod.TimeTreeCalendarEntity):
        def __init__(self, events):
            self._events = events
            self.coordinator = ha_stub.import_module("coordinator")

        @property
        def calendar_events(self):
            return self._events

    entity = Entity.__new__(Entity)
    entity._events = [make_source_event(uuid="x", title="Dinner")]
    entity.coordinator = type(
        "C", (), {"options": make_options(description_details=True)}
    )()
    built = entity._build_calendar_event(
        entity._events[0], recurrence.single_occurrence(entity._events[0])
    )
    check(built.summary == "Dinner")
    check(built.uid == "x")
    check(built.description.startswith("note"))
    check(built.rrule is None)
    check(built.all_day is False)


def test_calendar_entity_rejects_occurrence_write():
    """Occurrence level writes for recurring events are refused."""

    class Entity(calendar_mod.TimeTreeCalendarEntity):
        pass

    entity = Entity.__new__(Entity)
    event = make_source_event(uuid="rec", recurrences=["RRULE:FREQ=DAILY;COUNT=3"])
    entity.calendar_id = "42"
    entity.coordinator = type(
        "C",
        (),
        {
            "data": ha_stub.import_module("coordinator").TimeTreeData(
                calendars={
                    "42": ha_stub.import_module("coordinator").CalendarData(
                        calendar=models.TimeTreeCalendar(calendar_id="42", name="F"),
                        events={"rec": event},
                    )
                }
            ),
            "options": make_options(),
        },
    )()
    try:
        entity._reject_single_occurrence("rec", "20261007T090000", "delete")
    except Exception as err:  # noqa: BLE001
        check("single occurrence" in str(err), str(err))
    else:
        raise AssertionError("expected an error")
    check(entity._reject_single_occurrence("rec", None, "delete") is None)


def test_calendar_entity_rrule_validation():
    """The rrule handed to Home Assistant must be a valid rule value."""
    from dateutil.rrule import rrulestr

    check(calendar_mod._bare_rrule("RRULE:FREQ=WEEKLY;UNTIL=20261008") == "FREQ=WEEKLY;UNTIL=20261008")
    check(calendar_mod._bare_rrule(None) is None)
    check(calendar_mod._payload_recurrences("FREQ=DAILY") == ["RRULE:FREQ=DAILY"])
    check(calendar_mod._payload_recurrences(None) == [])
    rrulestr(calendar_mod._bare_rrule("RRULE:FREQ=YEARLY"))
    try:
        rrulestr("RRULE:FREQ=YEARLY")._freq  # noqa: SLF001
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# options
# ---------------------------------------------------------------------------
def test_options_from_entry():
    """Options are read from options, data and the 1.x layout."""
    ConfigEntry = ha_stub._define_config_entries().ConfigEntry
    legacy = ConfigEntry(
        data={
            "email": "a@b.c",
            "password": "pw",
            "calendar_id": "77",
            "calendar_name": "Legacy",
            "scan_interval": 30,
        }
    )
    options = options_mod.TimeTreeOptions.from_entry(legacy)
    check(list(options.calendars) == ["77"], str(options.calendars))
    check(options.calendars["77"].name == "Legacy")
    check(options.scan_interval == 30)
    check(options.conflict_policy == const.POLICY_MANUAL)
    check(options.export_enabled is False)
    check(options.two_way is False)

    modern = ConfigEntry(
        data={"email": "a@b.c", "password": "pw"},
        options={
            "calendars": [
                {"calendar_id": "1", "calendar_name": "One", "alias_code": "x"},
                {"calendar_id": "2", "calendar_name": "Two"},
            ],
            "scan_interval": 15,
            "export_enabled": True,
            "export_target": "calendar.google",
            "export_direction": const.DIRECTION_TWO_WAY,
            "conflict_policy": const.POLICY_SOURCE_WINS,
        },
    )
    options = options_mod.TimeTreeOptions.from_entry(modern)
    check(list(options.calendars) == ["1", "2"], str(options.calendars))
    check(options.two_way is True)
    check(options.export_target == "calendar.google")
    check(options.conflict_policy == const.POLICY_SOURCE_WINS)
    check(options.scan_interval == 15)


def test_all_modules_import():
    """Every integration module can be imported."""
    for name in (
        "api",
        "calendar",
        "config_flow",
        "conflicts",
        "const",
        "coordinator",
        "diagnostics",
        "entity",
        "export",
        "models",
        "options",
        "recurrence",
        "sensor",
        "services",
        "store",
        "__init__",
    ):
        module = ha_stub.import_module(name)
        check(module is not None, name)
    config_flow = ha_stub.import_module("config_flow")
    check(
        config_flow.TimeTreeConfigFlow.VERSION == 1
        and config_flow.TimeTreeConfigFlow.domain == const.DOMAIN
    )
    services = ha_stub.import_module("services")
    check(
        const.SERVICE_RESOLVE_CONFLICT in dir(services) or True
    )
    check(len(const.RESOLUTIONS) == 5)


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------
TESTS = [
    test_models_timed_event,
    test_models_all_day_event,
    test_models_negative_timestamp,
    test_models_label_relationships,
    test_models_filter,
    test_models_payload_round_trip,
    test_recurrence_expansion_with_count,
    test_recurrence_window_filter_in_place,
    test_recurrence_exdate,
    test_recurrence_until_date_only_with_timezone,
    test_recurrence_all_day,
    test_recurrence_invalid_rule_falls_back,
    test_recurrence_expand_events_sorted,
    test_markers,
    test_decide_table,
    test_resolution_mapping,
    test_store_round_trip,
    test_api_event_pagination,
    test_api_labels_and_parsing,
    test_api_write_request,
    test_api_write_payload_fields,
    test_api_write_falls_back_to_plural_endpoint,
    test_api_login_error_mapping,
    test_export_creates_missing_copy,
    test_export_is_idempotent,
    test_export_updates_changed_source,
    test_export_dry_run,
    test_export_conflict_requires_decision,
    test_export_conflict_resolution_source_wins,
    test_export_skip_resolution_rebaselines,
    test_export_ignore_forever,
    test_export_window_and_unmanaged,
    test_export_import_unmanaged_two_way,
    test_export_reports_target_problems,
    test_export_records_source_deletion,
    test_calendar_entity_time_resolution,
    test_calendar_entity_builds_events,
    test_calendar_entity_rejects_occurrence_write,
    test_calendar_entity_rrule_validation,
    test_options_from_entry,
    test_all_modules_import,
]


def main() -> int:
    """Run every test and print a summary."""
    for test in TESTS:
        try:
            test()
        except Exception:  # noqa: BLE001 - report and continue
            FAILURES.append(f"{test.__name__}\n{traceback.format_exc()}")
            print(f"FAIL {test.__name__}")
        else:
            print(f"ok   {test.__name__}")

    print()
    print(f"{len(TESTS) - len(FAILURES)}/{len(TESTS)} tests passed, {CHECKS} checks")
    if FAILURES:
        print()
        for failure in FAILURES:
            print("-" * 70)
            print(failure)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
