import threading
import time
from contextlib import contextmanager
from typing import Callable

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.errors import ApiError
from app.main import create_app
from app.security import User

USERS = {
    "test-token-a": User(uid="user-a", email="a@example.com"),
    "test-token-b": User(uid="user-b"),
}

SECRET = "0123456789abcdef0123456789abcdef"
HW = "AABBCCDDEEFF"
CODE = "ABCD-1234"


def fake_verifier(token: str) -> User:
    if token not in USERS:
        raise ApiError(401, "invalid_token", "unknown test token")
    return USERS[token]


@pytest.fixture
def app():
    return create_app(
        Settings(
            repo_backend="memory",
            rtdb_backend="memory",
            scheduler_enabled=False,
            command_timeout_s=3.0,
            log_events=True,
        )
    )


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        app.state.ctx.verify_token = fake_verifier
        app.state.ctx.mint_token = lambda device_id: f"test-token:{device_id}"
        test_client.headers.update({"Authorization": "Bearer test-token-a"})
        yield test_client


@pytest.fixture
def local_app():
    return create_app(
        Settings(
            repo_backend="memory",
            rtdb_backend="memory",
            scheduler_enabled=False,
            command_timeout_s=3.0,
            log_events=True,
            local_fulfillment=True,
        )
    )


@pytest.fixture
def local_client(local_app):
    with TestClient(local_app) as test_client:
        local_app.state.ctx.verify_token = fake_verifier
        local_app.state.ctx.mint_token = lambda device_id: f"test-token:{device_id}"
        test_client.headers.update({"Authorization": "Bearer test-token-a"})
        yield test_client


@pytest.fixture
def local_ctx(local_client, local_app):
    return local_app.state.ctx


@pytest.fixture
def ctx(client, app):
    return app.state.ctx


def wait_for(predicate: Callable[[], bool], timeout: float = 3.0, interval: float = 0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    raise AssertionError("condition was not met in time")


def now_ms() -> int:
    return int(time.time() * 1000)


def register_device(
    client: TestClient,
    hw: str = HW,
    code: str = CODE,
    channels: int = 8,
    secret: str = SECRET,
):
    return client.post(
        "/api/v1/device/register",
        json={
            "hw": hw,
            "code": code,
            "secret": secret,
            "fw": "1.1.0",
            "model": "fanctl-8",
            "channels": channels,
        },
    )


class FakeDevice(threading.Thread):
    """Stands in for the firmware: watches its command node and writes acks."""

    def __init__(self, rtdb, device_id: str, count: int = 8, state=None) -> None:
        super().__init__(daemon=True)
        self.rtdb = rtdb
        self.device_id = device_id
        self.count = count
        self.state = list(state or [False] * count)
        self.seen: set[str] = set()
        self.applied: list[str] = []
        self.assistant_keys: list[str] = []
        self._stop = threading.Event()
        self.rtdb.put_sync(f"devices/{device_id}/info", self.info())
        self.rtdb.put_sync(f"devices/{device_id}/state", self.state)

    def info(self, age_ms: int = 0) -> dict:
        return {
            "ip": "192.168.1.42",
            "port": 80,
            "fw": "1.1.0",
            "rssi": -55,
            "uptime_s": 5,
            "heap": 200000,
            "ts": now_ms() - age_ms,
        }

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                self.step()
            except Exception:
                pass
            time.sleep(0.02)

    def step(self) -> None:
        commands = self.rtdb.get_sync(f"devices/{self.device_id}/commands") or {}
        for command_id, command in list(commands.items()):
            if command_id in self.seen:
                self.ack(command_id, True, None)  # retry: ack without re-applying
                continue
            self.seen.add(command_id)
            self.apply(command_id, command)
        self.rtdb.patch_sync(f"devices/{self.device_id}/info", {"ts": now_ms()})

    def apply(self, command_id: str, command: dict) -> None:
        action = command.get("action")
        error = None
        if action == "set":
            channel = int(command.get("ch", -1))
            if 0 <= channel < self.count:
                self.state[channel] = bool(command.get("on"))
            else:
                error = "bad_channel"
        elif action == "all":
            self.state = [bool(command.get("on"))] * self.count
        elif action == "toggle":
            channel = int(command.get("ch", -1))
            if 0 <= channel < self.count:
                self.state[channel] = not self.state[channel]
            else:
                error = "bad_channel"
        elif action in ("pet", "feed", "play", "sleep", "wake", "alarm", "alarm_stop", "reminder"):
            pass  # display/audio reactions only, the relay state is untouched
        elif action == "set_assistant_key":
            self.assistant_keys.append(str(command.get("key") or ""))
        elif action != "identify":
            error = "bad_action"

        self.applied.append(action or "")
        if action not in ("identify", "set_assistant_key"):
            self.rtdb.put_sync(f"devices/{self.device_id}/state", self.state)
        self.ack(command_id, error is None, error)

    def ack(self, command_id: str, ok: bool, error: str | None) -> None:
        payload = {"ok": ok, "state": self.state, "ts": now_ms()}
        if error:
            payload["error"] = error
        self.rtdb.put_sync(f"devices/{self.device_id}/acks/{command_id}", payload)

    def go_offline(self) -> None:
        self.rtdb.put_sync(f"devices/{self.device_id}/info", self.info(age_ms=180_000))

    def stop(self) -> None:
        self._stop.set()
        self.join(timeout=2)


def claim_device(
    client: TestClient,
    hw: str = HW,
    code: str = CODE,
    name: str | None = "Living room",
    secret: str = SECRET,
) -> dict:
    response = register_device(client, hw=hw, code=code, secret=secret)
    assert response.status_code == 200, response.text

    body: dict = {"code": code}
    if name:
        body["name"] = name
    claimed = client.post("/api/v1/devices/claim", json=body)
    assert claimed.status_code == 200, claimed.text

    token = client.post("/api/v1/device/token", json={"hw": hw, "secret": secret})
    assert token.status_code == 200, token.text
    return {**claimed.json(), "custom_token": token.json()["custom_token"]}


class DeviceSession:
    def __init__(self, ctx, device: dict, sim: FakeDevice) -> None:
        self.ctx = ctx
        self.device = device
        self.sim = sim

    @property
    def id(self) -> str:
        return self.device["id"]

    def node(self) -> dict:
        return self.ctx.rtdb.get_sync(f"devices/{self.id}") or {}

    def commands(self) -> dict:
        return self.ctx.rtdb.get_sync(f"devices/{self.id}/commands") or {}

    def state(self) -> list[bool]:
        return self.ctx.rtdb.get_sync(f"devices/{self.id}/state") or []

    def go_offline(self) -> None:
        self.sim.stop()
        self.sim.go_offline()


@contextmanager
def connected_device(client: TestClient, ctx, hw: str = HW, code: str = CODE, name: str = "Living room"):
    device = claim_device(client, hw=hw, code=code, name=name)
    sim = FakeDevice(ctx.rtdb, device["id"], count=device["channel_count"])
    sim.start()
    try:
        wait_for(lambda: client.get(f"/api/v1/devices/{device['id']}").json()["online"] is True)
        yield DeviceSession(ctx, device, sim)
    finally:
        sim.stop()
