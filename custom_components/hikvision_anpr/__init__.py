from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN, PLATFORMS
from .fast_manager import HikvisionANPRFastManager as HikvisionANPRManager
from .view import HikvisionANPRView

type HikvisionANPRConfigEntry = ConfigEntry[HikvisionANPRManager]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    return True


async def async_setup_entry(hass: HomeAssistant, entry: HikvisionANPRConfigEntry) -> bool:
    manager = HikvisionANPRManager(hass, entry)
    details = await manager.async_initialize()
    entry.runtime_data = manager

    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, details.serial_number)},
        manufacturer=details.manufacturer,
        model=details.model,
        name=details.name,
        configuration_url=manager.base_url,
    )

    hass.http.register_view(HikvisionANPRView(manager))

    try:
        await manager.async_configure_listener_on_device()
    except Exception as err:
        await manager.async_stop()
        raise ConfigEntryNotReady(f"Failed to configure/test ANPR callback on device: {err}") from err

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HikvisionANPRConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await entry.runtime_data.async_stop()
    return unload_ok
