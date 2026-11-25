import secrets

from fastapi import APIRouter, Depends, Response

from ..errors import ApiError, forbidden, not_found
from ..models import ScheduleCreate, ScheduleOut, ScheduleUpdate, schedule_out
from ..security import User
from ..services.schedule_time import next_occurrence
from ..timeutil import utcnow
from .deps import current_user, get_ctx

router = APIRouter(prefix="/schedules", tags=["schedules"])


async def _owned_schedule(schedule_id: str, user: User, ctx) -> dict:
    schedule = await ctx.repo.get_schedule(schedule_id)
    if not schedule:
        raise not_found("schedule")
    if schedule.get("owner_id") != user.uid:
        raise forbidden()
    return schedule


@router.get("", response_model=list[ScheduleOut])
async def list_schedules(
    device_id: str | None = None,
    user: User = Depends(current_user),
    ctx=Depends(get_ctx),
):
    rows = await ctx.repo.list_schedules(user.uid)
    if device_id:
        rows = [row for row in rows if row.get("device_id") == device_id]
    return [schedule_out(row) for row in rows]


@router.post("", response_model=ScheduleOut, status_code=201)
async def create_schedule(
    req: ScheduleCreate, user: User = Depends(current_user), ctx=Depends(get_ctx)
):
    device = await ctx.repo.get_device(req.device_id)
    if not device:
        raise not_found("device")
    if device.get("owner_id") != user.uid:
        raise forbidden()

    if req.action.type == "set_channel":
        count = device.get("channel_count") or 8
        if req.action.channel is None or req.action.channel >= count:
            raise ApiError(422, "channel_out_of_range", f"channel must be 0..{count - 1}")

    now = utcnow()
    doc = {
        "id": "sch_" + secrets.token_hex(6),
        "owner_id": user.uid,
        "device_id": req.device_id,
        "name": req.name,
        "time": req.time,
        "days": sorted(req.days),
        "timezone": req.timezone,
        "action": req.action.model_dump(),
        "enabled": req.enabled,
        "next_run_at": next_occurrence(req.time, req.days, req.timezone, now) if req.enabled else None,
        "last_run_at": None,
        "created_at": now,
    }
    created = await ctx.repo.create_schedule(doc)
    return schedule_out(created)


@router.patch("/{schedule_id}", response_model=ScheduleOut)
async def update_schedule(
    schedule_id: str,
    req: ScheduleUpdate,
    user: User = Depends(current_user),
    ctx=Depends(get_ctx),
):
    schedule = await _owned_schedule(schedule_id, user, ctx)
    patch = req.model_dump(exclude_unset=True, exclude_none=True)
    if "action" in patch:
        patch["action"] = dict(patch["action"])

    merged = {**schedule, **patch}
    if merged.get("enabled", True):
        merged["next_run_at"] = next_occurrence(
            merged["time"], merged["days"], merged.get("timezone") or "UTC", utcnow()
        )
    else:
        merged["next_run_at"] = None
    patch["next_run_at"] = merged["next_run_at"]

    updated = await ctx.repo.update_schedule(schedule_id, patch)
    if updated is None:
        raise not_found("schedule")
    return schedule_out(updated)


@router.delete("/{schedule_id}", status_code=204)
async def delete_schedule(
    schedule_id: str, user: User = Depends(current_user), ctx=Depends(get_ctx)
):
    await _owned_schedule(schedule_id, user, ctx)
    await ctx.repo.delete_schedule(schedule_id)
    return Response(status_code=204)
