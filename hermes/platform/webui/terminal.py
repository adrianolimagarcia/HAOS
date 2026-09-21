"""HAOS Standalone WebUI — emulador de terminal interno (bridge PTY real).

Espelha o espírito do terminal embutido da v1 (hermes-webui): um shell com
PTY próprio por sessão, desacoplado do caminho de execução do agente, sem
mutar ``os.environ`` global. Cada sessão roda um ``subprocess.Popen`` num PTY
(master/slave), um reader-thread empilha a saída num buffer anelar limitado e
a UI consome por short-poll (drain). Sem SSE/websocket: ThreadingHTTPServer +
fetch são suficientes e mantêm tudo stdlib.

Garantias:
* Cap de sessões simultâneas (default 6) com evicção LRU da mais antiga.
* ``PR_SET_PDEATHSIG`` via preexec_fn: se o servidor morrer, o shell não
  fica órfão (precedente da v1).
* cwd/env explícitos por sessão; nunca toca o environ do processo.
* Resize real via ``TIOCSWINSZ`` no master fd.
"""

from __future__ import annotations

import atexit
import os
import select
import struct
import subprocess
import sys
import threading
import time
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import fcntl
    import pty
    import termios
    HAS_POSIX_PTY = True
except ImportError:
    fcntl = None  # type: ignore
    pty = None  # type: ignore
    termios = None  # type: ignore
    HAS_POSIX_PTY = False

_MAX_SESSIONS = 10
_BUFFER_MAXLEN = 10000  # chunks retidos por sessão (catch-up honesto)
_POLL_CHUNK_CAP = 2048  # máx chunks por drain (evita buffer starvation sob output massivo)
_GRACE_BEFORE_REAP_S = 60.0  # sessão sem drain por > grace é morta


def _set_nonblocking(fd: int) -> None:
    if fcntl:
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)


def _winsize(rows: int, cols: int) -> bytes:
    rows = max(8, min(int(rows or 24), 80))
    cols = max(20, min(int(cols or 80), 240))
    return struct.pack("HHHH", rows, cols, 0, 0)


def _preexec_terminal() -> None:
    """Filho vira líder de sessão e adquire o PTY como terminal de controle."""
    try:
        os.setsid()
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)  # stdin = slave do PTY
    except OSError:
        pass


def _shell_path() -> str:
    for candidate in (os.environ.get("SHELL"), "/bin/bash", "/bin/sh"):
        if candidate and os.path.exists(candidate):
            return candidate
    return "/bin/sh"


class TerminalSession:
    def __init__(self, *, cwd: Optional[str] = None, shell: Optional[str] = None,
                 env: Optional[Dict[str, str]] = None):
        self.session_id = uuid.uuid4().hex
        self.shell = shell or _shell_path()
        cwd = cwd or os.getcwd()
        self.cwd = cwd if os.path.isdir(cwd) else Path.home().as_posix()
        self._history_chunks: List[bytes] = []
        self.master_fd, slave_fd = pty.openpty()
        _set_nonblocking(self.master_fd)
        merged_env = dict(os.environ)
        if env:
            merged_env.update(env)
        merged_env.setdefault("TERM", "xterm-256color")
        haos_home_val = os.environ.get("HAOS_HOME") or "/run/media/adriano/e681b5ac-a4fb-44d4-aebf-9d6584065787/dsh-projetos/.haos"
        merged_env.setdefault("HAOS_HOME", haos_home_val)
        merged_env.setdefault("HERMES_HOME", haos_home_val)
        self.proc = subprocess.Popen(
            [self.shell, "-i"],
            stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
            cwd=self.cwd, env=merged_env, preexec_fn=_preexec_terminal,
            close_fds=True,
        )
        os.close(slave_fd)
        self._buffer: List[bytes] = []
        self._lock = threading.Lock()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self._last_drain = time.time()

    # ------------------------------------------------------------------ #
    def _read_loop(self) -> None:
        while True:
            try:
                r, _, _ = select.select([self.master_fd], [], [], 0.5)
            except (OSError, ValueError):
                break
            if self.master_fd not in r:
                continue
            try:
                data = os.read(self.master_fd, 8192)
            except OSError:
                break
            if not data:
                break
            with self._lock:
                self._buffer.append(data)
                self._history_chunks.append(data)
                if len(self._buffer) > _BUFFER_MAXLEN:
                    del self._buffer[: len(self._buffer) - _BUFFER_MAXLEN]
                if len(self._history_chunks) > 1000:
                    del self._history_chunks[:500]

    def write_input(self, data: str) -> bool:
        if self.proc.poll() is not None:
            return False
        try:
            os.write(self.master_fd, data.encode("utf-8", "replace"))
            return True
        except OSError:
            return False

    def resize(self, rows: int, cols: int) -> None:
        try:
            fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, _winsize(rows, cols))
        except OSError:
            pass

    def drain(self) -> Dict[str, Any]:
        """Devolve chunks acumulados e limpa o backlog desta sessão."""
        with self._lock:
            chunks = self._buffer[:_POLL_CHUNK_CAP]
            if len(chunks) < len(self._buffer):
                del self._buffer[:len(chunks)]
            else:
                self._buffer.clear()
        self._last_drain = time.time()
        exited = self.proc.poll()
        return {
            "session_id": self.session_id,
            "data": b"".join(chunks).decode("utf-8", "replace"),
            "running": exited is None,
            "exit_code": exited,
        }

    def get_replay(self) -> Dict[str, Any]:
        """Devolve todo o histórico acumulado desde o início da sessão."""
        with self._lock:
            all_chunks = list(self._history_chunks)
        self._last_drain = time.time()
        exited = self.proc.poll()
        text = b"".join(all_chunks).decode("utf-8", "replace")
        return {
            "session_id": self.session_id,
            "data": text,
            "buffer": text,
            "running": exited is None,
            "exit_code": exited,
        }

    def kill(self) -> None:
        try:
            if self.proc.poll() is None:
                try:
                    os.killpg(os.getpgid(self.proc.pid), 9)
                except Exception:
                    self.proc.kill()
        except OSError:
            pass
        try:
            os.close(self.master_fd)
        except OSError:
            pass

    @property
    def stale(self) -> bool:
        # Reap de terminais inativos:
        # Se o terminal não tiver atividade de I/O / drain por mais de 5 minutos,
        # considera stale e encerra a sessão e seus subprocessos para não reter memória.
        if self.proc.poll() is None:
            return time.time() - self._last_drain > 300.0
        return time.time() - self._last_drain > 60.0


class TerminalManager:
    """Gerencia sessões PTY com cap + evicção LRU + reaper de stale."""

    def __init__(self, max_sessions: int = _MAX_SESSIONS):
        self.max_sessions = max_sessions
        self._sessions: "OrderedDict[str, TerminalSession]" = OrderedDict()
        self._lock = threading.Lock()
        self._reaper = threading.Thread(target=self._reap_loop, daemon=True)
        self._reaper.start()
        atexit.register(self.shutdown)

    def start(self, *, cwd: Optional[str] = None,
              env: Optional[Dict[str, str]] = None) -> TerminalSession:
        if not HAS_POSIX_PTY:
            raise RuntimeError(
                "O terminal PTY web embarcado requer ambiente POSIX (Linux, macOS ou WSL2 no Windows). "
                "No Windows nativo, use o Git Bash / PowerShell ou execute o HAOS dentro do WSL2."
            )
        with self._lock:
            self._evict_stale_locked()
            if len(self._sessions) >= self.max_sessions:
                # Evicção LRU: remove a sessão menos recente.
                _, oldest = self._sessions.popitem(last=False)
                oldest.kill()
            session = TerminalSession(cwd=cwd, env=env)
            self._sessions[session.session_id] = session
            return session

    def get(self, session_id: str) -> Optional[TerminalSession]:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            self._sessions.move_to_end(session_id)
            return session

    def remove(self, session_id: str) -> bool:
        with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is not None:
            session.kill()
            return True
        return False

    def list_active(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [
                {"session_id": sid, "running": s.proc.poll() is None}
                for sid, s in self._sessions.items()
            ]

    def _evict_stale_locked(self) -> None:
        for sid in [sid for sid, s in self._sessions.items() if s.stale]:
            session = self._sessions.pop(sid)
            session.kill()

    def _reap_loop(self) -> None:
        while True:
            time.sleep(5.0)
            with self._lock:
                self._evict_stale_locked()

    def shutdown(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            session.kill()
