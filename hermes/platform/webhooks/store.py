"""Storage for Webhook metadata."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

from hermes_constants import get_hermes_home
from hermes.platform.webhooks.models import Webhook


class WebhookStore:
    def __init__(self, file_path: Optional[Path] = None):
        if file_path is None:
            self.file_path = Path(get_hermes_home()) / "webhooks.json"
        else:
            self.file_path = Path(file_path)
        self._cache: Dict[str, Webhook] = {}
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        if self.file_path.exists():
            try:
                data = json.loads(self.file_path.read_text(encoding="utf-8"))
                for item in data.get("webhooks", []):
                    wh = Webhook.from_dict(item)
                    self._cache[wh.id] = wh
            except Exception:
                self._cache = {}
        self._loaded = True

    def _persist(self) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "webhooks": [wh.to_dict() for wh in self._cache.values()]
        }
        self.file_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def save(self, webhook: Webhook) -> None:
        self._ensure_loaded()
        self._cache[webhook.id] = webhook
        self._persist()

    def get(self, webhook_id: str) -> Optional[Webhook]:
        self._ensure_loaded()
        return self._cache.get(webhook_id)

    def list_all(self) -> List[Webhook]:
        self._ensure_loaded()
        return list(self._cache.values())

    def revoke(self, webhook_id: str) -> bool:
        self._ensure_loaded()
        wh = self._cache.get(webhook_id)
        if wh:
            wh.active = False
            self._persist()
            return True
        return False

    def delete(self, webhook_id: str) -> bool:
        self._ensure_loaded()
        if webhook_id in self._cache:
            del self._cache[webhook_id]
            self._persist()
            return True
        return False


_GLOBAL_STORE: Optional[WebhookStore] = None


def get_webhook_store() -> WebhookStore:
    global _GLOBAL_STORE
    if _GLOBAL_STORE is None:
        _GLOBAL_STORE = WebhookStore()
    return _GLOBAL_STORE
