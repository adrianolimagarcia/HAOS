"""IdentityResolver — Safe, deterministic resolution of Bot identity assets.

Implements path safety, threat scanning, hash computation, caching,
legacy fallback, and drift detection according to haos-civ/README.md.
"""

from __future__ import annotations

import difflib
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .identity import (
    BotIdentityBundle,
    BotIdentitySpec,
    IdentityVersion,
    compute_bundle_hash,
    compute_sha256,
)
from .spec import BotSpec

logger = logging.getLogger("hermes.platform.bots.identity_resolver")


class PathTraversalError(PermissionError):
    """Raised when an identity asset path attempts to escape the allowed base directory."""
    pass


def resolve_safe_path(base_dir: Path, rel_path: str) -> Path:
    """Resolve rel_path safely within base_dir, preventing path traversal."""
    resolved_base = base_dir.resolve()
    target = (resolved_base / rel_path).resolve()
    try:
        target.relative_to(resolved_base)
    except ValueError:
        raise PathTraversalError(
            f"Access denied: path {rel_path!r} escapes base directory {resolved_base}"
        )
    return target


class IdentityResolver:
    """Resolves and caches identity bundles for Bots."""

    def __init__(self, root_dir: Optional[Path] = None):
        if root_dir is not None:
            self._root_dir = Path(root_dir)
        else:
            try:
                from hermes_constants import get_hermes_home
                self._root_dir = Path(get_hermes_home()) / "bots"
            except ImportError:
                import os
                self._root_dir = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")) / "bots"
        self._cache: Dict[Tuple[str, int], BotIdentityBundle] = {}

    def get_bot_dir(self, bot_id: str) -> Path:
        """Directory hosting assets for a specific bot."""
        return resolve_safe_path(self._root_dir, bot_id)

    def resolve(
        self,
        bot_spec: BotSpec,
        bot_dir: Optional[Path] = None,
        use_cache: bool = True,
    ) -> BotIdentityBundle:
        """Resolve identity assets for bot_spec. Falls back safely for legacy bots."""
        target_dir = bot_dir if bot_dir is not None else self.get_bot_dir(bot_spec.id)
        version = bot_spec.identity.version if bot_spec.identity else 1
        cache_key = (bot_spec.id, version)

        if use_cache and cache_key in self._cache:
            return self._cache[cache_key]

        # Legacy fallback if bot_spec.identity is missing
        if bot_spec.identity is None:
            bundle = self._resolve_legacy(bot_spec, target_dir)
            if use_cache:
                self._cache[cache_key] = bundle
            return bundle

        bundle = self._read_bundle(bot_spec.id, bot_spec.identity, target_dir)
        if use_cache:
            self._cache[cache_key] = bundle
        return bundle

    def _read_bundle(
        self, bot_id: str, identity_spec: BotIdentitySpec, bot_dir: Path
    ) -> BotIdentityBundle:
        """Read asset files from bot_dir and assemble bundle."""
        soul_text = self._read_asset(bot_dir, identity_spec.soul)
        id_text = self._read_asset(bot_dir, identity_spec.identity)
        val_text = self._read_asset(bot_dir, identity_spec.values)

        return BotIdentityBundle(
            bot_id=bot_id,
            identity_version=identity_spec.version,
            soul=soul_text,
            identity=id_text,
            values=val_text,
        )

    def _resolve_legacy(self, bot_spec: BotSpec, bot_dir: Path) -> BotIdentityBundle:
        """Graceful fallback for legacy BotSpec without explicit identity configuration."""
        soul_text = ""
        # Check if SOUL.md exists in bot_dir
        if bot_dir.exists():
            soul_path = bot_dir / "SOUL.md"
            if soul_path.is_file():
                soul_text = self._read_asset(bot_dir, "SOUL.md")

        # Synthesize minimal identity text from BotSpec description
        id_text = f"Bot ID: {bot_spec.id}\nName: {bot_spec.name}"
        if bot_spec.description:
            id_text += f"\nDescription: {bot_spec.description}"

        return BotIdentityBundle(
            bot_id=bot_spec.id,
            identity_version=1,
            soul=soul_text,
            identity=id_text,
            values="",
            metadata={"legacy_fallback": True},
        )

    def _read_asset(self, base_dir: Path, rel_path: str) -> str:
        """Read a text asset with path traversal safety."""
        safe_path = resolve_safe_path(base_dir, rel_path)
        if not safe_path.is_file():
            return ""
        try:
            return safe_path.read_text(encoding="utf-8").strip()
        except Exception as e:
            logger.warning("Could not read identity asset %s: %s", safe_path, e)
            return ""

    def invalidate_cache(self, bot_id: Optional[str] = None) -> None:
        """Invalidate cached bundles."""
        if bot_id is None:
            self._cache.clear()
        else:
            self._cache = {k: v for k, v in self._cache.items() if k[0] != bot_id}

    def check_drift(
        self,
        active_bundle: BotIdentityBundle,
        identity_spec: BotIdentitySpec,
        bot_dir: Optional[Path] = None,
    ) -> bool:
        """Check if on-disk files differ from the active bundle's hashes."""
        target_dir = bot_dir if bot_dir is not None else self.get_bot_dir(active_bundle.bot_id)
        current = self._read_bundle(active_bundle.bot_id, identity_spec, target_dir)
        return current.bundle_hash != active_bundle.bundle_hash

    @staticmethod
    def diff_bundles(
        bundle_a: BotIdentityBundle, bundle_b: BotIdentityBundle
    ) -> Dict[str, Any]:
        """Compute textual diffs between two bundles."""
        diffs = {}
        for section in ("soul", "identity", "values"):
            text_a = getattr(bundle_a, section).splitlines(keepends=True)
            text_b = getattr(bundle_b, section).splitlines(keepends=True)
            delta = list(
                difflib.unified_diff(
                    text_a,
                    text_b,
                    fromfile=f"{section}_v{bundle_a.identity_version}",
                    tofile=f"{section}_v{bundle_b.identity_version}",
                )
            )
            if delta:
                diffs[section] = "".join(delta)
        return {
            "has_changes": bool(diffs),
            "bundle_hash_a": bundle_a.bundle_hash,
            "bundle_hash_b": bundle_b.bundle_hash,
            "diffs": diffs,
        }
