from datetime import datetime, timezone

from .conftest import connected_device


def test_schedule_crud(client, ctx):
    with connected_device(client, ctx) as device:
        device_id = device.id

        created = client.post(
            "/api/v1/schedules",
            json={
                "device_id": device_id,
                "name": "Morning fan",
                "time": "06:45",
                "days": [0, 1, 2, 3, 4],
                "timezone": "Asia/Kolkata",
                "action": {"type": "set_channel", "channel": 0, "on": True},
            },
        )
        assert created.status_code == 201, created.text
        schedule = created.json()
        assert schedule["name"] == "Morning fan"
        assert schedule["next_run_at"] is not None
        next_run = datetime.fromisoformat(schedule["next_run_at"])
        assert next_run > datetime.now(timezone.utc)

        listed = client.get("/api/v1/schedules", params={"device_id": device_id}).json()
        assert len(listed) == 1

        disabled = client.patch(f"/api/v1/schedules/{schedule['id']}", json={"enabled": False})
        assert disabled.status_code == 200
        assert disabled.json()["enabled"] is False
        assert disabled.json()["next_run_at"] is None

        reenabled = client.patch(
            f"/api/v1/schedules/{schedule['id']}", json={"enabled": True, "time": "07:15"}
        )
        assert reenabled.status_code == 200
        assert reenabled.json()["time"] == "07:15"
        assert reenabled.json()["next_run_at"] is not None

        assert client.delete(f"/api/v1/schedules/{schedule['id']}").status_code == 204
        assert client.get("/api/v1/schedules").json() == []


def test_schedule_validation(client, ctx):
    with connected_device(client, ctx) as device:
        response = client.post(
            "/api/v1/schedules",
            json={
                "device_id": device.id,
                "time": "06:45",
                "days": [0],
                "timezone": "Mars/Olympus",
                "action": {"type": "set_all", "on": True},
            },
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"


def test_schedule_channel_must_exist(client, ctx):
    with connected_device(client, ctx) as device:
        response = client.post(
            "/api/v1/schedules",
            json={
                "device_id": device.id,
                "time": "06:45",
                "days": [0],
                "timezone": "UTC",
                "action": {"type": "set_channel", "channel": 20, "on": True},
            },
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "channel_out_of_range"


def test_schedule_requires_owned_device(client, ctx):
    with connected_device(client, ctx) as device:
        response = client.post(
            "/api/v1/schedules",
            json={
                "device_id": device.id,
                "time": "06:45",
                "days": [0],
                "timezone": "UTC",
                "action": {"type": "set_all", "on": True},
            },
            headers={"Authorization": "Bearer test-token-b"},
        )
        assert response.status_code == 403


def test_schedule_action_requires_on_for_relays(client, ctx):
    with connected_device(client, ctx) as device:
        response = client.post(
            "/api/v1/schedules",
            json={
                "device_id": device.id,
                "time": "06:45",
                "days": [0],
                "timezone": "UTC",
                "action": {"type": "set_all"},
            },
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"


def test_alarm_and_reminder_actions(client, ctx):
    with connected_device(client, ctx) as device:
        for kind in ("alarm", "reminder"):
            response = client.post(
                "/api/v1/schedules",
                json={
                    "device_id": device.id,
                    "name": kind.title(),
                    "time": "06:45",
                    "days": [0],
                    "timezone": "UTC",
                    "action": {"type": kind},
                },
            )
            assert response.status_code == 201, response.text
            assert response.json()["action"]["type"] == kind
            assert response.json()["action"]["on"] is None


def test_alarm_schedule_fires_chime():
    """Alarm and reminder schedules dispatch the chime action and leave relays alone."""
    import asyncio
    from datetime import timedelta

    from app.config import Settings
    from app.context import AppContext
    from app.repo.memory import MemoryRepo
    from app.rtdb import MemoryRtdb
    from app.services.ratelimit import RateLimiter
    from app.services.runner import ScheduleRunner
    from app.timeutil import utcnow

    async def scenario():
        rtdb = MemoryRtdb()
        ctx = AppContext(
            settings=Settings(
                repo_backend="memory",
                rtdb_backend="memory",
                scheduler_enabled=False,
                command_timeout_s=3.0,
            ),
            repo=MemoryRepo(),
            rtdb=rtdb,
            limiter=RateLimiter(100),
            verify_token=lambda token: None,
            mint_token=lambda device_id: f"test-token:{device_id}",
        )
        device = await ctx.repo.create_device(
            {
                "id": "dev_alarm",
                "hardware_id": "AABBCCDDEEFF",
                "owner_id": "user-a",
                "channel_count": 8,
                "channels": [],
                "state": [False] * 8,
            }
        )
        device_id = device["id"]
        await ctx.repo.create_schedule(
            {
                "id": "sch_alarm",
                "owner_id": "user-a",
                "device_id": device_id,
                "name": "Wake up",
                "time": "06:45",
                "days": [0],
                "timezone": "UTC",
                "action": {"type": "alarm"},
                "enabled": True,
                "next_run_at": utcnow() - timedelta(minutes=1),
            }
        )
        await rtdb.put(
            f"devices/{device_id}/info",
            {"ip": "192.168.1.42", "port": 80, "ts": int(utcnow().timestamp() * 1000)},
        )
        await rtdb.put(f"devices/{device_id}/state", [False] * 8)

        applied: list[dict] = []

        async def fake_device():
            seen: set[str] = set()
            while True:
                commands = await rtdb.get(f"devices/{device_id}/commands") or {}
                for command_id, command in commands.items():
                    if command_id in seen:
                        continue
                    seen.add(command_id)
                    applied.append(command)
                    await rtdb.put(
                        f"devices/{device_id}/acks/{command_id}",
                        {
                            "ok": True,
                            "state": [False] * 8,
                            "ts": int(utcnow().timestamp() * 1000),
                        },
                    )
                await asyncio.sleep(0.02)

        pump = asyncio.create_task(fake_device())
        try:
            runner = ScheduleRunner(ctx)
            assert await runner.tick() == 1
        finally:
            pump.cancel()
            try:
                await pump
            except asyncio.CancelledError:
                pass

        assert applied[0]["action"] == "alarm"
        assert "ch" not in applied[0] and "on" not in applied[0]
        assert (await ctx.repo.get_device(device_id))["state"] == [False] * 8

    asyncio.run(scenario())


def test_due_schedule_fires_once():
    """Drive the runner against a fake RTDB device instead of waiting for a clock."""
    import asyncio
    from datetime import timedelta

    from app.config import Settings
    from app.context import AppContext
    from app.repo.memory import MemoryRepo
    from app.rtdb import MemoryRtdb
    from app.services.ratelimit import RateLimiter
    from app.services.runner import ScheduleRunner
    from app.timeutil import utcnow

    async def scenario():
        rtdb = MemoryRtdb()
        ctx = AppContext(
            settings=Settings(
                repo_backend="memory",
                rtdb_backend="memory",
                scheduler_enabled=False,
                command_timeout_s=3.0,
            ),
            repo=MemoryRepo(),
            rtdb=rtdb,
            limiter=RateLimiter(100),
            verify_token=lambda token: None,
            mint_token=lambda device_id: f"test-token:{device_id}",
        )
        device = await ctx.repo.create_device(
            {
                "id": "dev_test",
                "hardware_id": "AABBCCDDEEFF",
                "owner_id": "user-a",
                "channel_count": 8,
                "channels": [],
                "state": [False] * 8,
            }
        )
        schedule = await ctx.repo.create_schedule(
            {
                "id": "sch_test",
                "owner_id": "user-a",
                "device_id": device["id"],
                "name": "Now",
                "time": "06:45",
                "days": [0],
                "timezone": "UTC",
                "action": {"type": "set_all", "on": True},
                "enabled": True,
                "next_run_at": utcnow() - timedelta(minutes=1),
            }
        )
        device_id = device["id"]
        await rtdb.put(
            f"devices/{device_id}/info",
            {"ip": "192.168.1.42", "port": 80, "ts": int(utcnow().timestamp() * 1000)},
        )
        await rtdb.put(f"devices/{device_id}/state", [False] * 8)

        applied: list[dict] = []

        async def fake_device():
            seen: set[str] = set()
            while True:
                commands = await rtdb.get(f"devices/{device_id}/commands") or {}
                for command_id, command in commands.items():
                    if command_id in seen:
                        continue
                    seen.add(command_id)
                    applied.append(command)
                    state = [bool(command.get("on"))] * 8
                    await rtdb.put(f"devices/{device_id}/state", state)
                    await rtdb.put(
                        f"devices/{device_id}/acks/{command_id}",
                        {"ok": True, "state": state, "ts": int(utcnow().timestamp() * 1000)},
                    )
                await asyncio.sleep(0.02)

        pump = asyncio.create_task(fake_device())
        try:
            runner = ScheduleRunner(ctx)
            fired = await runner.tick()
        finally:
            pump.cancel()
            try:
                await pump
            except asyncio.CancelledError:
                pass

        assert fired == 1
        assert applied[0]["action"] == "all"
        assert applied[0]["on"] is True
        assert (await rtdb.get(f"devices/{device_id}/commands")) is None
        assert (await rtdb.get(f"devices/{device_id}/acks")) is None
        assert (await rtdb.get(f"devices/{device_id}/state")) == [True] * 8
        assert (await ctx.repo.get_device(device_id))["state"] == [True] * 8

        updated = await ctx.repo.get_schedule(schedule["id"])
        assert updated["last_run_at"] is not None
        assert updated["next_run_at"] > utcnow()

        # A second tick must not fire it again.
        assert await runner.tick() == 0

    asyncio.run(scenario())
