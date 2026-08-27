"""Test the Bosch Compress integration manifest."""

import json
from pathlib import Path
from typing import Any

MANIFEST_PATH = (
    Path(__file__).parents[1] / "custom_components" / "bosch_compress" / "manifest.json"
)


def test_manifest_metadata() -> None:
    """Verify the integration's foundational manifest metadata."""
    manifest: dict[str, Any] = json.loads(MANIFEST_PATH.read_text())

    assert manifest["domain"] == "bosch_compress"
    assert manifest["integration_type"] == "device"
    assert manifest["iot_class"] == "local_polling"
    assert manifest["version"]
