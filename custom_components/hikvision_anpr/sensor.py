from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .manager import HikvisionANPRManager
from .const import DOMAIN


@dataclass(frozen=True, kw_only=True)
class HikvisionANPRSensorDescription(SensorEntityDescription):
    value_key: str


SENSORS: tuple[HikvisionANPRSensorDescription, ...] = (
    HikvisionANPRSensorDescription(key="status", translation_key="status", value_key="status", icon="mdi:lan-connect"),
    HikvisionANPRSensorDescription(key="plate", translation_key="plate", value_key="plate", icon="mdi:card-text"),
    HikvisionANPRSensorDescription(key="confidence", translation_key="confidence", value_key="confidence", icon="mdi:percent"),
    HikvisionANPRSensorDescription(key="direction", translation_key="direction", value_key="direction", icon="mdi:swap-horizontal"),
    HikvisionANPRSensorDescription(key="list_result", translation_key="list_result", value_key="list_result", icon="mdi:format-list-bulleted"),
    HikvisionANPRSensorDescription(key="country", translation_key="country", value_key="country", icon="mdi:flag"),
    HikvisionANPRSensorDescription(key="brand", translation_key="brand", value_key="brand", icon="mdi:car-info"),
    HikvisionANPRSensorDescription(key="type", translation_key="type", value_key="type", icon="mdi:car"),
    HikvisionANPRSensorDescription(key="color", translation_key="color", value_key="color", icon="mdi:palette"),
    HikvisionANPRSensorDescription(key="event_time", translation_key="event_time", value_key="event_time", icon="mdi:clock-outline"),
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    manager: HikvisionANPRManager = entry.runtime_data
    async_add_entities(HikvisionANPRSensor(manager, description) for description in SENSORS)


class HikvisionANPRSensor(CoordinatorEntity[HikvisionANPRManager], SensorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, manager: HikvisionANPRManager, description: HikvisionANPRSensorDescription) -> None:
        super().__init__(manager.coordinator)
        self.entity_description = description
        self._manager = manager
        self._attr_unique_id = f"{manager.device_details.serial_number.lower()}_{description.key}"
        self._attr_device_info = manager.device_info()

    @property
    def native_value(self):
        return getattr(self.coordinator.data, self.entity_description.value_key)

    @property
    def extra_state_attributes(self):
        if self.entity_description.key != "status":
            return None
        return {"last_error": self.coordinator.data.last_error, "callback_path": self._manager.callback_path, "callback_url": self._manager.callback_url}
