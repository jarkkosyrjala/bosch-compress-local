"""Runtime data types for Bosch Compress config entries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry

if TYPE_CHECKING:
    from .coordinator import BoschDataUpdateCoordinator
    from .h60 import H60MqttClient


@dataclass(slots=True)
class BoschRuntimeData:
    """Resources owned by one loaded Bosch Compress config entry."""

    coordinator: BoschDataUpdateCoordinator
    gateway_uuid: str
    host: str
    h60: H60MqttClient | None = None


type BoschConfigEntry = ConfigEntry[BoschRuntimeData]
