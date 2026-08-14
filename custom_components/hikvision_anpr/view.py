from __future__ import annotations

from aiohttp import web
from homeassistant.components.http import HomeAssistantView

from .manager import HikvisionANPRManager


_FAST_SCAN_LIMIT = 1024 * 1024


class HikvisionANPRView(HomeAssistantView):
    requires_auth = False

    def __init__(self, manager: HikvisionANPRManager) -> None:
        self._manager = manager
        self.url = manager.callback_path
        self.name = f"api:{manager.domain}:{manager.entry.entry_id}"

    async def post(self, request: web.Request) -> web.Response:
        headers = {key.lower(): value for key, value in request.headers.items()}
        body = bytearray()
        fast_emitted = False
        fast_scan_finished = False

        fast_handler = getattr(self._manager, "async_try_fast_event", None)
        full_handler = getattr(self._manager, "async_handle_full_callback", None)

        async for chunk in request.content.iter_any():
            body.extend(chunk)

            if (
                fast_handler is not None
                and not fast_emitted
                and not fast_scan_finished
                and (b"ANPR" in body or b"anpr" in body)
            ):
                scan_length = min(len(body), _FAST_SCAN_LIMIT)
                fast_emitted = await fast_handler(headers, bytes(body[:scan_length]))
                if not fast_emitted and len(body) >= _FAST_SCAN_LIMIT:
                    fast_scan_finished = True

        complete_body = bytes(body)

        if fast_emitted and full_handler is not None:
            await full_handler(headers, complete_body)
        else:
            await self._manager.async_handle_callback(headers, complete_body)

        return web.Response(
            text=(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<ResponseStatus version="2.0" xmlns="http://www.isapi.org/ver20/XMLSchema">'
                '<requestURL></requestURL><statusCode>1</statusCode><statusString>OK</statusString>'
                '</ResponseStatus>'
            ),
            content_type="application/xml",
        )
