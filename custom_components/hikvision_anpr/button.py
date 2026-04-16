from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry

from .manager import HikvisionANPRManager


@dataclass(frozen=True, kw_only=True)
class HikvisionANPRButtonDescription(ButtonEntityDescription):
    action: str


BUTTONS: tuple[HikvisionANPRButtonDescription, ...] = (
    HikvisionANPRButtonDescription(
        key="reconfigure_listening",
        name="Reconfigure listening on device",
        icon="mdi:webhook",
        action="configure",
    ),
    HikvisionANPRButtonDescription(
        key="test_listening",
        name="Test listening on device",
        icon="mdi:connection",
        action="test",
    ),
    HikvisionANPRButtonDescription(
        key="fetch_mnpr_result",
        name="Fetch MNPR result",
        icon="mdi:camera-burst",
        action="mnpr",
    ),
)


async def async_setup_entry(hass, entry: ConfigEntry, async_add_entities) -> None:
    manager: HikvisionANPRManager = entry.runtime_data
    async_add_entities([HikvisionANPRButton(manager, description) for description in BUTTONS])


class HikvisionANPRButton(ButtonEntity):
    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, manager: HikvisionANPRManager, description: HikvisionANPRButtonDescription) -> None:
        self.entity_description = description
        self._manager = manager
        self._attr_unique_id = f"{manager.device_details.serial_number.lower()}_{description.key}"
        self._attr_device_info = manager.device_info()

    async def async_press(self) -> None:
        if self.entity_description.action == "configure":
            await self._manager.async_configure_listener_on_device()
        elif self.entity_description.action == "test":
            await self._manager.async_test_http_host()
        else:
            await self._manager.async_fetch_mnpr_result()
