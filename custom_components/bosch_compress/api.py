"""Async local API client for Bosch IVT-compatible IP modules."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
from collections import deque
from collections.abc import Collection, Mapping
from typing import Any, Final
from urllib.parse import unquote

from aiohttp import ClientError, ClientResponse, ClientSession, ClientTimeout
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .models import BoschEndpoint

_AES_BLOCK_SIZE: Final = 16
_DEFAULT_TIMEOUT: Final = ClientTimeout(total=10)
_IVT_MAGIC: Final = bytes.fromhex(
    "867845e97c4e29dce522b9a7d3a3e07b152bffadddbed7f5ffd842e9895ad1e4"
)
_HTTP_HEADERS: Final = {
    "Connection": "keep-alive",
    "Content-Type": "application/json",
    "User-Agent": "TeleHeater",
}

KNOWN_DISCOVERY_ROOTS: Final = (
    "/application",
    "/dhwCircuits",
    "/gateway",
    "/gservice_tariff",
    "/heatSources",
    "/heatingCircuits",
    "/notifications",
    "/solarCircuits",
    "/system",
)
_SENSITIVE_PATH_MARKERS: Final = (
    "credential",
    "password",
    "passwd",
    "secret",
    "token",
)
_DISCOVERY_EXCLUDED_PATHS: Final = frozenset(
    {
        # This path returns a firmware blob rather than encrypted JSON metadata.
        "/gateway/firmware",
    }
)


class BoschApiError(Exception):
    """Base exception for Bosch local API failures."""


class BoschConnectionError(BoschApiError):
    """Raised when the Bosch IP module cannot be reached."""


class BoschAuthenticationError(BoschApiError):
    """Raised when a response cannot be decrypted with the configured credentials."""


class BoschEndpointNotFoundError(BoschApiError):
    """Raised when the Bosch IP module does not expose an endpoint."""


class BoschForbiddenError(BoschApiError):
    """Raised when the gateway exposes but denies an endpoint."""


class BoschResponseError(BoschApiError):
    """Raised when the Bosch IP module returns an invalid response."""


class BoschEncryption:
    """Encrypt and decrypt the Bosch IVT local HTTP payload format."""

    def __init__(self, access_token: str, password: str) -> None:
        """Derive the AES-256 key from the gateway token and password."""
        normalized_token = access_token.replace("-", "")
        token_hash = hashlib.md5(
            normalized_token.encode() + _IVT_MAGIC,
            usedforsecurity=False,
        ).digest()
        password_hash = hashlib.md5(
            _IVT_MAGIC + password.encode(),
            usedforsecurity=False,
        ).digest()
        self._key = token_hash + password_hash

    @property
    def key_hex(self) -> str:
        """Return the derived key as hexadecimal for migration compatibility."""
        return self._key.hex()

    def encrypt_json(self, value: Any) -> bytes:
        """Serialize and encrypt a JSON value for a Bosch PUT request."""
        plaintext = json.dumps(value, separators=(",", ":")).encode()
        padded = plaintext + bytes(-len(plaintext) % _AES_BLOCK_SIZE)
        encryptor = Cipher(algorithms.AES(self._key), modes.ECB()).encryptor()
        return base64.b64encode(encryptor.update(padded) + encryptor.finalize())

    def decrypt_json(self, payload: str | bytes) -> Any:
        """Decrypt and deserialize a Bosch HTTP response."""
        if not payload:
            return None
        try:
            encrypted = base64.b64decode(payload.strip(), validate=True)
            if not encrypted or len(encrypted) % _AES_BLOCK_SIZE:
                raise ValueError("Encrypted payload has an invalid length")
            decryptor = Cipher(algorithms.AES(self._key), modes.ECB()).decryptor()
            plaintext = (decryptor.update(encrypted) + decryptor.finalize()).rstrip(
                b"\0"
            )
            return json.loads(plaintext)
        except (
            binascii.Error,
            json.JSONDecodeError,
            UnicodeDecodeError,
            ValueError,
        ) as error:
            raise BoschAuthenticationError(
                "Unable to decrypt the Bosch gateway response"
            ) from error


class BoschClient:
    """Perform serialized encrypted requests against a Bosch IP module."""

    def __init__(
        self,
        session: ClientSession,
        host: str,
        access_token: str,
        password: str,
        *,
        timeout: ClientTimeout = _DEFAULT_TIMEOUT,
    ) -> None:
        """Initialize the Bosch local client."""
        self._session = session
        self._host = host.strip().rstrip("/")
        self._encryption = BoschEncryption(access_token, password)
        self._timeout = timeout
        self._request_lock = asyncio.Lock()

    @property
    def key_hex(self) -> str:
        """Return the derived key for migrating legacy config entries."""
        return self._encryption.key_hex

    async def async_get(self, path: str) -> Any:
        """Read and decrypt one endpoint."""
        async with self._request_lock:
            try:
                async with self._session.get(
                    self._url(path),
                    headers=_HTTP_HEADERS,
                    skip_auto_headers={"Accept", "Accept-Encoding"},
                    timeout=self._timeout,
                ) as response:
                    await self._raise_for_status(path, response)
                    return self._encryption.decrypt_json(await response.text())
            except BoschApiError:
                raise
            except (ClientError, TimeoutError) as error:
                raise BoschConnectionError(
                    f"Unable to read Bosch endpoint {path}"
                ) from error

    async def async_put(self, path: str, value: Any) -> None:
        """Encrypt and write one endpoint value."""
        payload = self._encryption.encrypt_json({"value": value})
        async with self._request_lock:
            try:
                async with self._session.put(
                    self._url(path),
                    data=payload,
                    headers=_HTTP_HEADERS,
                    skip_auto_headers={"Accept", "Accept-Encoding"},
                    timeout=self._timeout,
                ) as response:
                    await self._raise_for_status(path, response)
            except BoschApiError:
                raise
            except (ClientError, TimeoutError) as error:
                raise BoschConnectionError(
                    f"Unable to write Bosch endpoint {path}"
                ) from error

    async def async_get_endpoint(self, path: str) -> BoschEndpoint:
        """Read one endpoint and return its typed representation."""
        response = await self.async_get(path)
        if not isinstance(response, Mapping):
            raise BoschResponseError(f"Bosch endpoint {path} returned invalid metadata")
        try:
            return BoschEndpoint.from_payload(response, path=path)
        except ValueError as error:
            raise BoschResponseError(
                f"Bosch endpoint {path} returned invalid metadata"
            ) from error

    async def async_discover_endpoints(
        self,
        roots: Collection[str] = KNOWN_DISCOVERY_ROOTS,
        *,
        max_depth: int = 8,
        max_endpoints: int = 1024,
    ) -> dict[str, BoschEndpoint]:
        """Discover readable endpoint metadata without issuing any writes.

        Traversal is restricted to descendants of the supplied roots, skips paths
        that can expose credentials, and is bounded by both depth and request count.
        Missing, forbidden, and malformed branches do not prevent other roots from
        being discovered. Authentication and connection failures remain fatal.
        """
        if max_depth < 0:
            raise ValueError("max_depth must not be negative")
        if max_endpoints < 1:
            raise ValueError("max_endpoints must be positive")

        normalized_roots = tuple(
            dict.fromkeys(self._validate_path(path) for path in roots)
        )
        queue = deque((path, path, 0) for path in normalized_roots)
        queued = set(normalized_roots)
        requested: set[str] = set()
        endpoints: dict[str, BoschEndpoint] = {}

        while queue and len(requested) < max_endpoints:
            path, root, depth = queue.popleft()
            queued.discard(path)
            if path in requested or self._is_discovery_excluded(path):
                continue
            requested.add(path)

            try:
                response = await self.async_get(path)
            except (
                BoschEndpointNotFoundError,
                BoschForbiddenError,
                BoschResponseError,
            ):
                continue

            if not isinstance(response, Mapping):
                continue

            try:
                endpoints[path] = BoschEndpoint.from_payload(response, path=path)
            except ValueError:
                pass

            if depth >= max_depth:
                continue
            references = response.get("references")
            if not isinstance(references, list):
                continue
            for reference in references:
                if not isinstance(reference, Mapping):
                    continue
                reference_path = reference.get("id")
                if not isinstance(reference_path, str):
                    continue
                try:
                    reference_path = self._validate_path(reference_path)
                except ValueError:
                    continue
                if not self._is_descendant(reference_path, root):
                    continue
                if (
                    reference_path in requested
                    or reference_path in queued
                    or self._is_discovery_excluded(reference_path)
                ):
                    continue
                queue.append((reference_path, root, depth + 1))
                queued.add(reference_path)

        return endpoints

    async def async_gateway_uuid(self) -> str:
        """Return the stable gateway UUID after validating credentials."""
        response = await self.async_get("/gateway/uuid")
        if not isinstance(response, dict) or not isinstance(response.get("value"), str):
            raise BoschResponseError("Bosch gateway UUID response is invalid")
        return response["value"]

    def _url(self, path: str) -> str:
        """Build a gateway URL for an absolute API path."""
        path = self._validate_path(path)
        return f"http://{self._host}{path}"

    @staticmethod
    def _validate_path(path: str) -> str:
        """Validate one absolute gateway API path."""
        if (
            not isinstance(path, str)
            or not path.startswith("/")
            or path.startswith("//")
            or "?" in path
            or "#" in path
            or any(part in (".", "..") for part in unquote(path).split("/"))
        ):
            raise ValueError("Bosch endpoint must be a safe absolute path")
        return path.rstrip("/") or "/"

    @staticmethod
    def _is_descendant(path: str, root: str) -> bool:
        """Return whether a discovered reference stays within its root."""
        return path == root or path.startswith(f"{root}/")

    @staticmethod
    def _is_sensitive_path(path: str) -> bool:
        """Return whether reading a path could expose credential material."""
        for segment in unquote(path).lower().split("/"):
            if any(marker in segment for marker in _SENSITIVE_PATH_MARKERS):
                return True
            if segment == "key" or segment.endswith("key"):
                return True
        return False

    @classmethod
    def _is_discovery_excluded(cls, path: str) -> bool:
        """Return whether a path is unsafe or incompatible with metadata reads."""
        return path in _DISCOVERY_EXCLUDED_PATHS or cls._is_sensitive_path(path)

    @staticmethod
    async def _raise_for_status(path: str, response: ClientResponse) -> None:
        """Translate an HTTP response into a domain-specific error."""
        if response.status in (200, 204):
            return
        await response.read()
        if response.status == 401:
            raise BoschAuthenticationError("Bosch gateway rejected the credentials")
        if response.status == 403:
            raise BoschForbiddenError(f"Bosch endpoint is forbidden: {path}")
        if response.status == 404:
            raise BoschEndpointNotFoundError(f"Bosch endpoint is unavailable: {path}")
        raise BoschResponseError(
            f"Bosch endpoint {path} returned HTTP {response.status}"
        )
