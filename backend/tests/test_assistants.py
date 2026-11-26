import asyncio

import pytest

from app.errors import ApiError
from app.models import default_channels
from app.services import assistants
from app.timeutil import utcnow

from .conftest import CODE, HW, connected_device


def make_device(ctx, device_id: str, owner: str, name: str, count: int = 2, channels=None):
    asyncio.run(
        ctx.repo.create_device(
            {
                "id": device_id,
                "owner_id": owner,
                "hardware_id": "AABBCCDDEEFF",
                "name": name,
                "room": "Workshop",
                "channel_count": count,
                "channels": channels or default_channels(count),
                "state": [False] * count,
                "created_at": utcnow(),
            }
        )
    )


def test_names_are_prefixed_only_when_the_owner_has_several_controllers(client, ctx):
    make_device(ctx, "dev_a", "user-a", "Living room", count=2)
    devices = asyncio.run(assistants.list_assistant_devices(ctx, "user-a"))
    assert [d["name"] for d in devices] == ["Fan", "Light 1"]
    assert devices[0]["id"] == "dev_a.ch0"
    assert devices[0]["type"] == "fan"
    assert devices[1]["type"] == "light"
    assert devices[0]["room"] == "Workshop"

    make_device(ctx, "dev_b", "user-a", "Garage", count=2)
    names = [d["name"] for d in asyncio.run(assistants.list_assistant_devices(ctx, "user-a"))]
    assert names == ["Living room Fan", "Living room Light 1", "Garage Fan", "Garage Light 1"]

    # another owner's controllers never leak in
    assert asyncio.run(assistants.list_assistant_devices(ctx, "user-b")) == []


def test_snapshot_reads_state_and_presence_from_the_device_node(client, ctx):
    with connected_device(client, ctx) as session:
        snapshot = asyncio.run(assistants.snapshot(ctx, "user-a"))
        key = assistants.assistant_device_id(session.id, 0)
        assert snapshot[key] == {"on": False, "online": True}
        assert len(snapshot) == 8

        asyncio.run(
            assistants.execute_switches(
                ctx, "user-a", [{"device_id": session.id, "channel": 3, "on": True}], "google"
            )
        )
        snapshot = asyncio.run(
            assistants.snapshot(ctx, "user-a", [assistants.assistant_device_id(session.id, 3)])
        )
        assert snapshot == {assistants.assistant_device_id(session.id, 3): {"on": True, "online": True}}

        session.go_offline()
        offline = asyncio.run(assistants.snapshot(ctx, "user-a"))
        assert offline[key]["online"] is False


def test_execute_switches_drives_the_relay_and_tags_the_event_source(client, ctx):
    with connected_device(client, ctx) as session:
        results = asyncio.run(
            assistants.execute_switches(
                ctx, "user-a", [{"device_id": session.id, "channel": 2, "on": True}], "google"
            )
        )
        assert results[0]["acked"] is True
        assert results[0]["online"] is True
        assert results[0]["error"] is None
        assert session.state()[2] is True

        events = [e for e in ctx.repo.events if e.get("kind") == "command"]
        assert len(events) == 1
        assert events[0]["detail"]["source"] == "google"
        assert events[0]["detail"]["channel"] == 2
        assert events[0]["detail"]["on"] is True


def test_execute_validates_every_target_before_dispatching(client, ctx):
    with connected_device(client, ctx) as session:
        with pytest.raises(ApiError) as foreign:
            asyncio.run(
                assistants.execute_switches(
                    ctx, "user-b", [{"device_id": session.id, "channel": 0, "on": True}], "google"
                )
            )
        assert foreign.value.status_code == 404

        with pytest.raises(ApiError) as out_of_range:
            asyncio.run(
                assistants.execute_switches(
                    ctx,
                    "user-a",
                    [
                        {"device_id": session.id, "channel": 0, "on": True},
                        {"device_id": session.id, "channel": 99, "on": True},
                    ],
                    "google",
                )
            )
        assert out_of_range.value.status_code == 422
        assert session.commands() == {}
        assert session.state() == [False] * 8


def test_request_id_makes_a_retried_command_run_once(client, ctx):
    with connected_device(client, ctx) as session:
        async def produce():
            results = await assistants.execute_switches(
                ctx, "user-a", [{"device_id": session.id, "channel": 1, "on": True}], "smartthings"
            )
            return {"results": results}

        first = asyncio.run(assistants.run_once(ctx, "user-a", "smartthings", "req-1", produce))
        second = asyncio.run(assistants.run_once(ctx, "user-a", "smartthings", "req-1", produce))
        assert first == second
        assert session.sim.applied == ["set"]
        assert session.state()[1] is True

        third = asyncio.run(assistants.run_once(ctx, "user-a", "smartthings", "req-2", produce))
        assert third["results"][0]["acked"] is True
        assert session.sim.applied == ["set", "set"]
