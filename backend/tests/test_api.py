import asyncio
from datetime import datetime, timezone

from .conftest import HW, claim_device, connected_device, register_device


def test_health(client):
    body = client.get("/api/v1/health").json()
    assert body["ok"] is True
    assert body["repo"] == "memory"


def test_missing_token_is_rejected(client):
    response = client.get("/api/v1/devices", headers={"Authorization": ""})
    assert response.status_code == 401
    assert response.json()["error"]["code"] in {"missing_token", "invalid_token"}


def test_invalid_token_is_rejected(client):
    response = client.get("/api/v1/devices", headers={"Authorization": "Bearer nope"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


def test_claim_unknown_code(client):
    response = client.post("/api/v1/devices/claim", json={"code": "ZZZZ-9999"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "invalid_code"


def test_claim_requires_a_recent_registration(client, ctx):
    assert register_device(client).status_code == 200
    doc = asyncio.run(ctx.repo.get_device_by_hardware(HW))
    asyncio.run(
        ctx.repo.update_device(
            doc["id"], {"last_seen": datetime(2020, 1, 1, tzinfo=timezone.utc)}
        )
    )
    response = client.post("/api/v1/devices/claim", json={"code": "ABCD-1234"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "device_offline"


def test_claim_and_list(client, ctx):
    with connected_device(client, ctx) as session:
        assert session.device["name"] == "Living room"
        assert session.device["lan_key"].startswith("dk_")
        assert len(session.device["channels"]) == 8
        assert session.device["channels"][0]["type"] == "fan"

        listed = client.get("/api/v1/devices").json()
        assert [d["id"] for d in listed] == [session.id]
        assert listed[0]["online"] is True
        assert listed[0]["state"] == [False] * 8
        assert listed[0]["local_ip"] == "192.168.1.42"
        assert listed[0]["telemetry"]["rssi"] == -55


def test_second_claim_is_rejected(client, ctx):
    first = claim_device(client, HW, "ABCD-1234")
    assert first["id"]

    # A registered controller that already holds a key refuses to register again.
    again = register_device(client, hw=HW, code="WXYZ-5678")
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "already_claimed"

    # And the original pairing code was consumed by the claim.
    response = client.post("/api/v1/devices/claim", json={"code": "ABCD-1234"})
    assert response.status_code == 404


def test_other_user_cannot_see_device(client, ctx):
    with connected_device(client, ctx) as session:
        response = client.get(
            f"/api/v1/devices/{session.id}",
            headers={"Authorization": "Bearer token-b"},
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "forbidden"


def test_patch_device_names_and_channels(client, ctx):
    with connected_device(client, ctx) as session:
        channels = [
            {"index": i, "name": f"Relay {i + 1}", "type": "socket"} for i in range(8)
        ]
        response = client.patch(
            f"/api/v1/devices/{session.id}",
            json={"name": "Bedroom", "room": "Upstairs", "channels": channels},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["name"] == "Bedroom"
        assert body["room"] == "Upstairs"
        assert body["channels"][7]["name"] == "Relay 8"

        # The RTDB mirror is updated so the app and the device see the names.
        assert session.node()["name"] == "Bedroom"
        assert session.node()["channels"][7]["name"] == "Relay 8"

        bad = client.patch(
            f"/api/v1/devices/{session.id}",
            json={"channels": channels[:3]},
        )
        assert bad.status_code == 422
        assert bad.json()["error"]["code"] == "invalid_channels"
