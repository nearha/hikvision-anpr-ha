from __future__ import annotations

from dataclasses import dataclass, replace
import csv
import json
import datetime as dt
import logging
from pathlib import Path
from typing import Any
from collections.abc import Callable
from urllib.parse import quote, urlparse

import requests
from requests.auth import HTTPBasicAuth, HTTPDigestAuth
from requests.exceptions import HTTPError

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.network import NoURLAvailableError, get_url
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    AUTH_BASIC,
    CONF_AUTH_MODE,
    CONF_CHANNEL,
    CONF_HTTP_HOST_ID,
    CONF_MEDIA_DIR,
    CONF_PORT,
    CONF_USE_HTTPS,
    DEFAULT_MEDIA_DIR,
    DOMAIN,
    EVENT_TYPE,
    IMAGE_DETECTION,
    IMAGE_LICENSE_PLATE,
    IMAGE_VEHICLE,
    STATE_CONNECTED,
    STATE_DISCONNECTED,
    STATE_STOPPED,
    STATE_UNKNOWN,
)
from .mappings import COUNTRY_MAP, VEHICLE_BRAND_MAP
from .parser import (
    content_name,
    ensure_list,
    extract_boundary,
    guess_extension,
    parse_header_lines,
    parse_xml_bytes,
    sanitize_filename,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class DeviceDetails:
    name: str
    model: str
    serial_number: str
    mac_address: str
    manufacturer: str = "Hikvision"


@dataclass(slots=True)
class DeclaredPicture:
    file_name: str | None
    type_name: str | None
    p_id: str | None
    received_path: str | None = None


@dataclass(slots=True)
class LatestEventState:
    status: str = STATE_DISCONNECTED
    last_error: str | None = None
    event_id: str | None = None
    event_time: str = STATE_UNKNOWN
    plate: str = STATE_UNKNOWN
    confidence: str = STATE_UNKNOWN
    direction: str = STATE_UNKNOWN
    list_result: str = STATE_UNKNOWN
    country: str = STATE_UNKNOWN
    brand: str = STATE_UNKNOWN
    type: str = STATE_UNKNOWN
    color: str = STATE_UNKNOWN
    license_plate_image_path: str | None = None
    vehicle_image_path: str | None = None
    detection_image_path: str | None = None
    license_plate_image_media_source: str | None = None
    vehicle_image_media_source: str | None = None
    detection_image_media_source: str | None = None
    image_last_updated: dt.datetime | None = None


@dataclass(slots=True)
class EventRecord:
    event_id: str
    event_dir: Path
    event_time: str
    plate: str
    confidence: str
    direction: str
    list_result: str
    country: str
    brand: str
    type: str
    color: str
    declared_pictures: list[DeclaredPicture]



def _value_or_unknown(value: Any) -> str:
    if value is None:
        return STATE_UNKNOWN
    text = str(value).strip()
    return text if text else STATE_UNKNOWN


class HikvisionANPRManager:
    def __init__(self, hass: HomeAssistant, entry) -> None:
        self.hass = hass
        self.entry = entry
        self.domain = DOMAIN
        self.host: str = entry.data["host"]
        self.port: int = entry.data[CONF_PORT]
        self.use_https: bool = entry.data[CONF_USE_HTTPS]
        self.username: str = entry.data["username"]
        self.password: str = entry.data["password"]
        self.auth_mode: str = entry.data[CONF_AUTH_MODE]
        self.channel: int = entry.data[CONF_CHANNEL]
        self.http_host_id: int = entry.data[CONF_HTTP_HOST_ID]
        self.media_dir = Path(entry.data.get(CONF_MEDIA_DIR, DEFAULT_MEDIA_DIR))
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self.csv_path = self.media_dir / "events.csv"
        self.state_path = self.media_dir / "last_event_state.json"
        self.device_details: DeviceDetails | None = None
        self._session: requests.Session | None = None
        self._current_state = LatestEventState()
        self._native_event_listeners: list[Callable[[LatestEventState], None]] = []
        self.coordinator = DataUpdateCoordinator[LatestEventState](
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}",
        )
        self._callback_base_url: str | None = None

    @property
    def base_url(self) -> str:
        protocol = "https" if self.use_https else "http"
        return f"{protocol}://{self.host}:{self.port}"

    @property
    def callback_path(self) -> str:
        return f"/api/{DOMAIN}/{self.entry.entry_id}"

    @property
    def callback_url(self) -> str | None:
        if not self._callback_base_url:
            return None
        return f"{self._callback_base_url}{self.callback_path}"

    async def async_initialize(self) -> DeviceDetails:
        self.device_details = await self.hass.async_add_executor_job(self._fetch_device_details_sync)
        self._ensure_csv()
        restored_state = await self.hass.async_add_executor_job(self._load_last_state_sync)
        if restored_state is not None:
            self._set_state(replace(restored_state, status=STATE_DISCONNECTED, last_error=None))
        else:
            self._set_state(replace(self._current_state, status=STATE_DISCONNECTED, last_error=None))
        await self._discover_callback_base_url()
        return self.device_details

    async def async_stop(self) -> None:
        session = self._session
        if session is not None:
            try:
                session.close()
            except Exception:
                pass
        self._set_state(replace(self._current_state, status=STATE_STOPPED))

    async def _discover_callback_base_url(self) -> None:
        try:
            url = get_url(
                self.hass,
                allow_internal=True,
                allow_external=True,
                allow_ip=True,
                prefer_external=False,
                prefer_cloud=False,
            )
        except NoURLAvailableError as err:
            raise ValueError("Home Assistant does not have a usable internal/external URL configured") from err
        self._callback_base_url = url.rstrip("/")

    def device_info(self) -> dict[str, Any]:
        serial = self.device_details.serial_number if self.device_details else self.entry.entry_id
        model = self.device_details.model if self.device_details else "Hikvision ANPR"
        name = self.device_details.name if self.device_details else self.host
        manufacturer = self.device_details.manufacturer if self.device_details else "Hikvision"
        return {
            "identifiers": {(DOMAIN, serial)},
            "name": name,
            "manufacturer": manufacturer,
            "model": model,
            "configuration_url": self.base_url,
        }

    def _build_session(self) -> requests.Session:
        session = requests.Session()
        session.verify = bool(self.entry.data.get("verify_ssl", False))
        if self.auth_mode == AUTH_BASIC:
            session.auth = HTTPBasicAuth(self.username, self.password)
        else:
            session.auth = HTTPDigestAuth(self.username, self.password)
        session.headers.update({"User-Agent": "homeassistant-hikvision-anpr/0.2.0"})
        return session

    def _fetch_device_details_sync(self) -> DeviceDetails:
        session = self._build_session()
        try:
            response = session.get(f"{self.base_url}/ISAPI/System/deviceInfo", timeout=(10, 15))
            response.raise_for_status()
            parsed = parse_xml_bytes(response.content)
            root = parsed.get("DeviceInfo", parsed)
            if not isinstance(root, dict):
                raise ValueError("Invalid device info response")
            return DeviceDetails(
                name=_value_or_unknown(root.get("deviceName")),
                model=_value_or_unknown(root.get("model")),
                serial_number=_value_or_unknown(root.get("serialNumber")),
                mac_address=_value_or_unknown(root.get("macAddress")),
                manufacturer=_value_or_unknown(root.get("manufacturer")),
            )
        finally:
            session.close()

    def _ensure_csv(self) -> None:
        if self.csv_path.exists():
            return
        with self.csv_path.open("w", newline="", encoding="utf-8") as file_handle:
            writer = csv.writer(file_handle)
            writer.writerow([
                "event_id",
                "event_time",
                "plate",
                "confidence",
                "direction",
                "list_result",
                "country",
                "brand",
                "type",
                "color",
                "license_plate_image",
                "vehicle_image",
                "detection_image",
            ])

    def _serialize_state(self, state: LatestEventState) -> dict[str, Any]:
        return {
            "status": state.status,
            "last_error": state.last_error,
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
            "license_plate_image_media_source": state.license_plate_image_media_source,
            "vehicle_image_media_source": state.vehicle_image_media_source,
            "detection_image_media_source": state.detection_image_media_source,
            "image_last_updated": state.image_last_updated.isoformat() if state.image_last_updated else None,
        }

    def _save_last_state_sync(self, state: LatestEventState) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self._serialize_state(state), ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_last_state_sync(self) -> LatestEventState | None:
        if not self.state_path.exists():
            return None
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        image_last_updated = None
        if payload.get("image_last_updated"):
            try:
                image_last_updated = dt.datetime.fromisoformat(payload["image_last_updated"])
            except Exception:
                image_last_updated = None
        state = LatestEventState(
            status=_value_or_unknown(payload.get("status")),
            last_error=payload.get("last_error"),
            event_id=payload.get("event_id"),
            event_time=_value_or_unknown(payload.get("event_time")),
            plate=_value_or_unknown(payload.get("plate")),
            confidence=_value_or_unknown(payload.get("confidence")),
            direction=_value_or_unknown(payload.get("direction")),
            list_result=_value_or_unknown(payload.get("list_result")),
            country=_value_or_unknown(payload.get("country")),
            brand=_value_or_unknown(payload.get("brand")),
            type=_value_or_unknown(payload.get("type")),
            color=_value_or_unknown(payload.get("color")),
            license_plate_image_path=payload.get("license_plate_image_path"),
            vehicle_image_path=payload.get("vehicle_image_path"),
            detection_image_path=payload.get("detection_image_path"),
            license_plate_image_media_source=payload.get("license_plate_image_media_source"),
            vehicle_image_media_source=payload.get("vehicle_image_media_source"),
            detection_image_media_source=payload.get("detection_image_media_source"),
            image_last_updated=image_last_updated,
        )
        for path_attr, media_attr in (
            ("license_plate_image_path", "license_plate_image_media_source"),
            ("vehicle_image_path", "vehicle_image_media_source"),
            ("detection_image_path", "detection_image_media_source"),
        ):
            path_value = getattr(state, path_attr)
            if path_value and not Path(path_value).exists():
                setattr(state, path_attr, None)
                setattr(state, media_attr, None)
        return state

    def _translate_country(self, raw_value: Any) -> str:
        text = _value_or_unknown(raw_value)
        if text == STATE_UNKNOWN:
            return STATE_UNKNOWN
        return COUNTRY_MAP.get(text, text)

    def _translate_brand(self, raw_value: Any) -> str:
        text = _value_or_unknown(raw_value)
        if text == STATE_UNKNOWN:
            return STATE_UNKNOWN
        return VEHICLE_BRAND_MAP.get(text, text)

    def _event_fragment(self, value: str | None) -> str:
        if not value:
            return dt.datetime.now().strftime("%Y%m%dT%H%M%S")
        safe = "".join(ch for ch in value if ch.isdigit() or ch in "T:+-")
        return safe.replace(":", "").replace("-", "")[:32]

    def _relative_media_path(self, absolute_path: str | None) -> str | None:
        if not absolute_path:
            return None
        path = Path(absolute_path)
        try:
            relative = path.relative_to(Path("/media"))
        except ValueError:
            return None
        return relative.as_posix()

    def _media_source_uri(self, absolute_path: str | None) -> str | None:
        relative = self._relative_media_path(absolute_path)
        if not relative:
            return None
        return f"media-source://media_source/local/{quote(relative)}"

    def _find_event_dict(self, value: Any) -> dict[str, Any] | None:
        if isinstance(value, dict):
            root = value.get("EventNotificationAlert")
            if isinstance(root, dict):
                return root
            if _value_or_unknown(value.get("eventType")).upper() == "ANPR":
                return value
            for child in value.values():
                found = self._find_event_dict(child)
                if found is not None:
                    return found
        elif isinstance(value, list):
            for item in value:
                found = self._find_event_dict(item)
                if found is not None:
                    return found
        return None

    def _parse_multipart(self, content_type: str, body: bytes) -> tuple[list[tuple[dict[str, str], bytes]], bytes | None, dict[str, Any] | None]:
        boundary = extract_boundary(content_type)
        if not boundary:
            return [], None, None
        token = b"--" + boundary
        parts: list[tuple[dict[str, str], bytes]] = []
        event_raw: bytes | None = None
        event_obj: dict[str, Any] | None = None
        for chunk in body.split(token):
            chunk = chunk.strip()
            if not chunk or chunk == b"--":
                continue
            if chunk.endswith(b"--"):
                chunk = chunk[:-2]
            chunk = chunk.lstrip(b"\r\n")
            if not chunk:
                continue
            header_blob, sep, part_body = chunk.partition(b"\r\n\r\n")
            if not sep:
                continue
            part_body = part_body.rstrip(b"\r\n")
            headers = parse_header_lines(header_blob)
            parts.append((headers, part_body))
            ctype = (headers.get("content-type") or "").lower()
            if event_obj is None and ("json" in ctype or part_body.lstrip().startswith(b"{")):
                try:
                    obj = json.loads(part_body.decode("utf-8", errors="replace"))
                except Exception:
                    obj = None
                if obj is not None and self._find_event_dict(obj) is not None:
                    event_obj = obj
                    event_raw = part_body
            if event_obj is None and ("xml" in ctype or part_body.lstrip().startswith(b"<")):
                try:
                    obj = parse_xml_bytes(part_body)
                except Exception:
                    obj = None
                if obj is not None and self._find_event_dict(obj) is not None:
                    event_obj = obj
                    event_raw = part_body
        return parts, event_raw, event_obj

    def _parse_payload(self, headers: dict[str, str], body: bytes) -> tuple[dict[str, Any] | None, bytes | None, list[tuple[dict[str, str], bytes]]]:
        content_type = (headers.get("content-type") or "").lower()
        if "multipart/" in content_type:
            parts, raw, event_obj = self._parse_multipart(content_type, body)
            return event_obj, raw, parts
        if body.lstrip().startswith(b"{"):
            try:
                return json.loads(body.decode("utf-8", errors="replace")), body, []
            except Exception:
                return None, None, []
        if body.lstrip().startswith(b"<"):
            try:
                return parse_xml_bytes(body), body, []
            except Exception:
                return None, None, []
        return None, None, []

    def _save_image(self, event_dir: Path, picture: DeclaredPicture, headers: dict[str, str], body: bytes) -> str:
        filename, stem = content_name(headers)
        ext = Path(filename).suffix or guess_extension(headers.get("content-type", ""), ".jpg")
        out_name = f"{sanitize_filename(picture.type_name or stem or 'image')}__{sanitize_filename(picture.p_id or Path(picture.file_name or stem or 'image').stem)}{ext}"
        out_path = event_dir / "images" / out_name
        out_path.write_bytes(body)
        return str(out_path)

    def _match_declared_picture(self, declared: list[DeclaredPicture], headers: dict[str, str], index: int) -> DeclaredPicture:
        filename, stem = content_name(headers)
        name = ""
        content_disposition = headers.get("content-disposition", "")
        if 'name="' in content_disposition:
            try:
                name = content_disposition.split('name="', 1)[1].split('"', 1)[0]
            except Exception:
                name = ""
        for picture in declared:
            if picture.received_path:
                continue
            if picture.p_id and picture.p_id == stem:
                return picture
            if picture.file_name and picture.file_name in {filename, name}:
                return picture
        # fallback by likely order if headers are weak
        ordered = [p for p in declared if not p.received_path]
        if ordered:
            return ordered[min(index, len(ordered) - 1)]
        return DeclaredPicture(file_name=filename or name or f"image_{index}", type_name=stem or f"image_{index}", p_id=stem or None)

    def _extract_record(self, payload: dict[str, Any], raw_body: bytes | None, parts: list[tuple[dict[str, str], bytes]]) -> EventRecord | None:
        root = self._find_event_dict(payload)
        if root is None or _value_or_unknown(root.get("eventType")).upper() != "ANPR":
            return None
        anpr = root.get("ANPR") if isinstance(root.get("ANPR"), dict) else {}
        vehicle_info = anpr.get("vehicleInfo") if isinstance(anpr.get("vehicleInfo"), dict) else {}
        pic_list = anpr.get("pictureInfoList") if isinstance(anpr.get("pictureInfoList"), dict) else {}
        pics = [item for item in ensure_list(pic_list.get("pictureInfo")) if isinstance(item, dict)]
        declared = [
            DeclaredPicture(
                file_name=item.get("fileName") if item.get("fileName") is not None else None,
                type_name=item.get("type") if item.get("type") is not None else None,
                p_id=item.get("pId") if item.get("pId") is not None else None,
            )
            for item in pics
        ]
        event_time = _value_or_unknown(root.get("dateTime"))
        plate = _value_or_unknown(anpr.get("licensePlate") or anpr.get("originalLicensePlate"))
        event_uuid = _value_or_unknown(root.get("UUID") or f"evt_{dt.datetime.now().strftime('%H%M%S%f')}")
        event_id = f"{self._event_fragment(event_time)}_{sanitize_filename(plate)}_{sanitize_filename(event_uuid)[:40]}"
        event_date = dt.datetime.now().strftime("%Y-%m-%d")
        event_dir = self.media_dir / event_date / event_id
        (event_dir / "images").mkdir(parents=True, exist_ok=True)
        if raw_body is not None:
            suffix = ".json" if raw_body.lstrip().startswith(b"{") else ".xml"
            (event_dir / f"event_raw{suffix}").write_bytes(raw_body)

        image_index = 0
        for part_headers, part_body in parts:
            ctype = (part_headers.get("content-type") or "").lower()
            if not ctype.startswith("image/"):
                continue
            picture = self._match_declared_picture(declared, part_headers, image_index)
            image_index += 1
            picture.received_path = self._save_image(event_dir, picture, part_headers, part_body)

        record = EventRecord(
            event_id=event_id,
            event_dir=event_dir,
            event_time=event_time,
            plate=plate,
            confidence=_value_or_unknown(anpr.get("confidenceLevel")),
            direction=_value_or_unknown(anpr.get("direction")),
            list_result=_value_or_unknown(anpr.get("vehicleListName")),
            country=self._translate_country(anpr.get("country")),
            brand=self._translate_brand(vehicle_info.get("vehicleLogoRecog")),
            type=_value_or_unknown(anpr.get("vehicleType")),
            color=_value_or_unknown(vehicle_info.get("color")),
            declared_pictures=declared,
        )
        self._write_event_txt(record)
        return record

    def _write_event_txt(self, record: EventRecord) -> None:
        lines = [
            f"EventID={record.event_id}",
            f"EventTime={record.event_time}",
            f"Plate={record.plate}",
            f"Confidence={record.confidence}",
            f"Direction={record.direction}",
            f"ListResult={record.list_result}",
            f"Country={record.country}",
            f"Brand={record.brand}",
            f"Type={record.type}",
            f"Color={record.color}",
        ]
        (record.event_dir / "event.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _state_from_record(self, record: EventRecord) -> LatestEventState:
        image_paths: dict[str, str | None] = {
            IMAGE_LICENSE_PLATE: None,
            IMAGE_VEHICLE: None,
            IMAGE_DETECTION: None,
        }
        for picture in record.declared_pictures:
            if picture.type_name in image_paths and picture.received_path:
                image_paths[picture.type_name] = picture.received_path
        return LatestEventState(
            status=STATE_CONNECTED,
            last_error=None,
            event_id=record.event_id,
            event_time=record.event_time,
            plate=record.plate,
            confidence=record.confidence,
            direction=record.direction,
            list_result=record.list_result,
            country=record.country,
            brand=record.brand,
            type=record.type,
            color=record.color,
            license_plate_image_path=image_paths[IMAGE_LICENSE_PLATE],
            vehicle_image_path=image_paths[IMAGE_VEHICLE],
            detection_image_path=image_paths[IMAGE_DETECTION],
            license_plate_image_media_source=self._media_source_uri(image_paths[IMAGE_LICENSE_PLATE]),
            vehicle_image_media_source=self._media_source_uri(image_paths[IMAGE_VEHICLE]),
            detection_image_media_source=self._media_source_uri(image_paths[IMAGE_DETECTION]),
            image_last_updated=dt.datetime.now(dt.timezone.utc) if any(image_paths.values()) else None,
        )

    def _append_csv(self, record: EventRecord) -> None:
        def find(type_name: str) -> str:
            for picture in record.declared_pictures:
                if picture.type_name == type_name and picture.received_path:
                    return picture.received_path
            return ""
        with self.csv_path.open("a", newline="", encoding="utf-8") as file_handle:
            csv.writer(file_handle).writerow([
                record.event_id,
                record.event_time,
                record.plate,
                record.confidence,
                record.direction,
                record.list_result,
                record.country,
                record.brand,
                record.type,
                record.color,
                find(IMAGE_LICENSE_PLATE),
                find(IMAGE_VEHICLE),
                find(IMAGE_DETECTION),
            ])

    @callback
    def _set_state(self, state: LatestEventState) -> None:
        self._current_state = state
        self.coordinator.async_set_updated_data(state)

    @callback
    def async_register_native_event_listener(self, listener: Callable[[LatestEventState], None]) -> Callable[[], None]:
        self._native_event_listeners.append(listener)

        @callback
        def _remove() -> None:
            if listener in self._native_event_listeners:
                self._native_event_listeners.remove(listener)

        return _remove

    @callback
    def _fire_native_event(self, state: LatestEventState) -> None:
        for listener in list(self._native_event_listeners):
            try:
                listener(state)
            except Exception:
                _LOGGER.exception("Error delivering native ANPR event entity update")

    @callback
    def _fire_bus_event(self, state: LatestEventState) -> None:
        device_id = None
        if self.device_details:
            registry = dr.async_get(self.hass)
            device_entry = registry.async_get_device({(DOMAIN, self.device_details.serial_number)})
            device_id = device_entry.id if device_entry else None
        self.hass.bus.async_fire(
            EVENT_TYPE,
            {
                "device_id": device_id,
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
                "license_plate_image_media_source": state.license_plate_image_media_source,
                "vehicle_image_media_source": state.vehicle_image_media_source,
                "detection_image_media_source": state.detection_image_media_source,
            },
        )

    @callback
    def _apply_state(self, state: LatestEventState, *, emit_events: bool) -> None:
        self._set_state(state)
        if emit_events:
            self._fire_bus_event(state)
            self._fire_native_event(state)
        try:
            self._save_last_state_sync(state)
        except Exception as err:
            _LOGGER.exception("Failed to persist last ANPR state: %s", err)

    async def async_handle_callback(self, headers: dict[str, str], body: bytes) -> None:
        state = await self.hass.async_add_executor_job(self._handle_callback_sync, headers, body)
        if state is None:
            return
        self._apply_state(state, emit_events=True)

    def _handle_callback_sync(self, headers: dict[str, str], body: bytes) -> LatestEventState | None:
        payload, raw, parts = self._parse_payload(headers, body)
        if payload is None:
            return None
        record = self._extract_record(payload, raw, parts)
        if record is None:
            return None
        self._append_csv(record)
        return self._state_from_record(record)

    def _http_host_xml(self) -> str:
        parsed = urlparse(self.callback_url or "")
        if not parsed.hostname:
            raise ValueError("Home Assistant callback URL is not available")
        callback_path = parsed.path.rstrip("/") or self.callback_path
        if callback_path != self.callback_path and not callback_path.endswith(self.callback_path):
            callback_path = f"{callback_path}{self.callback_path}"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<HttpHostNotification version="2.0" xmlns="http://www.isapi.org/ver20/XMLSchema">'
            f'<id>{self.http_host_id}</id>'
            f'<url>{callback_path}</url>'
            '<addressingFormatType>ipaddress</addressingFormatType>'
            f'<ipAddress>{parsed.hostname}</ipAddress>'
            f'<portNo>{port}</portNo>'
            '</HttpHostNotification>'
        )

    def _ensure_baseline_protocol_sync(self) -> None:
        session = self._build_session()
        try:
            xml_body = (
                '<?xml version="1.0" encoding="utf-8"?>'
                '<AlarmHttpPushProtocol version="2.0" xmlns="http://www.isapi.org/ver20/XMLSchema">'
                '<baseLineProtocolEnabled>true</baseLineProtocolEnabled>'
                '</AlarmHttpPushProtocol>'
            )
            response = session.put(
                f"{self.base_url}/ISAPI/Traffic/ANPR/alarmHttpPushProtocol",
                data=xml_body.encode("utf-8"),
                headers={"Content-Type": 'application/xml; charset="UTF-8"'},
                timeout=(10, 20),
            )
            if response.status_code >= 400:
                _LOGGER.debug("Ignoring baseline protocol setup response %s: %s", response.status_code, response.text)
        finally:
            session.close()

    def _configure_http_host_sync(self) -> str:
        session = self._build_session()
        try:
            xml_body = self._http_host_xml()
            response = session.put(
                f"{self.base_url}/ISAPI/Event/notification/httpHosts/{self.http_host_id}",
                data=xml_body.encode("utf-8"),
                headers={"Content-Type": 'application/xml; charset="UTF-8"'},
                timeout=(10, 20),
            )
            response.raise_for_status()
            return response.text
        finally:
            session.close()

    def _test_http_host_sync(self) -> str:
        session = self._build_session()
        try:
            xml_body = self._http_host_xml()
            response = session.post(
                f"{self.base_url}/ISAPI/Event/notification/httpHosts/{self.http_host_id}/test",
                data=xml_body.encode("utf-8"),
                headers={"Content-Type": 'application/xml; charset="UTF-8"'},
                timeout=(10, 20),
            )
            response.raise_for_status()
            return response.text
        finally:
            session.close()

    async def async_configure_listener_on_device(self) -> None:
        await self._discover_callback_base_url()
        await self.hass.async_add_executor_job(self._ensure_baseline_protocol_sync)
        try:
            await self.hass.async_add_executor_job(self._configure_http_host_sync)
            await self.hass.async_add_executor_job(self._test_http_host_sync)
            self._set_state(replace(self._current_state, last_error=None, status=self._current_state.status))
        except Exception as err:
            self._set_state(replace(self._current_state, last_error=str(err)))
            raise

    async def async_test_http_host(self) -> None:
        try:
            await self.hass.async_add_executor_job(self._test_http_host_sync)
            self._set_state(replace(self._current_state, last_error=None, status=self._current_state.status))
        except Exception as err:
            self._set_state(replace(self._current_state, last_error=str(err)))
            raise

    def _fetch_mnpr_sync(self) -> LatestEventState | None:
        session = self._build_session()
        try:
            response = session.get(
                f"{self.base_url}/ISAPI/Traffic/MNPR/channels/{self.channel}",
                timeout=(10, 30),
            )
            response.raise_for_status()
            return self._handle_callback_sync({"content-type": response.headers.get("Content-Type", "")}, response.content)
        finally:
            session.close()

    async def async_fetch_mnpr_result(self) -> None:
        state = await self.hass.async_add_executor_job(self._fetch_mnpr_sync)
        if state is None:
            raise ValueError("MNPR did not return an ANPR event")
        self._apply_state(state, emit_events=True)
