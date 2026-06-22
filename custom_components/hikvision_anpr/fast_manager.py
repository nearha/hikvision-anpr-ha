from __future__ import annotations

import datetime as dt
from collections.abc import Callable

from homeassistant.core import callback

from .const import STATE_CONNECTED
from .manager import HikvisionANPRManager, LatestEventState, _value_or_unknown
from .parser import sanitize_filename


class HikvisionANPRFastManager(HikvisionANPRManager):
    """Manager with a metadata-only event emitted before image persistence."""

    def __init__(self, hass, entry) -> None:
        super().__init__(hass, entry)
        self._fast_event_listeners: list[Callable[[LatestEventState], None]] = []

    @callback
    def async_register_fast_event_listener(self, listener: Callable[[LatestEventState], None]) -> Callable[[], None]:
        self._fast_event_listeners.append(listener)

        @callback
        def _remove() -> None:
            if listener in self._fast_event_listeners:
                self._fast_event_listeners.remove(listener)

        return _remove

    @callback
    def _fire_fast_native_event(self, state: LatestEventState) -> None:
        for listener in list(self._fast_event_listeners):
            try:
                listener(state)
            except Exception:
                self._logger_exception_fast_event()

    def _logger_exception_fast_event(self) -> None:
        import logging

        logging.getLogger(__name__).exception("Error delivering fast ANPR event entity update")

    def _fast_state_from_callback_sync(self, headers: dict[str, str], body: bytes) -> LatestEventState | None:
        payload, _raw, _parts = self._parse_payload(headers, body)
        if payload is None:
            return None
        root = self._find_event_dict(payload)
        if root is None or _value_or_unknown(root.get("eventType")).upper() != "ANPR":
            return None

        anpr = root.get("ANPR") if isinstance(root.get("ANPR"), dict) else {}
        vehicle_info = anpr.get("vehicleInfo") if isinstance(anpr.get("vehicleInfo"), dict) else {}
        event_time = _value_or_unknown(root.get("dateTime"))
        plate = _value_or_unknown(anpr.get("licensePlate") or anpr.get("originalLicensePlate"))
        event_uuid = _value_or_unknown(root.get("UUID") or f"evt_{dt.datetime.now().strftime('%H%M%S%f')}")
        event_id = f"{self._event_fragment(event_time)}_{sanitize_filename(plate)}_{sanitize_filename(event_uuid)[:40]}"

        return LatestEventState(
            status=STATE_CONNECTED,
            last_error=None,
            event_id=event_id,
            event_time=event_time,
            plate=plate,
            confidence=_value_or_unknown(anpr.get("confidenceLevel")),
            direction=_value_or_unknown(anpr.get("direction")),
            list_result=_value_or_unknown(anpr.get("vehicleListName")),
            country=self._translate_country(anpr.get("country")),
            brand=self._translate_brand(vehicle_info.get("vehicleLogoRecog")),
            type=_value_or_unknown(anpr.get("vehicleType")),
            color=_value_or_unknown(vehicle_info.get("color")),
        )

    async def async_handle_callback(self, headers: dict[str, str], body: bytes) -> None:
        fast_state = await self.hass.async_add_executor_job(self._fast_state_from_callback_sync, headers, body)
        if fast_state is not None:
            self._fire_fast_native_event(fast_state)

        state = await self.hass.async_add_executor_job(self._handle_callback_sync, headers, body)
        if state is None:
            return
        self._apply_state(state, emit_events=True)

    async def async_fetch_mnpr_result(self) -> None:
        state = await self.hass.async_add_executor_job(self._fetch_mnpr_sync)
        if state is None:
            raise ValueError("MNPR did not return an ANPR event")
        self._fire_fast_native_event(state)
        self._apply_state(state, emit_events=True)
