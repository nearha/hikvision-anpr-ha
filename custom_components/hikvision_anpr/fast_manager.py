from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Callable
from typing import Any

from homeassistant.core import callback

from .const import STATE_CONNECTED, STATE_UNKNOWN
from .manager import HikvisionANPRManager, LatestEventState, _value_or_unknown
from .parser import sanitize_filename

_LOGGER = logging.getLogger(__name__)


class HikvisionANPRFastManager(HikvisionANPRManager):
    """Manager that emits a metadata-only event before image file persistence."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._fast_native_event_listeners: list[Callable[[LatestEventState], None]] = []

    @callback
    def async_register_fast_native_event_listener(self, listener: Callable[[LatestEventState], None]) -> Callable[[], None]:
        self._fast_native_event_listeners.append(listener)

        @callback
        def _remove() -> None:
            if listener in self._fast_native_event_listeners:
                self._fast_native_event_listeners.remove(listener)

        return _remove

    @callback
