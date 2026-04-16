from __future__ import annotations

from aiohttp import web
from homeassistant.components.http import HomeAssistantView

from .manager import HikvisionANPRManager


class HikvisionANPRView(HomeAssistantView):
    requires_auth = False

    def __init__(self, manager: HikvisionANPRManager) -> None:
        self._manager = manager
        self.url = manager.callback_path
        self.name = f"api:{manager.domain}:{manager.entry.entry_id}"

    async def post(self, request: web.Request) -> web.Response:
        body = await request.read()
        headers = {key.lower(): value for key, value in request.headers.items()}
        await self._manager.async_handle_callback(headers, body)
        return web.Response(
            text=(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<ResponseStatus version="2.0" xmlns="http://www.isapi.org/ver20/XMLSchema">'
                '<requestURL></requestURL><statusCode>1</statusCode><statusString>OK</statusString>'
                '</ResponseStatus>'
            ),
            content_type="application/xml",
        )
