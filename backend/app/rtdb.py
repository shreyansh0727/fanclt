"""Firebase Realtime Database access.

The Python admin SDK has no RTDB client, so the backend speaks the same REST
protocol the firmware does (one `.json` path per node, plus an access token).
`MemoryRtdb` backs the test-suite and FANCTL_REPO_BACKEND=memory.
"""

from __future__ import annotations

import copy
import os
import threading
from typing import Any, Protocol

import httpx

from .config import Settings

RTDB_SCOPES = [
    "https://www.googleapis.com/auth/firebase.database",
    "https://www.googleapis.com/auth/userinfo.email",
]


class Rtdb(Protocol):
    async def get(self, path: str) -> Any | None: ...
    async def put(self, path: str, value: Any) -> None: ...
    async def patch(self, path: str, value: dict) -> None: ...
    async def delete(self, path: str) -> None: ...
    async def aclose(self) -> None: ...


def _parts(path: str) -> list[str]:
    return [part for part in path.strip("/").split("/") if part]


class MemoryRtdb:
    """A dict tree with RTDB's write semantics: PUT replaces a subtree and a
    JSON null (None) deletes it."""

    def __init__(self) -> None:
        self._data: dict = {}
        self.lock = threading.RLock()

    # -- synchronous helpers, used by the simulated device in tests ---------
    def _node(self, path: str, create: bool) -> tuple[dict | None, str | None]:
        node: Any = self._data
        parts = _parts(path)
        for part in parts[:-1]:
            child = node.get(part)
            if not isinstance(child, dict):
                if not create:
                    return None, None
                child = {}
                node[part] = child
            node = child
        return node, (parts[-1] if parts else None)

    def get_sync(self, path: str) -> Any | None:
        with self.lock:
            node, key = self._node(path, create=False)
            if node is None:
                return None
            return copy.deepcopy(node if key is None else node.get(key))

    def put_sync(self, path: str, value: Any) -> None:
        with self.lock:
            node, key = self._node(path, create=True)
            if node is None:
                return
            if key is None:
                self._data = copy.deepcopy(value) if isinstance(value, dict) else {}
            elif value is None:
                node.pop(key, None)
            else:
                node[key] = copy.deepcopy(value)
            self._prune()

    def patch_sync(self, path: str, value: dict) -> None:
        with self.lock:
            node, key = self._node(path, create=True)
            if node is None:
                return
            target = node if key is None else node.setdefault(key, {})
            for child, child_value in value.items():
                if child_value is None:
                    target.pop(child, None)
                else:
                    target[child] = copy.deepcopy(child_value)
            self._prune()

    def delete_sync(self, path: str) -> None:
        self.put_sync(path, None)

    def _prune(self) -> None:
        def walk(node: dict) -> bool:
            for key in list(node):
                child = node[key]
                if isinstance(child, dict):
                    if walk(child):
                        del node[key]
            return not node

        walk(self._data)

    # -- async interface -----------------------------------------------------
    async def get(self, path: str) -> Any | None:
        return self.get_sync(path)

    async def put(self, path: str, value: Any) -> None:
        self.put_sync(path, value)

    async def patch(self, path: str, value: dict) -> None:
        self.patch_sync(path, value)

    async def delete(self, path: str) -> None:
        self.delete_sync(path)

    async def aclose(self) -> None:
        return None


class HttpRtdb:
    """REST client. Paths map to `<base><path>.json?ns=...&access_token=...`."""

    def __init__(self, base_url: str, namespace: str | None = None, credentials=None) -> None:
        self.base_url = base_url.rstrip("/")
        self.namespace = namespace
        self._credentials = credentials
        self._client = httpx.AsyncClient(timeout=10.0)

    def _params(self) -> dict[str, str]:
        params: dict[str, str] = {}
        if self.namespace:
            params["ns"] = self.namespace
        token = self._access_token()
        if token:
            params["access_token"] = token
        return params

    def _access_token(self) -> str | None:
        if self._credentials is None:
            return None
        # Blocks for a moment at most once an hour.
        if not self._credentials.valid:
            from google.auth.transport.requests import Request

            self._credentials.refresh(Request())
        return self._credentials.token

    def url(self, path: str) -> str:
        suffix = "".join("/" + part for part in _parts(path))
        return f"{self.base_url}{suffix}.json"

    async def _request(self, method: str, path: str, payload: Any = None) -> Any:
        response = await self._client.request(
            method, self.url(path), params=self._params(), json=payload
        )
        response.raise_for_status()
        if method == "GET" and response.content:
            return response.json()
        return None

    async def get(self, path: str) -> Any | None:
        return await self._request("GET", path)

    async def put(self, path: str, value: Any) -> None:
        await self._request("PUT", path, value)

    async def patch(self, path: str, value: dict) -> None:
        await self._request("PATCH", path, value)

    async def delete(self, path: str) -> None:
        await self._request("DELETE", path)

    async def aclose(self) -> None:
        await self._client.aclose()


def _service_account_credentials(settings: Settings):
    """Database-scoped credentials for the REST calls; None if unavailable."""
    try:
        if settings.credentials_file and os.path.exists(settings.credentials_file):
            from google.oauth2 import service_account

            return service_account.Credentials.from_service_account_file(
                settings.credentials_file, scopes=RTDB_SCOPES
            )
        import google.auth

        credentials, _ = google.auth.default(scopes=RTDB_SCOPES)
        return credentials
    except Exception:
        return None


def default_namespace(settings: Settings) -> str:
    return settings.rtdb_namespace or f"{settings.project_id or 'demo-fanctl'}-default-rtdb"


def build_rtdb(settings: Settings) -> Rtdb:
    if settings.rtdb_backend == "memory" or (
        settings.rtdb_backend == "auto" and settings.repo_backend == "memory"
    ):
        return MemoryRtdb()

    emulator_host = settings.database_emulator_host or os.environ.get(
        "FIREBASE_DATABASE_EMULATOR_HOST"
    )
    if emulator_host:
        return HttpRtdb(f"http://{emulator_host}", default_namespace(settings))

    if not settings.database_url:
        raise RuntimeError(
            "FANCTL_DATABASE_URL is required (or set FIREBASE_DATABASE_EMULATOR_HOST)"
        )
    return HttpRtdb(
        settings.database_url,
        settings.rtdb_namespace or None,
        _service_account_credentials(settings),
    )
