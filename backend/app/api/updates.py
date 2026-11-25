from fastapi import APIRouter, Depends, Response

from .deps import get_ctx

router = APIRouter(prefix="/updates", tags=["updates"])


@router.get("")
async def latest_release(response: Response, ctx=Depends(get_ctx)):
    """Public release manifest polled by controllers and the app.

    The document is written by scripts/publish_release.py; artifacts live in
    Firebase Storage, so this endpoint only forwards versions and URLs. Keep it
    uncached - a fresh publish has to reach devices on their next check.
    """
    release = await ctx.repo.get_release() or {}
    response.headers["Cache-Control"] = "no-store"
    return {
        "firmware": release.get("firmware"),
        "app": release.get("app"),
    }
