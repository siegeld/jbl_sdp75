"""Config flow for JBL SDP-75 integration."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST
from homeassistant.data_entry_flow import FlowResult
import homeassistant.helpers.config_validation as cv

from . import DOMAIN

_LOGGER = logging.getLogger(__name__)

CONF_HIDDEN_SOURCES = "hidden_sources"

async def validate_host(host: str) -> bool:
    """Validate the host is reachable and responds to commands."""
    try:
        return True

    except (OSError, asyncio.TimeoutError):
        return False

class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for JBL SDP-75."""

    VERSION = 1
    DOMAIN = DOMAIN

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> OptionsFlow:
        """Get the options flow for this handler."""
        return OptionsFlow(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors = {}

        if user_input is not None:
            try:
                host = user_input[CONF_HOST]
                # Check if device is already configured
                await self.async_set_unique_id(f"jbl_sdp75_{host}")
                self._abort_if_unique_id_configured()

                # Validate connection to device
                if not await validate_host(host):
                    errors["base"] = "cannot_connect"
                else:
                    return self.async_create_entry(
                        title=f"JBL SDP-75 ({host})",
                        data=user_input,
                    )
            except vol.Invalid:
                errors[CONF_HOST] = "invalid_host"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST): str,
                }
            ),
            errors=errors,
        )


class OptionsFlow(config_entries.OptionsFlow):
    """Handle options for JBL SDP-75."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        entry_data = self.hass.data.get(DOMAIN, {}).get(
            self._config_entry.entry_id, {}
        )
        entity: Any = entry_data.get("entity")

        # Get available sources from the running entity
        available_sources: list[str] = []
        if entity and entity._sources:
            available_sources = sorted(entity._sources.values())

        if not available_sources:
            return self.async_abort(reason="no_sources")

        if user_input is not None:
            return self.async_create_entry(
                title="",
                data={CONF_HIDDEN_SOURCES: user_input.get(CONF_HIDDEN_SOURCES, [])},
            )

        current_hidden = self._config_entry.options.get(CONF_HIDDEN_SOURCES, [])

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_HIDDEN_SOURCES,
                        default=current_hidden,
                    ): vol.All(
                        cv.multi_select(
                            {src: src for src in available_sources}
                        ),
                    ),
                }
            ),
        )
