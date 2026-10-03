"""Constants for the TimeTree integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "timetree"
NAME: Final = "TimeTree Calendar"
MANUFACTURER: Final = "TimeTree"
ATTRIBUTION: Final = "Data provided by TimeTree"
CONFIGURATION_URL: Final = "https://timetreeapp.com"

# --- TimeTree internal (web client) API -------------------------------------
API_BASE_URI: Final = "https://timetreeapp.com/api/v1"
API_WEB_ORIGIN: Final = "https://timetreeapp.com"
API_USER_AGENT: Final = "web/2.1.0/en"
API_TIMEOUT: Final = 30
# The write endpoints are behind a WAF that fingerprints the TLS client hello,
# so plain ``requests`` is rejected with a generic 422. ``curl_cffi`` can
# impersonate a real browser and is used for every request when available.
IMPERSONATE: Final = "firefox135"
CSRF_PATH: Final = "/calendars/{alias_code}/events/new"
DEFAULT_LABEL_ID: Final = 1

# --- config / options keys ---------------------------------------------------
CONF_CALENDARS: Final = "calendars"
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_INCLUDE_BIRTHDAYS: Final = "include_birthdays"
CONF_INCLUDE_COMMENTS: Final = "include_comments"
CONF_DESCRIPTION_DETAILS: Final = "description_details"
CONF_CALENDAR_ID: Final = "calendar_id"
CONF_CALENDAR_NAME: Final = "calendar_name"
CONF_ALIAS_CODE: Final = "alias_code"

CONF_EXPORT_ENABLED: Final = "export_enabled"
CONF_EXPORT_TARGET: Final = "export_target"
CONF_EXPORT_INTERVAL: Final = "export_interval"
CONF_EXPORT_DIRECTION: Final = "export_direction"
CONF_EXPORT_PAST_DAYS: Final = "export_past_days"
CONF_EXPORT_FUTURE_DAYS: Final = "export_future_days"
CONF_EXPORT_DELETE_REMOVED: Final = "export_delete_removed"
CONF_EXPORT_RECREATE_REMOVED: Final = "export_recreate_removed"
CONF_EXPORT_DRY_RUN: Final = "export_dry_run"
CONF_IMPORT_UNMANAGED: Final = "import_unmanaged"
CONF_CONFLICT_POLICY: Final = "conflict_policy"
CONF_NOTIFY_CONFLICTS: Final = "notify_conflicts"

# --- defaults ---------------------------------------------------------------
DEFAULT_SCAN_INTERVAL: Final = 60
MIN_SCAN_INTERVAL: Final = 5
MAX_SCAN_INTERVAL: Final = 1440

DEFAULT_EXPORT_INTERVAL: Final = 60
MIN_EXPORT_INTERVAL: Final = 5
MAX_EXPORT_INTERVAL: Final = 1440
DEFAULT_EXPORT_PAST_DAYS: Final = 7
DEFAULT_EXPORT_FUTURE_DAYS: Final = 90
MIN_EXPORT_DAYS: Final = 0
MAX_EXPORT_DAYS: Final = 730

DIRECTION_EXPORT_ONLY: Final = "export_only"
DIRECTION_TWO_WAY: Final = "two_way"
DIRECTIONS: Final = [DIRECTION_EXPORT_ONLY, DIRECTION_TWO_WAY]

POLICY_MANUAL: Final = "manual"
POLICY_SOURCE_WINS: Final = "source_wins"
POLICY_TARGET_WINS: Final = "target_wins"
POLICY_NEWEST_WINS: Final = "newest_wins"
CONFLICT_POLICIES: Final = [
    POLICY_MANUAL,
    POLICY_SOURCE_WINS,
    POLICY_TARGET_WINS,
    POLICY_NEWEST_WINS,
]

# --- services ---------------------------------------------------------------
SERVICE_EXPORT_NOW: Final = "export_now"
SERVICE_REFRESH_NOW: Final = "refresh_now"
SERVICE_LIST_CONFLICTS: Final = "list_conflicts"
SERVICE_RESOLVE_CONFLICT: Final = "resolve_conflict"
SERVICE_RESOLVE_ALL_CONFLICTS: Final = "resolve_all_conflicts"
SERVICE_GET_EVENT: Final = "get_event"

ATTR_CALENDAR_ID: Final = "calendar_id"
ATTR_CONFLICT_ID: Final = "conflict_id"
ATTR_ACTION: Final = "action"
ATTR_DRY_RUN: Final = "dry_run"
ATTR_ENTRY_ID: Final = "config_entry_id"
ATTR_TARGET: Final = "target"

# resolutions accepted by the resolve services
RESOLUTION_USE_SOURCE: Final = "use_timetree"
RESOLUTION_USE_TARGET: Final = "use_export_target"
RESOLUTION_USE_NEWEST: Final = "use_newest"
RESOLUTION_SKIP: Final = "skip"
RESOLUTION_IGNORE: Final = "ignore_forever"
RESOLUTIONS: Final = [
    RESOLUTION_USE_SOURCE,
    RESOLUTION_USE_TARGET,
    RESOLUTION_USE_NEWEST,
    RESOLUTION_SKIP,
    RESOLUTION_IGNORE,
]

# --- events fired on the HA event bus --------------------------------------
EVENT_EXPORT_COMPLETED: Final = f"{DOMAIN}_export_completed"
EVENT_CONFLICT_DETECTED: Final = f"{DOMAIN}_conflict_detected"

# --- dispatcher signals -----------------------------------------------------
SIGNAL_STORE_UPDATED: Final = f"{DOMAIN}_store_updated"

# --- misc -------------------------------------------------------------------
LOGGER_NAME: Final = f"custom_components.{DOMAIN}"
STORAGE_VERSION: Final = 1
MARKER_PREFIX: Final = "[timetree:"
MARKER_SUFFIX: Final = "]"
EXPORT_MARKER: Final = f"{MARKER_PREFIX}{{uuid}}{MARKER_SUFFIX}"
