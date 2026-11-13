
"""
HIVE Startup Recovery Integration Tests

Tests the interaction between:
- StartupManager
- RecoveryManager
- CheckpointManager
- ProcessLockManager
- SQLite database

Run from the HIVE repository root:

    python -m backend.tests.test_startup_recovery_integration_manual

All test databases, workspaces, and lock files are isolated
inside temporary directories. The real HIVE database and
workspaces are never modified.
"""

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from backend.app.database.base import Base
from backend.app.database.models import Project, Task, Asset

from backend.app.core.checkpoint_manager import CheckpointManager
from backend.app.core.recovery_manager import RecoveryManager
from backend.app.core.process_lock_manager import ProcessLockManager
from backend.app.core.startup_manager import StartupManager


# Track resources so cleanup happens even when assertions fail.
ACTIVE_ENGINES = []
ACTIVE_STARTUP_MANAGERS = []


# ---------------------------------------------------------
# TEST ENVIRONMENT
# ---------------------------------------------------------

def create_test_environment(tmp_path, monkeypatch):
    """Create an isolated database, workspace, and HIVE managers."""

    database_path = tmp_path / "hive_test.db"
    workspace_path = tmp_path / "workspaces"

    workspace_path.mkdir(parents=True, exist_ok=True)

    engine = create_engine(
        f"sqlite:///{database_path.as_posix()}",
        connect_args={"check_same_thread": False},
        echo=False,
    )

    ACTIVE_ENGINES.append(engine)

    # Make SQLite enforce foreign-key relationships.
    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    TestSessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    Base.metadata.create_all(bind=engine)

    checkpoint_manager = CheckpointManager(
        session_factory=TestSessionLocal,
    )

    recovery_manager = RecoveryManager(
        session_factory=TestSessionLocal,
        checkpoint_manager=checkpoint_manager,
    )

    process_lock = ProcessLockManager(
        tmp_path / "hive_test.lock"
    )

    # Replace the imported settings object inside startup_manager.
    # This prevents tests from modifying real HIVE directories.
    from backend.app.core import startup_manager as startup_module

    monkeypatch.setattr(
        startup_module,
        "settings",
        SimpleNamespace(
            ensure_directories=lambda: None,
        ),
    )

    startup_manager = StartupManager(
        process_lock=process_lock,
        recovery_manager=recovery_manager,
        database_initializer=lambda: None,
        logging_configurator=lambda: None,
    )

    ACTIVE_STARTUP_MANAGERS.append(startup_manager)

    return {
        "engine": engine,
        "session_factory": TestSessionLocal,
        "workspace": workspace_path,
        "checkpoint_manager": checkpoint_manager,
        "recovery_manager": recovery_manager,
        "process_lock": process_lock,
        "startup_manager": startup_manager,
    }


# ---------------------------------------------------------
# DATABASE HELPERS
# ---------------------------------------------------------

def create_project(
    session_factory,
    workspace_root,
    status="running",
):
    """Create a project with its own isolated workspace."""

    workspace = workspace_root / "integration_project"
    workspace.mkdir(parents=True, exist_ok=True)

    with session_factory() as session:
        project = Project(
            name="Integration Test Project",
            topic="Test topic",
            status=status,
            workspace_path=str(workspace),
        )

        session.add(project)
        session.commit()
        session.refresh(project)

        return project.id


def create_task(
    session_factory,
    project_id,
    task_key,
    status="pending",
    attempts=0,
    max_attempts=3,
    depends_on=None,
):
    """Create a task using the actual Task model fields."""

    with session_factory() as session:
        task = Task(
            project_id=project_id,
            task_key=task_key,
            module="integration_test",
            operation=task_key,
            status=status,
            attempts=attempts,
            max_attempts=max_attempts,
            depends_on=depends_on or [],
        )

        session.add(task)
        session.commit()
        session.refresh(task)

        return task.id


def create_asset(
    session_factory,
    project_id,
    relative_path,
    content=b"valid test asset",
):
    """Write an asset inside the project's actual workspace."""

    with session_factory() as session:
        project = session.get(Project, project_id)

        if project is None:
            raise ValueError(f"Project not found: {project_id}")

        workspace = Path(project.workspace_path)

    asset_path = workspace / relative_path

    asset_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    asset_path.write_bytes(content)

    return relative_path


def get_task(session_factory, task_id):
    """Retrieve relevant task information."""

    with session_factory() as session:
        task = session.get(Task, task_id)

        if task is None:
            raise ValueError(f"Task not found: {task_id}")

        return {
            "id": task.id,
            "task_key": task.task_key,
            "status": task.status,
            "attempts": task.attempts,
        }


def get_asset(session_factory, project_id, relative_path):
    """Retrieve relevant asset information."""

    with session_factory() as session:
        asset = session.scalar(
            select(Asset).where(
                Asset.project_id == project_id,
                Asset.relative_path == relative_path,
            )
        )

        if asset is None:
            raise ValueError(
                f"Asset not found: {relative_path}"
            )

        return {
            "id": asset.id,
            "status": asset.status,
            "checksum_sha256": asset.checksum_sha256,
            "size_bytes": asset.size_bytes,
        }


def get_project_workspace(session_factory, project_id):
    """Get the workspace path stored on the project."""

    with session_factory() as session:
        project = session.get(Project, project_id)

        if project is None:
            raise ValueError(f"Project not found: {project_id}")

        return Path(project.workspace_path)


# ---------------------------------------------------------
# TEST 1: INTERRUPTED TASK RECOVERY
# ---------------------------------------------------------

def test_startup_recovers_interrupted_task(
    tmp_path,
    monkeypatch,
):
    env = create_test_environment(tmp_path, monkeypatch)

    project_id = create_project(
        env["session_factory"],
        env["workspace"],
    )

    task_id = create_task(
        env["session_factory"],
        project_id,
        "generate_script",
        status="running",
        attempts=1,
    )

    report = env["startup_manager"].start()

    task = get_task(
        env["session_factory"],
        task_id,
    )

    assert task["status"] == "failed"
    assert task["attempts"] == 1

    assert env["startup_manager"].is_started is True

    assert report["projects_inspected"] == 1
    assert report["ready_tasks"] == 1

    assert report["ready_task_details"][0]["task_key"] == "generate_script"


# ---------------------------------------------------------
# TEST 2: DOWNSTREAM TASK INVALIDATION
# ---------------------------------------------------------

def test_startup_invalidates_completed_downstream_tasks(
    tmp_path,
    monkeypatch,
):
    env = create_test_environment(tmp_path, monkeypatch)

    project_id = create_project(
        env["session_factory"],
        env["workspace"],
    )

    upstream_id = create_task(
        env["session_factory"],
        project_id,
        "generate_script",
        status="running",
        attempts=1,
    )

    downstream_id = create_task(
        env["session_factory"],
        project_id,
        "generate_scenes",
        status="completed",
        attempts=1,
        depends_on=["generate_script"],
    )

    env["startup_manager"].start()

    upstream = get_task(
        env["session_factory"],
        upstream_id,
    )

    downstream = get_task(
        env["session_factory"],
        downstream_id,
    )

    assert upstream["status"] == "failed"
    assert downstream["status"] == "failed"

    # The upstream task should be eligible for recovery.
    report = env["startup_manager"].startup_report

    assert report["ready_tasks"] == 1
    assert report["ready_task_details"][0]["task_key"] == "generate_script"


# ---------------------------------------------------------
# TEST 3: VALID ASSET INTEGRITY
# ---------------------------------------------------------

def test_valid_assets_pass_integrity_verification(
    tmp_path,
    monkeypatch,
):
    env = create_test_environment(tmp_path, monkeypatch)

    project_id = create_project(
        env["session_factory"],
        env["workspace"],
    )

    task_id = create_task(
        env["session_factory"],
        project_id,
        "generate_script",
        status="completed",
        attempts=1,
    )

    relative_path = create_asset(
        env["session_factory"],
        project_id,
        "script/script.md",
    )

    env["checkpoint_manager"].register_asset(
        project_id=project_id,
        asset_type="script",
        relative_path=relative_path,
        task_id=task_id,
    )

    env["startup_manager"].start()

    task = get_task(
        env["session_factory"],
        task_id,
    )

    asset = get_asset(
        env["session_factory"],
        project_id,
        relative_path,
    )

    assert task["status"] == "completed"
    assert asset["status"] == "available"
    assert asset["checksum_sha256"] is not None
    assert asset["size_bytes"] > 0


# ---------------------------------------------------------
# TEST 4: INTENTIONALLY DELETED ASSET
# ---------------------------------------------------------

def test_deleted_assets_are_not_restored(
    tmp_path,
    monkeypatch,
):
    env = create_test_environment(tmp_path, monkeypatch)

    project_id = create_project(
        env["session_factory"],
        env["workspace"],
    )

    task_id = create_task(
        env["session_factory"],
        project_id,
        "generate_script",
        status="completed",
        attempts=1,
    )

    relative_path = create_asset(
        env["session_factory"],
        project_id,
        "script/script.md",
    )

    env["checkpoint_manager"].register_asset(
        project_id=project_id,
        asset_type="script",
        relative_path=relative_path,
        task_id=task_id,
    )

    # Simulate a user-approved deletion.
    asset_path = (
        get_project_workspace(
            env["session_factory"],
            project_id,
        )
        / relative_path
    )

    assert asset_path.is_file()

    asset_path.unlink()

    # Mark the asset as intentionally deleted.
    with env["session_factory"]() as session:
        asset = session.scalar(
            select(Asset).where(
                Asset.project_id == project_id,
                Asset.relative_path == relative_path,
            )
        )

        assert asset is not None

        asset.status = "deleted"
        session.commit()

    env["startup_manager"].start()

    task = get_task(
        env["session_factory"],
        task_id,
    )

    asset = get_asset(
        env["session_factory"],
        project_id,
        relative_path,
    )

    assert task["status"] == "completed"
    assert asset["status"] == "deleted"
    assert not asset_path.exists()


# ---------------------------------------------------------
# TEST 5: MISSING ASSET RECOVERY
# ---------------------------------------------------------

def test_missing_asset_triggers_recovery(
    tmp_path,
    monkeypatch,
):
    env = create_test_environment(tmp_path, monkeypatch)

    project_id = create_project(
        env["session_factory"],
        env["workspace"],
    )

    task_id = create_task(
        env["session_factory"],
        project_id,
        "generate_script",
        status="completed",
        attempts=1,
    )

    relative_path = create_asset(
        env["session_factory"],
        project_id,
        "script/script.md",
    )

    env["checkpoint_manager"].register_asset(
        project_id=project_id,
        asset_type="script",
        relative_path=relative_path,
        task_id=task_id,
    )

    asset_path = (
        get_project_workspace(
            env["session_factory"],
            project_id,
        )
        / relative_path
    )

    # Remove the file without marking it as intentionally deleted.
    asset_path.unlink()

    env["startup_manager"].start()

    task = get_task(
        env["session_factory"],
        task_id,
    )

    asset = get_asset(
        env["session_factory"],
        project_id,
        relative_path,
    )

    assert task["status"] == "failed"
    assert asset["status"] == "invalid"
    assert not asset_path.exists()


# ---------------------------------------------------------
# TEST 6: CORRUPTED ASSET RECOVERY
# ---------------------------------------------------------

def test_corrupted_asset_triggers_recovery(
    tmp_path,
    monkeypatch,
):
    env = create_test_environment(tmp_path, monkeypatch)

    project_id = create_project(
        env["session_factory"],
        env["workspace"],
    )

    task_id = create_task(
        env["session_factory"],
        project_id,
        "generate_script",
        status="completed",
        attempts=1,
    )

    relative_path = create_asset(
        env["session_factory"],
        project_id,
        "script/script.md",
    )

    env["checkpoint_manager"].register_asset(
        project_id=project_id,
        asset_type="script",
        relative_path=relative_path,
        task_id=task_id,
    )

    asset_path = (
        get_project_workspace(
            env["session_factory"],
            project_id,
        )
        / relative_path
    )

    original_asset = get_asset(
        env["session_factory"],
        project_id,
        relative_path,
    )

    # Change the file after its checksum has been recorded.
    asset_path.write_bytes(b"corrupted asset content")

    env["startup_manager"].start()

    task = get_task(
        env["session_factory"],
        task_id,
    )

    asset = get_asset(
        env["session_factory"],
        project_id,
        relative_path,
    )

    assert task["status"] == "failed"
    assert asset["status"] == "invalid"

    assert (
        asset["checksum_sha256"]
        == original_asset["checksum_sha256"]
    )


# ---------------------------------------------------------
# TEST 7: STARTUP REPORT AND LOCK LIFECYCLE
# ---------------------------------------------------------

def test_startup_report_and_lock_lifecycle(
    tmp_path,
    monkeypatch,
):
    env = create_test_environment(tmp_path, monkeypatch)

    project_id = create_project(
        env["session_factory"],
        env["workspace"],
    )

    create_task(
        env["session_factory"],
        project_id,
        "research",
        status="running",
        attempts=1,
    )

    report = env["startup_manager"].start()

    assert env["startup_manager"].is_started is True
    assert env["process_lock"].is_acquired is True

    assert report["projects_inspected"] == 1
    assert report["recovery_plans_created"] == 1
    assert report["ready_tasks"] == 1

    assert "started_at" in report
    assert "completed_at" in report

    env["startup_manager"].shutdown()

    assert env["process_lock"].is_acquired is False
    assert env["startup_manager"].is_started is False


# ---------------------------------------------------------
# TEST 8: STARTUP FAILURE RELEASES PROCESS LOCK
# ---------------------------------------------------------

def test_startup_failure_releases_process_lock(
    tmp_path,
    monkeypatch,
):
    env = create_test_environment(tmp_path, monkeypatch)

    def failing_database_initializer():
        raise RuntimeError(
            "Simulated database initialization failure"
        )

    startup_manager = StartupManager(
        process_lock=env["process_lock"],
        recovery_manager=env["recovery_manager"],
        database_initializer=failing_database_initializer,
        logging_configurator=lambda: None,
    )

    ACTIVE_STARTUP_MANAGERS.append(startup_manager)

    try:
        startup_manager.start()

        raise AssertionError(
            "Startup should have raised RuntimeError"
        )

    except RuntimeError as exc:
        assert (
            "Simulated database initialization failure"
            in str(exc)
        )

    assert env["process_lock"].is_acquired is False
    assert startup_manager.is_started is False
    assert startup_manager.startup_report is None


# ---------------------------------------------------------
# RESOURCE CLEANUP
# ---------------------------------------------------------

def cleanup_test_resources(monkeypatch):
    """Release locks and close engines regardless of test outcome."""

    # Restore patched settings first.
    if monkeypatch is not None:
        monkeypatch.undo()

    # Release any process locks left behind by failed assertions.
    for manager in ACTIVE_STARTUP_MANAGERS:
        try:
            manager.shutdown()
        except Exception as exc:
            print(
                f"[CLEANUP WARNING] Could not shut down manager: {exc}"
            )

    ACTIVE_STARTUP_MANAGERS.clear()

    # Dispose all SQLite engines before temporary directories vanish.
    for engine in ACTIVE_ENGINES:
        try:
            engine.dispose()
        except Exception as exc:
            print(
                f"[CLEANUP WARNING] Could not dispose engine: {exc}"
            )

    ACTIVE_ENGINES.clear()


# ---------------------------------------------------------
# MANUAL TEST RUNNER
# ---------------------------------------------------------


def run_all_tests():
    tests = [
        test_startup_recovers_interrupted_task,
        test_startup_invalidates_completed_downstream_tasks,
        test_valid_assets_pass_integrity_verification,
        test_deleted_assets_are_not_restored,
        test_missing_asset_triggers_recovery,
        test_corrupted_asset_triggers_recovery,
        test_startup_report_and_lock_lifecycle,
        test_startup_failure_releases_process_lock,
    ]

    failures = []

    print(
        "\n========== HIVE STARTUP RECOVERY INTEGRATION TESTS ==========\n"
    )

    from _pytest.monkeypatch import MonkeyPatch

    for test in tests:
        ACTIVE_ENGINES.clear()
        ACTIVE_STARTUP_MANAGERS.clear()

        patch = MonkeyPatch()

        try:
            with TemporaryDirectory() as temp_dir:
                try:
                    test(Path(temp_dir), patch)
                finally:
                    # IMPORTANT:
                    # Close managers and database engines
                    # BEFORE TemporaryDirectory is deleted.
                    cleanup_test_resources(patch)

            print(f"[PASS] {test.__name__}")

        except Exception as exc:
            failures.append((test.__name__, exc))

            print(
                f"[FAIL] {test.__name__}: "
                f"{type(exc).__name__}: {exc}"
            )

    print(
        "\n========================= SUMMARY =========================\n"
    )

    passed = len(tests) - len(failures)

    print(f"Passed: {passed}/{len(tests)}")
    print(f"Failed: {len(failures)}/{len(tests)}")

    if failures:
        print("\nFAILED TESTS:")

        for test_name, error in failures:
            print(f"\n- {test_name}")
            print(f"  {type(error).__name__}: {error}")

        raise SystemExit(1)

    print("\nAll startup recovery integration tests passed!")

if __name__ == "__main__":
    run_all_tests()
