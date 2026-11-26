from datetime import datetime, timezone

from starlette.concurrency import run_in_threadpool

from ..timeutil import aware

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_FAR_FUTURE = datetime(9999, 1, 1, tzinfo=timezone.utc)


def _field_filter(field: str, op: str, value):
    from google.cloud.firestore_v1.base_query import FieldFilter

    return FieldFilter(field, op, value)


class FirestoreRepo:
    """Firestore implementation. The sync client is offloaded to a thread so the
    event loop keeps serving requests while a query runs."""

    def __init__(self, client) -> None:
        self.db = client

    # devices ---------------------------------------------------------------
    async def create_device(self, doc: dict) -> dict:
        payload = dict(doc)
        await run_in_threadpool(
            lambda: self.db.collection("devices").document(payload["id"]).set(payload)
        )
        return payload

    async def get_device(self, device_id: str) -> dict | None:
        def _read():
            snapshot = self.db.collection("devices").document(device_id).get()
            return {**snapshot.to_dict(), "id": snapshot.id} if snapshot.exists else None

        return await run_in_threadpool(_read)

    async def get_device_by_hardware(self, hardware_id: str) -> dict | None:
        return await run_in_threadpool(self._first_by, "devices", "hardware_id", hardware_id)

    async def find_device_by_pair_hash(self, code_hash: str) -> dict | None:
        return await run_in_threadpool(self._first_by, "devices", "pair_code_hash", code_hash)

    async def update_device(self, device_id: str, patch: dict) -> dict | None:
        return await run_in_threadpool(self._update, "devices", device_id, dict(patch))

    async def list_devices(self, owner_id: str) -> list[dict]:
        rows = await run_in_threadpool(self._list_by, "devices", "owner_id", owner_id)
        rows.sort(key=lambda d: aware(d.get("created_at")) or _EPOCH)
        return rows

    async def delete_device(self, device_id: str) -> None:
        await run_in_threadpool(
            lambda: self.db.collection("devices").document(device_id).delete()
        )

    # schedules -------------------------------------------------------------
    async def create_schedule(self, doc: dict) -> dict:
        payload = dict(doc)
        await run_in_threadpool(
            lambda: self.db.collection("schedules").document(payload["id"]).set(payload)
        )
        return payload

    async def get_schedule(self, schedule_id: str) -> dict | None:
        def _read():
            snapshot = self.db.collection("schedules").document(schedule_id).get()
            return {**snapshot.to_dict(), "id": snapshot.id} if snapshot.exists else None

        return await run_in_threadpool(_read)

    async def update_schedule(self, schedule_id: str, patch: dict) -> dict | None:
        return await run_in_threadpool(self._update, "schedules", schedule_id, dict(patch))

    async def delete_schedule(self, schedule_id: str) -> bool:
        def _delete():
            ref = self.db.collection("schedules").document(schedule_id)
            if not ref.get().exists:
                return False
            ref.delete()
            return True

        return await run_in_threadpool(_delete)

    async def list_schedules(self, owner_id: str) -> list[dict]:
        rows = await run_in_threadpool(self._list_by, "schedules", "owner_id", owner_id)
        rows.sort(key=lambda s: aware(s.get("next_run_at")) or _FAR_FUTURE)
        return rows

    async def due_schedules(self, now: datetime) -> list[dict]:
        # One range filter only, so no composite index is needed; `enabled` is
        # checked in Python.
        def _read():
            query = (
                self.db.collection("schedules")
                .where(filter=_field_filter("next_run_at", "<=", now))
                .limit(100)
            )
            return [{**s.to_dict(), "id": s.id} for s in query.stream()]

        rows = await run_in_threadpool(_read)
        due = []
        for row in rows:
            next_run = aware(row.get("next_run_at"))
            if row.get("enabled", True) and next_run and next_run <= now:
                due.append(row)
        return due

    async def delete_schedules_for_device(self, device_id: str) -> None:
        def _delete():
            query = self.db.collection("schedules").where(
                filter=_field_filter("device_id", "==", device_id)
            )
            for snapshot in query.stream():
                snapshot.reference.delete()

        await run_in_threadpool(_delete)

    # events ----------------------------------------------------------------
    async def add_event(self, doc: dict) -> None:
        await run_in_threadpool(
            lambda: self.db.collection("events").document(doc["id"]).set(dict(doc))
        )

    async def list_events(self, owner_id: str, limit: int = 50) -> list[dict]:
        rows = await run_in_threadpool(self._list_by, "events", "owner_id", owner_id, 200)
        rows.sort(key=lambda e: aware(e.get("ts")) or _EPOCH, reverse=True)
        return rows[:limit]

    # releases --------------------------------------------------------------
    async def get_release(self) -> dict | None:
        def _read():
            snapshot = self.db.collection("releases").document("current").get()
            return snapshot.to_dict() if snapshot.exists else None

        return await run_in_threadpool(_read)

    # oauth clients + auth codes -------------------------------------------
    async def create_oauth_client(self, doc: dict) -> dict:
        payload = dict(doc)
        await run_in_threadpool(
            lambda: self.db.collection("oauth_clients").document(payload["id"]).set(payload)
        )
        return payload

    async def get_oauth_client(self, client_id: str) -> dict | None:
        return await run_in_threadpool(self._get, "oauth_clients", client_id)

    async def create_auth_code(self, doc: dict) -> dict:
        payload = dict(doc)
        await run_in_threadpool(
            lambda: self.db.collection("oauth_codes").document(payload["id"]).set(payload)
        )
        return payload

    async def get_auth_code(self, code_hash: str) -> dict | None:
        return await run_in_threadpool(self._get, "oauth_codes", code_hash)

    async def update_auth_code(self, code_hash: str, patch: dict) -> dict | None:
        return await run_in_threadpool(self._update, "oauth_codes", code_hash, dict(patch))

    # assistant links ------------------------------------------------------
    async def create_link(self, doc: dict) -> dict:
        payload = dict(doc)
        await run_in_threadpool(
            lambda: self.db.collection("assistant_links").document(payload["id"]).set(payload)
        )
        return payload

    async def get_link(self, link_id: str) -> dict | None:
        return await run_in_threadpool(self._get, "assistant_links", link_id)

    async def update_link(self, link_id: str, patch: dict) -> dict | None:
        return await run_in_threadpool(self._update, "assistant_links", link_id, dict(patch))

    async def get_link_by_hash(self, field: str, value: str) -> dict | None:
        if field not in ("access_hash", "refresh_hash"):
            raise ValueError(f"unsupported link lookup field: {field}")
        return await run_in_threadpool(self._first_by, "assistant_links", field, value)

    async def list_links(self, owner_id: str) -> list[dict]:
        rows = await run_in_threadpool(self._list_by, "assistant_links", "owner_id", owner_id)
        rows.sort(key=lambda link: aware(link.get("created_at")) or _EPOCH, reverse=True)
        return rows

    async def delete_link(self, link_id: str) -> None:
        await run_in_threadpool(
            lambda: self.db.collection("assistant_links").document(link_id).delete()
        )

    # assistant command idempotency ----------------------------------------
    async def get_assistant_command(self, key: str) -> dict | None:
        return await run_in_threadpool(self._get, "assistant_commands", key)

    async def save_assistant_command(self, doc: dict) -> None:
        payload = dict(doc)
        await run_in_threadpool(
            lambda: self.db.collection("assistant_commands").document(payload["id"]).set(payload)
        )

    # helpers ---------------------------------------------------------------
    def _get(self, collection: str, doc_id: str) -> dict | None:
        snapshot = self.db.collection(collection).document(doc_id).get()
        return {**snapshot.to_dict(), "id": snapshot.id} if snapshot.exists else None

    def _first_by(self, collection: str, field: str, value) -> dict | None:
        query = (
            self.db.collection(collection)
            .where(filter=_field_filter(field, "==", value))
            .limit(1)
        )
        for snapshot in query.stream():
            return {**snapshot.to_dict(), "id": snapshot.id}
        return None

    def _list_by(self, collection: str, field: str, value, limit: int | None = None) -> list[dict]:
        query = self.db.collection(collection).where(filter=_field_filter(field, "==", value))
        if limit:
            query = query.limit(limit)
        return [{**s.to_dict(), "id": s.id} for s in query.stream()]

    def _update(self, collection: str, doc_id: str, patch: dict) -> dict | None:
        ref = self.db.collection(collection).document(doc_id)
        if not ref.get().exists:
            return None
        ref.update(patch)
        snapshot = ref.get()
        return {**snapshot.to_dict(), "id": snapshot.id}
