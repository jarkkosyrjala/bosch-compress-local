"""Discovery and persistence helpers for Bosch circuit topology."""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from .runtime import BoschRuntimeData

CONF_KNOWN_CAPABILITIES: Final = "_known_capabilities"

_HEATING_PREFIX: Final = "/heatingCircuits/"
_DHW_PREFIX: Final = "/dhwCircuits/"
_CIRCUIT_ID: Final = re.compile(r"^[A-Za-z0-9_-]+$")
_HEATING_CLIMATE_ENDPOINTS: Final = frozenset(
    {
        "actualSupplyTemperature",
        "currentRoomSetpoint",
        "manualRoomSetpoint",
        "operationMode",
        "pumpModulation",
        "roomtemperature",
        "status",
        "supplyTemperatureSetpoint",
        "temperatureRoomSetpoint",
        "temporaryRoomSetpoint",
    }
)
_DHW_WATER_HEATER_ENDPOINTS: Final = frozenset(
    {"actualTemp", "currentSetpoint", "status"}
)


def _stored_ids(value: object) -> frozenset[str]:
    """Return valid, normalized circuit IDs from persisted JSON data."""
    if not isinstance(value, list):
        return frozenset()
    return frozenset(
        circuit_id
        for circuit_id in value
        if isinstance(circuit_id, str)
        and circuit_id
        and _CIRCUIT_ID.fullmatch(circuit_id)
    )


def _path_parts(path: str, prefix: str) -> tuple[str, str] | None:
    """Return a circuit ID and direct endpoint name for a gateway path."""
    if not path.startswith(prefix):
        return None
    parts = path.removeprefix(prefix).split("/")
    if len(parts) != 2 or not all(parts) or _CIRCUIT_ID.fullmatch(parts[0]) is None:
        return None
    return parts[0], parts[1]


@dataclass(frozen=True, slots=True)
class BoschTopology:
    """Compound-entity capabilities observed for one Bosch gateway."""

    heating_circuits: frozenset[str] = frozenset()
    dhw_circuits: frozenset[str] = frozenset()
    dhw_charge_circuits: frozenset[str] = frozenset()
    dhw_program_circuits: frozenset[str] = frozenset()

    @classmethod
    def from_mapping(cls, value: object) -> BoschTopology:
        """Decode a topology snapshot from config-entry JSON data."""
        if not isinstance(value, Mapping):
            return cls()
        return cls(
            heating_circuits=_stored_ids(value.get("heating_circuits")),
            dhw_circuits=_stored_ids(value.get("dhw_circuits")),
            dhw_charge_circuits=_stored_ids(value.get("dhw_charge_circuits")),
            dhw_program_circuits=_stored_ids(value.get("dhw_program_circuits")),
        )

    def merged(self, other: BoschTopology) -> BoschTopology:
        """Return the monotonic union of two known topology snapshots."""
        return BoschTopology(
            heating_circuits=self.heating_circuits | other.heating_circuits,
            dhw_circuits=self.dhw_circuits | other.dhw_circuits,
            dhw_charge_circuits=(self.dhw_charge_circuits | other.dhw_charge_circuits),
            dhw_program_circuits=(
                self.dhw_program_circuits | other.dhw_program_circuits
            ),
        )

    def as_mapping(self) -> dict[str, list[str]]:
        """Encode the topology deterministically for config-entry storage."""
        return {
            "heating_circuits": sorted(self.heating_circuits),
            "dhw_circuits": sorted(self.dhw_circuits),
            "dhw_charge_circuits": sorted(self.dhw_charge_circuits),
            "dhw_program_circuits": sorted(self.dhw_program_circuits),
        }


def merge_capability_storage(stored: object, topology: BoschTopology) -> dict[str, Any]:
    """Update topology fields while retaining capabilities owned elsewhere."""
    capabilities = dict(stored) if isinstance(stored, Mapping) else {}
    capabilities.update(topology.as_mapping())
    return capabilities


def discover_topology(paths: Collection[str]) -> BoschTopology:
    """Infer compound-entity capabilities from discovered endpoint paths."""
    heating_circuits: set[str] = set()
    dhw_circuits: set[str] = set()
    dhw_charge_circuits: set[str] = set()
    dhw_program_circuits: set[str] = set()

    for path in paths:
        if (parts := _path_parts(path, _HEATING_PREFIX)) is not None:
            circuit_id, endpoint_name = parts
            if endpoint_name in _HEATING_CLIMATE_ENDPOINTS:
                heating_circuits.add(circuit_id)
            continue
        if (parts := _path_parts(path, _DHW_PREFIX)) is None:
            continue
        circuit_id, endpoint_name = parts
        if endpoint_name in _DHW_WATER_HEATER_ENDPOINTS:
            dhw_circuits.add(circuit_id)
        if endpoint_name == "charge":
            dhw_charge_circuits.add(circuit_id)
        elif endpoint_name == "operationMode":
            dhw_program_circuits.add(circuit_id)

    return BoschTopology(
        heating_circuits=frozenset(heating_circuits),
        dhw_circuits=frozenset(dhw_circuits),
        dhw_charge_circuits=frozenset(dhw_charge_circuits),
        dhw_program_circuits=frozenset(dhw_program_circuits),
    )


def effective_topology(runtime_data: BoschRuntimeData) -> BoschTopology:
    """Include endpoints injected by tests or discovered after runtime creation."""
    current = discover_topology(runtime_data.coordinator.discovered_endpoints)
    return runtime_data.topology.merged(current)
