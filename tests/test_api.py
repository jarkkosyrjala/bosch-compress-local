"""Tests for the Bosch local API client."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from aiohttp import ClientConnectionError

from custom_components.bosch_compress.api import (
    KNOWN_DISCOVERY_ROOTS,
    BoschAuthenticationError,
    BoschClient,
    BoschConnectionError,
    BoschEncryption,
    BoschEndpointNotFoundError,
    BoschForbiddenError,
    BoschResponseError,
)
from custom_components.bosch_compress.models import BoschEndpoint


class FakeResponse:
    """Minimal aiohttp response context manager."""

    def __init__(self, status: int, body: str | bytes = b"") -> None:
        """Initialize a fake response."""
        self.status = status
        self._body = body.decode() if isinstance(body, bytes) else body

    async def __aenter__(self) -> FakeResponse:
        """Enter the response context."""
        return self

    async def __aexit__(self, *_args: Any) -> None:
        """Exit the response context."""

    async def text(self) -> str:
        """Return the response body as text."""
        return self._body

    async def read(self) -> bytes:
        """Return the response body as bytes."""
        return self._body.encode()


class FakeSession:
    """Minimal fake aiohttp client session."""

    def __init__(self, responses: dict[str, FakeResponse | Exception]) -> None:
        """Initialize canned responses."""
        self.responses = responses
        self.requested: list[str] = []
        self.puts: list[tuple[str, bytes]] = []

    def get(self, url: str, **_kwargs: Any) -> FakeResponse:
        """Return a canned GET response."""
        path = "/" + url.split("/", 3)[-1]
        self.requested.append(path)
        response = self.responses[path]
        if isinstance(response, Exception):
            raise response
        return response

    def put(self, url: str, *, data: bytes, **_kwargs: Any) -> FakeResponse:
        """Record a PUT and return a canned response."""
        path = "/" + url.split("/", 3)[-1]
        self.puts.append((path, data))
        response = self.responses[path]
        if isinstance(response, Exception):
            raise response
        return response


def encrypted_response(encryption: BoschEncryption, payload: Any) -> FakeResponse:
    """Create an encrypted Bosch response."""
    return FakeResponse(200, encryption.encrypt_json(payload))


def test_endpoint_model_preserves_metadata() -> None:
    """Endpoint metadata is parsed into stable typed fields."""
    endpoint = BoschEndpoint.from_payload(
        {
            "id": "/test/value",
            "type": "floatValue",
            "value": 21.5,
            "writeable": 1,
            "minValue": 10,
            "maxValue": 30.0,
            "allowedValues": [10, 20, 30],
            "unitOfMeasure": "C",
        }
    )

    assert endpoint.path == "/test/value"
    assert endpoint.value == 21.5
    assert endpoint.value_type == "floatValue"
    assert endpoint.writable is True
    assert endpoint.minimum == 10
    assert endpoint.maximum == 30.0
    assert endpoint.allowed_values == (10, 20, 30)
    assert endpoint.unit == "C"


@pytest.mark.parametrize(
    ("value_key", "value_type"),
    [("values", "errorList"), ("switchPoints", "switchProgram")],
)
def test_endpoint_model_preserves_complex_values(
    value_key: str, value_type: str
) -> None:
    """Array and schedule endpoints retain their non-scalar payload values."""
    endpoint = BoschEndpoint.from_payload(
        {
            "id": "/test/complex",
            "type": value_type,
            value_key: [{"example": 1}],
        }
    )

    assert endpoint.value == [{"example": 1}]


def test_encryption_round_trip() -> None:
    """Bosch JSON encryption accepts gateway whitespace and round-trips JSON."""
    encryption = BoschEncryption("12-34", "password")
    payload = {"value": [1, "two", True]}

    encrypted = encryption.encrypt_json(payload)
    assert encryption.decrypt_json(b"\r\n" + encrypted + b"\n") == payload


def test_get_put_and_typed_errors() -> None:
    """The client decrypts reads, encrypts writes, and translates HTTP errors."""
    encryption = BoschEncryption("token", "password")
    session = FakeSession(
        {
            "/value": encrypted_response(
                encryption,
                {"id": "/value", "type": "floatValue", "value": 12.5},
            ),
            "/write": FakeResponse(204),
            "/unauthorized": FakeResponse(401),
            "/forbidden": FakeResponse(403),
            "/missing": FakeResponse(404),
            "/broken": FakeResponse(500),
            "/offline": ClientConnectionError("offline"),
        }
    )
    client = BoschClient(session, "gateway", "token", "password")  # type: ignore[arg-type]

    async def run_test() -> None:
        endpoint = await client.async_get_endpoint("/value")
        assert endpoint.value == 12.5
        await client.async_put("/write", 1)
        assert encryption.decrypt_json(session.puts[0][1]) == {"value": 1}

        for path, error_type in (
            ("/unauthorized", BoschAuthenticationError),
            ("/forbidden", BoschForbiddenError),
            ("/missing", BoschEndpointNotFoundError),
            ("/broken", BoschResponseError),
            ("/offline", BoschConnectionError),
        ):
            with pytest.raises(error_type):
                await client.async_get(path)

    asyncio.run(run_test())


def test_discovery_is_recursive_bounded_and_skips_secrets() -> None:
    """Discovery follows safe descendants once and never reads password paths."""
    encryption = BoschEncryption("token", "password")
    session = FakeSession(
        {
            "/root": encrypted_response(
                encryption,
                {
                    "id": "/root",
                    "type": "refEnum",
                    "references": [
                        {"id": "/root/value"},
                        {"id": "/root/value"},
                        {"id": "/root/userPassword"},
                        {"id": "/gateway/firmware"},
                        {"id": "/outside"},
                    ],
                },
            ),
            "/root/value": encrypted_response(
                encryption,
                {"id": "/root/value", "type": "floatValue", "value": 4.2},
            ),
        }
    )
    client = BoschClient(session, "gateway", "token", "password")  # type: ignore[arg-type]

    endpoints = asyncio.run(
        client.async_discover_endpoints(["/root"], max_depth=2, max_endpoints=2)
    )

    assert set(endpoints) == {"/root", "/root/value"}
    assert session.requested == ["/root", "/root/value"]


def test_discovery_validates_bounds_and_paths() -> None:
    """Invalid discovery bounds and unsafe paths are rejected locally."""
    client = BoschClient(FakeSession({}), "gateway", "token", "password")  # type: ignore[arg-type]

    with pytest.raises(ValueError):
        asyncio.run(client.async_discover_endpoints(["/root"], max_depth=-1))
    with pytest.raises(ValueError):
        asyncio.run(client.async_discover_endpoints(["/root"], max_endpoints=0))
    with pytest.raises(ValueError):
        asyncio.run(client.async_get("/root/../secret"))


def test_default_discovery_includes_notification_history_not_recordings() -> None:
    """Current diagnostics are discoverable without crawling archived recordings."""
    assert "/notifications" in KNOWN_DISCOVERY_ROOTS
    assert "/recordings" not in KNOWN_DISCOVERY_ROOTS
