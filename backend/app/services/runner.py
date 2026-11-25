import asyncio
import logging
from typing import TYPE_CHECKING

from ..models import CommandRequest
from ..timeutil import utcnow
from .commands import dispatch_command
from .schedule_time import next_occurrence

if TYPE_CHECKING:
    from ..context import AppContext

log = logging.getLogger(__name__)


class ScheduleRunner:
    """Polls Firestore for due schedules and fires them at the device."""

    def __init__(self, ctx: "AppContext") -> None:
        self.ctx = ctx
        self._task: asyncio.Task | None = None

    def start(self) -> asyncio.Task:
        self._task = asyncio.create_task(self._loop(), name="schedule-runner")
        return self._task

    async def stop(self) -> None:
        if not self._task:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("schedule tick failed")
            await asyncio.sleep(self.ctx.settings.scheduler_tick_s)

    async def tick(self, now=None) -> int:
        now = now or utcnow()
        due = await self.ctx.repo.due_schedules(now)
        fired = 0
        for schedule in due:
            try:
                await self._run_one(schedule, now)
                fired += 1
            except Exception:
                log.exception("schedule %s failed", schedule.get("id"))
        return fired

    async def _run_one(self, schedule: dict, now) -> None:
        following = next_occurrence(
            schedule["time"], schedule["days"], schedule.get("timezone") or "UTC", now
        )
        # Move the cursor before dispatching so a slow device cannot cause the
        # same schedule to fire twice on the next tick.
        await self.ctx.repo.update_schedule(
            schedule["id"], {"last_run_at": now, "next_run_at": following}
        )

        device = await self.ctx.repo.get_device(schedule["device_id"])
        if not device:
            return

        action = schedule["action"]
        kind = action["type"]
        if kind == "set_all":
            request = CommandRequest(action="all", on=action["on"])
        elif kind == "set_channel":
            request = CommandRequest(action="set", channel=action.get("channel"), on=action["on"])
        else:  # alarm / reminder play a chime and a scene, no relay change
            request = CommandRequest(action=kind)
        result = await dispatch_command(self.ctx, device, request, source="schedule")
        log.info(
            "schedule %s ran on %s -> acked=%s",
            schedule["id"],
            device["id"],
            result.acked,
        )
