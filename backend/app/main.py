import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api import device_api, devices, health, schedules, updates
from .config import Settings, get_settings
from .context import AppContext
from .errors import ApiError
from .firebase import configure_logging
from .repo.factory import build_repo
from .rtdb import build_rtdb
from .security import mint_device_token, verify_firebase_token
from .services.ratelimit import RateLimiter
from .services.runner import ScheduleRunner

log = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging(settings)
        emulator = bool(
            os.environ.get("FIREBASE_AUTH_EMULATOR_HOST") or settings.database_emulator_host
        )
        project_id = settings.project_id or "demo-fanctl"
        ctx = AppContext(
            settings=settings,
            repo=build_repo(settings),
            rtdb=build_rtdb(settings),
            limiter=RateLimiter(settings.command_rate_per_sec),
            verify_token=verify_firebase_token,
            mint_token=lambda device_id: mint_device_token(project_id, device_id, emulator),
        )
        app.state.ctx = ctx
        if settings.scheduler_enabled:
            ctx.runner = ScheduleRunner(ctx)
            ctx.runner.start()
            log.info("scheduler started (tick %.0fs)", settings.scheduler_tick_s)

        yield

        if ctx.runner:
            await ctx.runner.stop()
        await ctx.rtdb.aclose()

    app = FastAPI(title="FanCtl API", version="1.0.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list or ["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError):
        body = {"error": {"code": exc.code, "message": exc.message}}
        if exc.details is not None:
            body["error"]["details"] = jsonable_encoder(exc.details)
        return JSONResponse(status_code=exc.status_code, content=body)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        errors = [
            {key: value for key, value in err.items() if key in ("type", "loc", "msg", "input")}
            for err in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "request body or query is invalid",
                    "details": jsonable_encoder(errors),
                }
            },
        )

    app.include_router(health.router, prefix="/api/v1")
    app.include_router(devices.router, prefix="/api/v1")
    app.include_router(schedules.router, prefix="/api/v1")
    app.include_router(device_api.router, prefix="/api/v1")
    app.include_router(updates.router, prefix="/api/v1")
    return app


app = create_app()
