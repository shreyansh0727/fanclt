"""Google local fulfillment provisioning.

The local app runs inside the Google Home runtime and reaches the controller
over the LAN, so it needs a credential for the board's HTTP API. That
credential is minted here, kept on the controller document and pushed to the
board over the authenticated database channel. The owner's device key and the
app's LAN key are never part of the assistant path.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import uuid

from ..timeutil import utcnow
from .commands import ACK_POLL_S, now_ms

log = logging.getLogger(__name__)

KEY_PREFIX = "lk_"
ACK_TIMEOUT_S = 3.0


def stored_key(doc: dict) -> str:
    key = doc.get("local_key")
    return key if isinstance(key, str) and len(key) >= 16 else ""


async def ensure_key(ctx, doc: dict) -> str:
    """Mint the assistant key once per controller; SYNC customData is built
    from this value."""
    key = stored_key(doc)
    if key:
        return key
    key = KEY_PREFIX + secrets.token_urlsafe(24)
    await ctx.repo.update_device(doc["id"], {"local_key": key})
    doc["local_key"] = key
    return key


async def provision(ctx, doc: dict) -> bool:
    """Push the key to the board and wait for its ack.

    A command written while the controller is offline is discarded by its
    first-snapshot rule, so the key only counts as delivered after the ack;
    otherwise the next SYNC tries again."""
    key = await ensure_key(ctx, doc)
    if doc.get("local_key_pushed") == key:
        return True

    device_id = doc["id"]
    command_id = uuid.uuid4().hex
    try:
        await ctx.rtdb.put(
            f"devices/{device_id}/commands/{command_id}",
            {"action": "set_assistant_key", "key": key, "ts": now_ms()},
        )

        ack = None
        loop = asyncio.get_running_loop()
        deadline = loop.time() + ACK_TIMEOUT_S
        while loop.time() < deadline:
            ack = await ctx.rtdb.get(f"devices/{device_id}/acks/{command_id}")
            if isinstance(ack, dict):
                break
            await asyncio.sleep(ACK_POLL_S)
    except Exception:
        log.warning("could not provision assistant key for %s", device_id, exc_info=True)
        return False
    finally:
        for path in (
            f"devices/{device_id}/commands/{command_id}",
            f"devices/{device_id}/acks/{command_id}",
        ):
            try:
                await ctx.rtdb.delete(path)
            except Exception:  # pruning only, command ids are unique
                log.debug("could not prune %s", path, exc_info=True)

    ok = bool(isinstance(ack, dict) and ack.get("ok"))
    if not ok:
        log.info("controller %s did not ack the assistant key", device_id)
        return False

    await ctx.repo.update_device(
        device_id, {"local_key_pushed": key, "local_key_pushed_at": utcnow()}
    )
    doc["local_key_pushed"] = key
    return True
