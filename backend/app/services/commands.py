import asyncio
import logging
import uuid
from typing import TYPE_CHECKING

from fastapi import BackgroundTasks

from ..errors import ApiError
from ..models import CommandRequest, CommandResponse
from ..timeutil import utcnow
from .live import info_online

if TYPE_CHECKING:
    from ..context import AppContext

log = logging.getLogger(__name__)

# The Realtime Database lives in us-central1 while the service runs in
# asia-south1, so every REST call costs ~300-400 ms. Anything that does not
# have to be in front of the reply runs after it, in the background.
ACK_POLL_S = 0.1


def now_ms() -> int:
    return int(utcnow().timestamp() * 1000)


async def _await_ack(ctx: "AppContext", device_id: str, command_id: str, timeout: float) -> dict | None:
    path = f"devices/{device_id}/acks/{command_id}"
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        ack = await ctx.rtdb.get(path)
        if isinstance(ack, dict):
            return ack
        await asyncio.sleep(ACK_POLL_S)
    return None


async def _log_event(ctx: "AppContext", device: dict, req: CommandRequest, source: str,
                     command_id: str, ok: bool, error: str | None) -> None:
    if not ctx.settings.log_events:
        return
    await ctx.repo.add_event(
        {
            "id": "evt_" + uuid.uuid4().hex[:16],
            "owner_id": device.get("owner_id"),
            "device_id": device["id"],
            "kind": "command",
            "ts": utcnow(),
            "detail": {
                "action": req.action,
                "channel": req.channel,
                "on": req.on,
                "source": source,
                "acked": ok,
                "error": error,
                "command_id": command_id,
            },
        }
    )


async def _drop(ctx: "AppContext", path: str) -> None:
    try:
        await ctx.rtdb.delete(path)
    except Exception:  # pruning must never fail a command
        log.warning("could not prune %s", path, exc_info=True)


async def _finish_command(
    ctx: "AppContext",
    device: dict,
    req: CommandRequest,
    source: str,
    command_id: str,
    state: list[bool],
    ok: bool,
    error: str | None,
) -> None:
    """Work that used to sit in front of the HTTP reply: the Firestore state
    mirror, the event row and the pruning of the command/ack nodes. Runs on
    Starlette's background slot in production and inline (awaited) elsewhere."""
    try:
        await asyncio.gather(
            _drop(ctx, f"devices/{device['id']}/commands/{command_id}"),
            _drop(ctx, f"devices/{device['id']}/acks/{command_id}"),
            _log_event(ctx, device, req, source, command_id, ok, error),
            ctx.repo.update_device(device["id"], {"state": state, "last_seen": utcnow()}),
        )
    except Exception:
        log.exception("command cleanup for %s failed", command_id)


async def _live_state(ctx: "AppContext", device_id: str, count: int, fallback: list[bool]) -> list[bool]:
    """After a missed ack the Firestore copy may be behind (or the command may
    have landed without its ack), so prefer what the controller publishes."""
    try:
        reported = await ctx.rtdb.get(f"devices/{device_id}/state")
    except Exception:
        return fallback
    if isinstance(reported, list) and len(reported) == count:
        return [bool(value) for value in reported]
    return fallback


async def dispatch_command(
    ctx: "AppContext",
    device: dict,
    req: CommandRequest,
    source: str = "app",
    background: BackgroundTasks | None = None,
) -> CommandResponse:
    count = device.get("channel_count") or 8

    if req.action == "all":
        if req.on is None:
            raise ApiError(422, "invalid_command", "'on' is required for action=all")
    elif req.action in ("set", "toggle"):
        if req.channel is None:
            raise ApiError(422, "invalid_command", f"'channel' is required for action={req.action}")
        if req.channel >= count:
            raise ApiError(422, "channel_out_of_range", f"channel must be 0..{count - 1}")
        if req.action == "set" and req.on is None:
            raise ApiError(422, "invalid_command", "'on' is required for action=set")
    # pet/feed/play/sleep/wake and alarm/alarm_stop/reminder need no channel -
    # they only animate the pet, ring the chime or silence a ringing alarm.

    if not ctx.limiter.allow(device["id"]):
        raise ApiError(429, "rate_limited", "too many commands for this device, slow down")

    command_id = req.command_id or uuid.uuid4().hex
    fallback_state = list(device.get("state") or [False] * count)

    command: dict = {"action": req.action, "ts": now_ms()}
    if req.channel is not None:
        command["ch"] = req.channel
    if req.on is not None:
        command["on"] = req.on

    settled = False

    async def settle(state: list[bool], ok: bool, error: str | None) -> None:
        nonlocal settled
        if settled:
            return
        settled = True
        if background is not None:
            background.add_task(
                _finish_command, ctx, device, req, source, command_id, state, ok, error
            )
        else:
            await _finish_command(ctx, device, req, source, command_id, state, ok, error)

    # Presence and the write go out together: two round trips in series were
    # pure latency. A command written to an offline controller is pruned below;
    # the firmware ignores anything it finds in its first snapshot.
    node_task = asyncio.create_task(ctx.rtdb.get(f"devices/{device['id']}"))
    put_task = asyncio.create_task(
        ctx.rtdb.put(f"devices/{device['id']}/commands/{command_id}", command)
    )
    try:
        node = await node_task
        await put_task
    except Exception:
        for task in (node_task, put_task):
            if not task.done():
                task.cancel()
        await settle(fallback_state, False, "dispatch_failed")
        raise

    info = node.get("info") if isinstance(node, dict) else None
    online = info_online(info, ctx.settings.device_online_s)

    if not online:
        await settle(fallback_state, False, "device_offline")
        return CommandResponse(
            command_id=command_id, acked=False, online=False, state=fallback_state,
            error="device_offline",
        )

    ack = await _await_ack(ctx, device["id"], command_id, ctx.settings.command_timeout_s)
    ok = bool(ack and ack.get("ok"))

    state = fallback_state
    reported = (ack or {}).get("state")
    if isinstance(reported, list) and len(reported) == count:
        state = [bool(value) for value in reported]
    elif ack is None:
        state = await _live_state(ctx, device["id"], count, fallback_state)

    error = None if ok else (ack or {}).get("error") or "no_ack"
    await settle(state, ok, error)

    return CommandResponse(
        command_id=command_id, acked=ok, online=True, state=state, error=error
    )


async def _prune_identify(ctx: "AppContext", device_id: str, now: int) -> None:
    """Nothing waits on an identify command, so old ones would otherwise sit in
    the database forever. Commands younger than 15 s are left alone."""
    commands = await ctx.rtdb.get(f"devices/{device_id}/commands")
    if not isinstance(commands, dict):
        return
    for command_id, command in commands.items():
        if not isinstance(command, dict) or command.get("action") != "identify":
            continue
        ts = command.get("ts")
        if isinstance(ts, (int, float)) and now - ts < 15_000:
            continue
        for path in (
            f"devices/{device_id}/commands/{command_id}",
            f"devices/{device_id}/acks/{command_id}",
        ):
            try:
                await ctx.rtdb.delete(path)
            except Exception:
                pass


async def send_identify(ctx: "AppContext", device: dict, seconds: int = 5) -> None:
    command_id = uuid.uuid4().hex
    now = now_ms()
    await _prune_identify(ctx, device["id"], now)
    await ctx.rtdb.put(
        f"devices/{device['id']}/commands/{command_id}",
        {"action": "identify", "seconds": seconds, "ts": now},
    )
