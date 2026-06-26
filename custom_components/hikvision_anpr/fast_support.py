from __future__ import annotations

from collections.abc import Callable
import datetime as dt
import logging
from typing import Any

from homeassistant.core import callback

from .const import STATE_CONNECTED
from .manager import HikvisionANPRManager, LatestEventState, _value_or_unknown
from .parser import sanitize_filename

_LOGGER = logging.getLogger(__name__)


def attach_fast_event_support(manager: HikvisionANPRManager) -> None:
    """Attach a safe metadata-only fast event path to a manager instance.

    The original manager class, callback URL, and complete ANPR processing path stay
    untouched. Fast parsing is best-effort and must never block the original event.
    """

    fast_event_listeners: list[Callable[[LatestEventState], None]] = []
    original_async_handle_callback = manager.async_handle_callback

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

    def _fast_state_from_payload(payload: dict[str, Any]) -> LatestEventState | None:
        root = manager._find_event_dict(payload)
        if root is None or _value_or_unknown(root.get("eventType")).upper() != "ANPR":
            return None

        anpr = root.get("ANPR") if isinstance(root.get("ANPR"), dict) else {}
        vehicle_info = anpr.get("vehicleInfo") if isinstance(anpr.get("vehicleInfo"), dict) else {}
        event_time = _value_or_unknown(root.get("dateTime"))
        plate = _value_or_unknown(anpr.get("licensePlate") or anpr.get("originalLicensePlate"))
        event_uuid = _value_or_unknown(root.get("UUID") or root.get("uuid") or f"evt_{dt.datetime.now().strftime('%H%M%S%f')}")
        event_id = f"{manager._event_fragment(event_time)}_{sanitize_filename(plate)}_{sanitize_filename(event_uuid)[:40]}"

        return LatestEventState(
            status=STATE_CONNECTED,
            last_error=None,
            event_id=event_id,
            event_time=event_time,
            plate=plate,
            confidence=_value_or_unknown(anpr.get("confidenceLevel")),
            direction=_value_or_unknown(anpr.get("direction")),
            list_result=_value_or_unknown(anpr.get("vehicleListName")),
            country=manager._translate_country(anpr.get("country")),
            brand=manager._translate_brand(vehicle_info.get("vehicleLogoRecog")),
            type=_value_or_unknown(anpr.get("vehicleType")),
            color=_value_or_unknown(vehicle_info.get("color")),
        )

    def _fast_state_from_callback_sync(
        headers: dict[str, str],
        body: bytes,
    ) -> LatestEventState | None:
        payload, _raw, _parts = manager._parse_payload(headers, body)
        if payload is None:
            return None
        return _fast_state_from_payload(payload)

    async def async_handle_callback(headers: dict[str, str], body: bytes) -> None:
        try:
            fast_state = await manager.hass.async_add_executor_job(
                _fast_state_from_callback_sync,
                headers,
                body,
            )
            if fast_state is not None:
                _fire_fast_native_event(fast_state)
        except Exception:
            _LOGGER.exception("Fast ANPR event processing failed; continuing with full event")

        await original_async_handle_callback(headers, body)

    async def async_fetch_mnpr_result() -> None:
        state = await manager.hass.async_add_executor_job(manager._fetch_mnpr_sync)
        if state is None:
            raise ValueError("MNPR did not return an ANPR event")
        try:
            _fire_fast_native_event(state)
        except Exception:
            _LOGGER.exception("Fast ANPR manual event processing failed; continuing with full event")
        manager._apply_state(state, emit_events=True)

    manager.async_register_fast_event_listener = async_register_fast_event_listener  # type: ignore[attr-defined, method-assign]
    manager.async_handle_callback = async_handle_callback  # type: ignore[method-assign]
    manager.async_fetch_mnpr_result = async_fetch_mnpr_result  # type: ignore[method-assign]
