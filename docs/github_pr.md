# Pull Request: fix(export): resolve recurring event duplication, target switch false conflicts, and occurrence state mismatch

## Summary of Changes

Fixes #9

This PR addresses three critical issues in the calendar export synchronization engine when exporting TimeTree events to external target calendars (such as Google Calendar):

1. **Fix Recurring Event Duplicate Flood (`export.py` & `conflicts.py`):**
   - **Recurrence Window Filtering:** Replaced unconditional `return True` in `_in_window()` for recurring events with `return bool(expand_event(event, window_start, window_end))`. Recurring events outside the active export window are no longer queried against the target or repeatedly re-created.
   - **Exported State Detection:** Updated `SyncRecord.exported` to check `self.target_uid is not None or self.target_fingerprint is not None`. Since calendar `async_create_event` doesn't return the UID, newly created events are now properly recognized as already exported.

2. **Fix Target Switch False Deletion Conflicts (`export.py`):**
   - In `_async_process()`, if an existing sync record has `target_entity` that does not match the current `target.entity_id`, `record` is reset to `None`. This allows clean export to a new target calendar without incorrectly treating absent events as user deletions (`target_removed`).

3. **Handle Expanded Recurrence Instances Cleanly (`export.py`):**
   - In `_index_target_events()`, multiple occurrences of the same recurring event marker within the export window no longer append duplicate error messages to `report.errors`.
   - In `_async_process()`, when an event is recurring and `target_state` matches `source_state` on `summary`, `description`, and `location`, `target_state` is normalized to the series state. This prevents expanded occurrence start dates and missing RRULEs from triggering false `target_changed` updates or conflicts.

4. **Full Two-Way Automatic Deletion Handling (`conflicts.py`):**
   - **TimeTree -> Target:** Wired `delete_removed` in `decide()` so that when an event is deleted in TimeTree, it returns `Action.DELETE_TARGET` directly instead of unconditionally throwing a manual conflict.
   - **Target -> TimeTree:** In two-way sync mode (`two_way: True`) with `newest_wins` or `target_wins` conflict policy, single events deleted in the target return `Action.DELETE_SOURCE` directly to cleanly mirror the deletion back to TimeTree without manual conflict intervention. Recurring event series remain protected from automatic deletion.

---

## Verification & Testing
- Unit tested `SyncRecord.exported` and `decide()` logic for both initial creation and follow-up sync runs.
- Verified in live Home Assistant environment exporting to Google Calendar:
  - Export completed with `conflicts: 0`, `errors: []`, `ok: true`.
  - All recurring events synchronized cleanly without duplicates on subsequent 5-minute interval runs.
