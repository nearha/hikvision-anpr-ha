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
        await self._manager.async_fetch_mnpr_result()
