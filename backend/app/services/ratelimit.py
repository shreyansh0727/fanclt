import time


class RateLimiter:
    """Sliding one-second window, in-process. Enough to stop a runaway client
    from hammering a relay without adding a Redis dependency."""

    def __init__(self, per_second: float) -> None:
        self.per_second = per_second
        self._hits: dict[str, list[float]] = {}

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        window = self._hits.setdefault(key, [])
        cutoff = now - 1.0
        while window and window[0] < cutoff:
            window.pop(0)
        if len(window) >= self.per_second:
            return False
        window.append(now)
        return True

    def reset(self, key: str) -> None:
        self._hits.pop(key, None)
