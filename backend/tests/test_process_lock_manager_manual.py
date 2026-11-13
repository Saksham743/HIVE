
import json
import subprocess
import sys
import tempfile

from pathlib import Path

from backend.app.core.process_lock_manager import (
    ProcessLockManager,
    ProcessAlreadyRunningError,
)


def test_basic_acquisition():
    with tempfile.TemporaryDirectory() as temp_dir:
        lock_path = Path(temp_dir) / "test.lock"

        manager = ProcessLockManager(lock_path)

        assert manager.acquire() is True
        assert manager.is_acquired is True

        assert manager.metadata["application"] == "HIVE"
        assert manager.metadata["pid"] > 0
        assert manager.metadata["session_id"]

        manager.release()

        assert manager.is_acquired is False

        print("[PASS] Basic acquisition and release")


def test_duplicate_acquisition_same_manager():
    with tempfile.TemporaryDirectory() as temp_dir:
        lock_path = Path(temp_dir) / "test.lock"

        manager = ProcessLockManager(lock_path)

        assert manager.acquire() is True
        assert manager.acquire() is True

        manager.release()

        print("[PASS] Repeated acquisition by same manager")


def test_duplicate_acquisition_different_managers():
    with tempfile.TemporaryDirectory() as temp_dir:
        lock_path = Path(temp_dir) / "test.lock"

        first = ProcessLockManager(lock_path)
        second = ProcessLockManager(lock_path)

        first.acquire()

        try:
            second.acquire()
            raise AssertionError(
                "Second manager unexpectedly acquired the lock."
            )
        except ProcessAlreadyRunningError:
            pass
        finally:
            first.release()

        print("[PASS] Duplicate manager protection")


def test_lock_release_and_reacquisition():
    with tempfile.TemporaryDirectory() as temp_dir:
        lock_path = Path(temp_dir) / "test.lock"

        first = ProcessLockManager(lock_path)
        second = ProcessLockManager(lock_path)

        first.acquire()
        first.release()

        assert second.acquire() is True

        second.release()

        print("[PASS] Lock can be reacquired after release")


def test_context_manager():
    with tempfile.TemporaryDirectory() as temp_dir:
        lock_path = Path(temp_dir) / "test.lock"

        manager = ProcessLockManager(lock_path)

        with manager:
            assert manager.is_acquired is True

        assert manager.is_acquired is False

        print("[PASS] Context manager releases lock")


def test_metadata_persistence():
    with tempfile.TemporaryDirectory() as temp_dir:
        lock_path = Path(temp_dir) / "test.lock"

        manager = ProcessLockManager(lock_path)
        manager.acquire()

        original_session = manager.session_id
        manager.release()

        assert lock_path.exists()

        # The file remains, but its OS lock has been released.
        second = ProcessLockManager(lock_path)
        second.acquire()

        assert second.session_id != original_session

        second.release()

        print("[PASS] Lock file persists and metadata refreshes")


def test_stale_metadata_does_not_block_acquisition():
    with tempfile.TemporaryDirectory() as temp_dir:
        lock_path = Path(temp_dir) / "test.lock"

        lock_path.write_bytes(
            b"\0" + json.dumps({
                "pid": 999999,
                "session_id": "old-session",
                "application": "HIVE"
            }).encode("utf-8")
        )

        manager = ProcessLockManager(lock_path)

        assert manager.acquire() is True
        assert manager.metadata["session_id"] == manager.session_id

        manager.release()

        print("[PASS] Stale metadata does not block acquisition")


def test_cross_process_lock():
    with tempfile.TemporaryDirectory() as temp_dir:
        lock_path = Path(temp_dir) / "test.lock"

        manager = ProcessLockManager(lock_path)
        manager.acquire()

        child_code = """
import sys
from backend.app.core.process_lock_manager import (
    ProcessLockManager,
    ProcessAlreadyRunningError,
)

manager = ProcessLockManager(sys.argv[1])

try:
    manager.acquire()
except ProcessAlreadyRunningError:
    print("LOCK_BLOCKED")
    sys.exit(0)
else:
    manager.release()
    print("LOCK_UNEXPECTEDLY_ACQUIRED")
    sys.exit(1)
"""

        try:
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    child_code,
                    str(lock_path),
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )

            assert result.returncode == 0, result.stderr
            assert "LOCK_BLOCKED" in result.stdout

        finally:
            manager.release()

        print("[PASS] Cross-process lock protection")


def test_release_is_idempotent():
    with tempfile.TemporaryDirectory() as temp_dir:
        lock_path = Path(temp_dir) / "test.lock"

        manager = ProcessLockManager(lock_path)

        manager.acquire()
        manager.release()
        manager.release()

        assert manager.is_acquired is False

        print("[PASS] Repeated release is safe")


if __name__ == "__main__":
    tests = [
        test_basic_acquisition,
        test_duplicate_acquisition_same_manager,
        test_duplicate_acquisition_different_managers,
        test_lock_release_and_reacquisition,
        test_context_manager,
        test_metadata_persistence,
        test_stale_metadata_does_not_block_acquisition,
        test_cross_process_lock,
        test_release_is_idempotent,
    ]

    for test in tests:
        test()

    print("\nALL PROCESS LOCK MANAGER TESTS PASSED!")
