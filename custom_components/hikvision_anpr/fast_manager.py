from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import Any

from homeassistant.core import callback

from .const import STATE_CONNECTED, STATE_UNKNOWN
from .manager import HikvisionANPRManager, LatestEventState, _value_or_unknown
from .parser import sanitize_filename


class HikvisionANPRFastManager(HikvisionANPRManager):
    """Hikvision ANPR manager with a separate fast metadata event path.""