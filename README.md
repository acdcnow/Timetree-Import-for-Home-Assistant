# TimeTree Calendar for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![Maintainer](https://img.shields.io/badge/maintainer-%40acdcnow-blue.svg)](https://github.com/acdcnow)
[![Version](https://img.shields.io/badge/version-2.0.3-blue.svg)](https://github.com/acdcnow/Timetree-Import-for-Home-Assistant/releases/tag/2.0.3)
[![License](https://img.shields.io/badge/license-GPL--3.0--only-blue.svg)](LICENSE)

<img src="ha_timetree.jpg" alt="TimeTree Calendar &amp; Scheduling — Home Assistant integration" width="900">

A custom component for Home Assistant that imports TimeTree calendars through the
official TimeTree web API. Every calendar of your account becomes a real Home
Assistant `calendar` entity, so you can display it, create, change and delete
events from Home Assistant — and optionally mirror everything into a second
calendar such as **Google Calendar**, with conflict handling that asks you
before something is lost.

## 📚 Documentation

| Document | Contents |
| :--- | :--- |
| 🏠 **[Documentation home](https://github.com/acdcnow/Timetree-Import-for-Home-Assistant/wiki)** | which document for which situation, capability matrix of 1.1.3 vs. 2.0.x |
| 📐 **[Architecture Design Document](https://github.com/acdcnow/Timetree-Import-for-Home-Assistant/wiki/Architecture-Design-Document)** | requirements, system context, components, 14 architectural decisions, risks |
| 🧩 **[Software Design Document](https://github.com/acdcnow/Timetree-Import-for-Home-Assistant/wiki/Software-Design-Document)** | module inventory, interface contracts, data model, error matrix, test harness, traceability |
| 🗺️ **[Workflow Diagrams](https://github.com/acdcnow/Timetree-Import-for-Home-Assistant/wiki/Workflow-Diagrams)** | GitDiagram repository map plus setup, polling, write, export and conflict flows |
| 🗄️ **[Design Documentation 1.1.3 (archived)](https://github.com/acdcnow/Timetree-Import-for-Home-Assistant/wiki/Archive-1.1.3-Design-Documentation)** | the previous architecture, plus the defect list that shaped 2.0.x |
| 📝 **[CHANGELOG](CHANGELOG.md)** | what changed in every release |

---

## ✨ Features

### Reading
* **All calendars of an account** — pick as many as you like during setup, each one
  becomes its own calendar entity (and its own device).
* **Complete event import**, including
  * recurring events (expanded into single occurrences for the calendar card,
    with `rrule`/`recurrence_id` preserved for clients),
  * `EXDATE`/`RDATE` exceptions, `COUNT`/`UNTIL` limits (lunar rules fall back to
    the stored occurrence instead of breaking the view),
  * multi day and single day all-day events (TimeTree stores an *inclusive* end,
    Home Assistant expects an *exclusive* one — both directions are converted),
  * events before 1970 (negative timestamps),
  * labels/colours, attendees and optional event comments,
  * `memos`, birthdays are skipped by default (option),
  * deleted events are dropped.
* **Chunked sync** — the `events/sync` cursor is followed until the last page, so
  large calendars are imported completely.
* Diagnostic sensors for the last successful sync, conflict count, last export and
  export status.

### Writing
* `calendar.create_event`, `calendar.update_event`/`calendar/event/update` and
  `calendar.delete_event`/`calendar/event/delete` are supported for every
  TimeTree calendar entity.
* The full browser payload is sent (label, attendees, attachment, alerts,
  recurrences), the CSRF token is scraped from the event editor page and the
  requests use a browser shaped TLS fingerprint through
  [`curl_cffi`](https://pypi.org/project/curl_cffi/) — the TimeTree API rejects
  plain `requests` clients with a generic `422`.
* When `curl_cffi` is unavailable the integration still works for reading and
  logs a warning; writes may then be refused by TimeTree's anti bot layer.

### Export / sync to another calendar
* Export to **any** Home Assistant calendar entity, e.g. the Google Calendar
  integration. TimeTree stays the source of truth.
* Exported copies carry an invisible marker (`[timetree:<uuid>]`) at the end of
  their description, which is how the pair is matched again later.
* Configurable loop interval (5–1440 min), export window (past/future days),
  `export_only` or `two_way`, dry-run mode, and optional import of events that
  only exist in the target calendar.
* **Attendee filter** — restrict the export to events that include at least one
  of the selected members (discovered from your calendars), with a switch for
  events that carry no attendee at all.
* Recurring events are exported as a series (`RRULE`), so Google keeps expanding
  them natively.

### Conflict management
A conflict is raised whenever a change cannot be applied without losing data.
Typical cases:

| Conflict | Meaning |
| --- | --- |
| `both_changed` | The TimeTree event **and** its exported copy changed |
| `target_changed` | Only the exported copy changed (export-only mode) |
| `target_removed` | The exported copy was deleted or moved out of the window |
| `source_removed` | The TimeTree event was deleted, but the copy changed |

Conflict policies: `manual` (default, queue it), `source_wins`, `target_wins`,
`newest_wins`. With `manual` nothing is written, a notification is created, the
`sensor.<calendar>_sync_conflicts` sensor lists the pending conflicts, the
`timetree_conflict_detected` event fires and you decide per conflict:

| Resolution | Effect |
| --- | --- |
| `use_timetree` | Write the TimeTree version to the export target (or delete the copy) |
| `use_export_target` | Write the copy back into TimeTree (or delete the TimeTree event) |
| `use_newest` | Pick the side with the newer modification timestamp |
| `skip` | Keep both versions and accept the divergence as the new baseline |
| `ignore_forever` | Exclude this event from synchronisation permanently |

---

## 📦 Installation

### HACS (recommended)
1. HACS → Integrations → ⋮ → *Custom repositories*.
2. Add `https://github.com/acdcnow/Timetree-Import-for-Home-Assistant`, category *Integration*.
3. Install **TimeTree Calendar**, restart Home Assistant.

### Manual
1. Download the latest release.
2. Copy `custom_components/timetree` into `/config/custom_components/`.
3. Restart Home Assistant.

> **Home Assistant 2026.9 or newer** is required. The integration ships its own
> brand images in `custom_components/timetree/brand/`, so no separate brand PR is
> needed.

---

## ⚙️ Configuration

1. *Settings → Devices & Services → + Add integration → TimeTree Calendar*.
2. Enter the e-mail address and password of your TimeTree account.
3. Select **all** calendars you want in Home Assistant.

Each calendar becomes a device with a calendar entity
(`calendar.<calendar name>`) and a *Last updated* diagnostic sensor.

### Changing settings
*Settings → Devices & Services → TimeTree Calendar → Configure* opens a menu:

| Menu | Options |
| --- | --- |
| **Calendars** | add/remove TimeTree calendars (entities are added/removed) |
| **Polling and events** | poll interval (5–1440 min), include birthdays, include comments, labels/attendees in the description |
| **Export to another calendar** | enable, target entity, interval, direction, window, attendee filter, delete/recreate behaviour, dry run, import unmanaged events |
| **Conflict handling** | policy and notifications |

---

## 🛠 Usage

### Creating, updating and deleting events

```yaml
service: calendar.create_event
target:
  entity_id: calendar.family
data:
  summary: "Family Dinner"
  description: "Pizza night!"
  location: "Home"
  start_date_time: "2026-12-31 18:00:00"
  end_date_time: "2026-12-31 20:00:00"
```

All-day events use `start_date`/`end_date` (Home Assistant's end date is
exclusive, TimeTree's is inclusive — the conversion happens automatically).

### Exporting to Google Calendar

```yaml
action:
  - service: timetree.export_now
    data:
      dry_run: true      # show what would happen first
```

A typical setup:

1. Add your Google calendar to Home Assistant (Google Calendar integration) and
   make sure the entity is *not* read-only.
2. *Configure → Export to another calendar*: enable the loop, pick
   `calendar.google`, choose an interval (e.g. every 60 minutes) and a window
   (e.g. 7 days back / 90 days forward).
3. Run `timetree.export_now` once with *dry run* enabled to see the result.

Export marks only touch the exported copies. Deleting an exported copy in the
target will create a `target_removed` conflict (or recreate it, if
*Recreate deleted exported copies* is enabled).

### Resolving conflicts

```yaml
# inspect
- service: timetree.list_conflicts
  response_variable: pending
# decide
- service: timetree.resolve_conflict
  data:
    conflict_id: "{{ pending.conflicts[0].conflict_id }}"
    action: use_timetree
```

Or apply one decision to everything:
`timetree.resolve_all_conflicts` with `action: skip`.

---

## 🔌 Services

| Service | Response | Description |
| --- | --- | --- |
| `timetree.refresh_now` | optional | poll TimeTree immediately (`config_entry_id` optional) |
| `timetree.export_now` | optional | run the export/sync now (`dry_run` supported) |
| `timetree.list_conflicts` | **only** | every pending conflict with both versions |
| `timetree.resolve_conflict` | optional | resolve one conflict (`conflict_id`, `action`) |
| `timetree.resolve_all_conflicts` | optional | apply `action` to every pending conflict |
| `timetree.get_event` | **only** | raw TimeTree details of one event (`uuid`) |

### Events on the Home Assistant event bus

| Event | Payload |
| --- | --- |
| `timetree_export_completed` | counters of the run (`created`, `updated`, `deleted`, `imported`, `conflicts`, `errors`, `dry_run`) |
| `timetree_conflict_detected` | `conflict_id`, `kind`, `summary`, `calendar_id`, `timetree_uuid`, `message` |

---

## 🐞 Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| `invalid_auth`, error code `-702` | TimeTree temporarily blocked the sign-in (too many attempts). Wait a few minutes and use *Re-authenticate*. |
| Writes fail with `422` / `failed to api request` | TimeTree's WAF rejected the request. Make sure `curl_cffi` was installed (check *Diagnostics → export → browser_transport*) and that the calendar has an `alias_code` (reload the integration). |
| Events missing in a month view | Fixed in 2.0.0: `RRULE:` prefixes are stripped before handing rules to Home Assistant. |
| All-day events show one day too long/short | Fixed in 2.0.0: TimeTree's inclusive end is converted to Home Assistant's exclusive end (and vice versa on write). |
| `create_event` fails with `combine() argument 1 must be datetime.date, not None` | Fixed in 2.0.0: the entity now reads the `dtstart`/`dtend` keys that Home Assistant actually sends. |
| `Could not reach the TimeTree API` | Check the internet connection; the integration uses `timetreeapp.com`. |
| Conflicts pile up | Set a policy that matches your workflow (`source_wins`) or resolve them in bulk. |

Verbose logging:

```yaml
logger:
  default: info
  logs:
    custom_components.timetree: debug
```

Diagnostics (*Settings → Devices & Services → TimeTree → Download diagnostics*)
contain the parsed options, per calendar statistics, the sync store state and the
pending conflicts — with credentials, e-mail and the password redacted.

---

## 🧩 Technical reference

The full design is documented in the wiki: the
[Architecture Design Document](https://github.com/acdcnow/Timetree-Import-for-Home-Assistant/wiki/Architecture-Design-Document)
(decisions and constraints), the
[Software Design Document](https://github.com/acdcnow/Timetree-Import-for-Home-Assistant/wiki/Software-Design-Document)
(modules, contracts, data model, verification) and the
[Workflow Diagrams](https://github.com/acdcnow/Timetree-Import-for-Home-Assistant/wiki/Workflow-Diagrams).

| Purpose | Endpoint |
| --- | --- |
| Login | `PUT /api/v1/auth/email/signin` (`_session_id` cookie) |
| Own user id | `GET /api/v1/user` |
| Calendars + members | `GET /api/v1/calendars?since=0` |
| Labels | `GET /api/v1/calendar/{id}/labels` |
| Events | `GET /api/v1/calendar/{id}/events/sync[?since=]` (chunked) |
| Comments | `GET /api/v1/calendar/{id}/event/{uuid}/activities?since=0` |
| Create | `POST /api/v1/calendar/{id}/event` |
| Update | `PUT /api/v1/calendar/{id}/event/{uuid}` |
| Delete | `DELETE /api/v1/calendar/{id}/event/{uuid}` |

Notes:

* `POST`/`PUT`/`DELETE` need a fresh `X-CSRF-Token`, which is scraped from
  `GET /calendars/{alias_code}/events/new`, plus browser shaped headers
  (`Origin`, `Referer`, `Sec-Fetch-*`) and a browser TLS fingerprint — hence
  `curl_cffi`. The event resource is **singular** (`/event`, not `/events`).
* TimeTree stores all-day events as UTC midnights with an **inclusive** end date;
  a single day event therefore has `start_at == end_at`.
* Update/delete use the singular resource first and fall back to the plural one,
  which keeps them working if TimeTree changes the route again.

---

## ⚠️ Limitations

* TimeTree is a cloud service: reading and writing require internet access and
  are subject to TimeTree's rate limits.
* A single occurrence of a recurring event cannot be edited or deleted through
  the API; TimeTree only knows whole series. Home Assistant gets a clear error in
  that case instead of silently touching the series.
* `use_newest` can only compare modification timestamps that both sides expose.
  TimeTree provides one, most calendar targets do not, so ambiguous cases still
  end up as a conflict.
* The importer for events that only exist in the target calendar
  (*Import unmanaged events*) is opt-in and matches new TimeTree events through
  the marker it writes back into the target copy.
* The integration uses TimeTree's internal API. A backend change can break it.

---

## 🏆 Credits

**Huge thanks to [eoleedi](https://github.com/eoleedi)** for
[TimeTree-Exporter](https://github.com/eoleedi/TimeTree-Exporter). Its
reverse-engineered API handling and data structures are the foundation of this
integration — without that project none of this would exist.

Also thanks to everyone who reported the issues that shaped 2.0.0
(`#1`–`#6`) and to [ratzepumml](https://github.com/ratzepumml) for the recurring
event analysis in `#7`.

## License

GNU General Public License v3.0 — see [LICENSE](LICENSE). This program comes
with absolutely no warranty.
