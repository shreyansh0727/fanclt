"""Reads the RTDB mirror of a controller and folds it into the REST model."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from ..models import DeviceOut, default_channels

if TYPE_CHECKING:
    from ..context import AppContext


def info_online(info: dict | None, tolerance_s: float) -> bool:
    if not isinstance(info, dict):
        return False
    stamp_ms = info.get("ts")
    if not isinstance(stamp_ms, (int, float)):
        return False
    age = datetime.now(timezone.utc).timestamp() - stamp_ms / 1000.0
    return -30.0 < age < tolerance_s


def _as_datetime(stamp_ms) -> datetime | None:
    if not isinstance(stamp_ms, (int, float)):
        return None
    return datetime.fromtimestamp(stamp_ms / 1000.0, tz=timezone.utc)


def live_device(doc: dict, node: dict | None, tolerance_s: float) -> DeviceOut:
    count = doc.get("channel_count") or 8
    node = node if isinstance(node, dict) else {}
    info = node.get("info") if isinstance(node.get("info"), dict) else None
    state = node.get("state")
    if not isinstance(state, list) or len(state) != count:
        state = doc.get("state") or [False] * count

    ip = info.get("ip") if info else doc.get("local_ip")
    port = info.get("port") if info else doc.get("local_port")
    return DeviceOut(
        id=doc["id"],
        hardware_id=doc.get("hardware_id", ""),
        name=doc.get("name") or node.get("name") or "Controller",
        room=doc.get("room") or node.get("room") or "",
        online=info_online(info, tolerance_s),
        last_seen=_as_datetime(info.get("ts")) if info else doc.get("last_seen"),
        firmware=(info or {}).get("fw") or doc.get("firmware"),
        local_ip=ip,
        local_port=port or 80,
        lan_key=doc.get("device_key"),
        channel_count=count,
        channels=doc.get("channels") or node.get("channels") or default_channels(count),
        state=[bool(value) for value in state],
        telemetry={
            "rssi": (info or {}).get("rssi"),
            "uptime_s": (info or {}).get("uptime_s"),
            "free_heap": (info or {}).get("heap"),
        },
        created_at=doc.get("created_at"),
    )


async def rtdb_device_out(ctx: "AppContext", doc: dict) -> DeviceOut:
    node = await ctx.rtdb.get(f"devices/{doc['id']}")
    return live_device(doc, node if isinstance(node, dict) else None, ctx.settings.device_online_s)
