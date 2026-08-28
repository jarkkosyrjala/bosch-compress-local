"""Runtime data types for Bosch Compress config entries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry

if TYPE_CHECKING:
    from .coordinator import BoschDataUpdateCoordinator


@dataclass(slots=True)
class BoschRuntimeData:
    """Resources owned by one loaded Bosch Compress config entry."""

    coordinator: BoschDataUpdateCoordinator
    gateway_uuid: str
    host: str


type BoschConfigEntry = ConfigEntry[BoschRuntimeData]
