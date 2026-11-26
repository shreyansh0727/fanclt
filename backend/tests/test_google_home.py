import asyncio

from app.services import assistants

from .conftest import connected_device, wait_for

CLIENT_ID = "g_home"
CLIENT_SECRET = "s3cret-client-value"
REDIRECT = "https://oauth-redirect.googleusercontent.com/r/home-test"


def link_token(ctx, owner: str = "user-a", provider: str = "google") -> str:
    client = asyncio.run(
        assistants.ensure_client(
            ctx,
            client_id=CLIENT_ID,
            provider=provider,
            name="Google Home",
            secret=CLIENT_SECRET,
            redirect_uris=[REDIRECT],
        )
    )
    return asyncio.run(assistants.issue_tokens(ctx, client, owner))["access_token"]


def fulfil(client, token: str, intent: str, payload=None, request_id: str = "req-1"):
    return client.post(
        "/google/fulfillment",
        json={"requestId": request_id, "inputs": [{"intent": intent, "payload": payload or {}}]},
        headers={"Authorization": f"Bearer {token}"},
    )


def onoff(on: bool = True) -> list[dict]:
    return [{"command": "action.devices.commands.OnOff", "params": {"on": on}}]


def test_sync_lists_every_channel_with_google_types(client, ctx):
    with connected_device(client, ctx) as session:
        token = link_token(ctx)
        response = fulfil(client, token, "action.devices.SYNC")
        assert response.status_code == 200
        payload = response.json()["payload"]
        assert payload["agentUserId"] == "user-a"

        devices = payload["devices"]
        assert len(devices) == 8
        assert devices[0]["id"] == f"{session.id}.ch0"
        assert devices[0]["name"]["name"] == "Fan"
        assert devices[0]["type"] == "action.devices.types.FAN"
        assert devices[0]["traits"] == ["action.devices.traits.OnOff"]
        assert devices[0]["willReportState"] is False
        assert "roomHint" not in devices[0]  # the controller has no room set
        assert [d["type"] for d in devices[:5]] == [
            "action.devices.types.FAN",
            "action.devices.types.LIGHT",
            "action.devices.types.LIGHT",
            "action.devices.types.LIGHT",
            "action.devices.types.OUTLET",
        ]
        assert devices[6]["type"] == "action.devices.types.SWITCH"
        # Local fulfillment is opt-in; the default response stays cloud-only.
        assert "otherDeviceIds" not in devices[0]
        assert "customData" not in devices[0]


def test_local_sync_shares_a_dedicated_key_and_provisions_the_board(local_client, local_ctx):
    with connected_device(local_client, local_ctx) as session:
        token = link_token(local_ctx)
        devices = fulfil(local_client, token, "action.devices.SYNC").json()["payload"]["devices"]

        keys = set()
        for index, entry in enumerate(devices):
            assert entry["otherDeviceIds"] == [{"deviceId": f"{session.id}.ch{index}"}]
            data = entry["customData"]
            assert data["id"] == session.id
            assert data["channel"] == index
            keys.add(data["key"])
        assert len(keys) == 1
        key = keys.pop()
        assert key.startswith("lk_") and len(key) >= 16

        doc = asyncio.run(local_ctx.repo.get_device(session.id))
        assert doc["local_key"] == key
        # The assistant key is its own credential, not the owner's keys.
        assert key != doc["device_key"]
        assert key != session.device.get("lan_key")

        wait_for(lambda: len(session.sim.assistant_keys) == 1)
        assert session.sim.assistant_keys == [key]

        again = fulfil(
            local_client, token, "action.devices.SYNC", request_id="sync-2"
        ).json()["payload"]["devices"]
        assert [entry["customData"]["key"] for entry in again] == [key] * 8
        # An already-provisioned board is not sent the same key again.
        assert session.sim.assistant_keys == [key]


def test_fulfilment_requires_a_google_access_token(client, ctx):
    with connected_device(client, ctx):
        missing = client.post(
            "/google/fulfillment",
            json={"requestId": "r", "inputs": [{"intent": "action.devices.SYNC"}]},
            headers={"Authorization": "Bearer "},
        )
        assert missing.status_code == 401

        smartthings = link_token(ctx, provider="smartthings")
        wrong_platform = fulfil(client, smartthings, "action.devices.SYNC")
        assert wrong_platform.status_code == 401

        unknown = fulfil(client, "at_not-a-real-token", "action.devices.SYNC")
        assert unknown.status_code == 401


def test_query_reports_on_and_online_and_marks_unknown_ids(client, ctx):
    with connected_device(client, ctx) as session:
        token = link_token(ctx)
        client.post(
            f"/api/v1/devices/{session.id}/commands",
            json={"action": "set", "channel": 0, "on": True},
        ).raise_for_status()

        key = f"{session.id}.ch0"
        response = fulfil(
            client,
            token,
            "action.devices.QUERY",
            {"devices": [{"id": key}, {"id": "ghost.ch3"}, {"id": "garbage"}]},
        )
        assert response.status_code == 200
        devices = response.json()["payload"]["devices"]
        assert devices[key] == {"status": "SUCCESS", "on": True, "online": True}
        assert devices["ghost.ch3"] == {"status": "ERROR", "errorCode": "deviceNotFound"}
        assert devices["garbage"] == {"status": "ERROR", "errorCode": "deviceNotFound"}

        session.go_offline()
        offline = fulfil(client, token, "action.devices.QUERY", {"devices": [{"id": key}]})
        assert offline.json()["payload"]["devices"][key] == {
            "status": "SUCCESS",
            "on": True,
            "online": False,
        }


def test_execute_drives_the_relay_once_per_request_id(client, ctx):
    with connected_device(client, ctx) as session:
        token = link_token(ctx)
        key = f"{session.id}.ch1"
        payload = {"commands": [{"devices": [{"id": key}], "execution": onoff(True)}]}

        first = fulfil(client, token, "action.devices.EXECUTE", payload, request_id="exec-1")
        assert first.json()["payload"]["commands"] == [
            {"ids": [key], "status": "SUCCESS", "states": {"on": True, "online": True}}
        ]
        assert session.state()[1] is True
        assert session.sim.applied == ["set"]

        replay = fulfil(client, token, "action.devices.EXECUTE", payload, request_id="exec-1")
        assert replay.json() == first.json()
        assert session.sim.applied == ["set"]

        fulfil(client, token, "action.devices.EXECUTE", payload, request_id="exec-2")
        assert session.sim.applied == ["set", "set"]


def test_execute_maps_failures_to_google_error_codes(client, ctx):
    with connected_device(client, ctx) as session:
        token = link_token(ctx)
        payload = {
            "commands": [
                {"devices": [{"id": "ghost.ch0"}], "execution": onoff()},
            ]
        }
        unknown = fulfil(client, token, "action.devices.EXECUTE", payload, request_id="err-1")
        assert unknown.json()["payload"]["commands"][0] == {
            "ids": ["ghost.ch0"],
            "status": "ERROR",
            "errorCode": "deviceNotFound",
        }

        out_of_range = fulfil(
            client,
            token,
            "action.devices.EXECUTE",
            {"commands": [{"devices": [{"id": f"{session.id}.ch99"}], "execution": onoff()}]},
            request_id="err-2",
        )
        assert out_of_range.json()["payload"]["commands"][0]["errorCode"] == "deviceNotFound"
        assert session.commands() == {}

        unsupported = fulfil(
            client,
            token,
            "action.devices.EXECUTE",
            {
                "commands": [
                    {
                        "devices": [{"id": f"{session.id}.ch0"}],
                        "execution": [{"command": "action.devices.commands.BrightnessAbsolute"}],
                    }
                ]
            },
            request_id="err-3",
        )
        assert unsupported.json()["payload"]["commands"][0]["errorCode"] == "functionNotSupported"

        session.go_offline()
        offline = fulfil(
            client,
            token,
            "action.devices.EXECUTE",
            {"commands": [{"devices": [{"id": f"{session.id}.ch0"}], "execution": onoff()}]},
            request_id="err-4",
        )
        assert offline.json()["payload"]["commands"][0] == {
            "ids": [f"{session.id}.ch0"],
            "status": "ERROR",
            "errorCode": "deviceOffline",
        }


def test_another_owners_token_cannot_see_or_drive_the_device(client, ctx):
    with connected_device(client, ctx) as session:
        token = link_token(ctx, owner="user-b")

        sync = fulfil(client, token, "action.devices.SYNC")
        assert sync.json()["payload"] == {"agentUserId": "user-b", "devices": []}

        execute = fulfil(
            client,
            token,
            "action.devices.EXECUTE",
            {"commands": [{"devices": [{"id": f"{session.id}.ch0"}], "execution": onoff()}]},
        )
        assert execute.json()["payload"]["commands"][0]["errorCode"] == "deviceNotFound"
        assert session.commands() == {}


def test_disconnect_revokes_the_link(client, ctx):
    with connected_device(client, ctx):
        token = link_token(ctx)
        assert fulfil(client, token, "action.devices.DISCONNECT").status_code == 200

        after = fulfil(client, token, "action.devices.QUERY", {"devices": []})
        assert after.status_code == 401


def test_malformed_requests_are_rejected(client, ctx):
    with connected_device(client, ctx):
        token = link_token(ctx)

        no_request = client.post(
            "/google/fulfillment",
            json={"inputs": [{"intent": "action.devices.SYNC"}]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert no_request.status_code == 400

        no_inputs = client.post(
            "/google/fulfillment",
            json={"requestId": "r"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert no_inputs.status_code == 400

        unsupported = fulfil(client, token, "action.devices.DELETE")
        assert unsupported.status_code == 400

        not_json = client.post(
            "/google/fulfillment",
            content=b"<xml/>",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        assert not_json.status_code == 400
