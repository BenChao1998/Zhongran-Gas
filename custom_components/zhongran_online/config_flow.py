from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ZhongranApiClient, ZhongranApiError, ZhongranCredentials, build_image_code_url
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_CUST_CODE,
    CONF_CUST_NAME,
    CONF_MINI_OPEN_ID,
    CONF_MOBILE,
    CONF_SCAN_INTERVAL_HOURS,
    CONF_SID,
    CONF_UNION_ID,
    CONF_USER_ID,
    DEFAULT_SCAN_INTERVAL_HOURS,
    DOMAIN,
)

CONF_IMAGE_CODE = "image_code"
CONF_SMS_CODE = "sms_code"


class ZhongranConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a Zhongran Online config flow."""

    VERSION = 1

    def __init__(self) -> None:
        self._mobile: str = ""
        self._union_id: str = ""
        self._mini_open_id: str = ""
        self._pending_credentials: ZhongranCredentials | None = None
        self._pending_accounts: list[dict[str, Any]] = []
        self._reauth_entry: config_entries.ConfigEntry | None = None
        self._last_error: str = ""

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> "ZhongranOptionsFlow":
        return ZhongranOptionsFlow(config_entry)

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Start a new SMS login flow."""
        return await self._async_step_collect_login_context("user", user_input)

    async def async_step_captcha(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Request the SMS verification code."""
        if not self._mobile:
            return await self.async_step_user()

        errors: dict[str, str] = {}
        if user_input is not None:
            image_code = str(user_input[CONF_IMAGE_CODE]).strip()
            try:
                await ZhongranApiClient(async_get_clientsession(self.hass)).async_send_sms_code(
                    self._mobile,
                    image_code,
                )
            except ZhongranApiError as err:
                errors["base"] = "cannot_send_sms"
                self._last_error = str(err)
            else:
                self._last_error = ""
                return await self.async_step_sms()

        schema = vol.Schema({vol.Required(CONF_IMAGE_CODE): str})
        return self.async_show_form(
            step_id="captcha",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "mobile": self._mobile,
                "captcha_url": build_image_code_url(self._mobile),
                "error_detail": self._error_detail(),
            },
        )

    async def async_step_sms(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Complete SMS login and load the bound accounts."""
        if not self._mobile:
            return await self.async_step_user()

        errors: dict[str, str] = {}
        if user_input is not None:
            sms_code = str(user_input[CONF_SMS_CODE]).strip()
            client = ZhongranApiClient(async_get_clientsession(self.hass))
            try:
                credentials = await client.async_login_by_mobile(
                    self._mobile,
                    sms_code,
                    union_id=self._union_id or None,
                    mini_open_id=self._mini_open_id or None,
                )
                accounts = await client.async_get_bound_gas_customers()
            except ZhongranApiError as err:
                errors["base"] = "cannot_login"
                self._last_error = str(err)
            else:
                if not accounts:
                    errors["base"] = "no_accounts"
                    self._last_error = "No bound gas accounts were returned for this login."
                elif self._reauth_entry is not None:
                    current_code = str(self._reauth_entry.data[CONF_CUST_CODE])
                    account = next(
                        (
                            item
                            for item in accounts
                            if str(item.get("custCode", "")) == current_code
                        ),
                        None,
                    )
                    if account is None:
                        errors["base"] = "account_not_found"
                        self._last_error = (
                            "The configured gas account is no longer bound to this mobile number."
                        )
                    else:
                        self._last_error = ""
                        return await self._async_finish_login(credentials, account)
                elif len(accounts) == 1:
                    self._last_error = ""
                    return await self._async_finish_login(credentials, accounts[0])
                else:
                    self._pending_credentials = credentials
                    self._pending_accounts = accounts
                    self._last_error = ""
                    return await self.async_step_account()

        schema = vol.Schema({vol.Required(CONF_SMS_CODE): str})
        return self.async_show_form(
            step_id="sms",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "mobile": self._mobile,
                "error_detail": self._error_detail(),
            },
        )

    async def async_step_account(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Pick one of the bound gas accounts."""
        if self._pending_credentials is None or not self._pending_accounts:
            return await self.async_step_user()

        errors: dict[str, str] = {}
        options = {
            str(account.get("custCode", "")): _account_label(account)
            for account in self._pending_accounts
            if account.get("custCode")
        }
        if not options:
            self._last_error = "No selectable gas accounts were returned by Zhongran."
            return await self.async_step_sms()

        if user_input is not None:
            selected_code = str(user_input[CONF_CUST_CODE])
            account = next(
                (
                    item
                    for item in self._pending_accounts
                    if str(item.get("custCode", "")) == selected_code
                ),
                None,
            )
            if account is None:
                errors["base"] = "account_not_found"
                self._last_error = "The selected gas account could not be found."
            else:
                return await self._async_finish_login(self._pending_credentials, account)

        schema = vol.Schema({vol.Required(CONF_CUST_CODE): vol.In(options)})
        return self.async_show_form(
            step_id="account",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "mobile": self._mobile,
                "error_detail": self._error_detail(),
            },
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> dict[str, Any]:
        """Refresh an expired Zhongran session."""
        del entry_data
        self._reauth_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if self._reauth_entry is None:
            return self.async_abort(reason="reauth_failed")

        self.context["title_placeholders"] = {"name": self._reauth_entry.title}
        self._mobile = str(self._reauth_entry.data.get(CONF_MOBILE, "")).strip()
        self._union_id = str(self._reauth_entry.data.get(CONF_UNION_ID, "")).strip()
        self._mini_open_id = str(self._reauth_entry.data.get(CONF_MINI_OPEN_ID, "")).strip()

        if not self._mobile:
            return await self.async_step_reauth_user()
        return await self.async_step_captcha()

    async def async_step_reauth_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Collect the mobile number if the old entry did not store it."""
        return await self._async_step_collect_login_context("reauth_user", user_input)

    async def _async_step_collect_login_context(
        self,
        step_id: str,
        user_input: dict[str, Any] | None,
    ) -> dict[str, Any]:
        errors: dict[str, str] = {}
        if user_input is not None:
            mobile = str(user_input[CONF_MOBILE]).strip()
            if not mobile:
                errors["base"] = "cannot_login"
                self._last_error = "A mobile number is required."
            else:
                self._mobile = mobile
                self._union_id = str(user_input.get(CONF_UNION_ID, "")).strip()
                self._mini_open_id = str(user_input.get(CONF_MINI_OPEN_ID, "")).strip()
                self._last_error = ""
                return await self.async_step_captcha()

        schema = vol.Schema(
            {
                vol.Required(CONF_MOBILE, default=self._mobile): str,
                vol.Optional(CONF_UNION_ID, default=self._union_id): str,
                vol.Optional(CONF_MINI_OPEN_ID, default=self._mini_open_id): str,
            }
        )
        return self.async_show_form(
            step_id=step_id,
            data_schema=schema,
            errors=errors,
            description_placeholders={"error_detail": self._error_detail()},
        )

    async def _async_finish_login(
        self,
        credentials: ZhongranCredentials,
        account: dict[str, Any],
    ) -> dict[str, Any]:
        cust_code = str(account.get("custCode") or "")
        cust_name = str(account.get("custName") or "")
        if not cust_code or not cust_name:
            self._last_error = "The selected gas account did not include a custCode/custName."
            if self._pending_accounts:
                return await self.async_step_account()
            return await self.async_step_sms()

        data = {
            CONF_USER_ID: credentials.user_id,
            CONF_ACCESS_TOKEN: credentials.access_token,
            CONF_SID: credentials.sid,
            CONF_CUST_CODE: cust_code,
            CONF_CUST_NAME: cust_name,
            CONF_MOBILE: credentials.mobile or self._mobile,
            CONF_UNION_ID: credentials.union_id or self._union_id,
            CONF_MINI_OPEN_ID: credentials.mini_open_id or self._mini_open_id,
        }

        if self._reauth_entry is not None:
            self._pending_credentials = None
            self._pending_accounts = []
            if hasattr(self, "async_update_reload_and_abort"):
                return self.async_update_reload_and_abort(
                    self._reauth_entry,
                    data_updates=data,
                    reason="reauth_successful",
                )

            self.hass.config_entries.async_update_entry(
                self._reauth_entry,
                data={**self._reauth_entry.data, **data},
            )
            await self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
            return self.async_abort(reason="reauth_successful")

        self._pending_credentials = None
        self._pending_accounts = []
        await self.async_set_unique_id(cust_code)
        self._abort_if_unique_id_configured()
        title = f"Zhongran Gas {cust_code}"
        return self.async_create_entry(title=title, data=data)

    def _error_detail(self) -> str:
        if not self._last_error:
            return ""
        return f"Last error: {self._last_error}"


class ZhongranOptionsFlow(config_entries.OptionsFlow):
    """Allow tuning update interval without recreating the entry."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        scan_interval_hours = self._config_entry.options.get(
            CONF_SCAN_INTERVAL_HOURS,
            DEFAULT_SCAN_INTERVAL_HOURS,
        )
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL_HOURS,
                    default=scan_interval_hours,
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=48)),
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema)


def _account_label(account: dict[str, Any]) -> str:
    cust_name = str(account.get("custName") or "")
    cust_code = str(account.get("custCode") or "")
    company = str(account.get("compName") or "")
    if company:
        return f"{cust_name} ({cust_code}) - {company}"
    return f"{cust_name} ({cust_code})"
