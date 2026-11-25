from dataclasses import dataclass
from typing import Callable

from .config import Settings
from .repo.base import Repo
from .rtdb import Rtdb
from .security import User
from .services.ratelimit import RateLimiter
from .services.runner import ScheduleRunner


@dataclass
class AppContext:
    settings: Settings
    repo: Repo
    rtdb: Rtdb
    limiter: RateLimiter
    verify_token: Callable[[str], User]
    mint_token: Callable[[str], str]
    runner: ScheduleRunner | None = None
