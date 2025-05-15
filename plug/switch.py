"""Support for MyDlink Smart Plugs."""
import logging
from datetime import timedelta

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import (
    DOMAIN,
    DATA_API_CLIENT,
    DATA_DEVICES,
    ATTR_DEVICE_ID,
    ATTR_DEVICE_NAME,
    ATTR_DEVICE_MODEL,
    ATTR_MAC,
    ATTR_MYDLINK_ID,
)

_LOGGER = logging.getLogger(__name__)

# Aktualizace stavu každých 60 sekund
SCAN_INTERVAL = timedelta(seconds=60)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the MyDlink switch platform."""
    data = hass.data[DOMAIN][entry.entry_id]
    api = data[DATA_API_CLIENT]
    devices = data[DATA_DEVICES]

    # Vybereme pouze zařízení, která jsou typu zásuvka (model začínající na DSP-)
    plugs = [device for device in devices if device.get("device_model", "").startswith("DSP-")]

    if not plugs:
        _LOGGER.info("No MyDlink plugs found")
        return

    _LOGGER.debug("Found %d MyDlink plugs", len(plugs))
    
    # Vytvoříme koordinátor pro aktualizaci stavu
    async def async_update_data():
        """Fetch data from API."""
        # Nebudeme aktualizovat data přes koordinátor,
        # místo toho budeme používat WebSocket pro aktualizace v reálném čase
        return True

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=DOMAIN,
        update_method=async_update_data,
        update_interval=SCAN_INTERVAL,
    )

    # Počáteční aktualizace dat
    await coordinator.async_config_entry_first_refresh()

    entities = []
    for plug in plugs:
        # Vytvoříme entitu pro každou zásuvku
        entity = MyDlinkSwitch(coordinator, api, plug)
        entities.append(entity)

    async_add_entities(entities)


class MyDlinkSwitch(CoordinatorEntity, SwitchEntity):
    """Representation of a MyDlink switch."""

    def __init__(self, coordinator, api, device_info):
        """Initialize the MyDlink switch."""
        super().__init__(coordinator)
        self._api = api
        self._state = None
        self._device_info = device_info
        self._mac = device_info["mac"]
        self._device_id = device_info["mac"]
        self._attr_unique_id = f"{DOMAIN}_{self._mac}"
        self._attr_name = device_info["device_name"]
        self._attr_device_class = "outlet"
        
        # Registrujeme callback pro aktualizaci stavu ze WebSocket
        self._register_callback()

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information about this entity."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=self._device_info["device_name"],
            manufacturer="D-Link",
            model=self._device_info["device_model"],
        )

    @property
    def is_on(self) -> bool:
        """Return True if entity is on."""
        return self._state

    @property
    def available(self) -> bool:
        """Return if switch is available."""
        return self._state is not None

    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        return {
            ATTR_DEVICE_ID: self._device_id,
            ATTR_DEVICE_NAME: self._device_info["device_name"],
            ATTR_DEVICE_MODEL: self._device_info["device_model"],
            ATTR_MAC: self._mac,
            ATTR_MYDLINK_ID: self._device_info.get("mydlink_id", ""),
        }

    async def async_turn_on(self, **kwargs) -> None:
        """Turn the entity on."""
        await self._api.async_set_device_state(self._device_id, True)
        self._state = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        """Turn the entity off."""
        await self._api.async_set_device_state(self._device_id, False)
        self._state = False
        self.async_write_ha_state()

    @callback
    def _handle_state_update(self, state):
        """Handle state update from WebSocket."""
        self._state = state
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added to hass."""
        await super().async_added_to_hass()
        
        # Po přidání do HASS vyžádáme aktuální stav zařízení
        await self._api.async_get_device_state(self._device_id, self._handle_state_update)

    def _register_callback(self):
        """Register callback for device state updates."""
        self._api._ws_callback[self._device_id] = self._handle_state_update
