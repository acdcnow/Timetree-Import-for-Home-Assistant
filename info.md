# TimeTree Calendar for Home Assistant

Imports your TimeTree calendars into Home Assistant — as many calendars as you
want, read **and** write, and optionally mirrored into a second calendar such as
Google Calendar with proper conflict handling.

## Highlights of 2.0.0

* **Every calendar of your account** becomes its own `calendar.*` entity with a
  device, selectable in dashboards, scripts, automations and services.
* **Complete event import**: chunked sync, recurring events expanded into
  occurrences (with `EXDATE`/`RDATE`/`COUNT`/`UNTIL`), all-day events with the
  correct exclusive end, negative timestamps, labels, attendees, optional
  comments, memos; birthdays are skipped by default.
* **Create, update and delete** events from Home Assistant (browser payload,
  CSRF token and browser TLS fingerprint through `curl_cffi`).
* **Export loop** to any calendar entity (e.g. Google Calendar) with a selectable
  interval, a past/future window, one-way or two-way mode and a dry run.
* **Conflict management**: when both sides changed, nothing is overwritten.
  Pending conflicts show up in a sensor, a notification and the
  `timetree_conflict_detected` event and are resolved manually with
  `timetree.resolve_conflict` (`use_timetree`, `use_export_target`, `use_newest`,
  `skip`, `ignore_forever`) — or automatically through a policy.
* Fixes the reported issues `#1`–`#6`: recurring events breaking month views,
  `create_event` kwarg mismatch, `422` on writes, all-day off-by-one, missing
  labels, and the stalled views.

## Installation

1. HACS → Integrations → ⋮ → *Custom repositories* →
   `https://github.com/acdcnow/Timetree-Import-for-Home-Assistant` (Integration).
2. Install **TimeTree Calendar** and restart Home Assistant.
3. *Settings → Devices & Services → + Add integration → TimeTree Calendar*, sign
   in with your TimeTree account and select your calendars.

Requires Home Assistant **2026.9** or newer.

The full documentation — options, services, conflict handling, the technical API
reference and troubleshooting — is in the [README](README.md).
