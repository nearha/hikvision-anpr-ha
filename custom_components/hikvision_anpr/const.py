from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "hikvision_anpr"
PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.IMAGE, Platform.EVENT]

EVENT_TYPE = "hikvision_anpr_event"
DEFAULT_PORT = 80
DEFAULT_USE_HTTPS = False
DEFAULT_VERIFY_SSL = False
DEFAULT_AUTH_MODE = "digest"
DEFAULT_CHANNEL = 1
DEFAULT_MEDIA_DIR = "/media/hikvison_anpr"
DEFAULT_HTTP_HOST_ID = 1

CONF_PORT = "port"
CONF_USE_HTTPS = "use_https"
CONF_AUTH_MODE = "auth_mode"
CONF_CHANNEL = "channel"
CONF_MEDIA_DIR = "media_dir"
CONF_HTTP_HOST_ID = "http_host_id"

AUTH_DIGEST = "digest"
AUTH_BASIC = "basic"

IMAGE_LICENSE_PLATE = "licensePlatePicture"
IMAGE_VEHICLE = "vehiclePicture"
IMAGE_DETECTION = "detectionPicture"

STATE_UNKNOWN = "unknown"
STATE_DISCONNECTED = "disconnected"
STATE_CONNECTED = "connected"
STATE_STOPPED = "stopped"
