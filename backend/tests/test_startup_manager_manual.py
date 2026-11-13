
import tempfile

from pathlib import Path

from backend.app.core.process_lock_manager import ProcessLockManager
from backend.app.core.startup_manager import StartupManager


class FakeRecoveryManager:
    def __init__(self, events=None):
        self.events = events if events is not None else []
        self.prepare_calls = []

    def inspect_incomplete_projects(self):
        self.events.append("inspect")

        return [
            {
                "project_id": "project-001",
                "name": "Test Documentary",
                "topic": "Test topic",
                "status": "running",
                "current_stage": "narration",
                "task_counts": {
                    "pending": 1,
                    "running": 1,
                    "completed": 2,
                    "failed": 0,
                    "skipped": 0,
                },
                "total_tasks": 4,
            }
        ]

    def prepare_recovery(
        self,
        project_id,
        process_confirmed_stopped=False,
    ):
        self.events.append("prepare")

        assert process_confirmed_stopped is True

        self.prepare_calls.append(project_id)

        return {
            "project": {
                "id": project_id,
                "name": "Test Documentary",
            },
            "recovery_status": "prepared",
            "ready_tasks": [
                {
                    "task_key": "narration",
                    "module": "narration",
                    "operation": "generate_voice",
                }
            ],
            "task_updates": [
                {
                    "task_key": "narration",
                    "reason": "Previous execution was interrupted.",
                }
            ],
        }


def create_manager(
    temp_dir,
    events=None,
    db_initializer=None,
    logging_configurator=None,
):
    lock_path = Path(temp_dir) / "hive-test.lock"

    if events is None:
        events = []

    recovery = FakeRecoveryManager(events)

    if db_initializer is None:
        db_initializer = lambda: events.append("database")

    if logging_configurator is None:
        logging_configurator = lambda: events.append("logging")

    manager = StartupManager(
        process_lock=ProcessLockManager(lock_path),
        recovery_manager=recovery,
        database_initializer=db_initializer,
        logging_configurator=logging_configurator,
    )

    return manager, recovery


def test_successful_startup():
    with tempfile.TemporaryDirectory() as temp_dir:
        events = []

        manager, recovery = create_manager(
            temp_dir,
            events,
        )

        report = manager.start()

        assert manager.is_started is True
        assert manager.process_lock.is_acquired is True

        assert report["projects_inspected"] == 1
        assert report["recovery_plans_created"] == 1
        assert report["ready_tasks"] == 1
        assert report["updated_tasks"] == 1

        assert recovery.prepare_calls == ["project-001"]

        manager.shutdown()

        assert manager.is_started is False
        assert manager.process_lock.is_acquired is False

        print("[PASS] Successful startup and shutdown")


def test_startup_order():
    with tempfile.TemporaryDirectory() as temp_dir:
        events = []

        manager, recovery = create_manager(
            temp_dir,
            events,
        )

        manager.start()

        assert events == [
            "logging",
            "database",
            "inspect",
            "prepare",
        ]

        manager.shutdown()

        print("[PASS] Logging, database and recovery startup order")


def test_startup_is_idempotent():
    with tempfile.TemporaryDirectory() as temp_dir:
        events = []

        manager, recovery = create_manager(
            temp_dir,
            events,
        )

        first_report = manager.start()
        second_report = manager.start()

        assert first_report is second_report
        assert len(recovery.prepare_calls) == 1
        assert events.count("logging") == 1

        manager.shutdown()

        print("[PASS] Repeated startup does not duplicate recovery")


def test_shutdown_is_idempotent():
    with tempfile.TemporaryDirectory() as temp_dir:
        manager, _ = create_manager(temp_dir, [])

        manager.start()

        manager.shutdown()
        manager.shutdown()

        assert manager.is_started is False
        assert manager.process_lock.is_acquired is False

        print("[PASS] Repeated shutdown is safe")


def test_database_failure_releases_lock():
    with tempfile.TemporaryDirectory() as temp_dir:

        def failing_database_initializer():
            raise RuntimeError("Simulated database failure")

        manager, _ = create_manager(
            temp_dir,
            [],
            db_initializer=failing_database_initializer,
        )

        try:
            manager.start()
            raise AssertionError(
                "Startup should have failed."
            )
        except RuntimeError as exc:
            assert "Simulated database failure" in str(exc)

        assert manager.process_lock.is_acquired is False
        assert manager.is_started is False

        print("[PASS] Database failure releases process lock")


def test_recovery_failure_releases_lock():
    with tempfile.TemporaryDirectory() as temp_dir:

        class FailingRecoveryManager:
            def inspect_incomplete_projects(self):
                raise RuntimeError("Simulated recovery failure")

            def prepare_recovery(self, **kwargs):
                raise AssertionError("Should not reach this method")

        manager = StartupManager(
            process_lock=ProcessLockManager(
                Path(temp_dir) / "hive-test.lock"
            ),
            recovery_manager=FailingRecoveryManager(),
            database_initializer=lambda: None,
            logging_configurator=lambda: None,
        )

        try:
            manager.start()
            raise AssertionError(
                "Startup should have failed."
            )
        except RuntimeError as exc:
            assert "Simulated recovery failure" in str(exc)

        assert manager.process_lock.is_acquired is False
        assert manager.is_started is False

        print("[PASS] Recovery failure releases process lock")


def test_empty_incomplete_projects():
    with tempfile.TemporaryDirectory() as temp_dir:

        class EmptyRecoveryManager:
            def inspect_incomplete_projects(self):
                return []

            def prepare_recovery(self, **kwargs):
                raise AssertionError(
                    "No recovery should be prepared."
                )

        manager = StartupManager(
            process_lock=ProcessLockManager(
                Path(temp_dir) / "hive-test.lock"
            ),
            recovery_manager=EmptyRecoveryManager(),
            database_initializer=lambda: None,
            logging_configurator=lambda: None,
        )

        report = manager.start()

        assert report["projects_inspected"] == 0
        assert report["recovery_plans_created"] == 0
        assert report["ready_tasks"] == 0

        manager.shutdown()

        print("[PASS] Startup with no incomplete projects")


def test_context_manager():
    with tempfile.TemporaryDirectory() as temp_dir:
        manager, _ = create_manager(temp_dir, [])

        with manager:
            assert manager.is_started is True
            assert manager.process_lock.is_acquired is True

        assert manager.is_started is False
        assert manager.process_lock.is_acquired is False

        print("[PASS] Context manager handles lifecycle")


if __name__ == "__main__":
    tests = [
        test_successful_startup,
        test_startup_order,
        test_startup_is_idempotent,
        test_shutdown_is_idempotent,
        test_database_failure_releases_lock,
        test_recovery_failure_releases_lock,
        test_empty_incomplete_projects,
        test_context_manager,
    ]

    print("\n========== HIVE STARTUP MANAGER TESTS ==========\n")

    for test in tests:
        test()

    print("\nALL STARTUP MANAGER TESTS PASSED!")
