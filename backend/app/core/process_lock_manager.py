
import json
import os
import socket
import threading
import uuid

from datetime import datetime, timezone
from pathlib import Path
from typing import Any


if os.name == "nt":
    import msvcrt
else:
    import fcntl


class ProcessAlreadyRunningError(RuntimeError):
    """Raised when another HIVE process already owns the lock."""


class ProcessLockManager:
    """
    Ensures that only one HIVE process runs at a time.

    Uses:
    - Windows: msvcrt byte-range locking
    - Linux/macOS: fcntl advisory locking

    The operating system releases the lock automatically
    if the process exits or crashes.
    """

    _registry: dict[str, str] = {}
    _registry_guard = threading.Lock()

    def __init__(self, lock_path: str | Path | None = None):
        if lock_path is None:
            from backend.app.config import settings

            lock_path = settings.data_dir / "hive.process.lock"

        self.lock_path = Path(lock_path).resolve()
        self._lock_handle = None
        self._acquired = False

        self.session_id = str(uuid.uuid4())
        self.metadata: dict[str, Any] = {}

    @property
    def is_acquired(self) -> bool:
        return self._acquired

    def acquire(self) -> bool:
        """
        Acquire the exclusive process lock.

        Returns True when successfully acquired.
        Raises ProcessAlreadyRunningError if another
        HIVE instance already owns it.
        """

        if self._acquired:
            return True

        self.lock_path.parent.mkdir(parents=True, exist_ok=True)

        registry_key = str(self.lock_path).lower()

        with self._registry_guard:
            # Prevent duplicate acquisition within this Python process.
            if registry_key in self._registry:
                raise ProcessAlreadyRunningError(
                    "Another HIVE instance in this process "
                    "already owns the process lock."
                )

            self.lock_path.touch(exist_ok=True)

            handle = open(self.lock_path, "r+b")

            try:
                # Reserve the first byte as the lock sentinel.
                handle.seek(0, os.SEEK_END)

                if handle.tell() == 0:
                    handle.write(b"\0")
                    handle.flush()

                # Acquire the OS-level exclusive lock.
                handle.seek(0)
                self._acquire_os_lock(handle)

                self._lock_handle = handle
                self._acquired = True

                self.metadata = {
                    "application": "HIVE",
                    "pid": os.getpid(),
                    "session_id": self.session_id,
                    "hostname": socket.gethostname(),
                    "started_at": datetime.now(
                        timezone.utc
                    ).isoformat(),
                }

                # Store diagnostic metadata after the locked byte.
                metadata_bytes = json.dumps(
                    self.metadata,
                    indent=2
                ).encode("utf-8")

                handle.seek(1)
                handle.truncate(1)
                handle.write(metadata_bytes)
                handle.flush()
                os.fsync(handle.fileno())

                self._registry[registry_key] = self.session_id

                return True

            except (BlockingIOError, OSError) as exc:
                if self._acquired:
                    self._release_os_lock(handle)

                existing_metadata = self._read_metadata(handle)
                handle.close()

                self._lock_handle = None
                self._acquired = False

                # A failed lock attempt means another process
                # or handle currently owns the OS lock.
                raise ProcessAlreadyRunningError(
                    "HIVE is already running. "
                    f"Existing process metadata: {existing_metadata}"
                ) from exc

            except Exception:
                if self._acquired:
                    self._release_os_lock(handle)

                handle.close()
                self._lock_handle = None
                self._acquired = False
                raise

    def release(self) -> None:
        """
        Release the lock safely.

        Idempotent: calling release multiple times is safe.
        The lock file is intentionally not deleted.
        """

        if not self._acquired or self._lock_handle is None:
            return

        registry_key = str(self.lock_path).lower()

        with self._registry_guard:
            try:
                self._release_os_lock(self._lock_handle)
            finally:
                self._lock_handle.close()
                self._lock_handle = None
                self._acquired = False

                if self._registry.get(registry_key) == self.session_id:
                    del self._registry[registry_key]

    def _acquire_os_lock(self, handle) -> None:
        handle.seek(0)

        if os.name == "nt":
            msvcrt.locking(
                handle.fileno(),
                msvcrt.LK_NBLCK,
                1
            )
        else:
            fcntl.flock(
                handle.fileno(),
                fcntl.LOCK_EX | fcntl.LOCK_NB
            )

    def _release_os_lock(self, handle) -> None:
        handle.seek(0)

        if os.name == "nt":
            msvcrt.locking(
                handle.fileno(),
                msvcrt.LK_UNLCK,
                1
            )
        else:
            fcntl.flock(
                handle.fileno(),
                fcntl.LOCK_UN
            )

    @staticmethod
    def _read_metadata(handle) -> dict[str, Any]:
        """Read metadata left by the process holding the lock."""

        try:
            handle.seek(1)
            raw = handle.read()

            if not raw:
                return {}

            return json.loads(raw.decode("utf-8"))

        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {}

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.release()
        return False
