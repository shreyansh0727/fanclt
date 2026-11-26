"""SmartThings Schema (st-schema) endpoint for Bixby and the SmartThings app.

SmartThings discovers one switch per relay channel, polls state with
stateRefreshRequest and drives relays with commandRequest. The access token
arrives in the body (`authentication.token`), not the Authorization header.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response
from fastapi.responses import JSONResponse

from ..errors import ApiError
from ..services import assistants
from .deps import get_ctx

log = logging.getLogger(__name__)

router = APIRouter(tags=["smartthings"])

_SWITCH = "st.switch"
_HEALTH = "st.healthCheck"

# ST Schema error enums use hyphens, and the partner is told to stop retrying by
# answering with a deviceError instead of an HTTP failure.
_ERROR_ENUM = {
    "device_offline": "DEVICE-OFFLINE",
    "device_not_found": "DEVICE-DELETED",
    "channel_out_of_range": "DEVICE-DELETED",
    "rate_limited": "RESOURCE-CONSTRAINT-VIOLATION",
    "no_ack": "DEVICE-UNAVAILABLE",
    "dispatch_failed": "DEVICE-UNAVAILABLE",
}


def _headers(request_id: str, interaction: str) -> dict:
    return {
        "schema": "st-schema",
        "version": "1.0",
        "interactionType": interaction,
        "requestId": request_id,
    }


def _reply(request_id: str, interaction: str, body: dict | None = None) -> JSONResponse:
    content = {"headers": _headers(request_id, interaction), **(body or {})}
    response = JSONResponse(content)
    response.headers["Cache-Control"] = "no-store"
    return response


def _token(request: Request, body: dict) -> str:
    auth = body.get("authentication")
    if isinstance(auth, dict) and isinstance(auth.get("token"), str):
        return auth["token"]
    header = request.headers.get("authorization") or ""
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return ""


async def _link(request: Request, ctx) -> dict:
    body = await _body(request)
    token = _token(request, body)
    if not token:
        raise ApiError(401, "missing_token", "assistant access token required")
    link = await assistants.resolve_link(ctx, token)
    if (link.get("provider") or "") != "smartthings":
        raise ApiError(401, "invalid_token", "this token was not issued for SmartThings")
    return link


async def _body(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        raise ApiError(400, "bad_request", "body must be JSON")
    if not isinstance(body, dict):
        raise ApiError(400, "bad_request", "body must be a JSON object")
    return body


def _envelope(body: dict) -> tuple[str, str]:
    headers = body.get("headers")
    if not isinstance(headers, dict):
        raise ApiError(400, "bad_request", "headers are required")
    request_id = headers.get("requestId")
    interaction = headers.get("interactionType")
    if not isinstance(request_id, str) or not request_id:
        raise ApiError(400, "bad_request", "headers.requestId is required")
    if not isinstance(interaction, str) or not interaction:
        raise ApiError(400, "bad_request", "headers.interactionType is required")
    return request_id, interaction


def _external_ids(body: dict) -> list[str]:
    devices = body.get("devices")
    if not isinstance(devices, list):
        return []
    return [
        item["externalDeviceId"]
        for item in devices
        if isinstance(item, dict) and isinstance(item.get("externalDeviceId"), str)
    ]


def _switch_state(on: bool, online: bool = True) -> list[dict]:
    return [
        {"component": "main", "capability": _SWITCH, "attribute": "switch", "value": "on" if on else "off"},
        {"component": "main", "capability": _HEALTH, "attribute": "healthStatus", "value": "online" if online else "offline"},
    ]


def _error_state(external_id: str, enum: str, detail: str) -> dict:
    return {
        "externalDeviceId": external_id,
        "deviceError": [{"errorEnum": enum, "detail": detail}],
    }


async def _discovery(ctx, owner_id: str) -> list[dict]:
    devices = []
    for device in await assistants.list_assistant_devices(ctx, owner_id):
        entry = {
            "externalDeviceId": device["id"],
            "friendlyName": device["name"][:60],
            "manufacturerInfo": {"manufacturerName": "FanCtl", "modelName": "fanctl-8"},
            # c2c-switch is SmartThings' stock on/off handler (Switch + Health Check).
            "deviceHandlerType": "c2c-switch",
        }
        if device.get("room"):
            entry["deviceContext"] = {"roomName": device["room"]}
        devices.append(entry)
    return devices


async def _state_refresh(ctx, owner_id: str, body: dict) -> list[dict]:
    requested = _external_ids(body)
    states: list[dict] = []
    known = await assistants.snapshot(
        ctx,
        owner_id,
        [raw for raw in requested if _parses(raw)],
    )
    for raw in requested:
        current = known.get(raw)
        if current is None:
            states.append(_error_state(raw, "DEVICE-DELETED", "no such channel in this account"))
        else:
            states.append(
                {"externalDeviceId": raw, "states": _switch_state(current["on"], current["online"])}
            )
    return states


def _parses(raw: str) -> bool:
    try:
        assistants.parse_assistant_id(raw)
    except ApiError:
        return False
    return True


def _wanted_on(commands) -> tuple[bool | None, bool]:
    """Only st.switch on/off on the main component is supported."""
    on: bool | None = None
    if not isinstance(commands, list) or not commands:
        return None, False
    for command in commands:
        if not isinstance(command, dict):
            return None, False
        if command.get("component", "main") != "main":
            return None, False
        if command.get("capability") != _SWITCH or command.get("command") not in ("on", "off"):
            return None, False
        on = command["command"] == "on"
    return on, on is not None


async def _command(ctx, owner_id: str, body: dict, background: BackgroundTasks) -> list[dict]:
    devices = body.get("devices")
    if not isinstance(devices, list):
        return []

    states: list[dict] = []
    for item in devices:
        if not isinstance(item, dict):
            continue
        external_id = item.get("externalDeviceId")
        if not isinstance(external_id, str) or not external_id:
            continue

        on, supported = _wanted_on(item.get("commands"))
        if not supported:
            states.append(
                _error_state(external_id, "CAPABILITY-NOT-SUPPORTED", "only the switch on/off commands are supported")
            )
            continue

        try:
            device_id, channel = assistants.parse_assistant_id(external_id)
            results = await assistants.execute_switches(
                ctx,
                owner_id,
                [{"device_id": device_id, "channel": channel, "on": on}],
                "smartthings",
                background,
            )
        except ApiError as exc:
            states.append(
                _error_state(external_id, _ERROR_ENUM.get(exc.code, "DEVICE-UNAVAILABLE"), exc.message)
            )
            continue

        result = results[0]
        if not result["acked"]:
            enum = _ERROR_ENUM.get(result.get("error") or "", "DEVICE-UNAVAILABLE")
            states.append(_error_state(external_id, enum, result.get("error") or "command failed"))
            continue
        states.append({"externalDeviceId": external_id, "states": _switch_state(on)})
    return states


async def smartthings_link(request: Request, ctx=Depends(get_ctx)) -> dict:
    return await _link(request, ctx)


@router.post("/smartthings")
async def schema(
    request: Request,
    background: BackgroundTasks,
    link: dict = Depends(smartthings_link),
    ctx=Depends(get_ctx),
):
    body = await _body(request)
    request_id, interaction = _envelope(body)
    owner_id = link["owner_id"]

    if interaction == "discoveryRequest":
        return _reply(
            request_id, "discoveryResponse", {"devices": await _discovery(ctx, owner_id)}
        )

    if interaction == "stateRefreshRequest":
        return _reply(
            request_id,
            "stateRefreshResponse",
            {"deviceState": await _state_refresh(ctx, owner_id, body)},
        )

    if interaction == "commandRequest":
        # SmartThings retries deliveries with the same requestId; replaying the
        # stored reply keeps a retried "on" from being applied twice.
        device_state = await assistants.run_once(
            ctx,
            owner_id,
            "smartthings",
            request_id,
            lambda: _command(ctx, owner_id, body, background),
        )
        return _reply(request_id, "commandResponse", {"deviceState": device_state})

    if interaction == "grantCallbackAccess":
        # State callbacks are not used yet; the flag on discovery is left off so
        # SmartThings polls with stateRefreshRequest instead.
        log.info("SmartThings granted callback access for %s", owner_id)
        return JSONResponse({})

    if interaction == "integrationDeleted":
        await assistants.revoke_link(ctx, link)
        return Response(status_code=204)

    raise ApiError(400, "bad_request", f"unsupported interactionType {interaction}")
