from __future__ import annotations

import ipaddress
import logging

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DATA_MANAGERS, DOMAIN
from .manager import HikvisionANPRManager

_LOGGER = logging.getLogger(__name__)

# Hikvision callbacks may contain XML plus several high-resolution JPEGs. Keep
# the limit local to this unauthenticated camera endpoint rather than changing
# Home Assistant's global HTTP limit.
_MAX_CALLBACK_BODY_BYTES = 64 * 1024 * 1024
_READ_CHUNK_SIZE = 64 * 1024


def _normalize_ip(value: str | None) -> str | None:
    if not value:
        return None
    candidate = value.split("%", 1)[0]
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return candidate


class HikvisionANPRView(HomeAssistantView):
    requires_auth = False
    url = f"/api/{DOMAIN}/{{entry_id}}"
    name = f"api:{DOMAIN}:callback"

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def post(self, request: web.Request, entry_id: str) -> web.Response:
        domain_data = self._hass.data.get(DOMAIN, {})
        managers: dict[str, HikvisionANPRManager] = domain_data.get(DATA_MANAGERS, {})
        manager = managers.get(entry_id)
        if manager is None:
            return web.Response(status=503, text="Hikvision ANPR entry is not ready")

        allowed_ips: set[str] = getattr(manager, "_allowed_callback_ips", set())
        remote_ip = _normalize_ip(request.remote)
        if allowed_ips and remote_ip not in allowed_ips:
            _LOGGER.warning(
                "Rejected Hikvision ANPR callback from unexpected source %s",
                remote_ip or "unknown",
            )
            return web.Response(status=403, text="Unexpected callback source")

        if (
            request.content_length is not None
            and request.content_length > _MAX_CALLBACK_BODY_BYTES
        ):
            _LOGGER.warning(
                "Rejected Hikvision ANPR callback larger than %s bytes",
                _MAX_CALLBACK_BODY_BYTES,
            )
            return web.Response(status=413, text="ANPR callback is too large")

        body = bytearray()
        async for chunk in request.content.iter_chunked(_READ_CHUNK_SIZE):
            body.extend(chunk)
            if len(body) > _MAX_CALLBACK_BODY_BYTES:
                _LOGGER.warning(
                    "Rejected Hikvision ANPR callback after exceeding %s bytes",
                    _MAX_CALLBACK_BODY_BYTES,
                )
                return web.Response(status=413, text="ANPR callback is too large")

        headers = {key.lower(): value for key, value in request.headers.items()}
        await manager.async_handle_callback(headers, bytes(body))
        return web.Response(
            text=(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<ResponseStatus version="2.0" xmlns="http://www.isapi.org/ver20/XMLSchema">'
                '<requestURL></requestURL><statusCode>1</statusCode><statusString>OK</statusString>'
                '</ResponseStatus>'
            ),
            content_type="application/xml",
        )
