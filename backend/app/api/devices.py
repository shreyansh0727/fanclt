import asyncio

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Response

from ..errors import ApiError, forbidden, not_found
from ..models import (
    ClaimRequest,
    CommandRequest,
    CommandResponse,
    DeviceOut,
    DevicePatch,
    EventOut,
    default_channels,
)
from ..security import User, hash_pair_code, new_device_key
from ..services.commands import dispatch_command, send_identify
from ..services.live import info_online, live_device, rtdb_device_out
from ..timeutil import aware, utcnow
from .deps import current_user, get_ctx, owned_device

router = APIRouter(prefix="/devices", tags=["devices"])


async def _refresh_presence(ctx, doc: dict) -> dict:
    """A controller counts as present when it polled /device/token recently."""
    last_seen = aware(doc.get("last_seen"))
    if not last_seen or (utcnow() - last_seen).total_seconds() > ctx.settings.claim_presence_s:
        raise ApiError(
            409, "device_offline", "the controller must be online and unpaired to claim it"
        )
    return doc


def _owner_node(doc: dict) -> dict:
    count = doc.get("channel_count") or 8
    return {
        "owner": doc["owner_id"],
        "name": doc.get("name") or "Controller",
        "room": doc.get("room") or "",
        "channels": doc.get("channels") or default_channels(count),
        "channel_count": count,
    }


@router.post("/claim", response_model=DeviceOut)
async def claim_device(req: ClaimRequest, user: User = Depends(current_user), ctx=Depends(get_ctx)):
    doc = await ctx.repo.find_device_by_pair_hash(hash_pair_code(req.code))
    if not doc:
        raise ApiError(
            404,
            "invalid_code",
            "pairing code not recognised - check the code and that the controller is online",
        )
    if doc.get("owner_id"):
        raise ApiError(409, "already_claimed", "this controller is already paired to an account")

    expires = aware(doc.get("pair_code_expires_at"))
    if expires and expires < utcnow():
        raise ApiError(410, "code_expired", "pairing window closed, reboot the controller and retry")

    await _refresh_presence(ctx, doc)

    updated = await ctx.repo.update_device(
        doc["id"],
        {
            "owner_id": user.uid,
            "device_key": new_device_key(),
            "name": req.name or doc.get("name") or "Controller",
            "claimed_at": utcnow(),
            "pair_code_hash": None,
            "pair_code_expires_at": None,
        },
    )
    updated = updated or doc

    # PUT replaces the node, so a previous claim's leftovers cannot leak into
    # the new pairing (and any stale `unclaim` flag is cleared).
    await ctx.rtdb.put(f"devices/{updated['id']}", _owner_node(updated))
    return await rtdb_device_out(ctx, updated)


@router.get("", response_model=list[DeviceOut])
async def list_devices(user: User = Depends(current_user), ctx=Depends(get_ctx)):
    docs = await ctx.repo.list_devices(user.uid)
    nodes = await asyncio.gather(*[ctx.rtdb.get(f"devices/{doc['id']}") for doc in docs])
    return [
        live_device(doc, node if isinstance(node, dict) else None, ctx.settings.device_online_s)
        for doc, node in zip(docs, nodes)
    ]


@router.get("/{device_id}", response_model=DeviceOut)
async def get_device(device: dict = Depends(owned_device), ctx=Depends(get_ctx)):
    return await rtdb_device_out(ctx, device)


@router.patch("/{device_id}", response_model=DeviceOut)
async def patch_device(
    req: DevicePatch, device: dict = Depends(owned_device), ctx=Depends(get_ctx)
):
    patch: dict = {}
    if req.name is not None:
        patch["name"] = req.name
    if req.room is not None:
        patch["room"] = req.room
    if req.channels is not None:
        count = device.get("channel_count") or 8
        if len(req.channels) != count:
            raise ApiError(422, "invalid_channels", f"this controller has exactly {count} channels")
        if sorted(channel.index for channel in req.channels) != list(range(count)):
            raise ApiError(422, "invalid_channels", "channel indexes must cover 0..count-1 exactly")
        patch["channels"] = [channel.model_dump() for channel in req.channels]

    if not patch:
        return await rtdb_device_out(ctx, device)

    updated = await ctx.repo.update_device(device["id"], patch)
    if updated is None:
        raise not_found("device")

    mirror = {key: patch[key] for key in ("name", "room", "channels") if key in patch}
    if mirror and isinstance(await ctx.rtdb.get(f"devices/{device['id']}"), dict):
        await ctx.rtdb.patch(f"devices/{device['id']}", mirror)
    return await rtdb_device_out(ctx, updated)


@router.delete("/{device_id}", status_code=204)
async def delete_device(device: dict = Depends(owned_device), ctx=Depends(get_ctx)):
    if device.get("owner_id") and device.get("device_key"):
        # The node shrinks to just the flag: the device wipes its key when it
        # sees it, and the owner can no longer read the node.
        await ctx.rtdb.put(f"devices/{device['id']}", {"unclaim": True})
    await ctx.repo.delete_schedules_for_device(device["id"])
    await ctx.repo.delete_device(device["id"])
    return Response(status_code=204)


@router.post("/{device_id}/commands", response_model=CommandResponse)
async def send_command(
    req: CommandRequest,
    background: BackgroundTasks,
    device: dict = Depends(owned_device),
    ctx=Depends(get_ctx),
):
    # The reply goes out as soon as the controller acks; mirroring the state,
    # logging the event and pruning the database nodes happen afterwards.
    return await dispatch_command(ctx, device, req, source="app", background=background)


@router.get("/{device_id}/events", response_model=list[EventOut])
async def list_events(
    device_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    user: User = Depends(current_user),
    ctx=Depends(get_ctx),
):
    device = await ctx.repo.get_device(device_id)
    if not device:
        raise not_found("device")
    if device.get("owner_id") != user.uid:
        raise forbidden()
    rows = await ctx.repo.list_events(user.uid, limit=limit)
    return [row for row in rows if row.get("device_id") == device_id]


@router.post("/{device_id}/identify", status_code=202)
async def identify_device(device: dict = Depends(owned_device), ctx=Depends(get_ctx)):
    """Blink the controller LED - useful when you have three identical boxes."""
    node = await ctx.rtdb.get(f"devices/{device['id']}")
    info = node.get("info") if isinstance(node, dict) else None
    if not info_online(info, ctx.settings.device_online_s):
        raise ApiError(409, "device_offline", "the controller is not connected")
    await send_identify(ctx, device)
    return {"sent": True}
