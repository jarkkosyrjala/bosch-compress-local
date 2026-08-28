"""Runtime data types for Bosch Compress config entries."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry

if TYPE_CHECKING:
    from .coordinator import BoschDataUpdateCoordinator

from .topology import BoschTopology


@dataclass(slots=True)
class BoschRuntimeData:
    """Resources owned by one loaded Bosch Compress config entry."""

    coordinator: BoschDataUpdateCoordinator
    gateway_uuid: str
    host: str
    topology: BoschTopology = field(default_factory=BoschTopology)


type BoschConfigEntry = ConfigEntry[BoschRuntimeData]
