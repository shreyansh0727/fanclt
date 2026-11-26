import asyncio

from app.services import assistants

from .conftest import connected_device

CLIENT_ID = "st_app"
CLIENT_SECRET = "s3cret-client-value"
REDIRECT = "https://api.smartthings.com/oauth/callback"


def link_token(ctx, owner: str = "user-a", provider: str = "smartthings") -> str:
    client = asyncio.run(
        assistants.ensure_client(
            ctx,
            client_id=CLIENT_ID,
            provider=provider,
            name="SmartThings",
            secret=CLIENT_SECRET,
            redirect_uris=[REDIRECT],
        )
    )
    return asyncio.run(assistants.issue_tokens(ctx, client, owner))["access_token"]


def send(client, token: str, interaction: str, extra=None, request_id: str = "st-1"):
    body = {
        "headers": {
            "schema": "st-schema",
            "version": "1.0",
            "interactionType": interaction,
            "requestId": request_id,
        },
        "authentication": {"tokenType": "Bearer", "token": token},
        **(extra or {}),
    }
    return client.post("/smartthings", json=body)


def switch_command(command: str = "on") -> dict:
    return {
        "component": "main",
        "capability": "st.switch",
        "command": command,
    }


def test_discovery_lists_channels_as_c2c_switches(client, ctx):
    with connected_device(client, ctx) as session:
        token = link_token(ctx)
        response = send(client, token, "discoveryRequest")
        assert response.status_code == 200
        body = response.json()
        assert body["headers"] == {
            "schema": "st-schema",
            "version": "1.0",
            "interactionType": "discoveryResponse",
            "requestId": "st-1",
        }

        devices = body["devices"]
        assert len(devices) == 8
        first = devices[0]
        assert first["externalDeviceId"] == f"{session.id}.ch0"
        assert first["friendlyName"] == "Fan"
        assert first["deviceHandlerType"] == "c2c-switch"
        assert first["manufacturerInfo"]["manufacturerName"] == "FanCtl"
        assert "deviceContext" not in first  # the controller has no room set


def test_token_is_read_from_the_body_and_must_belong_to_smartthings(client, ctx):
    with connected_device(client, ctx):
        header_only = client.post(
            "/smartthings",
            json={
                "headers": {
                    "schema": "st-schema",
                    "version": "1.0",
                    "interactionType": "discoveryRequest",
                    "requestId": "st-x",
                },
            },
        )
        assert header_only.status_code == 401

        google = link_token(ctx, provider="google")
        wrong_platform = send(client, google, "discoveryRequest")
        assert wrong_platform.status_code == 401


def test_state_refresh_reports_switch_and_health(client, ctx):
    with connected_device(client, ctx) as session:
        token = link_token(ctx)
        key = f"{session.id}.ch0"
        client.post(
            f"/api/v1/devices/{session.id}/commands",
            json={"action": "set", "channel": 0, "on": True},
        ).raise_for_status()

        response = send(
            client,
            token,
            "stateRefreshRequest",
            {"devices": [{"externalDeviceId": key}, {"externalDeviceId": "ghost.ch4"}]},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["headers"]["interactionType"] == "stateRefreshResponse"
        states = body["deviceState"]

        assert states[0]["externalDeviceId"] == key
        assert {(s["capability"], s["attribute"], s["value"]) for s in states[0]["states"]} == {
            ("st.switch", "switch", "on"),
            ("st.healthCheck", "healthStatus", "online"),
        }
        assert states[1] == {
            "externalDeviceId": "ghost.ch4",
            "deviceError": [{"errorEnum": "DEVICE-DELETED", "detail": "no such channel in this account"}],
        }

        session.go_offline()
        offline = send(
            client,
            token,
            "stateRefreshRequest",
            {"devices": [{"externalDeviceId": key}]},
            request_id="st-2",
        )
        values = {(s["capability"], s["value"]) for s in offline.json()["deviceState"][0]["states"]}
        assert ("st.healthCheck", "offline") in values


def test_command_request_drives_the_relay_once_per_request_id(client, ctx):
    with connected_device(client, ctx) as session:
        token = link_token(ctx)
        key = f"{session.id}.ch2"
        body = {"devices": [{"externalDeviceId": key, "commands": [switch_command("on")]}]}

        first = send(client, token, "commandRequest", body, request_id="cmd-1")
        assert first.status_code == 200
        payload = first.json()
        assert payload["headers"]["interactionType"] == "commandResponse"
        assert payload["deviceState"][0]["externalDeviceId"] == key
        assert {
            (s["capability"], s["attribute"], s["value"]) for s in payload["deviceState"][0]["states"]
        } == {("st.switch", "switch", "on"), ("st.healthCheck", "healthStatus", "online")}
        assert session.state()[2] is True
        assert session.sim.applied == ["set"]

        replay = send(client, token, "commandRequest", body, request_id="cmd-1")
        assert replay.json() == payload
        assert session.sim.applied == ["set"]

        off = send(
            client,
            token,
            "commandRequest",
            {"devices": [{"externalDeviceId": key, "commands": [switch_command("off")]}]},
            request_id="cmd-2",
        )
        values = {(s["capability"], s["value"]) for s in off.json()["deviceState"][0]["states"]}
        assert ("st.switch", "off") in values
        assert session.state()[2] is False


def test_command_errors_are_reported_per_device(client, ctx):
    with connected_device(client, ctx) as session:
        token = link_token(ctx)
        key = f"{session.id}.ch0"

        unsupported = send(
            client,
            token,
            "commandRequest",
            {
                "devices": [
                    {
                        "externalDeviceId": key,
                        "commands": [{"component": "main", "capability": "st.switchLevel", "command": "setLevel"}],
                    }
                ]
            },
        )
        assert unsupported.json()["deviceState"][0]["deviceError"][0]["errorEnum"] == "CAPABILITY-NOT-SUPPORTED"
        assert session.commands() == {}

        unknown = send(
            client,
            token,
            "commandRequest",
            {"devices": [{"externalDeviceId": "ghost.ch0", "commands": [switch_command()]}]},
            request_id="cmd-2",
        )
        assert unknown.json()["deviceState"][0]["deviceError"][0]["errorEnum"] == "DEVICE-DELETED"

        session.go_offline()
        offline = send(
            client,
            token,
            "commandRequest",
            {"devices": [{"externalDeviceId": key, "commands": [switch_command()]}]},
            request_id="cmd-3",
        )
        assert offline.json()["deviceState"][0]["deviceError"][0]["errorEnum"] == "DEVICE-OFFLINE"


def test_another_owners_token_cannot_see_or_drive_the_device(client, ctx):
    with connected_device(client, ctx) as session:
        token = link_token(ctx, owner="user-b")
        assert send(client, token, "discoveryRequest").json()["devices"] == []

        execute = send(
            client,
            token,
            "commandRequest",
            {"devices": [{"externalDeviceId": f"{session.id}.ch0", "commands": [switch_command()]}]},
        )
        assert execute.json()["deviceState"][0]["deviceError"][0]["errorEnum"] == "DEVICE-DELETED"
        assert session.commands() == {}


def test_deleting_the_integration_revokes_the_link(client, ctx):
    with connected_device(client, ctx):
        token = link_token(ctx)
        assert send(client, token, "grantCallbackAccess").status_code == 200

        assert send(client, token, "integrationDeleted").status_code == 204
        assert send(client, token, "discoveryRequest").status_code == 401


def test_malformed_envelopes_are_rejected(client, ctx):
    with connected_device(client, ctx):
        token = link_token(ctx)

        no_headers = client.post(
            "/smartthings",
            json={"authentication": {"tokenType": "Bearer", "token": token}},
        )
        assert no_headers.status_code == 400

        unknown = send(client, token, "statePushRequest")
        assert unknown.status_code == 400
