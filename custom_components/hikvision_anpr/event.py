from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import datetime as dt
from typing import Any

from homeassistant.components.event import EventEntity, EventEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import STATE_CONNECTED, STATE_UNKNOWN
from .manager import HikvisionANPRManager, LatestEventState, _value_or_unknown
from .parser import sanitize_filename

FAST_EVENT_TYPE = "anpr_fast"
COMPLETE_EVENT_TYPE = "anpr"
FAST_LISTENERS_ATTR = "_fast_metadata_event_listeners"
FAST_PATCHED_ATTR = "_fast_metadata_event_patch_installed"


@dataclass(frozen=True, kw_only=True)
class HikvisionANPREventDescription(EventEntityDescription):
    event_type: str
    is_fast: bool = False


FAST_EVENT_DESCRIPTION = HikvisionANPREventDescription(
    key="fast_event",
    name="Fast ANPR event",
    event_type=FAST_EVENT_TYPE,
    is_fast=True,
)

COMPLETE_EVENT_DESCRIPTION = HikvisionANPREventDescription(
    key="last_event",
    name="Last