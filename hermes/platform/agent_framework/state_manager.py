"""Atomic State Management with checkpoints and concurrency file locking.

Implements HAOSStateManager for robust, crash-consistent, concurrency-safe
state and checkpoint lifecycle management on top of standard POSIX primitives.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from hermes_constants import mkdir_under_hermes_home

try:
    import yaml
except ImportError:
    yaml = None


class LockTimeoutError(TimeoutError):
    """Raised when acquiring a file lock exceeds the configured timeout."""


class HAOSStateManager:
    """Atomic state and checkpoint manager backed by YAML and POSIX file locks.

    Default storage binds to `hermes_constants.get_hermes_home() / 'agent'`
    at construction. Profile resolution errors propagate rather than sharing storage.
    """

    def __init__(self, base_dir: Path | str | None = None) -> None:
        if base_dir is not None:
            self.base_dir = Path(base_dir).resolve()
        else:
            from hermes_constants import get_hermes_home

            self.base_dir = (Path(get_hermes_home()) / "agent").resolve()

        self.state_dir = self.base_dir / "state"
        self.checkpoints_dir = self.base_dir / "checkpoints"
        self.locks_dir = self.base_dir / "locks"

        # Keep storage components inside the owning profile, never through symlinks.
        for directory in (self.state_dir, self.checkpoints_dir, self.locks_dir):
            self._check_path(directory)
            mkdir_under_hermes_home(directory)

        self._current_state_file = self.state_dir / "current.yaml"

    @contextmanager
    def acquire_lock(self, lock_name: str, timeout_sec: float = 5.0) -> Iterator[Path]:
        """Context manager acquiring an exclusive non-blocking flock with polling timeout.

        Args:
            lock_name: Identifying name for the lock (e.g. 'state', 'checkpoint').
            timeout_sec: Maximum time in seconds to wait before raising LockTimeoutError.
        """
        clean_name = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in lock_name)
        lock_path = self.locks_dir / f"{clean_name}.lock"
        self._check_path(lock_path)
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        start_time = time.monotonic()
        acquired = False

        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except (BlockingIOError, OSError) as exc:
                    if time.monotonic() - start_time >= timeout_sec:
                        raise LockTimeoutError(
                            f"Timed out acquiring lock '{lock_name}' on {lock_path} after {timeout_sec:.2f}s"
                        ) from exc
                    time.sleep(0.02)
            yield lock_path
        finally:
            if acquired:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
            try:
                os.close(fd)
            except OSError:
                pass

    @staticmethod
    def _check_path(path: Path) -> None:
        # Reject links in every component, including dangling links, before I/O.
        if any(part.is_symlink() for part in (path, *path.parents)):
            raise ValueError(f"Symlink not permitted: {path}")

    @staticmethod
    def _safe_name(name: str) -> bool:
        return isinstance(name, str) and bool(re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", name)) and name not in (".", "..")

    def _read_yaml_file(self, path: Path) -> dict[str, Any]:
        """Missing state is empty; existing malformed state must never be overwritten implicitly."""
        self._check_path(path)
        try:
            content = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        try:
            parsed = yaml.safe_load(content) if yaml is not None else json.loads(content)
        except (ValueError, yaml.YAMLError if yaml is not None else ValueError) as exc:
            raise ValueError(f"Malformed state: {path}") from exc
        if not isinstance(parsed, dict):
            raise ValueError(f"State must be a mapping: {path}")
        return parsed

    def _write_text_atomic(self, path: Path, rendered: str) -> None:
        """Durable per-file replacement; callers provide their own serialization lock."""
        self._check_path(path)
        mkdir_under_hermes_home(path.parent)
        tmp_path = path.with_name(f"{path.name}.tmp.{uuid.uuid4().hex}")
        try:
            fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(rendered)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp_path, path)
            directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            tmp_path.unlink(missing_ok=True)

    def _write_yaml_file_atomic(self, path: Path, data: dict[str, Any]) -> None:
        rendered = (yaml.safe_dump(data, default_flow_style=False, sort_keys=False)
                    if yaml is not None else json.dumps(data, indent=2, sort_keys=False))
        self._write_text_atomic(path, rendered)

    def _write_json_file_atomic(self, path: Path, data: dict[str, Any]) -> None:
        self._write_text_atomic(path, json.dumps(data, indent=2, ensure_ascii=False))

    def _deep_merge(self, base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
        """Recursively merge nested dictionaries."""
        result = dict(base)
        for key, value in update.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value
        return result

    def get_state(self, key: str | None = None) -> Any:
        """Read state from current.yaml.

        Args:
            key: Optional specific key to extract. If omitted, returns entire state dict.

        Returns:
            Dictionary containing state, or the specific key value (defaulting to {} if missing).
        """
        with self.acquire_lock("state", timeout_sec=5.0):
            state = self._read_yaml_file(self._current_state_file)
            if key is None:
                return state
            return state.get(key, {})

    def set_state(self, data: dict[str, Any], merge: bool = True) -> dict[str, Any]:
        """Atomically persist state data to current.yaml.

        Args:
            data: Data to write or merge.
            merge: If True, deep merges into existing state; if False, overwrites entirely.

        Returns:
            The complete updated state dictionary.
        """
        if not isinstance(data, dict):
            raise ValueError("State must be a mapping")
        with self.acquire_lock("state", timeout_sec=5.0):
            if merge:
                current = self._read_yaml_file(self._current_state_file)
                new_state = self._deep_merge(current, data)
            else:
                new_state = dict(data)

            self._write_yaml_file_atomic(self._current_state_file, new_state)
            return new_state

    def create_checkpoint(self, label: str, metadata: dict[str, Any] | None = None) -> str:
        """Capture an atomic snapshot of current state and active files into checkpoints/<id>.json.

        Args:
            label: Descriptive label for the checkpoint (e.g. 'pre_disk_rebalance').
            metadata: Optional additional metadata to store in the checkpoint.

        Returns:
            Unique checkpoint ID (format: `<timestamp>_<label>`).
        """
        clean_label = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in label)
        with self.acquire_lock("checkpoint", timeout_sec=5.0):
            now = time.time()
            checkpoint_id = f"{int(now)}_{clean_label}_{uuid.uuid4().hex}"
            checkpoint_file = self.checkpoints_dir / f"{checkpoint_id}.json"
            with self.acquire_lock("state", timeout_sec=5.0):
                current_state = self._read_yaml_file(self._current_state_file)

                # Capture other files under state_dir if present
                state_files: dict[str, str] = {}
                for path in self.state_dir.glob("*"):
                    self._check_path(path)
                    if path.is_file() and not path.name.startswith("."):
                        state_files[path.name] = path.read_text(encoding="utf-8")

                checkpoint_data = {
                    "checkpoint_id": checkpoint_id,
                    "label": clean_label,
                    "timestamp": now,
                    "metadata": dict(metadata or {}),
                    "state": current_state,
                    "files": state_files,
                }

                self._write_json_file_atomic(checkpoint_file, checkpoint_data)

        return checkpoint_id

    def list_checkpoints(self) -> list[dict[str, Any]]:
        """List existing checkpoints sorted newest first."""
        checkpoints: list[dict[str, Any]] = []
        if not self.checkpoints_dir.is_dir():
            return checkpoints

        for path in self.checkpoints_dir.glob("*.json"):
            try:
                content = json.loads(path.read_text(encoding="utf-8"))
                checkpoints.append({
                    "checkpoint_id": content.get("checkpoint_id", path.stem),
                    "label": content.get("label", ""),
                    "timestamp": float(content.get("timestamp", path.stat().st_mtime)),
                    "metadata": content.get("metadata", {}),
                    "path": str(path),
                })
            except Exception:
                # Corrupted or partial checkpoint file fallback
                checkpoints.append({
                    "checkpoint_id": path.stem,
                    "label": path.stem.split("_", 1)[-1] if "_" in path.stem else path.stem,
                    "timestamp": path.stat().st_mtime,
                    "metadata": {},
                    "path": str(path),
                })

        checkpoints.sort(key=lambda c: c["timestamp"], reverse=True)
        return checkpoints

    def restore_checkpoint(self, checkpoint_id: str) -> bool:
        """Restore state from a checkpoint.

        Args:
            checkpoint_id: Checkpoint ID (with or without .json extension).

        Returns:
            True if restored successfully, False if checkpoint not found or unreadable.
        """
        clean_id = checkpoint_id[:-5] if checkpoint_id.endswith(".json") else checkpoint_id
        if not self._safe_name(clean_id):
            return False
        checkpoint_file = self.checkpoints_dir / f"{clean_id}.json"

        with self.acquire_lock("checkpoint", timeout_sec=5.0):
            with self.acquire_lock("state", timeout_sec=5.0):
                try:
                    self._check_path(checkpoint_file)
                    content = json.loads(checkpoint_file.read_text(encoding="utf-8"))
                    if not isinstance(content, dict) or not isinstance(content.get("state"), dict):
                        return False
                    files = content.get("files", {})
                    if not isinstance(files, dict):
                        return False
                    self._check_path(self._current_state_file)
                    if self._current_state_file.exists() and not self._current_state_file.is_file():
                        return False
                    # Validate the complete manifest before the first replacement.
                    for filename, file_content in files.items():
                        if not self._safe_name(filename) or not isinstance(file_content, str):
                            return False
                        target = self.state_dir / filename
                        self._check_path(target)
                        if target.exists() and not target.is_file():
                            return False
                    saved_state = content["state"]
                    # Serialize before writing auxiliary files; malformed values cannot cause partial restore.
                    rendered = (yaml.safe_dump(saved_state, sort_keys=False) if yaml is not None
                                else json.dumps(saved_state, indent=2))
                except (OSError, ValueError, TypeError):
                    return False

                # Each replacement is atomic and durable, not a multi-file transaction.
                for filename, file_content in files.items():
                    if filename != "current.yaml":
                        self._write_text_atomic(self.state_dir / filename, file_content)
                self._write_text_atomic(self._current_state_file, rendered)

        return True
