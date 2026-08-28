"""Tests for Bosch circuit topology discovery and persistence."""

from __future__ import annotations

from custom_components.bosch_compress.topology import (
    BoschTopology,
    discover_topology,
    merge_capability_storage,
)


def test_discovery_distinguishes_compound_entity_capabilities() -> None:
    """Only direct, relevant endpoint paths create compound entity capabilities."""
    topology = discover_topology(
        {
            "/heatingCircuits/hc1/currentRoomSetpoint",
            "/heatingCircuits/hc3/status",
            "/heatingCircuits/hc4/unrelated",
            "/heatingCircuits/hc5/nested/status",
            "/dhwCircuits/dhw1/actualTemp",
            "/dhwCircuits/dhw1/charge",
            "/dhwCircuits/dhw2/operationMode",
            "/dhwCircuits/dhw3/unrelated",
            "/dhwCircuits/dhw4/nested/status",
            "/unrelated/heatingCircuits/hc2/status",
        }
    )

    assert topology == BoschTopology(
        heating_circuits=frozenset({"hc1", "hc3"}),
        dhw_circuits=frozenset({"dhw1"}),
        dhw_charge_circuits=frozenset({"dhw1"}),
        dhw_program_circuits=frozenset({"dhw2"}),
    )


def test_persisted_topology_round_trips_deterministically() -> None:
    """Known capabilities survive restart storage with stable ordering."""
    topology = BoschTopology(
        heating_circuits=frozenset({"hc3", "hc1", "hc2"}),
        dhw_circuits=frozenset({"dhw2", "dhw1"}),
        dhw_charge_circuits=frozenset({"dhw2"}),
        dhw_program_circuits=frozenset({"dhw1"}),
    )

    stored = topology.as_mapping()

    assert stored == {
        "heating_circuits": ["hc1", "hc2", "hc3"],
        "dhw_circuits": ["dhw1", "dhw2"],
        "dhw_charge_circuits": ["dhw2"],
        "dhw_program_circuits": ["dhw1"],
    }
    assert BoschTopology.from_mapping(stored) == topology


def test_persisted_topology_rejects_malformed_circuit_ids() -> None:
    """Invalid storage cannot be turned into arbitrary gateway paths."""
    topology = BoschTopology.from_mapping(
        {
            "heating_circuits": ["hc1", "", "../hc2", 3, None],
            "dhw_circuits": [".", "..", "tank"],
            "dhw_charge_circuits": "dhw1",
            "dhw_program_circuits": {"dhw1": True},
        }
    )

    assert topology == BoschTopology(
        heating_circuits=frozenset({"hc1"}),
        dhw_circuits=frozenset({"tank"}),
    )


def test_current_discovery_monotonically_extends_persisted_topology() -> None:
    """New circuits are added without losing temporarily absent known circuits."""
    persisted = BoschTopology(
        heating_circuits=frozenset({"hc1"}),
        dhw_circuits=frozenset({"dhw1"}),
        dhw_charge_circuits=frozenset({"dhw1"}),
    )
    current = discover_topology(
        {
            "/heatingCircuits/hc2/currentRoomSetpoint",
            "/dhwCircuits/dhw2/actualTemp",
            "/dhwCircuits/dhw2/operationMode",
        }
    )

    assert persisted.merged(current) == BoschTopology(
        heating_circuits=frozenset({"hc1", "hc2"}),
        dhw_circuits=frozenset({"dhw1", "dhw2"}),
        dhw_charge_circuits=frozenset({"dhw1"}),
        dhw_program_circuits=frozenset({"dhw2"}),
    )


def test_topology_persistence_preserves_other_capability_fields() -> None:
    """Topology updates do not overwrite optional capabilities managed elsewhere."""
    stored = {
        "heating_circuits": ["hc1"],
        "energy_recordings": ["compressor"],
    }

    merged = merge_capability_storage(
        stored,
        BoschTopology(heating_circuits=frozenset({"hc1", "hc2"})),
    )

    assert merged == {
        "heating_circuits": ["hc1", "hc2"],
        "dhw_circuits": [],
        "dhw_charge_circuits": [],
        "dhw_program_circuits": [],
        "energy_recordings": ["compressor"],
    }
