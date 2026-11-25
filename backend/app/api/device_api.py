"""Unauthenticated endpoints the controller itself uses before it holds any
Firebase credentials: announce a pairing code, then collect its pair claim and
a custom token. Everything after that goes straight to RTDB."""

import os
from datetime import timedelta
from urllib.parse import urlparse

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..errors import ApiError
from ..models import default_channels
from ..rtdb import default_namespace
from ..security import (
    hash_pair_code,
    hash_secret,
    keys_equal,
    new_device_id,
    normalize_pair_code,
)
from ..timeutil import utcnow
from .deps import get_ctx

router = APIRouter(prefix="/device", tags=["device"])

MAX_CHANNELS = 32


class RegisterRequest(BaseModel):
    hw: str = Field(min_length=6, max_length=32)
    code: str = Field(min_length=4, max_length=16)
    secret: str = Field(min_length=16, max_length=128)
    fw: str | None = Field(default=None, max_length=32)
    model: str | None = Field(default=None, max_length=32)
    channels: int | None = Field(default=None, ge=1, le=MAX_CHANNELS)


class TokenRequest(BaseModel):
    hw: str | None = Field(default=None, max_length=32)
    secret: str | None = Field(default=None, max_length=128)
    # Legacy path for a device that was paired before secrets existed.
    device_id: str | None = Field(default=None, max_length=64)
    device_key: str | None = Field(default=None, max_length=128)


def _hardware(value: str | None) -> str:
    hw = (value or "").strip().upper()
    if len(hw) != 12:
        raise ApiError(400, "bad_hardware_id", "hardware id must be 12 hexadecimal characters")
    return hw


def _split_host(value: str, default_port: int) -> tuple[str, int]:
    host, _, port = value.rpartition(":")
    if not host:
        host, port = value, str(default_port)
    return host, int(port)


def connection_info(settings) -> dict:
    """Where the controller should open its RTDB stream and Auth session.

    Advertised with the token so the firmware does not have to be rebuilt when
    the database moves between the emulator and production."""
    emulator = settings.database_emulator_host or os.environ.get(
        "FIREBASE_DATABASE_EMULATOR_HOST"
    )
    if emulator:
        host, port = _split_host(emulator, 9000)
        rtdb = {
            "host": host,
            "port": port,
            "tls": False,
            "ns": default_namespace(settings),
        }
    elif settings.database_url:
        parsed = urlparse(settings.database_url)
        tls = parsed.scheme == "https"
        rtdb = {
            "host": parsed.hostname,
            "port": parsed.port or (443 if tls else 80),
            "tls": tls,
            "ns": settings.rtdb_namespace,
        }
    else:
        rtdb = None

    auth_emulator = settings.auth_emulator_host or os.environ.get(
        "FIREBASE_AUTH_EMULATOR_HOST"
    )
    if auth_emulator:
        host, port = _split_host(auth_emulator, 9099)
        auth = {
            "host": host,
            "port": port,
            "tls": False,
            "api_key": settings.firebase_api_key or "fake-api-key",
        }
    else:
        auth = {
            "host": "identitytoolkit.googleapis.com",
            "port": 443,
            "tls": True,
            "api_key": settings.firebase_api_key,
        }
    return {"rtdb": rtdb, "auth": auth}


@router.post("/register")
async def register(req: RegisterRequest, ctx=Depends(get_ctx)):
    """Upsert the unclaimed controller and refresh its pairing window."""
    hw = _hardware(req.hw)
    code = normalize_pair_code(req.code)
    if not code:
        raise ApiError(400, "bad_register", "pairing code is empty")

    doc = await ctx.repo.get_device_by_hardware(hw)
    if doc and doc.get("device_key"):
        raise ApiError(409, "already_claimed", "this controller is already paired")

    channels = req.channels or (doc or {}).get("channel_count") or 8
    channels = max(1, min(MAX_CHANNELS, int(channels)))

    if not doc:
        doc = await ctx.repo.create_device(
            {
                "id": new_device_id(),
                "hardware_id": hw,
                "owner_id": None,
                "name": "New controller",
                "room": "",
                "device_key": None,
                "pair_code_hash": None,
                "pair_code_expires_at": None,
                "secret_hash": None,
                "online": False,
                "last_seen": None,
                "firmware": None,
                "local_ip": None,
                "local_port": 80,
                "channel_count": channels,
                "channels": default_channels(channels),
                "state": [False] * channels,
                "telemetry": {},
                "created_at": utcnow(),
            }
        )

    patch: dict = {
        "pair_code_hash": hash_pair_code(code),
        "pair_code_expires_at": utcnow() + timedelta(seconds=ctx.settings.pair_code_ttl_s),
        "secret_hash": hash_secret(req.secret),
        "firmware": req.fw or doc.get("firmware"),
        "channel_count": channels,
        "last_seen": utcnow(),
    }
    if channels != (doc.get("channel_count") or 8):
        patch["channels"] = default_channels(channels)
        patch["state"] = [False] * channels
    await ctx.repo.update_device(doc["id"], patch)
    return {"status": "waiting"}


@router.post("/token")
async def token(req: TokenRequest, ctx=Depends(get_ctx)):
    """Hands the controller its device id, LAN key and RTDB custom token.

    A device proves itself with the random secret it sent at registration (or,
    for controllers paired before secrets existed, with its device key)."""
    doc = None
    if req.device_id and req.device_key:
        doc = await ctx.repo.get_device(req.device_id)
        if not doc or not keys_equal(doc.get("device_key"), req.device_key):
            raise ApiError(401, "bad_key", "device id/key pair rejected")
        if req.hw and doc.get("hardware_id") != _hardware(req.hw):
            raise ApiError(401, "bad_key", "device id does not belong to this hardware id")
        if req.secret:
            await ctx.repo.update_device(doc["id"], {"secret_hash": hash_secret(req.secret)})
    elif req.hw and req.secret:
        doc = await ctx.repo.get_device_by_hardware(_hardware(req.hw))
        if not doc or not keys_equal(doc.get("secret_hash"), hash_secret(req.secret)):
            raise ApiError(401, "bad_secret", "register this controller again")
    else:
        raise ApiError(400, "bad_request", "send hw+secret or device_id+device_key")

    if not doc.get("owner_id") or not doc.get("device_key"):
        raise ApiError(409, "not_claimed", "waiting for the owner to claim this controller")

    await ctx.repo.update_device(doc["id"], {"last_seen": utcnow()})

    # Create the RTDB node if it is missing (fresh database, or a device that
    # paired while the backend still used sockets).
    node = await ctx.rtdb.get(f"devices/{doc['id']}")
    if not isinstance(node, dict) or not node.get("owner"):
        await ctx.rtdb.patch(
            f"devices/{doc['id']}",
            {
                "owner": doc["owner_id"],
                "name": doc.get("name") or "Controller",
                "room": doc.get("room") or "",
                "channels": doc.get("channels") or default_channels(doc.get("channel_count") or 8),
                "channel_count": doc.get("channel_count") or 8,
            },
        )

    return {
        "device_id": doc["id"],
        "device_key": doc["device_key"],
        "custom_token": ctx.mint_token(doc["id"]),
        **connection_info(ctx.settings),
    }
