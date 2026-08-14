from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import replace
import datetime as dt
import ipaddress
import logging
import socket
from threading import Lock
from urllib.parse import urlparse

from homeassistant.components.network import async_get_source_ip
from homeassistant.helpers.network import NoURLAvailableError, get_url

from .const import STATE_DISCONNECTED
from .manager import (
    HikvisionANPRManager,
    LatestEventState,
    _value_or_unknown,
)
from .parser import sanitize_filename

_LOGGER = logging.getLogger(__name__)

_MAX_RECENT_EVENT_IDS = 128


def attach_runtime_stability(manager: HikvisionANPRManager) -> None:
    """Attach low-risk runtime fixes without changing the core parser pipeline."""

    recent_event_ids: deque[str] = deque()
    recent_event_id_set: set[str] = set()
    recent_event_lock = Lock()
    persist_lock = asyncio.Lock()
    callback_lock = asyncio.Lock()

    manager._allowed_callback_ips = set()  # type: ignore[attr-defined]  # noqa: SLF001
    manager._callback_status = "initializing"  # type: ignore[attr-defined]  # noqa: SLF001
    manager._callback_last_error = None  # type: ignore[attr-defined]  # noqa: SLF001
    manager._last_callback_at = None  # type: ignore[attr-defined]  # noqa: SLF001
    manager._last_callback_event_id = None  # type: ignore[attr-defined]  # noqa: SLF001

    def _refresh_coordinator() -> None:
        data = manager.coordinator.data
        if data is not None:
            manager.coordinator.async_set_updated_data(data)

    def _set_callback_health(status: str, error: str | None = None) -> None:
        previous_status = getattr(manager, "_callback_status", None)
        previous_error = getattr(manager, "_callback_last_error", None)
        manager._callback_status = status  # type: ignore[attr-defined]  # noqa: SLF001
        manager._callback_last_error = error  # type: ignore[attr-defined]  # noqa: SLF001
        if previous_status != status or previous_error != error:
            _refresh_coordinator()

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

    def _resolve_camera_ips_sync() -> set[str]:
        host = manager.host.strip().strip("[]")
        try:
            return {str(ipaddress.ip_address(host))}
        except ValueError:
            pass

        resolved: set[str] = set()
        for info in socket.getaddrinfo(
            host,
            manager.port,
            type=socket.SOCK_STREAM,
        ):
            address = info[4][0].split("%", 1)[0]
            try:
                resolved.add(str(ipaddress.ip_address(address)))
            except ValueError:
                continue
        return resolved

    def _stable_event_id_from_payload(payload: dict) -> str | None:
        """Return the same stable ID as manager.py when camera UUID is present."""
        root = manager._find_event_dict(payload)  # noqa: SLF001
        if root is None or _value_or_unknown(root.get("eventType")).upper() != "ANPR":
            return None

        event_uuid_raw = root.get("UUID")
        if not event_uuid_raw:
            return None

        anpr = root.get("ANPR") if isinstance(root.get("ANPR"), dict) else {}
        event_time = _value_or_unknown(root.get("dateTime"))
        plate = _value_or_unknown(
            anpr.get("licensePlate") or anpr.get("originalLicensePlate")
        )
        event_uuid = _value_or_unknown(event_uuid_raw)
        return (
            f"{manager._event_fragment(event_time)}_"  # noqa: SLF001
            f"{sanitize_filename(plate)}_"
            f"{sanitize_filename(event_uuid)[:40]}"
        )

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
        """Build the callback from HA protocol and the route toward the camera."""
        allowed_ips: set[str] = getattr(manager, "_allowed_callback_ips", set())
        target_ip = next(
            (value for value in allowed_ips if ":" not in value),
            next(iter(allowed_ips), None),
        )

        if target_ip:
            local_ip = await async_get_source_ip(manager.hass, target_ip=target_ip)
        else:
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

        callback_host = f"[{local_ip}]" if ":" in local_ip else local_ip
        manager._callback_base_url = f"{scheme}://{callback_host}:{port}"  # noqa: SLF001

    async def async_initialize():
        manager.device_details = await manager.hass.async_add_executor_job(
            manager._fetch_device_details_sync  # noqa: SLF001
        )
        await manager.hass.async_add_executor_job(manager._ensure_csv)  # noqa: SLF001

        try:
            allowed_ips = await manager.hass.async_add_executor_job(
                _resolve_camera_ips_sync
            )
        except Exception as err:  # noqa: BLE001
            allowed_ips = set()
            _LOGGER.warning(
                "Could not resolve camera IP for callback filtering; callback IP "
                "restriction will be disabled: %s",
                err,
            )
        manager._allowed_callback_ips = allowed_ips  # type: ignore[attr-defined]  # noqa: SLF001

        restored_state = await manager.hass.async_add_executor_job(
            manager._load_last_state_sync  # noqa: SLF001
        )
        if restored_state is not None:
            if restored_state.event_id:
                _claim_event_id(restored_state.event_id)
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

        claimed_id = _stable_event_id_from_payload(payload)
        if claimed_id is not None and not _claim_event_id(claimed_id):
            _LOGGER.debug("Ignoring duplicate ANPR callback event %s", claimed_id)
            return None

        record = None
        try:
            record = manager._extract_record(payload, raw, parts)  # noqa: SLF001
            if record is None:
                if claimed_id is not None:
                    _forget_event_id(claimed_id)
                return None

            if claimed_id is None:
                claimed_id = record.event_id
                if not _claim_event_id(claimed_id):
                    _LOGGER.debug(
                        "Ignoring duplicate ANPR callback event %s",
                        claimed_id,
                    )
                    return None

            manager._append_csv(record)  # noqa: SLF001
            return manager._state_from_record(record)  # noqa: SLF001
        except Exception:
            if claimed_id is not None:
                _forget_event_id(claimed_id)
            raise

    async def async_handle_callback(headers: dict[str, str], body: bytes) -> None:
        manager._last_callback_at = dt.datetime.now(dt.timezone.utc).isoformat()  # type: ignore[attr-defined]  # noqa: SLF001

        # Full callbacks may contain multiple large images. Serialize them in
        # arrival order so an older callback cannot finish after a newer one and
        # overwrite the Last ANPR state/images.
        async with callback_lock:
            try:
                state = await manager.hass.async_add_executor_job(
                    _handle_callback_dedup_sync,
                    headers,
                    body,
                )
            except Exception as err:
                _set_callback_health("error", str(err))
                raise

            if state is None:
                return

            manager._callback_status = "active"  # type: ignore[attr-defined]  # noqa: SLF001
            manager._callback_last_error = None  # type: ignore[attr-defined]  # noqa: SLF001
            manager._last_callback_event_id = state.event_id  # type: ignore[attr-defined]  # noqa: SLF001
            manager._set_state(state)  # noqa: SLF001
            manager._fire_bus_event(state)  # noqa: SLF001
            manager._fire_native_event(state)  # noqa: SLF001
            await _persist_state(state)

    async def async_fetch_mnpr_result() -> None:
        async with callback_lock:
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
