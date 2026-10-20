"""Config flow for the TimeTree integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers import selector

from .api import TimeTreeApi, TimeTreeAuthError, TimeTreeError
from .const import (
    CONF_ALIAS_CODE,
    CONF_CALENDAR_ID,
    CONF_CALENDAR_NAME,
    CONF_CALENDARS,
    CONF_CONFLICT_POLICY,
    CONF_DESCRIPTION_DETAILS,
    CONF_EXPORT_ATTENDEES,
    CONF_EXPORT_DELETE_REMOVED,
    CONF_EXPORT_DIRECTION,
    CONF_EXPORT_DRY_RUN,
    CONF_EXPORT_ENABLED,
    CONF_EXPORT_FILTER_MODE,
    CONF_EXPORT_FUTURE_DAYS,
    CONF_EXPORT_INCLUDE_UNTAGGED,
    CONF_EXPORT_INCLUDE_UNTAGGED_TAGS,
    CONF_EXPORT_INTERVAL,
    CONF_EXPORT_PAST_DAYS,
    CONF_EXPORT_RECREATE_REMOVED,
    CONF_EXPORT_TAGS,
    CONF_EXPORT_TARGET,
    CONF_IMPORT_UNMANAGED,
    CONF_INCLUDE_BIRTHDAYS,
    CONF_INCLUDE_COMMENTS,
    CONF_NOTIFY_CONFLICTS,
    CONF_SCAN_INTERVAL,
    CONFLICT_POLICIES,
    DEFAULT_EXPORT_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DIRECTIONS,
    DIRECTION_EXPORT_ONLY,
    DOMAIN,
    FILTER_MODES,
    FILTER_MODE_ALL,
    MAX_EXPORT_DAYS,
    MAX_EXPORT_INTERVAL,
    MAX_SCAN_INTERVAL,
    MIN_EXPORT_DAYS,
    MIN_EXPORT_INTERVAL,
    MIN_SCAN_INTERVAL,
    POLICY_MANUAL,
)
from .models import TimeTreeCalendar
from .options import TimeTreeOptions

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.EMAIL)
        ),
        vol.Required(CONF_PASSWORD): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
        ),
    }
)

PASSWORD_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PASSWORD): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
        )
    }
)


def _calendar_select(
    calendars: list[TimeTreeCalendar], default: list[str]
) -> vol.Schema:
    """Build the calendar multi select schema."""
    options = [
        {"value": item.calendar_id, "label": item.name or item.calendar_id}
        for item in calendars
    ]
    if not options:
        options = [{"value": value, "label": value} for value in default]
    return vol.Schema(
        {
            vol.Required(CONF_CALENDARS, default=default): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=options,
                    multiple=True,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )
        }
    )


def _definitions(
    selected: list[str],
    calendars: list[TimeTreeCalendar],
    fallback: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return the storable definitions of the selected calendars."""
    result: list[dict[str, Any]] = []
    for calendar_id in selected:
        calendar = next(
            (item for item in calendars if item.calendar_id == calendar_id), None
        )
        if calendar is not None:
            result.append(
                {
                    CONF_CALENDAR_ID: calendar.calendar_id,
                    CONF_CALENDAR_NAME: calendar.name or calendar.calendar_id,
                    CONF_ALIAS_CODE: calendar.alias_code,
                }
            )
        elif fallback and calendar_id in fallback:
            result.append(fallback[calendar_id])
        else:
            result.append(
                {
                    CONF_CALENDAR_ID: calendar_id,
                    CONF_CALENDAR_NAME: calendar_id,
                    CONF_ALIAS_CODE: None,
                }
            )
    return result


class TimeTreeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the TimeTree config flow."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._email: str | None = None
        self._password: str | None = None
        self._calendars: list[TimeTreeCalendar] = []

    # ------------------------------------------------------------------
    # setup
    # ------------------------------------------------------------------
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the TimeTree credentials."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._email = user_input[CONF_EMAIL]
            self._password = user_input[CONF_PASSWORD]
            try:
                self._calendars = await self._async_load_calendars(
                    self._email, self._password
                )
            except TimeTreeAuthError:
                errors["base"] = "invalid_auth"
            except TimeTreeError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001 - report unexpected failures
                _LOGGER.exception("Unexpected error during TimeTree setup")
                errors["base"] = "unknown"
            else:
                if not self._calendars:
                    errors["base"] = "no_calendars"
                else:
                    await self.async_set_unique_id(self._email.lower())
                    self._abort_if_unique_id_configured()
                    return await self.async_step_calendars()

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    async def async_step_calendars(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select the calendars to import."""
        errors: dict[str, str] = {}
        if user_input is not None:
            selected = [str(value) for value in user_input[CONF_CALENDARS]]
            if not selected:
                errors["base"] = "no_calendars"
            else:
                calendars = _definitions(selected, self._calendars)
                title = (
                    calendars[0][CONF_CALENDAR_NAME]
                    if len(calendars) == 1
                    else f"TimeTree ({self._email})"
                )
                return self.async_create_entry(
                    title=title,
                    data={
                        CONF_EMAIL: self._email,
                        CONF_PASSWORD: self._password,
                        CONF_CALENDARS: calendars,
                    },
                    options={
                        CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
                        CONF_EXPORT_INTERVAL: DEFAULT_EXPORT_INTERVAL,
                    },
                )

        return self.async_show_form(
            step_id="calendars",
            data_schema=_calendar_select(self._calendars, []),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # reauth
    # ------------------------------------------------------------------
    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Start the re-authorization flow."""
        self._email = entry_data.get(CONF_EMAIL)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new password."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            self._password = user_input[CONF_PASSWORD]
            self._email = entry.data[CONF_EMAIL]
            try:
                self._calendars = await self._async_load_calendars(
                    self._email, self._password
                )
            except TimeTreeAuthError:
                errors["base"] = "invalid_auth"
            except TimeTreeError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: self._password}
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=PASSWORD_SCHEMA,
            description_placeholders={"email": entry.data.get(CONF_EMAIL, "")},
            errors=errors,
        )

    async def _async_load_calendars(
        self, email: str, password: str
    ) -> list[TimeTreeCalendar]:
        """Validate the credentials and return the calendars."""
        api = await TimeTreeApi.async_create(self.hass, email, password)
        try:
            return await api.async_validate()
        finally:
            await api.async_close()

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return TimeTreeOptionsFlow()


class TimeTreeOptionsFlow(OptionsFlow):
    """Handle the TimeTree options."""

    def __init__(self) -> None:
        """Initialize the options flow."""
        self._pending: dict[str, Any] = {}
        self._calendars: list[TimeTreeCalendar] = []

    @property
    def _effective(self) -> TimeTreeOptions:
        """Return the currently configured options."""
        return TimeTreeOptions.from_entry(self.config_entry)

    def _snapshot(self) -> dict[str, Any]:
        """Return the storable options as they are configured right now."""
        options = self._effective
        return {
            CONF_CALENDARS: [item.as_dict() for item in options.calendars.values()],
            CONF_SCAN_INTERVAL: options.scan_interval,
            CONF_INCLUDE_BIRTHDAYS: options.include_birthdays,
            CONF_INCLUDE_COMMENTS: options.include_comments,
            CONF_DESCRIPTION_DETAILS: options.description_details,
            CONF_EXPORT_ENABLED: options.export_enabled,
            CONF_EXPORT_TARGET: options.export_target,
            CONF_EXPORT_INTERVAL: options.export_interval,
            CONF_EXPORT_DIRECTION: options.export_direction,
            CONF_EXPORT_PAST_DAYS: options.export_past_days,
            CONF_EXPORT_FUTURE_DAYS: options.export_future_days,
            CONF_EXPORT_DELETE_REMOVED: options.export_delete_removed,
            CONF_EXPORT_RECREATE_REMOVED: options.export_recreate_removed,
            CONF_EXPORT_DRY_RUN: options.export_dry_run,
            CONF_IMPORT_UNMANAGED: options.import_unmanaged,
            CONF_EXPORT_ATTENDEES: [str(item) for item in options.export_attendees],
            CONF_EXPORT_INCLUDE_UNTAGGED: options.export_include_untagged,
            CONF_EXPORT_TAGS: [str(item) for item in options.export_tags],
            CONF_EXPORT_INCLUDE_UNTAGGED_TAGS: options.export_include_untagged_tags,
            CONF_EXPORT_FILTER_MODE: options.export_filter_mode,
            CONF_CONFLICT_POLICY: options.conflict_policy,
            CONF_NOTIFY_CONFLICTS: options.notify_conflicts,
        }

    @property
    def _selected_calendars(self) -> list[str]:
        """Return the ids of the selected calendars."""
        return [
            str(item.get(CONF_CALENDAR_ID))
            for item in self._pending.get(CONF_CALENDARS, [])
            if item.get(CONF_CALENDAR_ID)
        ]

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the settings menu."""
        self._pending = self._snapshot()
        return self.async_show_menu(
            step_id="init",
            menu_options=["calendars", "sync", "export", "conflicts"],
        )

    async def async_step_sync(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure the polling behaviour."""
        if user_input is not None:
            self._pending.update(user_input)
            return self.async_create_entry(title="", data=self._pending)
        current = self._effective
        return self.async_show_form(
            step_id="sync",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL, default=current.scan_interval
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=MIN_SCAN_INTERVAL,
                            max=MAX_SCAN_INTERVAL,
                            step=5,
                            unit_of_measurement="min",
                            mode=selector.NumberSelectorMode.SLIDER,
                        )
                    ),
                    vol.Required(
                        CONF_INCLUDE_BIRTHDAYS, default=current.include_birthdays
                    ): selector.BooleanSelector(),
                    vol.Required(
                        CONF_INCLUDE_COMMENTS, default=current.include_comments
                    ): selector.BooleanSelector(),
                    vol.Required(
                        CONF_DESCRIPTION_DETAILS, default=current.description_details
                    ): selector.BooleanSelector(),
                }
            ),
        )

    async def async_step_calendars(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add or remove TimeTree calendars."""
        errors: dict[str, str] = {}
        if not self._calendars:
            self._calendars = await self._async_available_calendars()

        if user_input is not None:
            selected = [str(value) for value in user_input[CONF_CALENDARS]]
            if not selected:
                errors["base"] = "no_calendars"
            else:
                fallback = {
                    str(item.get(CONF_CALENDAR_ID)): item
                    for item in self._pending.get(CONF_CALENDARS, [])
                }
                self._pending[CONF_CALENDARS] = _definitions(
                    selected, self._calendars, fallback
                )
                return self.async_create_entry(title="", data=self._pending)

        return self.async_show_form(
            step_id="calendars",
            data_schema=_calendar_select(self._calendars, self._selected_calendars),
            errors=errors,
        )

    async def _async_available_calendars(self) -> list[TimeTreeCalendar]:
        """Return the calendars of the account, falling back to the stored ones."""
        fallback = [
            TimeTreeCalendar(
                calendar_id=str(item.get(CONF_CALENDAR_ID)),
                name=str(item.get(CONF_CALENDAR_NAME) or item.get(CONF_CALENDAR_ID)),
                alias_code=item.get(CONF_ALIAS_CODE),
            )
            for item in self._pending.get(CONF_CALENDARS, [])
        ]
        try:
            api = await TimeTreeApi.async_create(
                self.hass,
                self.config_entry.data[CONF_EMAIL],
                self.config_entry.data[CONF_PASSWORD],
            )
            try:
                return await api.async_validate()
            finally:
                await api.async_close()
        except TimeTreeError as err:
            _LOGGER.error("Could not load the TimeTree calendars: %s", err)
            return fallback

    async def async_step_export(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure the calendar export."""
        if user_input is not None:
            self._pending.update(user_input)
            return self.async_create_entry(title="", data=self._pending)
        current = self._effective
        target_field: Any = selector.EntitySelector(
            selector.EntitySelectorConfig(domain="calendar")
        )
        schema: dict[Any, Any] = {
            vol.Required(
                CONF_EXPORT_ENABLED, default=current.export_enabled
            ): selector.BooleanSelector(),
            vol.Required(
                CONF_EXPORT_INTERVAL, default=current.export_interval
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=MIN_EXPORT_INTERVAL,
                    max=MAX_EXPORT_INTERVAL,
                    step=5,
                    unit_of_measurement="min",
                    mode=selector.NumberSelectorMode.SLIDER,
                )
            ),
            vol.Required(
                CONF_EXPORT_DIRECTION,
                default=current.export_direction or DIRECTION_EXPORT_ONLY,
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=DIRECTIONS,
                    translation_key="export_direction",
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(
                CONF_EXPORT_PAST_DAYS, default=current.export_past_days
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=MIN_EXPORT_DAYS,
                    max=MAX_EXPORT_DAYS,
                    step=1,
                    unit_of_measurement="days",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_EXPORT_FUTURE_DAYS, default=current.export_future_days
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=MIN_EXPORT_DAYS,
                    max=MAX_EXPORT_DAYS,
                    step=1,
                    unit_of_measurement="days",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_EXPORT_DELETE_REMOVED, default=current.export_delete_removed
            ): selector.BooleanSelector(),
            vol.Required(
                CONF_EXPORT_RECREATE_REMOVED, default=current.export_recreate_removed
            ): selector.BooleanSelector(),
            vol.Required(
                CONF_EXPORT_DRY_RUN, default=current.export_dry_run
            ): selector.BooleanSelector(),
            vol.Required(
                CONF_IMPORT_UNMANAGED, default=current.import_unmanaged
            ): selector.BooleanSelector(),
        }

        # Dynamically discover members from the coordinator
        user_options: list[selector.SelectOptionDict] = []
        seen_user_ids: set[str] = set()
        runtime = getattr(self.config_entry, "runtime_data", None)
        if runtime is not None and getattr(runtime, "coordinator", None) is not None:
            coordinator = runtime.coordinator
            for cal_id in coordinator.calendar_ids:
                users_map = coordinator._user_names.get(str(cal_id), {})
                for u_id, u_name in users_map.items():
                    val = u_name or str(u_id)
                    if val.lower() not in seen_user_ids:
                        seen_user_ids.add(val.lower())
                        user_options.append(
                            selector.SelectOptionDict(
                                value=val,
                                label=u_name or f"User {u_id}",
                            )
                        )
        for name in current.export_attendees:
            if name.lower() not in seen_user_ids:
                seen_user_ids.add(name.lower())
                user_options.append(
                    selector.SelectOptionDict(
                        value=name,
                        label=name,
                    )
                )

        # Dynamically discover tags/labels from the coordinator
        tag_options: list[selector.SelectOptionDict] = []
        seen_tag_ids: set[str] = set()
        if runtime is not None and getattr(runtime, "coordinator", None) is not None:
            coordinator = runtime.coordinator
            for cal_id in coordinator.calendar_ids:
                labels_map = coordinator._labels.get(str(cal_id), {})
                if not labels_map and coordinator.data:
                    cal_data = coordinator.data.get(str(cal_id))
                    if cal_data and cal_data.calendar.labels:
                        labels_map = {
                            lbl.label_id: lbl.name
                            for lbl in cal_data.calendar.labels.values()
                        }
                for l_id, l_name in labels_map.items():
                    val = l_name or str(l_id)
                    if val.lower() not in seen_tag_ids:
                        seen_tag_ids.add(val.lower())
                        tag_options.append(
                            selector.SelectOptionDict(
                                value=val,
                                label=l_name or f"Label {l_id}",
                            )
                        )
        for tag in current.export_tags:
            if tag.lower() not in seen_tag_ids:
                seen_tag_ids.add(tag.lower())
                tag_options.append(
                    selector.SelectOptionDict(
                        value=tag,
                        label=tag,
                    )
                )

        if user_options:
            schema[
                vol.Optional(
                    CONF_EXPORT_ATTENDEES,
                    default=[
                        str(uid)
                        for uid in current.export_attendees
                        if str(uid).lower() in seen_user_ids
                    ],
                )
            ] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=user_options,
                    multiple=True,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )
            schema[
                vol.Required(
                    CONF_EXPORT_INCLUDE_UNTAGGED,
                    default=current.export_include_untagged,
                )
            ] = selector.BooleanSelector()

        if tag_options:
            schema[
                vol.Optional(
                    CONF_EXPORT_TAGS,
                    default=[
                        str(t)
                        for t in current.export_tags
                        if str(t).lower() in seen_tag_ids
                    ],
                )
            ] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=tag_options,
                    multiple=True,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )
            schema[
                vol.Required(
                    CONF_EXPORT_INCLUDE_UNTAGGED_TAGS,
                    default=current.export_include_untagged_tags,
                )
            ] = selector.BooleanSelector()

        if user_options and tag_options:
            schema[
                vol.Required(
                    CONF_EXPORT_FILTER_MODE,
                    default=current.export_filter_mode or FILTER_MODE_ALL,
                )
            ] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=FILTER_MODES,
                    translation_key="export_filter_mode",
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )

        if current.export_target:
            schema[
                vol.Optional(CONF_EXPORT_TARGET, default=current.export_target)
            ] = target_field
        else:
            schema[vol.Optional(CONF_EXPORT_TARGET)] = target_field

        return self.async_show_form(step_id="export", data_schema=vol.Schema(schema))

    async def async_step_conflicts(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure the conflict handling."""
        if user_input is not None:
            self._pending.update(user_input)
            return self.async_create_entry(title="", data=self._pending)
        current = self._effective
        return self.async_show_form(
            step_id="conflicts",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_CONFLICT_POLICY,
                        default=current.conflict_policy or POLICY_MANUAL,
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=CONFLICT_POLICIES,
                            translation_key="conflict_policy",
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Required(
                        CONF_NOTIFY_CONFLICTS, default=current.notify_conflicts
                    ): selector.BooleanSelector(),
                }
            ),
        )
