from __future__ import annotations

from dataclasses import replace

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr

from .const import (
    DATA_MANAGERS,
    DATA_VIEW_REGISTERED,
    DOMAIN,
    PLATFORMS,
    STATE_CONNECTED,
)
from .fast_support import attach_fast_event_support
from .manager import HikvisionANPRManager
from .runtime_support import attach_runtime_stability
from .view import HikvisionANPRView

type HikvisionANPRConfigEntry = ConfigEntry[HikvisionANPRManager]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    domain_data = hass.data.setdefault(DOMAIN, {})
    domain_data.setdefault(DATA_MANAGERS, {})

    if not domain_data.get(DATA_VIEW_REGISTERED):
        hass.http.register_view(HikvisionANPRView(hass))
        domain_data[DATA_VIEW_REGISTERED] = True

    return True


async def async_setup_entry(hass: HomeAssistant, entry: HikvisionANPRConfigEntry) -> bool:
    manager = HikvisionANPRManager(hass, entry)
    attach_runtime_stability(manager)
    attach_fast_event_support(manager)
    details = await manager.async_initialize()
    entry.runtime_data = manager

    domain_data = hass.data.setdefault(DOMAIN, {})
    managers: dict[str, HikvisionANPRManager] = domain_data.setdefault(DATA_MANAGERS, {})
    managers[entry.entry_id] = manager

    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, details.serial_number)},
        manufacturer=details.manufacturer,
        model=details.model,
        name=details.name,
        configuration_url=manager.base_url,
    )

    try:
        await manager.async_configure_listener_on_device()
    except Exception as err:
        managers.pop(entry.entry_id, None)
        await manager.async_stop()
        raise ConfigEntryNotReady(f"Failed to configure/test ANPR callback on device: {err}") from err

    manager._callback_status = "ready"  # type: ignore[attr-defined]  # noqa: SLF001
    manager._callback_last_error = None  # type: ignore[attr-defined]  # noqa: SLF001
    manager._set_state(  # noqa: SLF001 - manager owns the shared coordinator state
        replace(manager._current_state, status=STATE_CONNECTED, last_error=None)  # noqa: SLF001
    )

    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

        start_fast_polling = getattr(manager, "async_start_fast_polling", None)
        if start_fast_polling is not None:
            await start_fast_polling()
    except Exception:
        managers.pop(entry.entry_id, None)
        await manager.async_stop()
        raise

    return True


async def async_unload_entry(hass: HomeAssistant, entry: HikvisionANPRConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        stop_fast_polling = getattr(entry.runtime_data, "async_stop_fast_polling", None)
        if stop_fast_polling is not None:
            await stop_fast_polling()

        domain_data = hass.data.get(DOMAIN, {})
        managers: dict[str, HikvisionANPRManager] = domain_data.get(DATA_MANAGERS, {})
        managers.pop(entry.entry_id, None)

        await entry.runtime_data.async_stop()
    return unload_ok
