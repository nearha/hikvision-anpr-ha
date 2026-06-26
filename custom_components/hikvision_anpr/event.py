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
    fast: bool = False


EVENT_DESCRIPTION = HikvisionANPREventDescription(
    key="last_event",
    name="Last ANPR event",
)

FAST_EVENT_DESCRIPTION = HikvisionANPREventDescription(
    key="fast_event",
    name="Fast ANPR event",
    fast=True,
)


async def async_setup_entry(hass, entry: ConfigEntry, async_add_entities) -> None:
    manager: HikvisionANPRManager = entry.runtime_data
    async_add_entities([
        HikvisionANPREventEntity(manager, EVENT_DESCRIPTION),
        HikvisionANPREventEntity(manager, FAST_EVENT_DESCRIPTION),
    ])


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
        self._last_fast_event_state: LatestEventState | None = None
        self._restored_extra_state_data: dict[str, Any] | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None:
            self._restored_extra_state_data = dict(last_state.attributes)

        if self.entity_description.fast:
            register = getattr(self._manager, "async_register_fast_event_listener", None)
            if register is not None:
                self._unsub_native_event = register(self._handle_native_event)
        else:
            self._unsub_native_event = self._manager.async_register_native_event_listener(self._handle_native_event)

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_native_event is not None:
            self._unsub_native_event()
            self._unsub_native_event = None

    @callback
    def _handle_native_event(self, state: LatestEventState) -> None:
        if self.entity_description.fast:
            self._last_fast_event_state = state
        self._trigger_event("anpr", self._event_payload(state))
        self.async_write_ha_state()

    def _event_payload(self, state: LatestEventState) -> dict[str, Any]:
        payload = {
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
        }
        if not self.entity_description.fast:
            payload.update({
                "license_plate_image_path": state.license_plate_image_path,
                "vehicle_image_path": state.vehicle_image_path,
                "detection_image_path": state.detection_image_path,
            })
        return payload

    @property
    def available(self) -> bool:
        return True

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        state = self._last_fast_event_state if self.entity_description.fast else self.coordinator.data
        if (state is None or state.event_id is None) and self._restored_extra_state_data is not None:
            return self._restored_extra_state_data
        if state is None or state.event_id is None:
            return None
        return self._event_payload(state)
