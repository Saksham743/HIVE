
import tempfile
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from backend.app.database.base import Base
from backend.app.database.models import Project

from backend.app.core.state_manager import StateManager
from backend.app.core.checkpoint_manager import CheckpointManager
from backend.app.core.pipeline_executor import (
    PipelineExecutor,
    TaskResult,
)
from backend.app.core.pipeline_runner import PipelineRunner


# ---------------------------------------------------------
# TEST ENVIRONMENT
# ---------------------------------------------------------

def create_test_environment(root: Path):
    """Create an isolated database and project workspace."""

    database_path = root / "runner_test.db"
    workspace = root / "workspace"
    workspace.mkdir()

    engine = create_engine(
        f"sqlite:///{database_path.as_posix()}",
        connect_args={"check_same_thread": False},
        echo=False,
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)

    SessionFactory = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )

    project_id = str(uuid4())

    with SessionFactory() as session:
        session.add(
            Project(
                id=project_id,
                name="HIVE Pipeline Runner Test",
                topic="The Fall of Constantinople",
                status="created",
                workspace_path=str(workspace),
            )
        )
        session.commit()

    state_manager = StateManager(
        session_factory=SessionFactory
    )

    checkpoint_manager = CheckpointManager(
        session_factory=SessionFactory
    )

    executor = PipelineExecutor(
        state_manager=state_manager,
        checkpoint_manager=checkpoint_manager,
    )

    runner = PipelineRunner(executor=executor)

    return {
        "engine": engine,
        "sessions": SessionFactory,
        "workspace": workspace,
        "project_id": project_id,
        "state": state_manager,
        "checkpoints": checkpoint_manager,
        "executor": executor,
        "runner": runner,
    }


def register_simple_task(
    state_manager,
    project_id,
    task_key,
    module,
    operation,
    depends_on=None,
    max_attempts=3,
):
    """Register a task using the real StateManager."""

    return state_manager.register_task(
        project_id=project_id,
        task_key=task_key,
        module=module,
        operation=operation,
        depends_on=depends_on or [],
        max_attempts=max_attempts,
    )


# ---------------------------------------------------------
# TEST 1: SEQUENTIAL DEPENDENT EXECUTION
# ---------------------------------------------------------

def test_sequential_dependent_execution():
    with tempfile.TemporaryDirectory() as temp:
        env = create_test_environment(Path(temp))

        try:
            project_id = env["project_id"]
            state = env["state"]
            executor = env["executor"]
            runner = env["runner"]

            execution_order = []

            register_simple_task(
                state,
                project_id,
                "research.collect",
                "research",
                "collect",
            )

            register_simple_task(
                state,
                project_id,
                "scripting.write",
                "scripting",
                "write",
                depends_on=["research.collect"],
            )

            register_simple_task(
                state,
                project_id,
                "scene_planning.create",
                "scene_planning",
                "create",
                depends_on=["scripting.write"],
            )

            def research_handler(context):
                execution_order.append("research.collect")

                return TaskResult(
                    output_data={"research": "completed"}
                )

            def scripting_handler(context):
                execution_order.append("scripting.write")

                return TaskResult(
                    output_data={"script": "completed"}
                )

            def scene_handler(context):
                execution_order.append("scene_planning.create")

                return TaskResult(
                    output_data={"scenes": 10}
                )

            executor.register_handler(
                "research",
                "collect",
                research_handler,
            )

            executor.register_handler(
                "scripting",
                "write",
                scripting_handler,
            )

            executor.register_handler(
                "scene_planning",
                "create",
                scene_handler,
            )

            summary = runner.run_project(project_id)

            assert execution_order == [
                "research.collect",
                "scripting.write",
                "scene_planning.create",
            ]

            assert summary["run_status"] == "completed"
            assert summary["tasks_attempted"] == 3
            assert summary["tasks_succeeded"] == 3
            assert summary["tasks_failed"] == 0
            assert summary["tasks_errored"] == 0

            assert summary["project_summary"]["completed_tasks"] == 3
            assert summary["project_summary"]["progress_percent"] == 100.0

            print("PASS: Sequential dependent execution")
            print("PASS: Newly unlocked tasks discovered automatically")

        finally:
            env["engine"].dispose()


# ---------------------------------------------------------
# TEST 2: CONTINUE AFTER A TASK FAILURE
# ---------------------------------------------------------

def test_runner_continues_after_failure():
    with tempfile.TemporaryDirectory() as temp:
        env = create_test_environment(Path(temp))

        try:
            project_id = env["project_id"]
            state = env["state"]
            executor = env["executor"]
            runner = env["runner"]

            execution_order = []

            register_simple_task(
                state,
                project_id,
                "a.failing_task",
                "research",
                "fail",
            )

            register_simple_task(
                state,
                project_id,
                "b.independent_task",
                "scripting",
                "succeed",
            )

            def failing_handler(context):
                execution_order.append("a.failing_task")

                raise RuntimeError("Simulated task failure")

            def successful_handler(context):
                execution_order.append("b.independent_task")

                return TaskResult(
                    output_data={"success": True}
                )

            executor.register_handler(
                "research",
                "fail",
                failing_handler,
            )

            executor.register_handler(
                "scripting",
                "succeed",
                successful_handler,
            )

            summary = runner.run_project(project_id)

            assert execution_order == [
                "a.failing_task",
                "b.independent_task",
            ]

            assert summary["tasks_attempted"] == 2
            assert summary["tasks_succeeded"] == 1
            assert summary["tasks_failed"] == 1
            assert summary["tasks_errored"] == 0

            assert summary["run_status"] == "incomplete"

            failed_task = state.get_task(
                project_id,
                "a.failing_task",
            )

            successful_task = state.get_task(
                project_id,
                "b.independent_task",
            )

            assert failed_task["status"] == "failed"
            assert successful_task["status"] == "completed"

            print("PASS: Runner continues after task failure")
            print("PASS: Independent task executes successfully")

        finally:
            env["engine"].dispose()


# ---------------------------------------------------------
# TEST 3: NO REPEATED RETRIES IN ONE RUN
# ---------------------------------------------------------


def test_task_retries_on_next_run():
    with tempfile.TemporaryDirectory() as temp:
        env = create_test_environment(Path(temp))

        try:
            project_id = env["project_id"]
            state = env["state"]
            executor = env["executor"]
            runner = env["runner"]

            calls = {"count": 0}

            register_simple_task(
                state,
                project_id,
                "research.retry",
                "research",
                "retry",
                max_attempts=3,
            )

            def handler(context):
                calls["count"] += 1

                if calls["count"] == 1:
                    raise RuntimeError("Temporary failure")

                return TaskResult(
                    output_data={"recovered": True}
                )

            executor.register_handler(
                "research",
                "retry",
                handler,
            )

            # First run: task fails.
            first_run = runner.run_project(project_id)

            task = state.get_task(
                project_id,
                "research.retry",
            )

            assert calls["count"] == 1
            assert task["attempts"] == 1
            assert task["status"] == "failed"
            assert first_run["tasks_failed"] == 1

            # Second run: the failed task is retried.
            second_run = runner.run_project(project_id)

            task = state.get_task(
                project_id,
                "research.retry",
            )

            assert calls["count"] == 2
            assert task["attempts"] == 2
            assert task["status"] == "completed"

            assert second_run["tasks_attempted"] == 1
            assert second_run["tasks_succeeded"] == 1
            assert second_run["run_status"] == "completed"

            print("PASS: No repeated retries within one run")
            print("PASS: Failed task retried on the next run")
            print("PASS: Subsequent retry completed successfully")

        finally:
            env["engine"].dispose()


# ---------------------------------------------------------
# TEST 4: MISSING HANDLER
# ---------------------------------------------------------

def test_missing_handler_is_recorded_as_execution_error():
    with tempfile.TemporaryDirectory() as temp:
        env = create_test_environment(Path(temp))

        try:
            project_id = env["project_id"]
            state = env["state"]
            runner = env["runner"]

            register_simple_task(
                state,
                project_id,
                "research.missing_handler",
                "research",
                "missing_handler",
            )

            summary = runner.run_project(project_id)

            task = state.get_task(
                project_id,
                "research.missing_handler",
            )

            assert summary["tasks_attempted"] == 1
            assert summary["tasks_errored"] == 1
            assert summary["tasks_failed"] == 0

            # A missing handler is a preparation error.
            # The task should not be marked as running or failed.
            assert task["status"] == "pending"
            assert task["attempts"] == 0

            assert summary["task_results"][0]["status"] == "error"

            print("PASS: Missing handler handled safely")
            print("PASS: Task state remains pending")

        finally:
            env["engine"].dispose()


# ---------------------------------------------------------
# TEST 5: NO READY TASKS
# ---------------------------------------------------------

def test_project_with_no_ready_tasks():
    with tempfile.TemporaryDirectory() as temp:
        env = create_test_environment(Path(temp))

        try:
            project_id = env["project_id"]
            state = env["state"]
            runner = env["runner"]

            # This task depends on a task that has not been registered.
            register_simple_task(
                state,
                project_id,
                "scripting.write",
                "scripting",
                "write",
                depends_on=["research.not_registered"],
            )

            summary = runner.run_project(project_id)

            assert summary["tasks_attempted"] == 0
            assert summary["tasks_succeeded"] == 0
            assert summary["tasks_failed"] == 0
            assert summary["tasks_errored"] == 0

            assert summary["run_status"] == "incomplete"

            task = state.get_task(
                project_id,
                "scripting.write",
            )

            assert task["status"] == "pending"
            assert task["attempts"] == 0

            print("PASS: Project with no ready tasks handled")

        finally:
            env["engine"].dispose()


# ---------------------------------------------------------
# TEST 6: PROJECT WITH NO TASKS
# ---------------------------------------------------------

def test_empty_project():
    with tempfile.TemporaryDirectory() as temp:
        env = create_test_environment(Path(temp))

        try:
            summary = env["runner"].run_project(
                env["project_id"]
            )

            assert summary["tasks_attempted"] == 0
            assert summary["tasks_succeeded"] == 0
            assert summary["run_status"] == "incomplete"

            print("PASS: Empty project handled safely")

        finally:
            env["engine"].dispose()


# ---------------------------------------------------------
# TEST 7: INVALID PROJECT ID
# ---------------------------------------------------------

def test_invalid_project_id():
    with tempfile.TemporaryDirectory() as temp:
        env = create_test_environment(Path(temp))

        try:
            try:
                env["runner"].run_project("")
            except ValueError:
                pass
            else:
                raise AssertionError(
                    "Expected invalid project ID error."
                )

            try:
                env["runner"].run_project("nonexistent-project")
            except ValueError:
                pass
            else:
                raise AssertionError(
                    "Expected missing project error."
                )

            print("PASS: Invalid project IDs rejected")
            print("PASS: Nonexistent project rejected")

        finally:
            env["engine"].dispose()


# ---------------------------------------------------------
# TEST RUNNER
# ---------------------------------------------------------

if __name__ == "__main__":
    test_sequential_dependent_execution()
    test_runner_continues_after_failure()
    test_task_retries_on_next_run()
    test_missing_handler_is_recorded_as_execution_error()
    test_project_with_no_ready_tasks()
    test_empty_project()
    test_invalid_project_id()

    print("\nALL PIPELINE RUNNER TESTS PASSED!")
