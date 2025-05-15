"""The MyDlink integration."""
import logging
import voluptuous as vol
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.const import (
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_DEVICES,
    Platform,
)
from homeassistant.exceptions import ConfigEntryNotReady, ConfigEntryAuthFailed
import homeassistant.helpers.config_validation as cv

from .api import MyDlinkAPI, DlinkAuthError
from .const import (
    DOMAIN,
    CONF_EMAIL,
    DATA_API_CLIENT,
    DATA_DEVICES,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SWITCH]

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required(CONF_EMAIL): cv.string,
                vol.Required(CONF_PASSWORD): cv.string,
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)

async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the MyDlink component."""
    hass.data.setdefault(DOMAIN, {})
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up MyDlink from a config entry."""
    email = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]

    # Vytvoření API klienta
    api = MyDlinkAPI(hass, email, password)
    
    # Načtení uložených přihlašovacích údajů
    if not await api.async_load_stored_credentials():
        try:
            # Pokud nemáme platné přihlašovací údaje, přihlásíme se
            await api.async_login()
        except DlinkAuthError as err:
            _LOGGER.error("Authentication failed: %s", str(err))
            raise ConfigEntryAuthFailed(f"Přihlášení selhalo: {err}") from err
        except Exception as err:
            _LOGGER.error("Error during setup: %s", str(err))
            raise ConfigEntryNotReady(f"Chyba při inicializaci: {err}") from err

    # Uložení API klienta do hass.data
    hass.data[DOMAIN][entry.entry_id] = {
        DATA_API_CLIENT: api,
        DATA_DEVICES: api.devices,
    }

    # Nastavení platforem
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    
    if unload_ok:
        # Ukončení API klienta
        api = hass.data[DOMAIN][entry.entry_id][DATA_API_CLIENT]
        api.shutdown()
        
        # Odstranění dat
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
