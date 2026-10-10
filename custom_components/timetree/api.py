"""TimeTree API client.

The integration talks to the internal TimeTree web API:

* ``PUT  /api/v1/auth/email/signin``                     – login (``_session_id`` cookie)
* ``GET  /api/v1/user``                                  – own numeric user id
* ``GET  /api/v1/calendars?since=0``                     – calendars + members
* ``GET  /api/v1/calendar/{id}/labels``                  – labels / colours
* ``GET  /api/v1/calendar/{id}/events/sync[?since=]``    – events (chunked)
* ``GET  /api/v1/calendar/{id}/event/{uuid}/activities`` – event comments
* ``POST /api/v1/calendar/{id}/event``                   – create event
* ``PUT  /api/v1/calendar/{id}/event/{uuid}``            – update event
* ``DELETE /api/v1/calendar/{id}/event/{uuid}``          – delete event

Write requests are fronted by a WAF that fingerprints the TLS client hello, so
the transport prefers ``curl_cffi`` with a browser impersonation profile and
falls back to plain ``requests`` (reads work either way).
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
import uuid as uuid_lib
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import partial
from typing import Any
from collections.abc import Mapping, Sequence

# The transport libraries are imported at module level on purpose. Home Assistant
# loads integration modules in the executor, while a deferred import inside a
# coroutine runs in the event loop - and ``curl_cffi`` reads package metadata
# through ``importlib.metadata``, which scans ``site-packages`` (a blocking
# ``listdir`` that trips HA's blocking call detector).
try:  # pragma: no cover - depends on the installed wheels
    from curl_cffi import requests as curl_requests
except Exception:  # noqa: BLE001 - optional native dependency
    curl_requests = None

try:  # pragma: no cover - ``requests`` ships with Home Assistant
    import requests as plain_requests
except Exception:  # noqa: BLE001 - optional dependency
    plain_requests = None

from .const import (
    API_BASE_URI,
    API_TIMEOUT,
    API_USER_AGENT,
    API_WEB_ORIGIN,
    CSRF_PATH,
    DEFAULT_LABEL_ID,
    IMPERSONATE,
)
from .models import (
    TimeTreeCalendar,
    TimeTreeLabel,
    TimeTreeEvent,
    filter_events,
    parse_event_list,
    to_int,
)

_LOGGER = logging.getLogger(__name__)

_CSRF_RE = re.compile(
    r"""<meta[^>]*name=["']csrf-token["'][^>]*content=["']([^"']+)["']""",
    re.IGNORECASE,
)
_CSRF_RE_ALT = re.compile(
    r"""<meta[^>]*content=["']([^"']+)["'][^>]*name=["']csrf-token["']""",
    re.IGNORECASE,
)
MAX_COMMENT_WORKERS = 4


class TimeTreeError(Exception):
    """Base error for the TimeTree client."""


class TimeTreeAuthError(TimeTreeError):
    """Raised when authentication fails or is rejected."""


class TimeTreeApiError(TimeTreeError):
    """Raised when the TimeTree API returns an error."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        body: str | None = None,
    ) -> None:
        """Initialize the error."""
        super().__init__(message)
        self.status = status
        self.body = body


class TimeTreeWriteError(TimeTreeApiError):
    """Raised when a write request is rejected by TimeTree."""


def create_transport(
    impersonate: str = IMPERSONATE,
) -> tuple[Any, bool]:
    """Return a HTTP session, preferring a browser shaped TLS stack.

    Synchronous on purpose: the caller runs it in the executor. Do not import
    the transport libraries here - see the note on the module level imports.
    """
    if curl_requests is not None:
        try:
            return curl_requests.Session(impersonate=impersonate), True
        except Exception:  # noqa: BLE001 - optional native dependency
            _LOGGER.debug(
                "curl_cffi is installed but no session could be created, "
                "falling back to requests",
                exc_info=True,
            )
    if plain_requests is None:
        raise TimeTreeError(
            "No HTTP transport available, install curl_cffi or requests"
        )
    return plain_requests.Session(), False


class TimeTreeApi:
    """Client for the TimeTree internal API."""

    def __init__(
        self,
        hass: Any,
        email: str,
        password: str,
        *,
        impersonate: str = IMPERSONATE,
    ) -> None:
        """Initialize the client."""
        self._hass = hass
        self._email = email
        self._password = password
        self._impersonate = impersonate
        self._session, self.browser_transport = create_transport(impersonate)
        self._session_id: str | None = None
        self._csrf_token: str | None = None
        self._csrf_alias: str | None = None
        self._user_id: int | None = None
        self._lock = threading.Lock()
        self._calendar_labels: dict[str, dict[int, TimeTreeLabel]] = {}

    @classmethod
    async def async_create(
        cls,
        hass: Any,
        email: str,
        password: str,
        impersonate: str = IMPERSONATE,
    ) -> TimeTreeApi:
        """Create a client without blocking the event loop.

        Building the session loads a native TLS library, so the constructor has
        to run in the executor as well. ``impersonate`` is keyword only, hence
        the partial (``async_add_executor_job`` forwards positional args only).
        """
        return await hass.async_add_executor_job(
            partial(cls, hass, email, password, impersonate=impersonate)
        )

    # ------------------------------------------------------------------
    # transport helpers (blocking)
    # ------------------------------------------------------------------
    @property
    def session_id(self) -> str | None:
        """Return the current session id (if logged in)."""
        return self._session_id

    def _log_headers(self) -> dict[str, str]:
        """Return the headers used for the internal ``/api`` endpoints."""
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Timetreea": API_USER_AGENT,
        }

    def _browser_headers(
        self,
        alias_code: str | None,
        *,
        with_csrf: bool = True,
    ) -> dict[str, str]:
        """Return the browser shaped headers the write endpoints expect."""
        headers = {
            **self._log_headers(),
            "Origin": API_WEB_ORIGIN,
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }
        if alias_code:
            headers["Referer"] = (
                f"{API_WEB_ORIGIN}{CSRF_PATH.format(alias_code=alias_code)}"
            )
        if with_csrf and self._csrf_token:
            headers["X-CSRF-Token"] = self._csrf_token
        return headers

    def _login(self) -> None:
        """Log in and store the session cookie."""
        url = f"{API_BASE_URI}/auth/email/signin"
        payload = {
            "uid": self._email,
            "password": self._password,
            "uuid": uuid_lib.uuid4().hex,
        }
        _LOGGER.debug("Logging in to TimeTree")
        try:
            response = self._session.put(
                url,
                json=payload,
                headers=self._browser_headers(None, with_csrf=False),
                timeout=API_TIMEOUT,
            )
        except Exception as err:  # noqa: BLE001 - transport specific
            raise TimeTreeAuthError(f"Connection error: {err}") from err

        if response.status_code != 200:
            body = response.text or ""
            _LOGGER.error(
                "TimeTree login failed (status %s): %s",
                response.status_code,
                body[:400],
            )
            if "-702" in body:
                raise TimeTreeAuthError(
                    "TimeTree temporarily rejected the sign in (code -702). "
                    "Wait a few minutes before trying again."
                )
            raise TimeTreeAuthError("Invalid credentials or blocked login")

        session_id = response.cookies.get("_session_id")
        if not session_id:
            raise TimeTreeAuthError("Login succeeded but no session id was returned")
        self._session_id = session_id
        try:
            self._session.cookies.set("_session_id", session_id)
        except Exception:  # noqa: BLE001 - cookie jar may already contain it
            pass
        self._user_id = None
        _LOGGER.debug("TimeTree login successful")

    def _ensure_login(self) -> None:
        """Make sure a session id is available."""
        with self._lock:
            if not self._session_id:
                self._login()

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        expect: Sequence[int] = (200,),
        retry_on_401: bool = True,
    ) -> Any:
        """Perform an authenticated request and return the parsed body."""
        self._ensure_login()
        url = path if path.startswith("http") else f"{API_BASE_URI}{path}"
        try:
            response = self._session.request(
                method,
                url,
                json=dict(json) if json is not None else None,
                params=dict(params) if params else None,
                headers=dict(headers or self._log_headers()),
                timeout=API_TIMEOUT,
            )
        except Exception as err:  # noqa: BLE001 - transport specific
            raise TimeTreeApiError(f"Request to {url} failed: {err}") from err

        if response.status_code == 401 and retry_on_401:
            _LOGGER.debug("TimeTree session expired, logging in again")
            self._session_id = None
            self._ensure_login()
            return self._request(
                method,
                path,
                json=json,
                params=params,
                headers=headers,
                expect=expect,
                retry_on_401=False,
            )

        if response.status_code not in expect:
            raise TimeTreeApiError(
                f"{method} {url} returned {response.status_code}",
                status=response.status_code,
                body=(response.text or "")[:1000],
            )

        if response.status_code == 204 or not (response.text or "").strip():
            return {}
        try:
            return response.json()
        except Exception as err:  # noqa: BLE001 - unexpected body
            raise TimeTreeApiError(
                f"{method} {url} returned an invalid JSON body",
                status=response.status_code,
                body=(response.text or "")[:500],
            ) from err

    # ------------------------------------------------------------------
    # read endpoints (blocking)
    # ------------------------------------------------------------------
    def _get_calendars(self) -> list[TimeTreeCalendar]:
        """Return the calendars of the account."""
        data = self._request("GET", "/calendars", params={"since": 0})
        calendars = []
        for item in data.get("calendars") or []:
            if item.get("deactivated_at") is not None:
                continue
            calendars.append(TimeTreeCalendar.from_api(item))
        return calendars

    def _get_user_id(self) -> int | None:
        """Return the numeric user id of the logged in account."""
        data = self._request("GET", "/user")
        user = data.get("user") if isinstance(data, Mapping) else None
        if isinstance(user, Mapping):
            return to_int(user.get("id"))
        return None

    def _get_labels(self, calendar_id: str) -> dict[int, TimeTreeLabel]:
        """Return the labels of a calendar (cached)."""
        cached = self._calendar_labels.get(calendar_id)
        if cached is not None:
            return cached
        try:
            data = self._request("GET", f"/calendar/{calendar_id}/labels")
        except TimeTreeApiError as err:
            _LOGGER.debug("Could not load labels for %s: %s", calendar_id, err)
            return {}
        labels: dict[int, TimeTreeLabel] = {}
        for item in data.get("calendar_labels") or []:
            if not isinstance(item, Mapping):
                continue
            label = TimeTreeLabel.from_api(item)
            if label.label_id:
                labels[label.label_id] = label
        self._calendar_labels[calendar_id] = labels
        return labels

    def _get_events_page(
        self, calendar_id: str, since: int | None = None
    ) -> Mapping[str, Any]:
        """Fetch one page of the event sync endpoint."""
        params = {"since": since} if since is not None else None
        return self._request("GET", f"/calendar/{calendar_id}/events/sync", params=params)

    def _get_events(self, calendar_id: str, since: int = 0) -> list[Mapping[str, Any]]:
        """Fetch all events of a calendar following the chunk cursor."""
        if since:
            payload = self._get_events_page(calendar_id, since)
        else:
            payload = self._get_events_page(calendar_id)
        events: list[Mapping[str, Any]] = list(payload.get("events") or [])
        pages = 1
        cursor = payload.get("since")
        while payload.get("chunk") is True and cursor is not None:
            if pages >= 100:
                _LOGGER.warning("Aborting TimeTree sync after 100 pages")
                break
            payload = self._get_events_page(calendar_id, to_int(cursor))
            events.extend(payload.get("events") or [])
            cursor = payload.get("since")
            pages += 1
        _LOGGER.debug("Fetched %s events from %s pages", len(events), pages)
        return events

    def _get_activities(self, calendar_id: str, event_uuid: str) -> list[str]:
        """Return the comments of an event."""
        comments: list[str] = []
        since = 0
        for _ in range(10):
            try:
                payload = self._request(
                    "GET",
                    f"/calendar/{calendar_id}/event/{event_uuid}/activities",
                    params={"since": since},
                )
            except TimeTreeApiError as err:
                _LOGGER.debug("No activities for %s: %s", event_uuid, err)
                return comments
            for activity in payload.get("activities") or payload.get(
                "event_activities"
            ) or []:
                comment = self._extract_comment(activity)
                if comment:
                    comments.append(comment)
            if payload.get("chunk") is not True or payload.get("since") is None:
                break
            since = to_int(payload.get("since")) or 0
        return comments

    @staticmethod
    def _extract_comment(activity: Mapping[str, Any]) -> str | None:
        """Return the text of an activity payload."""
        comment = activity.get("comment")
        if isinstance(comment, str):
            return comment
        if isinstance(comment, Mapping):
            for key in ("body", "text", "content", "message"):
                if comment.get(key):
                    return str(comment[key])
        attachment = activity.get("attachment")
        if isinstance(attachment, Mapping) and attachment.get("content"):
            return str(attachment["content"])
        for key in ("body", "text", "content", "message"):
            if activity.get(key):
                return str(activity[key])
        return None

    def _attach_comments(
        self, calendar_id: str, events: list[TimeTreeEvent]
    ) -> None:
        """Attach event comments in parallel (opt-in feature)."""
        with ThreadPoolExecutor(max_workers=MAX_COMMENT_WORKERS) as executor:
            futures = {
                executor.submit(self._get_activities, calendar_id, event.uuid): event
                for event in events
            }
            for future in as_completed(futures):
                event = futures[future]
                try:
                    event.comments = future.result()
                except Exception as err:  # noqa: BLE001 - never fail a refresh
                    _LOGGER.debug("Comments for %s failed: %s", event.uuid, err)

    # ------------------------------------------------------------------
    # write endpoints (blocking)
    # ------------------------------------------------------------------
    def _fetch_csrf_token(self, alias_code: str | None) -> str | None:
        """Scrape a CSRF token from the event editor page."""
        if not alias_code:
            return None
        url = f"{API_WEB_ORIGIN}{CSRF_PATH.format(alias_code=alias_code)}"
        try:
            response = self._session.get(
                url,
                headers={
                    "X-Timetreea": API_USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml",
                },
                timeout=API_TIMEOUT,
            )
        except Exception as err:  # noqa: BLE001 - transport specific
            _LOGGER.debug("Could not load %s: %s", url, err)
            return None
        if response.status_code != 200:
            _LOGGER.debug("CSRF page returned %s", response.status_code)
            return None
        html = response.text or ""
        match = _CSRF_RE.search(html) or _CSRF_RE_ALT.search(html)
        if match:
            return match.group(1)
        _LOGGER.debug("No csrf-token meta tag found on %s", url)
        return None

    def _csrf(self, alias_code: str | None, *, force: bool = False) -> str | None:
        """Return a (cached) CSRF token for a calendar."""
        if force or self._csrf_token is None or self._csrf_alias != alias_code:
            self._csrf_token = self._fetch_csrf_token(alias_code)
            self._csrf_alias = alias_code
        return self._csrf_token

    def _event_paths(
        self, calendar_id: str, event_uuid: str | None = None
    ) -> list[str]:
        """Return the candidate paths for a single event resource."""
        if event_uuid is None:
            return [f"/calendar/{calendar_id}/event"]
        return [
            f"/calendar/{calendar_id}/event/{event_uuid}",
            f"/calendar/{calendar_id}/events/{event_uuid}",
        ]

    def _prepare_attendees(self, payload: dict[str, Any]) -> dict[str, Any]:
        """TimeTree does not add the creator as attendee server side."""
        attendees = payload.get("attendees")
        if not attendees:
            if self._user_id is None:
                try:
                    self._user_id = self._get_user_id()
                except TimeTreeApiError as err:
                    _LOGGER.debug("Could not read the TimeTree user id: %s", err)
            if self._user_id is not None:
                payload["attendees"] = [self._user_id]
        payload.setdefault("label_id", DEFAULT_LABEL_ID)
        payload.setdefault("attachment", {"virtual_user_attendees": []})
        payload.setdefault("recurrences", [])
        payload.setdefault("alerts", [])
        payload.setdefault("category", 1)
        return payload

    def _write(
        self,
        method: str,
        calendar: TimeTreeCalendar,
        payload: dict[str, Any] | None,
        *,
        event_uuid: str | None = None,
    ) -> Any:
        """Perform a write request, retrying with a fresh CSRF token."""
        if not self.browser_transport:
            _LOGGER.warning(
                "curl_cffi is not available; TimeTree write requests may be "
                "rejected by the anti bot layer"
            )
        last_error: TimeTreeApiError | None = None
        for attempt in (0, 1):
            if attempt == 1:
                # The token is not single use, but it is bound to the session
                # and can go stale, so refresh it once before giving up.
                self._csrf(calendar.alias_code, force=True)
            headers = self._browser_headers(calendar.alias_code)
            for path in self._event_paths(calendar.calendar_id, event_uuid):
                try:
                    return self._request(
                        method,
                        path,
                        json=payload,
                        headers=headers,
                        expect=(200, 201, 204),
                    )
                except TimeTreeApiError as err:
                    last_error = err
                    if err.status in (404, 405):
                        continue
                    break
        raise TimeTreeWriteError(
            f"TimeTree rejected the {method} request"
            + (f" ({last_error.status})" if last_error and last_error.status else ""),
            status=last_error.status if last_error else None,
            body=last_error.body if last_error else None,
        )

    def _create_event(
        self, calendar: TimeTreeCalendar, payload: dict[str, Any]
    ) -> Any:
        """Create an event (``payload`` is built by ``build_event_payload``)."""
        self._csrf(calendar.alias_code)
        return self._write("POST", calendar, self._prepare_attendees(dict(payload)))

    def _update_event(
        self, calendar: TimeTreeCalendar, event_uuid: str, payload: dict[str, Any]
    ) -> Any:
        """Update an event."""
        self._csrf(calendar.alias_code)
        return self._write(
            "PUT",
            calendar,
            self._prepare_attendees(dict(payload)),
            event_uuid=event_uuid,
        )

    def _delete_event(self, calendar: TimeTreeCalendar, event_uuid: str) -> Any:
        """Delete an event."""
        self._csrf(calendar.alias_code)
        return self._write("DELETE", calendar, None, event_uuid=event_uuid)

    # ------------------------------------------------------------------
    # async public API
    # ------------------------------------------------------------------
    async def async_validate(self) -> list[TimeTreeCalendar]:
        """Validate the credentials and return the available calendars."""
        return await self._hass.async_add_executor_job(self._get_calendars)

    async def async_get_calendars(self) -> list[TimeTreeCalendar]:
        """Return the calendars of the account."""
        return await self._hass.async_add_executor_job(self._get_calendars)

    async def async_get_user_id(self) -> int | None:
        """Return the numeric TimeTree user id (used for attendees)."""
        return await self._hass.async_add_executor_job(self._get_user_id)

    async def async_fetch_events(
        self,
        calendar: TimeTreeCalendar,
        *,
        since: int = 0,
        include_comments: bool = False,
        include_birthdays: bool = False,
    ) -> list[TimeTreeEvent]:
        """Fetch and parse all events of a calendar."""
        raw_events, labels = await asyncio.gather(
            self._hass.async_add_executor_job(
                self._get_events, calendar.calendar_id, since
            ),
            self._hass.async_add_executor_job(self._get_labels, calendar.calendar_id),
        )
        calendar.labels.update(labels)
        events = parse_event_list(
            {"events": raw_events}, calendar.calendar_id, calendar=calendar
        )
        events = filter_events(events, include_birthdays=include_birthdays)
        if include_comments:
            await self._hass.async_add_executor_job(
                self._attach_comments, calendar.calendar_id, events
            )
        return events

    async def async_create_event(
        self, calendar: TimeTreeCalendar, payload: dict[str, Any]
    ) -> Any:
        """Create an event in TimeTree."""
        return await self._hass.async_add_executor_job(
            self._create_event, calendar, payload
        )

    async def async_update_event(
        self, calendar: TimeTreeCalendar, event_uuid: str, payload: dict[str, Any]
    ) -> Any:
        """Update an event in TimeTree."""
        return await self._hass.async_add_executor_job(
            self._update_event, calendar, event_uuid, payload
        )

    async def async_delete_event(
        self, calendar: TimeTreeCalendar, event_uuid: str
    ) -> None:
        """Delete an event in TimeTree."""
        await self._hass.async_add_executor_job(
            self._delete_event, calendar, event_uuid
        )

    async def async_close(self) -> None:
        """Close the underlying session."""

        def _close() -> None:
            try:
                self._session.close()
            except Exception:  # noqa: BLE001 - best effort
                pass

        await self._hass.async_add_executor_job(_close)
