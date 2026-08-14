from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
import datetime as dt
import json
import logging
from typing import Any

from homeassistant.core import callback

from .const import STATE_CONNECTED
from .manager import HikvisionANPRManager, LatestEventState, _value_or_unknown
from .parser import ensure_list, parse_xml_bytes, sanitize_filename

_LOGGER = logging.getLogger(__name__)

# Match the proven polling cadence used by node-red-contrib-hikvision-ultimate.
# Keeping this conservative reduces load on the camera while the normal HTTP
# callback continues independently for images and the complete ANPR event.
_FAST_POLL_INTERVAL = 3.0
_INITIAL_PIC_NAME = "202001301301320000"


def attach_fast_event_support(manager: HikvisionANPRManager) -> None:
    """Attach an isolated fast ANPR path backed by the camera plate database.

    The complete ANPR HTTP callback remains untouched. The fast entity is fed by
    polling Hikvision's lightweight vehicleDetect/plates endpoint with picName as
    a cursor, matching the strategy used by node-red-contrib-hikvision-ultimate.
    """

    fast_event_listeners: list[Callable[[LatestEventState], None]] = []
    fast_poll_task: asyncio.Task[None] | None = None

    @callback
    def async_register_fast_event_listener(
        listener: Callable[[LatestEventState], None],
    ) -> Callable[[], None]:
        fast_event_listeners.append(listener)

        @callback
        def _remove() -> None:
            if listener in fast_event_listeners:
                fast_event_listeners.remove(listener)

        return _remove

    @callback
    def _fire_fast_native_event(state: LatestEventState) -> None:
        for listener in list(fast_event_listeners):
            try:
                listener(state)
            except Exception:
                _LOGGER.exception("Error delivering fast ANPR event entity update")

    def _pic_name(value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

    def _pic_sort_key(plate: dict[str, Any]) -> tuple[int, int | str]:
        value = _pic_name(plate.get("picName"))
        try:
            return (0, int(value))
        except (TypeError, ValueError):
            return (1, value)

    def _pic_name_numeric_key(value: str) -> tuple[int, int | str]:
        try:
            return (1, int(value))
        except (TypeError, ValueError):
            return (0, value)

    def _is_newer_pic_name(value: str, cursor: str) -> bool:
        if not value:
            return False
        try:
            return int(value) > int(cursor)
        except (TypeError, ValueError):
            return value > cursor

    def _decode_plates_response(body: bytes) -> dict[str, Any]:
        stripped = body.lstrip()
        if stripped.startswith(b"{"):
            parsed = json.loads(stripped.decode("utf-8", errors="replace"))
            if not isinstance(parsed, dict):
                raise ValueError("Camera plate query returned invalid JSON")
            return parsed

        xml_start = body.find(b"<")
        if xml_start >= 0:
            return parse_xml_bytes(body[xml_start:])

        raise ValueError("Camera plate query returned neither XML nor JSON")

    def _fetch_plates_after_sync(cursor: str) -> list[dict[str, Any]]:
        request_body = (
            "<AfterTime>"
            f"<picTime>{cursor}</picTime>"
            "</AfterTime>"
        ).encode("utf-8")
        response = manager._request_sync(
            "POST",
            (
                f"{manager.base_url}/ISAPI/Traffic/channels/"
                f"{manager.channel}/vehicleDetect/plates"
            ),
            data=request_body,
            timeout=(5, 10),
        )
        response.raise_for_status()

        parsed = _decode_plates_response(response.content)
        root = parsed.get("Plates")
        if not isinstance(root, dict):
            raise ValueError("Camera plate query did not return a Plates object")

        return [
            item
            for item in ensure_list(root.get("Plate"))
            if isinstance(item, dict)
        ]

    def _fast_state_from_plate(plate_data: dict[str, Any]) -> LatestEventState:
        pic_name = _pic_name(plate_data.get("picName"))
        event_time = _value_or_unknown(
            plate_data.get("captureTime")
            or plate_data.get("dateTime")
        )
        plate = _value_or_unknown(
            plate_data.get("plateNumber")
            or plate_data.get("licensePlate")
        )
        event_fragment = manager._event_fragment(event_time)
        unique_fragment = sanitize_filename(
            pic_name
            or f"poll_{dt.datetime.now().strftime('%H%M%S%f')}"
        )[:40]

        return LatestEventState(
            status=STATE_CONNECTED,
            last_error=None,
            event_id=(
                f"{event_fragment}_"
                f"{sanitize_filename(plate)}_"
                f"{unique_fragment}"
            ),
            event_time=event_time,
            plate=plate,
            confidence=_value_or_unknown(
                plate_data.get("confidenceLevel")
                or plate_data.get("confidence")
            ),
            direction=_value_or_unknown(plate_data.get("direction")),
            list_result=_value_or_unknown(
                plate_data.get("matchingResult")
                or plate_data.get("vehicleListName")
                or plate_data.get("listName")
            ),
            country=manager._translate_country(plate_data.get("country")),
            brand=manager._translate_brand(
                plate_data.get("vehicleLogoRecog")
                or plate_data.get("brand")
            ),
            type=_value_or_unknown(plate_data.get("vehicleType")),
            color=_value_or_unknown(
                plate_data.get("color")
                or plate_data.get("vehicleColor")
            ),
        )

    async def _poll_fast_events() -> None:
        cursor = _INITIAL_PIC_NAME
        initialized = False
        failure_reported = False

        while True:
            try:
                plates = await manager.hass.async_add_executor_job(
                    _fetch_plates_after_sync,
                    cursor,
                )
                plates.sort(key=_pic_sort_key)

                if not initialized:
                    valid_pic_names = [
                        _pic_name(item.get("picName"))
                        for item in plates
                        if _pic_name(item.get("picName"))
                    ]
                    if valid_pic_names:
                        cursor = max(valid_pic_names, key=_pic_name_numeric_key)
                    initialized = True
                    if failure_reported:
                        _LOGGER.info("Fast ANPR polling connected")
                    failure_reported = False
                else:
                    for plate_data in plates:
                        pic_name = _pic_name(plate_data.get("picName"))
                        if not _is_newer_pic_name(pic_name, cursor):
                            continue

                        _fire_fast_native_event(
                            _fast_state_from_plate(plate_data)
                        )
                        cursor = pic_name

                    if failure_reported:
                        _LOGGER.info("Fast ANPR polling recovered")
                    failure_reported = False

            except asyncio.CancelledError:
                raise
            except Exception as err:
                if not failure_reported:
                    _LOGGER.warning(
                        "Fast ANPR polling unavailable; normal ANPR callback "
                        "continues unchanged: %s",
                        err,
                    )
                    failure_reported = True
                else:
                    _LOGGER.debug("Fast ANPR polling still unavailable: %s", err)

            await asyncio.sleep(_FAST_POLL_INTERVAL)

    async def async_start_fast_polling() -> None:
        nonlocal fast_poll_task
        if fast_poll_task is not None and not fast_poll_task.done():
            return

        fast_poll_task = manager.hass.async_create_task(
            _poll_fast_events(),
            name=f"{manager.domain}_fast_anpr_{manager.entry.entry_id}",
        )

    async def async_stop_fast_polling() -> None:
        nonlocal fast_poll_task
        task = fast_poll_task
        fast_poll_task = None
        if task is None:
            return

        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    manager.async_register_fast_event_listener = async_register_fast_event_listener  # type: ignore[attr-defined, method-assign]
    manager.async_start_fast_polling = async_start_fast_polling  # type: ignore[attr-defined, method-assign]
    manager.async_stop_fast_polling = async_stop_fast_polling  # type: ignore[attr-defined, method-assign]
