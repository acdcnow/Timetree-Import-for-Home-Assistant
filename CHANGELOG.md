# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/).

## [2.0.0-beta.2] – 2026-09-21

Documentation release. The integration code is unchanged from `2.0.0-beta.1`; this
release ships the restructured wiki and the updated project documentation so that the
released archive contains the same documents as the branch.

### Added

* Wiki restructure: a [documentation home](https://github.com/acdcnow/Timetree-Import-for-Home-Assistant/wiki)
  that routes between the current 2.0.x line and the archived 1.1.3 line, an
  [Architecture Design Document](https://github.com/acdcnow/Timetree-Import-for-Home-Assistant/wiki/Architecture-Design-Document)
  (14 decisions, requirements, deltas, risks), a
  [Software Design Document](https://github.com/acdcnow/Timetree-Import-for-Home-Assistant/wiki/Software-Design-Document)
  (modules, contracts, data model, error matrix, traceability) and
  [Workflow Diagrams](https://github.com/acdcnow/Timetree-Import-for-Home-Assistant/wiki/Workflow-Diagrams)
  (GitDiagram repository map plus the authored 2.0.x flows; the page also records the
  verified limitation that GitDiagram can only index the repository default branch).
* The former *Developer & Technical Reference Guide* is preserved as
  [archived design documentation](https://github.com/acdcnow/Timetree-Import-for-Home-Assistant/wiki/Archive-1.1.3-Design-Documentation)
  with a list of the defects fixed in 2.0.x.

### Changed

* README and `info.md` link the design documents; the manifest `documentation` key now
  points at the wiki.

## [2.0.0-beta.1] – 2026-09-19

Complete rework of the integration: multi calendar support, real write support,
an export loop to other calendars and conflict management.

### Added

* **Multiple calendars per account** — the setup flow lets you select any number
  of TimeTree calendars; each one becomes its own calendar entity and device,
  and the options flow can add or remove calendars later.
* **Write support** for `calendar.create_event`, `calendar.update_event` and
  `calendar.delete_event` (used by the WebSocket API, scripts and automations).
* **Export loop** (`custom_components/timetree/export.py`): mirrors TimeTree
  events into any calendar entity (for example a Google Calendar), with a
  selectable interval (5–1440 min), export window (past/future days),
  `export_only` or `two_way` direction, dry-run mode, optional deletion of
  copies of removed events, optional recreation of deleted copies and optional
  import of events that only exist in the target.
* **Export markers** — exported copies carry `[timetree:<uuid>]` in their
  description so they can be matched reliably on every run; the copy is created
  before a superseded version is removed, so a failed write never loses data.
* **Conflict management**: a persisted sync snapshot, a decision table
  (`conflicts.py`), a conflict policy (`manual`, `source_wins`, `target_wins`,
  `newest_wins`), pending conflicts in the `Sync conflicts` sensor, a persistent
  notification, the `timetree_conflict_detected` event and the services
  `timetree.list_conflicts`, `timetree.resolve_conflict` and
  `timetree.resolve_all_conflicts` with the resolutions `use_timetree`,
  `use_export_target`, `use_newest`, `skip` and `ignore_forever`.
* **New services**: `timetree.refresh_now`, `timetree.export_now`,
  `timetree.get_event` (plus the conflict services above).
* **New entities**: a calendar entity per TimeTree calendar, *Last updated*
  (per calendar), *Sync conflicts*, *Last export* and *Export status* sensors.
* **Recurrence expansion** (`recurrence.py`): recurring events are expanded into
  single occurrences for a queried range using `dateutil`, with support for
  `EXDATE`, `RDATE`, `COUNT`, `UNTIL` and all-day series. Non standard rules
  (lunar) fall back to the stored occurrence instead of breaking a view.
* **Richer event data**: labels (with colours), attendees, location coordinates,
  event URLs, optional event comments, memos; labels and attendees can be added
  to the description.
* **Diagnostics** with credentials, e-mail and passwords redacted.
* **Brand images** ship inside `custom_components/timetree/brand/` (icon, logo
  and dark logo, 1x and 2x).
* Events on the Home Assistant event bus: `timetree_export_completed` and
  `timetree_conflict_detected`.
* A local test harness (`tests/`) with 41 tests / 224 checks that runs without a
  Home Assistant installation.

### Fixed

* **Recurring events no longer break the calendar** (`#2`, `#7`): the `RRULE:`
  prefix is stripped before handing a rule to Home Assistant, rules are validated
  and occurrences are expanded per requested range, so month and week views load.
* **`calendar.create_event` works** (`#3`, `#5`): the entity reads the
  `dtstart`/`dtend` keys that Home Assistant core normalises the service data
  into (the old `start_date_time`/`start_date` keys never existed), accepts the
  WebSocket spelling as well and is covered by tests for both.
* **All-day events are no longer off by one** (`#5`) in either direction:
  TimeTree's inclusive end becomes Home Assistant's exclusive end on read, and
  the exclusive end becomes inclusive on write. All-day timestamps are written as
  UTC midnights, which is what the TimeTree web client does.
* **Writes are accepted by TimeTree** (`#5`): the singular
  `/api/v1/calendar/{id}/event` resource is used, the payload carries
  `label_id`, `attendees`, `attachment.virtual_user_attendees`, `recurrences`
  and `alerts`, a fresh `X-CSRF-Token` is scraped from the event editor page and
  browser shaped headers plus a browser TLS fingerprint (`curl_cffi`,
  `impersonate="firefox135"`) are used.
* **TimeTree API error handling**: `-702` (temporarily blocked login) and other
  rejections produce a readable message instead of a generic "unknown error";
  expired sessions are re-authenticated transparently.
* **Negative timestamps** (events before 1970) are converted with integer
  arithmetic instead of `datetime.fromtimestamp`, which raises on some platforms.
* **Complete event import**: the `events/sync` cursor is followed until `chunk`
  is `false` instead of stopping after the first page.
* **Home Assistant 2026.9 compatibility**: `entry.runtime_data` instead of
  `hass.data`, `ConfigFlowResult`, an `OptionsFlow` without a custom
  `__init__` taking the entry, device registry deprecations (`via_device_id`
  instead of `via_device`, `async_get_or_create` via `DeviceInfo`) and
  `AddConfigEntryEntitiesCallback`.
* **Manifest cleanup**: the unused `icalendar` requirement was dropped,
  `curl_cffi` was added, `integration_type`, `issue_tracker` and `loggers` were
  added and the minimum Home Assistant version is now explicit.

### Changed

* The integration is now a **hub style** integration: one config entry holds the
  account (e-mail/password) and the list of selected calendars.
* Options moved into a **menu based options flow** (calendars, polling/events,
  export, conflicts) instead of a single interval slider.
* Entities use the shared coordinator data model (`TimeTreeData`), device
  registry devices per calendar and a stable `unique_id` derived from the config
  entry and the TimeTree calendar id.
* The scan interval range was extended to 5–1440 minutes.
* Old 1.x config entries are picked up automatically (single calendar layout,
  legacy `calendar_id`/`calendar_name` keys).
* Documentation: the README was rewritten, `info.md` and this changelog were
  added.

### Removed

* The `icalendar` dependency (never used) and the duplicated parsing helpers.
* `hass.data[DOMAIN]` storage; everything lives on the config entry now.

### Upgrade notes

* Home Assistant **2026.9** is the new minimum version.
* `curl_cffi` is installed as a requirement of the integration. Reading works
  without it, writing may be rejected by TimeTree's anti bot layer.
* Nothing is written to a second calendar until the export loop is enabled in the
  options; the default is *disabled* with the `manual` conflict policy.

## Earlier releases

* **1.1.3** – last release of the 1.x series (single calendar, read plus create,
  polling interval slider).
* **1.0.1** – earlier 1.x release.
