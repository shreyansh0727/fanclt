import copy
import secrets
from datetime import datetime

from ..timeutil import aware


class MemoryRepo:
    """In-process store used by the test-suite and FANCTL_REPO_BACKEND=memory."""

    def __init__(self) -> None:
        self.devices: dict[str, dict] = {}
        self.schedules: dict[str, dict] = {}
        self.events: list[dict] = []
        self.release: dict | None = None

    # devices ---------------------------------------------------------------
    async def create_device(self, doc: dict) -> dict:
        doc = copy.deepcopy(doc)
        doc.setdefault("id", "dev_" + secrets.token_hex(8))
        self.devices[doc["id"]] = doc
        return copy.deepcopy(doc)

    async def get_device(self, device_id: str) -> dict | None:
        doc = self.devices.get(device_id)
        return copy.deepcopy(doc) if doc else None

    async def get_device_by_hardware(self, hardware_id: str) -> dict | None:
        for doc in self.devices.values():
            if doc.get("hardware_id") == hardware_id:
                return copy.deepcopy(doc)
        return None

    async def find_device_by_pair_hash(self, code_hash: str) -> dict | None:
        for doc in self.devices.values():
            if doc.get("pair_code_hash") and doc["pair_code_hash"] == code_hash:
                return copy.deepcopy(doc)
        return None

    async def update_device(self, device_id: str, patch: dict) -> dict | None:
        doc = self.devices.get(device_id)
        if not doc:
            return None
        doc.update(copy.deepcopy(patch))
        return copy.deepcopy(doc)

    async def list_devices(self, owner_id: str) -> list[dict]:
        return [copy.deepcopy(d) for d in self.devices.values() if d.get("owner_id") == owner_id]

    async def delete_device(self, device_id: str) -> None:
        self.devices.pop(device_id, None)

    # schedules -------------------------------------------------------------
    async def create_schedule(self, doc: dict) -> dict:
        doc = copy.deepcopy(doc)
        doc.setdefault("id", "sch_" + secrets.token_hex(6))
        self.schedules[doc["id"]] = doc
        return copy.deepcopy(doc)

    async def get_schedule(self, schedule_id: str) -> dict | None:
        doc = self.schedules.get(schedule_id)
        return copy.deepcopy(doc) if doc else None

    async def update_schedule(self, schedule_id: str, patch: dict) -> dict | None:
        doc = self.schedules.get(schedule_id)
        if not doc:
            return None
        doc.update(copy.deepcopy(patch))
        return copy.deepcopy(doc)

    async def delete_schedule(self, schedule_id: str) -> bool:
        return self.schedules.pop(schedule_id, None) is not None

    async def list_schedules(self, owner_id: str) -> list[dict]:
        return [copy.deepcopy(s) for s in self.schedules.values() if s.get("owner_id") == owner_id]

    async def due_schedules(self, now: datetime) -> list[dict]:
        due = []
        for schedule in self.schedules.values():
            next_run = aware(schedule.get("next_run_at"))
            if next_run and next_run <= now:
                due.append(copy.deepcopy(schedule))
        return due

    async def delete_schedules_for_device(self, device_id: str) -> None:
        for key in [k for k, v in self.schedules.items() if v.get("device_id") == device_id]:
            self.schedules.pop(key, None)

    # events ----------------------------------------------------------------
    async def add_event(self, doc: dict) -> None:
        event = copy.deepcopy(doc)
        event.setdefault("id", "evt_" + secrets.token_hex(6))
        self.events.append(event)
        if len(self.events) > 5000:
            del self.events[:1000]

    async def list_events(self, owner_id: str, limit: int = 50) -> list[dict]:
        rows = [e for e in self.events if e.get("owner_id") == owner_id]
        rows.sort(key=lambda e: e.get("ts") or 0, reverse=True)
        return rows[:limit]

    # releases --------------------------------------------------------------
    async def get_release(self) -> dict | None:
        return copy.deepcopy(self.release) if self.release else None
