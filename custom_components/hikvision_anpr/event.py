from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.event import EventEntity, EventEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .manager import HikvisionANPRManager, LatestEventState


@dataclass(frozen=True, kw_only=True)
class HikvisionANPREventDescription(EventEntityDescription):
    pass


EVENT_DESCRIPTION = HikvisionANPREventDescription(
    key="last_event",
    name="Last ANPR event",
)


async def async_setup_entry(hass, entry: ConfigEntry, async_add_entities) -> None:
    manager: HikvisionANPRManager = entry.runtime_data
    async_add_entities([HikvisionANPREventEntity(manager, EVENT_DESCRIPTION)])


class HikvisionANPREventEntity(CoordinatorEntity[HikvisionANPRManager], EventEntity, RestoreEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_event_types = ["anpr"]

    def __init__(self, manager: HikvisionANPRManager, description: HikvisionANPREventDescription) -> None:
        super().__init__(manager.coordinator)
        self.entity_description = description
        self._manager = manager
        self._attr_unique_id = f"{manager.device_details.serial_number.lower()}_{description.key}"
        self._attr_device_info = manager.device_info()
        self._attr_translation_key = None
        self._unsub_native_event = None
        self._restored_extra_state_data: dict[str, Any] | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None:
            self._restored_extra_state_data = dict(last_state.attributes)
        self._unsub_native_event = self._manager.async_register_native_event_listener(self._handle_native_event)

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_native_event is not None:
            self._unsub_native_event()
            self._unsub_native_event = None

    @callback
    def _handle_native_event(self, state: LatestEventState) -> None:
        self._trigger_event("anpr", self._event_payload(state))
        self.async_write_ha_state()

    def _event_payload(self, state: LatestEventState) -> dict[str, Any]:
        return {
            "event_id": state.event_id,
            "event_time": state.event_time,
            "plate": state.plate,
            "confidence": state.confidence,
            "direction": state.direction,
            "list_result": state.list_result,
            "country": state.country,
            "brand": state.brand,
            "type": state.type,
            "color": state.color,
            "license_plate_image_path": state.license_plate_image_path,
            "vehicle_image_path": state.vehicle_image_path,
            "detection_image_path": state.detection_image_path,
        }

    @property
    def available(self) -> bool:
        return True

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        state = self.coordinator.data
        if state.event_id is None and self._restored_extra_state_data is not None:
            return self._restored_extra_state_data
        return self._event_payload(state)
