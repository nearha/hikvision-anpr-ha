from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import replace
import logging
from threading import Lock
from urllib.parse import urlparse

from homeassistant.components.network import async_get_source_ip
from homeassistant.helpers.network import NoURLAvailableError, get_url

from .const import STATE_DISCONNECTED
from .manager import HikvisionANPRManager, LatestEventState

_LOGGER = logging.getLogger(__name__)

_MAX_RECENT_EVENT_IDS = 128


def attach_runtime_stability(manager: HikvisionANPRManager) -> None:
    """Attach low-risk runtime fixes without changing the core parser pipeline."""

    recent_event_ids: deque[str] = deque()
    recent_event_id_set: set[str] = set()
    recent_event_lock = Lock()
    persist_lock = asyncio.Lock()

    def _claim_event_id(event_id: str) -> bool:
        with recent_event_lock:
            if event_id in recent_event_id_set:
                return False

            recent_event_ids.append(event_id)
            recent_event_id_set.add(event_id)
            while len(recent_event_ids) > _MAX_RECENT_EVENT_IDS:
                expired = recent_event_ids.popleft()
                recent_event_id_set.discard(expired)
            return True

    def _forget_event_id(event_id: str) -> None:
        with recent_event_lock:
            recent_event_id_set.discard(event_id)
            try:
                recent_event_ids.remove(event_id)
            except ValueError:
                pass

    async def _persist_state(state: LatestEventState) -> None:
        async with persist_lock:
            try:
                await manager.hass.async_add_executor_job(
                    manager._save_last_state_sync,  # noqa: SLF001
                    state,
                )
            except Exception as err:  # noqa: BLE001
                _LOGGER.exception("Failed to persist last ANPR state: %s", err)

    async def _discover_callback_base_url() -> None:
        """Build the callback from Home Assistant's protocol, not camera protocol."""
        local_ip = await async_get_source_ip(manager.hass)
        if not local_ip:
            raise ValueError("Home Assistant local IP could not be determined")

        scheme = "http"
        port = 8123
        try:
            url = get_url(
                manager.hass,
                allow_internal=True,
                allow_external=False,
                allow_ip=True,
                prefer_external=False,
                prefer_cloud=False,
                require_ssl=False,
            )
            parsed = urlparse(url)
            if parsed.scheme.lower() in {"http", "https"}:
                scheme = parsed.scheme.lower()
            if parsed.port:
                port = parsed.port
            elif parsed.scheme.lower() == "https":
                port = 443
            elif parsed.scheme.lower() == "http":
                port = 80
        except NoURLAvailableError:
            pass

        manager._callback_base_url = f"{scheme}://{local_ip}:{port}"  # noqa: SLF001

    async def async_initialize():
        manager.device_details = await manager.hass.async_add_executor_job(
            manager._fetch_device_details_sync  # noqa: SLF001
        )
        await manager.hass.async_add_executor_job(manager._ensure_csv)  # noqa: SLF001
        restored_state = await manager.hass.async_add_executor_job(
            manager._load_last_state_sync  # noqa: SLF001
        )
        if restored_state is not None:
            manager._set_state(  # noqa: SLF001
                replace(restored_state, status=STATE_DISCONNECTED, last_error=None)
            )
        else:
            manager._set_state(  # noqa: SLF001
                replace(manager._current_state, status=STATE_DISCONNECTED, last_error=None)  # noqa: SLF001
            )
        await _discover_callback_base_url()
        return manager.device_details

    def _handle_callback_dedup_sync(
        headers: dict[str, str],
        body: bytes,
    ) -> LatestEventState | None:
        payload, raw, parts = manager._parse_payload(headers, body)  # noqa: SLF001
        if payload is None:
            return None

        record = manager._extract_record(payload, raw, parts)  # noqa: SLF001
        if record is None:
            return None

        if not _claim_event_id(record.event_id):
            _LOGGER.debug("Ignoring duplicate ANPR callback event %s", record.event_id)
            return None

        try:
            manager._append_csv(record)  # noqa: SLF001
        except Exception:
            _forget_event_id(record.event_id)
            raise

        return manager._state_from_record(record)  # noqa: SLF001

    async def async_handle_callback(headers: dict[str, str], body: bytes) -> None:
        state = await manager.hass.async_add_executor_job(
            _handle_callback_dedup_sync,
            headers,
            body,
        )
        if state is None:
            return

        manager._set_state(state)  # noqa: SLF001
        manager._fire_bus_event(state)  # noqa: SLF001
        manager._fire_native_event(state)  # noqa: SLF001
        await _persist_state(state)

    async def async_fetch_mnpr_result() -> None:
        state = await manager.hass.async_add_executor_job(manager._fetch_mnpr_sync)  # noqa: SLF001
        if state is None:
            raise ValueError("MNPR did not return an ANPR event")

        manager._set_state(state)  # noqa: SLF001
        manager._fire_bus_event(state)  # noqa: SLF001
        manager._fire_native_event(state)  # noqa: SLF001
        await _persist_state(state)

    manager._discover_callback_base_url = _discover_callback_base_url  # type: ignore[method-assign]  # noqa: SLF001
    manager.async_initialize = async_initialize  # type: ignore[method-assign]
    manager.async_handle_callback = async_handle_callback  # type: ignore[method-assign]
    manager.async_fetch_mnpr_result = async_fetch_mnpr_result  # type: ignore[method-assign]
