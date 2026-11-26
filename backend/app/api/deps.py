from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..context import AppContext
from ..errors import ApiError, forbidden, not_found
from ..security import User
from ..services.assistants import resolve_link

bearer = HTTPBearer(auto_error=False)


def get_ctx(request: Request) -> AppContext:
    return request.app.state.ctx


async def current_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> User:
    if creds is None or not creds.credentials:
        raise ApiError(401, "missing_token", "Authorization: Bearer <firebase-id-token> required")
    ctx: AppContext = request.app.state.ctx
    return ctx.verify_token(creds.credentials)


async def assistant_link(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> dict:
    """Resolves the opaque access token a voice platform sends on fulfilment
    calls into the link row (owner_id, provider, client)."""
    if creds is None or not creds.credentials:
        raise ApiError(401, "missing_token", "assistant access token required")
    ctx: AppContext = request.app.state.ctx
    return await resolve_link(ctx, creds.credentials)


async def owned_device(
    device_id: str,
    request: Request,
    user: User = Depends(current_user),
) -> dict:
    ctx: AppContext = request.app.state.ctx
    doc = await ctx.repo.get_device(device_id)
    if not doc:
        raise not_found("device")
    if doc.get("owner_id") != user.uid:
        raise forbidden()
    return doc
