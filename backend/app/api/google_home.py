"""Google Home cloud-to-cloud fulfilment.

Google posts SYNC/QUERY/EXECUTE/DISCONNECT here with the access token from
/oauth/token. Every relay channel of every controller is exposed as its own
switch device, so "turn on the fan" maps to one channel of one controller.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import JSONResponse

from ..errors import ApiError
from ..services import assistants, local_fulfillment
from .deps import assistant_link, get_ctx

log = logging.getLogger(__name__)

router = APIRouter(tags=["google-home"])

_ONOFF = "action.devices.commands.OnOff"

_GOOGLE_TYPE = {
    "fan": "action.devices.types.FAN",
    "light": "action.devices.types.LIGHT",
    "outlet": "action.devices.types.OUTLET",
    "switch": "action.devices.types.SWITCH",
}

# Google's retry semantics hang off these codes: transientError is retried,
# hardError is surfaced to the user as a failure.
_ERROR_CODE = {
    "device_offline": "deviceOffline",
    "device_not_found": "deviceNotFound",
    "channel_out_of_range": "deviceNotFound",
    "rate_limited": "tooManyRequests",
    "no_ack": "transientError",
    "dispatch_failed": "transientError",
}


def _reply(request_id: str, payload: dict) -> JSONResponse:
    response = JSONResponse({"requestId": request_id, "payload": payload})
    response.headers["Cache-Control"] = "no-store"
    return response


def _parse_request(body: Any) -> tuple[str, str, dict]:
    if not isinstance(body, dict):
        raise ApiError(400, "bad_request", "body must be a JSON object")
    request_id = body.get("requestId")
    inputs = body.get("inputs")
    if not isinstance(request_id, str) or not request_id:
        raise ApiError(400, "bad_request", "requestId is required")
    if not isinstance(inputs, list) or not inputs or not isinstance(inputs[0], dict):
        raise ApiError(400, "bad_request", "inputs is required")
    intent = inputs[0].get("intent")
    if not isinstance(intent, str):
        raise ApiError(400, "bad_request", "intent is required")
    payload = inputs[0].get("payload")
    return request_id, intent, payload if isinstance(payload, dict) else {}


async def _sync(ctx, owner_id: str, background: BackgroundTasks) -> dict:
    entries = await assistants.list_assistant_devices(ctx, owner_id)
    docs: dict[str, dict] = {}
    if ctx.settings.local_fulfillment:
        docs = {doc["id"]: doc for doc in await ctx.repo.list_devices(owner_id)}
        for doc in docs.values():
            await local_fulfillment.ensure_key(ctx, doc)
            # The board can be offline when Google syncs; the key push waits
            # for its ack in the background and is retried on the next SYNC.
            background.add_task(local_fulfillment.provision, ctx, doc)

    devices = []
    for device in entries:
        name = device["name"][:60]
        entry = {
            "id": device["id"],
            "type": _GOOGLE_TYPE.get(device["type"], "action.devices.types.SWITCH"),
            "traits": ["action.devices.traits.OnOff"],
            "name": {"name": name, "defaultNames": [name], "nicknames": []},
            "willReportState": False,
            "deviceInfo": {"manufacturer": "FanCtl", "model": "fanctl-8"},
        }
        if device.get("room"):
            entry["roomHint"] = device["room"]
        if ctx.settings.local_fulfillment:
            doc = docs.get(device["device_id"])
            if doc is not None:
                # The local app matches the mDNS TXT id to this channel id,
                # then calls the board over HTTP with the assistant key.
                entry["otherDeviceIds"] = [{"deviceId": device["id"]}]
                entry["customData"] = {
                    "id": device["device_id"],
                    "channel": device["channel"],
                    "key": local_fulfillment.stored_key(doc),
                }
        devices.append(entry)
    return {"agentUserId": owner_id, "devices": devices}


async def _query(ctx, owner_id: str, payload: dict) -> dict:
    requested = [
        item["id"]
        for item in payload.get("devices") or []
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    ]
    readable = []
    for raw in requested:
        try:
            assistants.parse_assistant_id(raw)
        except ApiError:
            continue  # answered as deviceNotFound below
        readable.append(raw)

    states = await assistants.snapshot(ctx, owner_id, readable)
    devices: dict[str, dict] = {}
    for raw in requested:
        known = states.get(raw)
        if known is None:
            devices[raw] = {"status": "ERROR", "errorCode": "deviceNotFound"}
        else:
            devices[raw] = {"status": "SUCCESS", "on": known["on"], "online": known["online"]}
    return {"devices": devices}


def _execution_on(execution: Any) -> tuple[bool | None, bool]:
    on: bool | None = None
    for step in execution or []:
        if not isinstance(step, dict) or step.get("command") != _ONOFF:
            return None, False
        params = step.get("params")
        if not isinstance(params, dict) or not isinstance(params.get("on"), bool):
            return None, False
        on = params["on"]
    return on, on is not None


async def _execute(ctx, owner_id: str, payload: dict, background: BackgroundTasks) -> dict:
    commands = []
    for command in payload.get("commands") or []:
        if not isinstance(command, dict):
            continue
        ids = [
            item["id"]
            for item in command.get("devices") or []
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        ]
        if not ids:
            continue

        on, supported = _execution_on(command.get("execution"))
        if not supported:
            commands.append({"ids": ids, "status": "ERROR", "errorCode": "functionNotSupported"})
            continue

        try:
            targets = [assistants.parse_assistant_id(raw) for raw in ids]
            results = await assistants.execute_switches(
                ctx,
                owner_id,
                [{"device_id": device_id, "channel": channel, "on": on} for device_id, channel in targets],
                "google",
                background,
            )
        except ApiError as exc:
            commands.append(
                {"ids": ids, "status": "ERROR", "errorCode": _ERROR_CODE.get(exc.code, "hardError")}
            )
            continue

        failure = next((result for result in results if not result["acked"]), None)
        if failure is not None:
            commands.append(
                {
                    "ids": ids,
                    "status": "ERROR",
                    "errorCode": _ERROR_CODE.get(failure.get("error") or "", "hardError"),
                }
            )
            continue
        commands.append({"ids": ids, "status": "SUCCESS", "states": {"on": on, "online": True}})
    return {"commands": commands}


async def google_link(link: dict = Depends(assistant_link)) -> dict:
    if (link.get("provider") or "") != "google":
        raise ApiError(401, "invalid_token", "this token was not issued for Google Home")
    return link


@router.post("/google/fulfillment")
async def fulfillment(
    request: Request,
    background: BackgroundTasks,
    link: dict = Depends(google_link),
    ctx=Depends(get_ctx),
):
    try:
        body = await request.json()
    except Exception:
        raise ApiError(400, "bad_request", "body must be JSON")
    request_id, intent, payload = _parse_request(body)
    owner_id = link["owner_id"]

    if intent == "action.devices.SYNC":
        return _reply(request_id, await _sync(ctx, owner_id, background))
    if intent == "action.devices.QUERY":
        return _reply(request_id, await _query(ctx, owner_id, payload))
    if intent == "action.devices.EXECUTE":
        # Google retries a delivery with the same requestId; replay the stored
        # reply so a retried "turn on" never toggles anything twice.
        result = await assistants.run_once(
            ctx,
            owner_id,
            "google",
            request_id,
            lambda: _execute(ctx, owner_id, payload, background),
        )
        return _reply(request_id, result)
    if intent == "action.devices.DISCONNECT":
        await assistants.revoke_link(ctx, link)
        return _reply(request_id, {})
    raise ApiError(400, "bad_request", f"unsupported intent {intent}")
