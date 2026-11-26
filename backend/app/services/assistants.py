"""Shared voice-assistant layer.

Google Home and SmartThings each speak their own protocol; both are reduced to
this module: a linked owner, a flat list of switch devices (one per relay
channel) and a snapshot/execute pair that runs through the normal command path.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
from datetime import timedelta
from typing import TYPE_CHECKING, Awaitable, Callable, Iterable

from ..errors import ApiError
from ..models import CommandRequest, default_channels
from ..security import hash_secret, keys_equal
from ..timeutil import aware, utcnow
from .commands import dispatch_command
from .live import info_online

if TYPE_CHECKING:
    from fastapi import BackgroundTasks

    from ..context import AppContext

log = logging.getLogger(__name__)

CLIENT_PEPPER = "fanctl-oauth-client-v1"
TOKEN_PEPPER = "fanctl-assistant-token-v1"

# Rewriting the link row on every query would add a Firestore write to the
# voice path; a coarse "last used" is enough for the app's linked-accounts list.
TOUCH_INTERVAL_S = 3600

_ID_SEP = ".ch"

_KIND = {"fan": "fan", "socket": "outlet", "light": "light", "other": "switch"}


# tokens --------------------------------------------------------------------
def hash_token(raw: str) -> str:
    return hashlib.sha256(f"{TOKEN_PEPPER}:{raw}".encode()).hexdigest()


def new_token(prefix: str) -> str:
    return prefix + secrets.token_urlsafe(32)


def hash_client_secret(secret: str) -> str:
    return hash_secret(secret, pepper=CLIENT_PEPPER)


def new_client_id(prefix: str = "fc_") -> str:
    return prefix + secrets.token_hex(8)


async def verify_client(
    ctx: "AppContext", client_id: str, secret: str | None = None, *, require_secret: bool = False
) -> dict:
    client = await ctx.repo.get_oauth_client(client_id) if client_id else None
    if not client:
        raise ApiError(401, "invalid_client", "unknown OAuth client")
    stored = client.get("secret_hash")
    # Consent runs in the owner's browser, which never holds the client secret;
    # the token endpoint is the confidential channel and always checks it.
    if stored and (require_secret or secret is not None):
        if not keys_equal(stored, hash_client_secret(secret or "")):
            raise ApiError(401, "invalid_client", "client secret rejected")
    return client


async def issue_tokens(ctx: "AppContext", client: dict, owner_id: str) -> dict:
    access = new_token("at_")
    refresh = new_token("rt_")
    now = utcnow()
    link = {
        "id": "lnk_" + secrets.token_hex(8),
        "owner_id": owner_id,
        "provider": client.get("provider") or "assistant",
        "client_id": client["id"],
        "client_name": client.get("name") or client["id"],
        "access_hash": hash_token(access),
        "refresh_hash": hash_token(refresh),
        "access_expires_at": now + timedelta(seconds=ctx.settings.oauth_access_ttl_s),
        "refresh_expires_at": now + timedelta(seconds=ctx.settings.oauth_refresh_ttl_s),
        "created_at": now,
        "last_used_at": None,
        "revoked_at": None,
    }
    await ctx.repo.create_link(link)
    return {
        "link": link,
        "access_token": access,
        "refresh_token": refresh,
        "expires_in": int(ctx.settings.oauth_access_ttl_s),
    }


def _grant(access: str, ctx: "AppContext", *, refresh: str | None = None) -> dict:
    body = {
        "access_token": access,
        "token_type": "Bearer",
        "expires_in": int(ctx.settings.oauth_access_ttl_s),
    }
    if refresh:
        body["refresh_token"] = refresh
    return body


async def refresh_access_token(ctx: "AppContext", client: dict, raw_refresh: str) -> dict:
    link = await ctx.repo.get_link_by_hash("refresh_hash", hash_token(raw_refresh))
    if not link or link.get("revoked_at") or link.get("client_id") != client["id"]:
        raise ApiError(400, "invalid_grant", "refresh token is not valid")
    expires = aware(link.get("refresh_expires_at"))
    if expires and expires <= utcnow():
        raise ApiError(400, "invalid_grant", "refresh token expired")

    access = new_token("at_")
    await ctx.repo.update_link(
        link["id"],
        {
            "access_hash": hash_token(access),
            "access_expires_at": utcnow() + timedelta(seconds=ctx.settings.oauth_access_ttl_s),
        },
    )
    return _grant(access, ctx)


async def resolve_link(ctx: "AppContext", raw_token: str) -> dict:
    if not raw_token:
        raise ApiError(401, "missing_token", "assistant access token required")
    link = await ctx.repo.get_link_by_hash("access_hash", hash_token(raw_token))
    if not link or link.get("revoked_at"):
        raise ApiError(401, "invalid_token", "assistant link is not valid")
    expires = aware(link.get("access_expires_at"))
    if expires and expires <= utcnow():
        raise ApiError(401, "expired_token", "assistant access token expired")

    last = aware(link.get("last_used_at"))
    if not last or (utcnow() - last).total_seconds() > TOUCH_INTERVAL_S:
        try:
            await ctx.repo.update_link(link["id"], {"last_used_at": utcnow()})
        except Exception:  # tracking only, never fail a voice command on it
            log.warning("could not record assistant link use", exc_info=True)
    return link


async def revoke_link(ctx: "AppContext", link: dict) -> None:
    await ctx.repo.update_link(link["id"], {"revoked_at": utcnow()})


async def revoke_owner_links(ctx: "AppContext", owner_id: str, provider: str | None = None) -> int:
    removed = 0
    for link in await ctx.repo.list_links(owner_id):
        if provider and link.get("provider") != provider:
            continue
        await ctx.repo.delete_link(link["id"])
        removed += 1
    return removed


async def list_owner_links(ctx: "AppContext", owner_id: str) -> list[dict]:
    rows = await ctx.repo.list_links(owner_id)
    return [
        {
            "id": link["id"],
            "provider": link.get("provider") or "assistant",
            "client_name": link.get("client_name") or link.get("client_id") or "Assistant",
            "created_at": link.get("created_at"),
            "last_used_at": link.get("last_used_at"),
        }
        for link in rows
        if not link.get("revoked_at")
    ]


async def ensure_client(
    ctx: "AppContext",
    *,
    client_id: str,
    provider: str,
    name: str,
    secret: str,
    redirect_uris: list[str],
) -> dict:
    existing = await ctx.repo.get_oauth_client(client_id)
    if existing:
        return existing
    return await ctx.repo.create_oauth_client(
        {
            "id": client_id,
            "provider": provider,
            "name": name,
            "secret_hash": hash_client_secret(secret),
            "redirect_uris": list(redirect_uris),
            "created_at": utcnow(),
        }
    )


# devices -------------------------------------------------------------------
def assistant_device_id(device_id: str, channel: int) -> str:
    return f"{device_id}{_ID_SEP}{channel}"


def parse_assistant_id(value: str) -> tuple[str, int]:
    device_id, sep, tail = (value or "").partition(_ID_SEP)
    if not sep or not tail.isdigit() or not device_id:
        raise ApiError(404, "device_not_found", "unknown assistant device id")
    return device_id, int(tail)


def _channel_name(channel: dict | None, index: int) -> tuple[str, str]:
    if not isinstance(channel, dict):
        return f"Channel {index + 1}", "other"
    return channel.get("name") or f"Channel {index + 1}", channel.get("type") or "other"


async def list_assistant_devices(ctx: "AppContext", owner_id: str) -> list[dict]:
    """One entry per relay channel. Names are prefixed with the controller name
    when the owner has more than one, so "turn on Fan" stays unambiguous."""
    devices = await ctx.repo.list_devices(owner_id)
    prefix = len(devices) > 1
    out: list[dict] = []
    for doc in devices:
        count = doc.get("channel_count") or 8
        channels = doc.get("channels") or default_channels(count)
        by_index = {c.get("index", i): c for i, c in enumerate(channels) if isinstance(c, dict)}
        controller = doc.get("name") or "Controller"
        for index in range(count):
            name, kind = _channel_name(by_index.get(index), index)
            out.append(
                {
                    "id": assistant_device_id(doc["id"], index),
                    "name": f"{controller} {name}" if prefix else name,
                    "room": doc.get("room") or "",
                    "type": _KIND.get(kind, "switch"),
                    "device_id": doc["id"],
                    "channel": index,
                    "controller": controller,
                }
            )
    return out


async def snapshot(
    ctx: "AppContext", owner_id: str, ids: Iterable[str] | None = None
) -> dict[str, dict]:
    """Current on/off and reachability for every channel. One RTDB read per
    controller, in parallel; a failed read falls back to the Firestore mirror."""
    devices = await ctx.repo.list_devices(owner_id)
    if not devices:
        return {}
    nodes = await asyncio.gather(
        *(ctx.rtdb.get(f"devices/{doc['id']}") for doc in devices),
        return_exceptions=True,
    )
    wanted = set(ids) if ids is not None else None
    out: dict[str, dict] = {}
    for doc, node in zip(devices, nodes):
        node = node if isinstance(node, dict) else {}
        count = doc.get("channel_count") or 8
        state = node.get("state")
        if not isinstance(state, list) or len(state) != count:
            state = doc.get("state") or [False] * count
        info = node.get("info") if isinstance(node.get("info"), dict) else None
        online = info_online(info, ctx.settings.device_online_s)
        for index in range(count):
            key = assistant_device_id(doc["id"], index)
            if wanted is not None and key not in wanted:
                continue
            out[key] = {"on": bool(state[index]), "online": online}
    return out


async def execute_switches(
    ctx: "AppContext",
    owner_id: str,
    commands: list[dict],
    source: str,
    background: "BackgroundTasks | None" = None,
) -> list[dict]:
    """commands: [{"device_id", "channel", "on"}] -> per-command results.

    Every target is validated before anything is dispatched so a bad id cannot
    leave half of a multi-device request applied."""
    devices: dict[str, dict] = {}
    for item in commands:
        device_id = item.get("device_id") or ""
        doc = devices.get(device_id)
        if doc is None:
            doc = await ctx.repo.get_device(device_id)
            if not doc or doc.get("owner_id") != owner_id:
                raise ApiError(404, "device_not_found", "unknown controller")
            devices[device_id] = doc
        count = doc.get("channel_count") or 8
        channel = int(item.get("channel", -1))
        if not 0 <= channel < count:
            raise ApiError(422, "channel_out_of_range", f"channel must be 0..{count - 1}")

    results: list[dict] = []
    for item in commands:
        doc = devices[item["device_id"]]
        channel = int(item["channel"])
        on = bool(item.get("on"))
        response = await dispatch_command(
            ctx,
            doc,
            CommandRequest(action="set", channel=channel, on=on),
            source=source,
            background=background,
        )
        results.append(
            {
                "device_id": doc["id"],
                "channel": channel,
                "on": on,
                "acked": response.acked,
                "online": response.online,
                "error": response.error,
                "state": response.state,
            }
        )
    return results


# idempotency ---------------------------------------------------------------
def command_key(owner_id: str, request_id: str) -> str:
    return hashlib.sha256(f"{owner_id}:{request_id}".encode()).hexdigest()


async def run_once(
    ctx: "AppContext",
    owner_id: str,
    provider: str,
    request_id: str | None,
    produce: Callable[[], Awaitable[dict]],
) -> dict:
    """Both platforms retry deliveries with the same requestId; replaying the
    stored result keeps a retried "turn on" from toggling anything twice."""
    if not request_id:
        return await produce()
    key = command_key(owner_id, request_id)
    cached = await ctx.repo.get_assistant_command(key)
    if cached and cached.get("owner_id") == owner_id:
        return cached.get("result") or {}
    result = await produce()
    await ctx.repo.save_assistant_command(
        {
            "id": key,
            "owner_id": owner_id,
            "provider": provider,
            "request_id": request_id,
            "created_at": utcnow(),
            "result": result,
        }
    )
    return result
