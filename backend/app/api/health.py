from fastapi import APIRouter, Request

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(request: Request):
    ctx = request.app.state.ctx
    return {
        "ok": True,
        "version": request.app.version,
        "env": ctx.settings.env,
        "repo": ctx.settings.repo_backend,
    }
