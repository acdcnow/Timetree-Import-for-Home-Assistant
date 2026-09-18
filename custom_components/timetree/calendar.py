"""Calendar platform for TimeTree."""
from datetime import datetime, date
import logging
from zoneinfo import ZoneInfo

from homeassistant.components.calendar import (
    CalendarEntity,
    CalendarEvent,
    CalendarEntityFeature
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .const import DOMAIN, CONF_CALENDAR_ID, CONF_CALENDAR_NAME
from .coordinator import TimeTreeCoordinator

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, entry, async_add_entities):
    """Set up the calendar entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]

    entity = TimeTreeCalendarEntity(
        coordinator,
        entry.data[CONF_CALENDAR_NAME]
    )
    async_add_entities([entity])


class TimeTreeCalendarEntity(CalendarEntity):
    """Representation of a TimeTree Calendar."""

    _attr_has_entity_name = True
    _attr_supported_features = CalendarEntityFeature.CREATE_EVENT

    def __init__(self, coordinator: TimeTreeCoordinator, name: str):
        """Initialize the entity."""
        self.coordinator = coordinator
        self._attr_name = name
        self._attr_unique_id = f"{coordinator.calendar_id}"

    @property
    def event(self):
        """Return the next upcoming event."""
        now = dt_util.now()
        events = self.coordinator.data or []

        future_events = []
        for e in events:
            if e["all_day"]:
                end_val = e["end"]
                if isinstance(end_val, datetime):
                    end_val = end_val.date()
                if end_val >= now.date():
                    future_events.append(e)
            else:
                if e["end"] > now:
                    future_events.append(e)

        if not future_events:
            return None

        def sort_key(x):
            start = x["start"]
            if isinstance(start, datetime):
                return start
            return dt_util.start_of_local_day(
                datetime.combine(start, datetime.min.time())
            )

        next_event = min(future_events, key=sort_key)
        return self._build_calendar_event(next_event)

    async def async_get_events(self, hass, start_date, end_date):
        """Return calendar events within a range, expanding recurring events."""
        if self.coordinator.data is None:
            await self.coordinator.async_request_refresh()

        from datetime import timedelta
        from dateutil.rrule import rrulestr

        events = []
        for event_data in self.coordinator.data or []:
            ev_start = event_data["start"]
            ev_end = event_data["end"]
            is_all_day = event_data["all_day"]
            recurrences = event_data.get("recurrences")

            rrule_line = None
            exdate_strs = set()
            if recurrences:
                for line in recurrences:
                    if line.upper().startswith("RRULE:"):
                        rrule_line = line[len("RRULE:"):]
                    elif line.upper().startswith("EXDATE"):
                        _, _, values = line.partition(":")
                        for v in values.split(","):
                            v = v.strip()
                            if v:
                                exdate_strs.add(v[:8])

            if rrule_line:
                try:
                    if is_all_day:
                        dtstart = datetime.combine(ev_start, datetime.min.time())
                        duration = datetime.combine(ev_end, datetime.min.time()) - dtstart
                        # A single-day all-day event has start == end (zero duration) in the
                        # raw TimeTree data. For the overlap check below it must span at
                        # least one full day, otherwise it is incorrectly dropped whenever a
                        # query window starts exactly on that day (e.g. "today's events").
                        if duration < timedelta(days=1):
                            duration = timedelta(days=1)
                        window_start = datetime.combine(start_date.date(), datetime.min.time())
                        window_end = datetime.combine(end_date.date(), datetime.min.time())
                    else:
                        dtstart = ev_start
                        duration = ev_end - ev_start
                        window_start = start_date
                        window_end = end_date

                    # RRULE UNTIL values in TimeTree feeds are often naive (local time,
                    # no 'Z'), while DTSTART is timezone-aware. dateutil then requires
                    # UNTIL to be in UTC and raises. Fix: strip the timezone for the
                    # expansion math and re-attach it afterwards.
                    tzinfo = dtstart.tzinfo
                    dtstart_naive = dtstart.replace(tzinfo=None) if tzinfo else dtstart
                    window_start_naive = window_start.replace(tzinfo=None) if window_start.tzinfo else window_start
                    window_end_naive = window_end.replace(tzinfo=None) if window_end.tzinfo else window_end

                    rule = rrulestr(rrule_line, dtstart=dtstart_naive)
                    for occ_start_naive in rule.between(window_start_naive - duration, window_end_naive, inc=True):
                        if occ_start_naive.strftime("%Y%m%d") in exdate_strs:
                            continue
                        occ_start = occ_start_naive.replace(tzinfo=tzinfo) if tzinfo else occ_start_naive
                        occ_end = occ_start + duration
                        if not (occ_start < window_end and occ_end > window_start):
                            continue
                        occ_event = dict(event_data)
                        occ_event["start"] = occ_start.date() if is_all_day else occ_start
                        occ_event["end"] = occ_end.date() if is_all_day else occ_end
                        events.append(self._build_calendar_event(occ_event))
                    continue
                except Exception as err:
                    _LOGGER.warning(
                        "TimeTree: could not process recurrence rule for '%s': %s",
                        event_data.get("summary"), err
                    )

            if is_all_day:
                effective_end = ev_end if ev_end > ev_start else ev_start + timedelta(days=1)
                if ev_start < end_date.date() and effective_end > start_date.date():
                    events.append(self._build_calendar_event(event_data))
            else:
                if ev_start < end_date and ev_end > start_date:
                    events.append(self._build_calendar_event(event_data))

        return events

    async def async_create_event(self, **kwargs):
        """Add a new event to the calendar."""
        summary = kwargs.get("summary", "New Event")
        description = kwargs.get("description", "")
        location = kwargs.get("location", "")
        start_dt = kwargs.get("start_date_time")
        end_dt = kwargs.get("end_date_time")

        if not start_dt:
            start_date = kwargs.get("start_date")
            end_date = kwargs.get("end_date")
            all_day = True
            dt_start = datetime.combine(start_date, datetime.min.time()).replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
            dt_end = datetime.combine(end_date, datetime.min.time()).replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
        else:
            all_day = False
            dt_start = start_dt
            dt_end = end_dt

        start_ms = int(dt_start.timestamp() * 1000)
        end_ms = int(dt_end.timestamp() * 1000)

        event_payload = {
            "summary": summary,
            "description": description,
            "location": location,
            "all_day": all_day,
            "start_at": start_ms,
            "end_at": end_ms,
            "timezone": str(dt_util.DEFAULT_TIME_ZONE)
        }

        try:
            await self.coordinator.api.async_create_event(self.coordinator.calendar_id, event_payload)
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("Error creating event: %s", err)
            # This raises a visible error in the HA UI
            raise HomeAssistantError(f"TimeTree API Failed: {err}") from err

    def _build_calendar_event(self, event_data):
        # TimeTree returns recurrence rules with an "RRULE:" prefix (e.g. "RRULE:FREQ=YEARLY"),
        # but HomeAssistant's CalendarEvent.rrule expects the bare rule without that prefix
        # (e.g. "FREQ=YEARLY"). Passing the raw string through raised
        # "HomeAssistantError: rrule did not contain FREQ" and crashed the entity on setup.
        rrule = None
        recurrences = event_data.get("recurrences")
        if recurrences:
            candidate = recurrences[0]
            if candidate and candidate.upper().startswith("RRULE:"):
                candidate = candidate[len("RRULE:"):]
            if candidate and "FREQ" in candidate:
                rrule = candidate
        return CalendarEvent(
            summary=event_data["summary"],
            start=event_data["start"],
            end=event_data["end"],
            location=event_data["location"],
            description=event_data["description"],
            uid=event_data["uid"],
            rrule=rrule
        )
