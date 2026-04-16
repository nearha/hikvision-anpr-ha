from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, CONF_VERIFY_SSL

from .const import (
    AUTH_BASIC,
    AUTH_DIGEST,
    CONF_AUTH_MODE,
    CONF_CHANNEL,
    CONF_HTTP_HOST_ID,
    CONF_MEDIA_DIR,
    CONF_PORT,
    CONF_USE_HTTPS,
    DEFAULT_AUTH_MODE,
    DEFAULT_CHANNEL,
    DEFAULT_HTTP_HOST_ID,
    DEFAULT_MEDIA_DIR,
    DEFAULT_PORT,
    DEFAULT_USE_HTTPS,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
)
from .manager import HikvisionANPRManager

_LOGGER = logging.getLogger(__name__)


class HikvisionANPRConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def _schema(self, user_input: dict[str, Any] | None = None) -> vol.Schema:
        user_input = user_input or {}
        return vol.Schema(
            {
                vol.Required(CONF_HOST, default=user_input.get(CONF_HOST, "")): str,
                vol.Required(CONF_PORT, default=user_input.get(CONF_PORT, DEFAULT_PORT)): int,
                vol.Required(CONF_USE_HTTPS, default=user_input.get(CONF_USE_HTTPS, DEFAULT_USE_HTTPS)): bool,
                vol.Required(CONF_USERNAME, default=user_input.get(CONF_USERNAME, "")): str,
                vol.Required(CONF_PASSWORD, default=user_input.get(CONF_PASSWORD, "")): str,
                vol.Required(CONF_AUTH_MODE, default=user_input.get(CONF_AUTH_MODE, DEFAULT_AUTH_MODE)): vol.In([AUTH_DIGEST, AUTH_BASIC]),
                vol.Required(CONF_VERIFY_SSL, default=user_input.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)): bool,
                vol.Required(CONF_CHANNEL, default=user_input.get(CONF_CHANNEL, DEFAULT_CHANNEL)): vol.All(int, vol.Range(min=1)),
                vol.Required(CONF_HTTP_HOST_ID, default=user_input.get(CONF_HTTP_HOST_ID, DEFAULT_HTTP_HOST_ID)): vol.All(int, vol.Range(min=1, max=8)),
                vol.Required(CONF_MEDIA_DIR, default=user_input.get(CONF_MEDIA_DIR, DEFAULT_MEDIA_DIR)): str,
            }
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                data = {**user_input, CONF_HOST: user_input[CONF_HOST].strip().rstrip("/")}
                manager = HikvisionANPRManager(self.hass, type("TmpEntry", (), {"data": data, "entry_id": "validate"})())
                details = await manager.async_initialize()
            except Exception as err:  # pylint: disable=broad-except
                errors["base"] = "cannot_connect"
                _LOGGER.warning("Cannot validate Hikvision ANPR config: %s", err)
            else:
                await self.async_set_unique_id(details.serial_number)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=details.name or data[CONF_HOST], data=data)

        return self.async_show_form(step_id="user", data_schema=self._schema(user_input), errors=errors)
