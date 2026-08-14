from __future__ import annotations

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DATA_MANAGERS, DOMAIN
from .manager import HikvisionANPRManager


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

        body = await request.read()
        headers = {key.lower(): value for key, value in request.headers.items()}
        await manager.async_handle_callback(headers, body)
        return web.Response(
            text=(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<ResponseStatus version="2.0" xmlns="http://www.isapi.org/ver20/XMLSchema">'
                '<requestURL></requestURL><statusCode>1</statusCode><statusString>OK</statusString>'
                '</ResponseStatus>'
            ),
            content_type="application/xml",
        )
