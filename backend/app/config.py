from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FANCTL_", env_file=".env", extra="ignore")

    env: str = "dev"
    repo_backend: str = "firestore"  # firestore | memory

    project_id: str | None = None
    credentials_file: str | None = None
    # Web API key the controllers use for signInWithCustomToken (public data).
    firebase_api_key: str | None = None
    auth_emulator_host: str | None = None

    # Realtime Database. Emulator host wins when set (auto in dev via
    # FIREBASE_DATABASE_EMULATOR_HOST); otherwise database_url is required.
    rtdb_backend: str = "auto"  # auto | rest | memory
    database_url: str | None = None
    database_emulator_host: str | None = None
    rtdb_namespace: str | None = None
    # The firmware writes info.ts every 10 s, so 40 s tolerates a reconnect
    # cycle without calling a live controller offline.
    device_online_s: float = 40.0
    claim_presence_s: float = 120.0  # a controller must have polled this recently

    cors_origins: str = "*"

    command_timeout_s: float = 6.0
    command_rate_per_sec: float = 6.0
    pair_code_ttl_s: int = 86400
    log_events: bool = True

    scheduler_enabled: bool = True
    scheduler_tick_s: float = 20.0

    # Voice assistants (Google Home, SmartThings). Tokens are opaque and only
    # their hashes are stored; OAuth clients are seeded out of band with
    # scripts/register_oauth_client.py.
    oauth_code_ttl_s: int = 300
    oauth_access_ttl_s: int = 3600
    oauth_refresh_ttl_s: int = 365 * 86400

    # Google local fulfillment. Off until the local app is registered in the
    # Home console: SYNC without a matching bundle only confuses the platform.
    local_fulfillment: bool = False

    log_level: str = "INFO"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
