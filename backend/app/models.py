from __future__ import annotations

from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, model_validator

ChannelType = Literal["fan", "light", "socket", "other"]

DEFAULT_CHANNEL_NAMES = [
    ("Fan", "fan"),
    ("Light 1", "light"),
    ("Light 2", "light"),
    ("Light 3", "light"),
    ("Socket 1", "socket"),
    ("Socket 2", "socket"),
    ("Spare 1", "other"),
    ("Spare 2", "other"),
]


def default_channels(count: int) -> list[dict]:
    out = []
    for i in range(count):
        name, kind = DEFAULT_CHANNEL_NAMES[i] if i < len(DEFAULT_CHANNEL_NAMES) else (f"Channel {i + 1}", "other")
        out.append({"index": i, "name": name, "type": kind})
    return out


class Channel(BaseModel):
    index: int = Field(ge=0, le=31)
    name: str = Field(min_length=1, max_length=32)
    type: ChannelType = "other"


class DeviceOut(BaseModel):
    id: str
    hardware_id: str
    name: str
    room: str = ""
    online: bool = False
    last_seen: datetime | None = None
    firmware: str | None = None
    local_ip: str | None = None
    local_port: int = 80
    lan_key: str | None = None
    channel_count: int = 8
    channels: list[Channel] = Field(default_factory=list)
    state: list[bool] = Field(default_factory=list)
    telemetry: dict = Field(default_factory=dict)
    created_at: datetime | None = None


class ClaimRequest(BaseModel):
    code: str = Field(min_length=4, max_length=16)
    name: str | None = Field(default=None, min_length=1, max_length=48)


class DevicePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=48)
    room: str | None = Field(default=None, max_length=48)
    channels: list[Channel] | None = None


class CommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Relay actions plus the pet's reactions; the pet and chime ones carry no
    # channel. alarm latches until alarm_stop.
    action: Literal[
        "set",
        "all",
        "toggle",
        "pet",
        "feed",
        "play",
        "sleep",
        "wake",
        "alarm",
        "alarm_stop",
        "reminder",
    ]
    channel: int | None = Field(default=None, ge=0, le=31)
    on: bool | None = None
    command_id: str | None = Field(default=None, min_length=8, max_length=64)


class CommandResponse(BaseModel):
    command_id: str
    acked: bool
    online: bool
    state: list[bool]
    error: str | None = None


class ScheduleAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Relay actions switch channels; alarm and reminder only play a chime and a
    # scene on the device panel, so they carry neither `on` nor `channel`.
    type: Literal["set_all", "set_channel", "alarm", "reminder"]
    on: bool | None = None
    channel: int | None = Field(default=None, ge=0, le=31)

    @model_validator(mode="after")
    def _fields_required(self):
        if self.type in ("set_all", "set_channel") and self.on is None:
            raise ValueError("on is required for relay actions")
        if self.type == "set_channel" and self.channel is None:
            raise ValueError("channel is required when type=set_channel")
        return self


class ScheduleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: str
    name: str = Field(default="Schedule", min_length=1, max_length=48)
    time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    days: list[int] = Field(min_length=1)
    timezone: str = "UTC"
    action: ScheduleAction
    enabled: bool = True

    @model_validator(mode="after")
    def _validate(self):
        if any(day < 0 or day > 6 for day in self.days):
            raise ValueError("days must be 0 (Mon) .. 6 (Sun)")
        if len(set(self.days)) != len(self.days):
            raise ValueError("days contains duplicates")
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone: {self.timezone}") from exc
        return self


class ScheduleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=48)
    time: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    days: list[int] | None = None
    timezone: str | None = None
    action: ScheduleAction | None = None
    enabled: bool | None = None

    @model_validator(mode="after")
    def _validate(self):
        if self.days is not None:
            if not self.days:
                raise ValueError("days cannot be empty")
            if any(day < 0 or day > 6 for day in self.days):
                raise ValueError("days must be 0 (Mon) .. 6 (Sun)")
        if self.timezone is not None:
            try:
                ZoneInfo(self.timezone)
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise ValueError(f"unknown timezone: {self.timezone}") from exc
        return self


class ScheduleOut(BaseModel):
    id: str
    device_id: str
    name: str
    time: str
    days: list[int]
    timezone: str
    action: ScheduleAction
    enabled: bool
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    created_at: datetime | None = None


class EventOut(BaseModel):
    id: str
    device_id: str
    kind: str
    ts: datetime | None = None
    detail: dict = Field(default_factory=dict)


def device_out(doc: dict, online: bool) -> DeviceOut:
    count = doc.get("channel_count") or 8
    state = doc.get("state") or [False] * count
    return DeviceOut(
        id=doc["id"],
        hardware_id=doc.get("hardware_id", ""),
        name=doc.get("name") or "Controller",
        room=doc.get("room") or "",
        online=online,
        last_seen=doc.get("last_seen"),
        firmware=doc.get("firmware"),
        local_ip=doc.get("local_ip"),
        local_port=doc.get("local_port") or 80,
        lan_key=doc.get("device_key"),
        channel_count=count,
        channels=doc.get("channels") or default_channels(count),
        state=state,
        telemetry=doc.get("telemetry") or {},
        created_at=doc.get("created_at"),
    )


def schedule_out(doc: dict) -> ScheduleOut:
    return ScheduleOut(
        id=doc["id"],
        device_id=doc["device_id"],
        name=doc.get("name") or "Schedule",
        time=doc["time"],
        days=doc.get("days") or [],
        timezone=doc.get("timezone") or "UTC",
        action=doc["action"],
        enabled=bool(doc.get("enabled", True)),
        next_run_at=doc.get("next_run_at"),
        last_run_at=doc.get("last_run_at"),
        created_at=doc.get("created_at"),
    )
