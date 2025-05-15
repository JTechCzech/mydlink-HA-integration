"""Config flow for MyDlink integration."""
import logging
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.const import (
    CONF_USERNAME,
    CONF_PASSWORD,
)
from homeassistant.data_entry_flow import FlowResult

from .api import MyDlinkAPI, DlinkAuthError
from .const import DOMAIN, DEFAULT_NAME

_LOGGER = logging.getLogger(__name__)

class MyDlinkConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for MyDlink."""

    VERSION = 1

    async def async_step_user(self, user_input=None) -> FlowResult:
        """Handle the initial step."""
        errors = {}

        if user_input is not None:
            email = user_input[CONF_USERNAME]
            password = user_input[CONF_PASSWORD]

            # Kontrola, zda už není účet nakonfigurován
            await self.async_set_unique_id(email)
            self._abort_if_unique_id_configured()

            # Zkusíme se přihlásit
            api = MyDlinkAPI(self.hass, email, password)
            try:
                await api.async_login()
                
                # Zkontrolujeme, zda byly nalezeny nějaké zařízení
                if not api.devices:
                    errors["base"] = "no_devices_found"
                else:
                    # Úspěšné přihlášení, ukládáme vstup
                    return self.async_create_entry(
                        title=f"MyDlink ({email})",
                        data={
                            CONF_USERNAME: email,
                            CONF_PASSWORD: password,
                        },
                    )
            except DlinkAuthError:
                errors["base"] = "invalid_auth"
            except Exception as err:
                _LOGGER.exception("Unexpected error: %s", err)
                errors["base"] = "unknown"

        # Zobrazení formuláře
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return MyDlinkOptionsFlow(config_entry)


class MyDlinkOptionsFlow(config_entries.OptionsFlow):
    """Handle MyDlink options."""

    def __init__(self, config_entry):
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        """Manage the options."""
        return await self.async_step_device_config()

    async def async_step_device_config(self, user_input=None):
        """Handle device configuration."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="device_config",
            data_schema=vol.Schema({}),
        )
