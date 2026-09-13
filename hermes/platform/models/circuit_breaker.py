import time
from typing import Dict, Any, Optional

class CircuitBreaker:
    """Per-(model, provider) circuit breaker.

    HAOS v1.1: a provider may be unhealthy for ONE model while healthy for
    another, so breaker state must be keyed by the pair, not by provider alone.
    The key is an opaque string; build it with :meth:`route_key` from the
    canonical ModelIdentity so the same provider serves separate buckets per
    model family/variant/revision.
    """

    def __init__(self, failure_threshold: int = 3, cooldown_seconds: float = 60.0):
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._failures: Dict[str, int] = {}
        self._open_until: Dict[str, float] = {}

    @staticmethod
    def route_key(provider_id: str, model_identity: Optional[Any] = None) -> str:
        if model_identity is None:
            return provider_id
        identity = getattr(model_identity, "family", "?") + ":" + getattr(model_identity, "variant", "?")
        revision = getattr(model_identity, "revision", "latest")
        return f"{identity}:{revision}:{provider_id}"

    def is_open(self, key: str) -> bool:
        now = time.time()
        if key in self._open_until:
            if now < self._open_until[key]:
                return True
            else:
                del self._open_until[key]
                self._failures[key] = 0
        return False

    def record_failure(self, key: str):
        count = self._failures.get(key, 0) + 1
        self._failures[key] = count
        if count >= self.failure_threshold:
            self._open_until[key] = time.time() + self.cooldown_seconds

    def record_success(self, key: str):
        self._failures[key] = 0
        if key in self._open_until:
            del self._open_until[key]
