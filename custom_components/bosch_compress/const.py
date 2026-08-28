"""Constants for the Bosch Compress integration."""

from datetime import timedelta
from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "bosch_compress"
CONFIG_ENTRY_VERSION: Final = 2

CONF_ACCESS_TOKEN: Final = "access_token"

DEFAULT_NAME: Final = "Bosch Compress"
DEFAULT_SCAN_INTERVAL: Final = timedelta(seconds=60)

PLATFORMS: Final = (
    Platform.BINARY_SENSOR,
    Platform.CLIMATE,
    Platform.DATETIME,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TEXT,
    Platform.WATER_HEATER,
)
