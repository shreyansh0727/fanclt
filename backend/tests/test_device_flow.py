import asyncio
from types import SimpleNamespace

from app.rtdb import MemoryRtdb
from app.services.commands import send_identify

from .conftest import SECRET, connected_device, register_device, wait_for


def test_command_round_trip(client, ctx):
    with connected_device(client, ctx) as device:
        response = client.post(
            f"/api/v1/devices/{device.id}/commands",
            json={"action": "set", "channel": 0, "on": True},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["acked"] is True
        assert body["online"] is True
        assert body["state"] == [True] + [False] * 7
        assert device.state() == [True] + [False] * 7
        # The resolved command node is cleaned out of RTDB.
        assert device.commands() == {}

        listed = client.get("/api/v1/devices").json()
        assert listed[0]["online"] is True
        assert listed[0]["state"] == [True] + [False] * 7


def test_pet_command_needs_no_channel(client, ctx):
    with connected_device(client, ctx) as device:
        response = client.post(
            f"/api/v1/devices/{device.id}/commands",
            json={"action": "play"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["acked"] is True
        assert body["state"] == [False] * 8
        assert "play" in device.sim.applied
        assert device.commands() == {}


def test_alarm_commands_need_no_channel(client, ctx):
    with connected_device(client, ctx) as device:
        started = client.post(
            f"/api/v1/devices/{device.id}/commands",
            json={"action": "alarm"},
        )
        assert started.status_code == 200, started.text
        assert started.json()["acked"] is True
        assert "alarm" in device.sim.applied

        stopped = client.post(
            f"/api/v1/devices/{device.id}/commands",
            json={"action": "alarm_stop"},
        )
        assert stopped.status_code == 200, stopped.text
        body = stopped.json()
        assert body["acked"] is True
        assert body["state"] == [False] * 8
        assert "alarm_stop" in device.sim.applied


def test_command_to_offline_device(client, ctx):
    with connected_device(client, ctx) as device:
        device.go_offline()
        response = client.post(
            f"/api/v1/devices/{device.id}/commands",
            json={"action": "all", "on": False},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["acked"] is False
        assert body["online"] is False
        assert body["error"] == "device_offline"


def test_unacked_command_is_pruned(client, ctx):
    with connected_device(client, ctx) as device:
        # Fresh info.ts keeps the device "online" while nothing answers, so the
        # command times out instead of short-circuiting as device_offline.
        device.sim.stop()
        response = client.post(
            f"/api/v1/devices/{device.id}/commands",
            json={"action": "set", "channel": 0, "on": True},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["acked"] is False
        assert body["error"] == "no_ack"
        assert device.commands() == {}


def test_command_validation(client, ctx):
    with connected_device(client, ctx) as device:
        missing_channel = client.post(
            f"/api/v1/devices/{device.id}/commands", json={"action": "set", "on": True}
        )
        assert missing_channel.status_code == 422
        assert missing_channel.json()["error"]["code"] == "invalid_command"

        out_of_range = client.post(
            f"/api/v1/devices/{device.id}/commands",
            json={"action": "set", "channel": 9, "on": True},
        )
        assert out_of_range.status_code == 422
        assert out_of_range.json()["error"]["code"] == "channel_out_of_range"


def test_duplicate_command_is_acked_once(client, ctx):
    with connected_device(client, ctx) as device:
        command_id = "cmd-fixed-0001"
        results = []
        for turn in (True, False):
            results.append(
                client.post(
                    f"/api/v1/devices/{device.id}/commands",
                    json={
                        "action": "set",
                        "channel": 1,
                        "on": turn,
                        "command_id": command_id,
                    },
                )
            )
        assert [r.json()["acked"] for r in results] == [True, True]
        # The retried id is acked but not applied a second time.
        assert device.sim.applied.count("set") == 1
        assert device.state()[1] is True


def test_events_are_recorded(client, ctx):
    with connected_device(client, ctx) as device:
        response = client.post(
            f"/api/v1/devices/{device.id}/commands",
            json={"action": "toggle", "channel": 2},
        )
        assert response.json()["acked"] is True

        events = client.get(f"/api/v1/devices/{device.id}/events").json()
        assert events
        assert events[0]["kind"] == "command"
        assert events[0]["detail"]["action"] == "toggle"


def test_identify_sends_a_command(client, ctx):
    with connected_device(client, ctx) as device:
        response = client.post(f"/api/v1/devices/{device.id}/identify")
        assert response.status_code == 202, response.text
        wait_for(lambda: "identify" in device.sim.applied)


def test_identify_requires_a_live_device(client, ctx):
    with connected_device(client, ctx) as device:
        device.go_offline()
        response = client.post(f"/api/v1/devices/{device.id}/identify")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "device_offline"


def test_identify_prunes_stale_nodes():
    rtdb = MemoryRtdb()
    rtdb.put_sync("devices/dev_1/commands/old", {"action": "identify", "seconds": 5, "ts": 1})
    rtdb.put_sync("devices/dev_1/acks/old", {"ok": True})
    rtdb.put_sync(
        "devices/dev_1/commands/pending", {"action": "set", "ch": 0, "on": True, "ts": 1}
    )

    asyncio.run(send_identify(SimpleNamespace(rtdb=rtdb), {"id": "dev_1"}))

    assert rtdb.get_sync("devices/dev_1/commands/old") is None
    assert rtdb.get_sync("devices/dev_1/acks/old") is None
    # An in-flight app command is left for its own timeout path.
    assert rtdb.get_sync("devices/dev_1/commands/pending") is not None
    commands = rtdb.get_sync("devices/dev_1/commands")
    identifies = [row for row in commands.values() if row.get("action") == "identify"]
    assert len(identifies) == 1


def test_delete_device_unclaims(client, ctx):
    with connected_device(client, ctx) as device:
        assert client.delete(f"/api/v1/devices/{device.id}").status_code == 204
        assert client.get("/api/v1/devices").json() == []
        assert device.node() == {"unclaim": True}


def test_token_endpoints(client, ctx):
    # Nothing registered yet.
    unregistered = client.post("/api/v1/device/token", json={"hw": "AABBCCDDEEFF", "secret": SECRET})
    assert unregistered.status_code == 401
    assert unregistered.json()["error"]["code"] == "bad_secret"

    assert register_device(client).status_code == 200
    waiting = client.post("/api/v1/device/token", json={"hw": "AABBCCDDEEFF", "secret": SECRET})
    assert waiting.status_code == 409
    assert waiting.json()["error"]["code"] == "not_claimed"

    wrong_secret = client.post(
        "/api/v1/device/token", json={"hw": "AABBCCDDEEFF", "secret": "f" * 32}
    )
    assert wrong_secret.status_code == 401


def test_legacy_device_key_can_fetch_a_token(client, ctx):
    with connected_device(client, ctx) as device:
        response = client.post(
            "/api/v1/device/token",
            json={"device_id": device.id, "device_key": device.device["lan_key"]},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["custom_token"].startswith("test-token:")
        # The controller needs somewhere to stream from and somewhere to sign in.
        assert body["auth"]["host"]
        assert "api_key" in body["auth"]
        assert body["rtdb"] is None or body["rtdb"]["port"]


def test_register_rejects_a_claimed_controller(client, ctx):
    with connected_device(client, ctx):
        response = register_device(client)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "already_claimed"
