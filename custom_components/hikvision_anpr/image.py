from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from homeassistant.components.image import ImageEntity, ImageEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .manager import HikvisionANPRManager


@dataclass(frozen=True, kw_only=True)
class HikvisionANPRImageDescription(ImageEntityDescription):
    path_key: str
    media_source_key: str


IMAGES: tuple[HikvisionANPRImageDescription, ...] = (
    HikvisionANPRImageDescription(
        key="license_plate",
        path_key="license_plate_image_path",
        media_source_key="license_plate_image_media_source",
        name="Last license plate image",
    ),
    HikvisionANPRImageDescription(
        key="vehicle",
        path_key="vehicle_image_path",
        media_source_key="vehicle_image_media_source",
        name="Last vehicle image",
    ),
    HikvisionANPRImageDescription(
        key="detection",
        path_key="detection_image_path",
        media_source_key="detection_image_media_source",
        name="Last detection image",
    ),
)


async def async_setup_entry(hass, entry: ConfigEntry, async_add_entities) -> None:
    manager: HikvisionANPRManager = entry.runtime_data
    async_add_entities([HikvisionANPRImage(manager, description) for description in IMAGES])


class HikvisionANPRImage(CoordinatorEntity[HikvisionANPRManager], ImageEntity):
    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_content_type = "image/jpeg"

    def __init__(self, manager: HikvisionANPRManager, description: HikvisionANPRImageDescription) -> None:
        CoordinatorEntity.__init__(self, manager.coordinator)
        ImageEntity.__init__(self, manager.hass)
        self.entity_description = description
        self._attr_unique_id = f"{manager.device_details.serial_number.lower()}_{description.key}_image"
        self._attr_device_info = manager.device_info()

    @property
    def available(self) -> bool:
        return bool(getattr(self.coordinator.data, self.entity_description.path_key))

    @property
    def image_last_updated(self):
        return self.coordinator.data.image_last_updated

    @property
    def extra_state_attributes(self):
        return {
            "image_path": getattr(self.coordinator.data, self.entity_description.path_key),
            "media_source": getattr(self.coordinator.data, self.entity_description.media_source_key),
        }

    async def async_image(self) -> bytes | None:
        path = getattr(self.coordinator.data, self.entity_description.path_key)
        if not path:
            return None
        return await self.hass.async_add_executor_job(Path(path).read_bytes)
