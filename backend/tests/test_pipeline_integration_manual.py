
import tempfile
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from backend.app.database.base import Base
from backend.app.database.models import (
    Project,
    Task,
    Asset,
    Checkpoint,
)

from backend.app.core.state_manager import StateManager
from backend.app.core.checkpoint_manager import CheckpointManager
from backend.app.core.pipeline_executor import (
    PipelineExecutor,
    ArtifactOutput,
    TaskResult,
)


def create_test_environment(root: Path):
    """Create an isolated database and workspace."""

    database_path = root / "integration_test.db"
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
                name="HIVE Integration Test",
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

    return {
        "engine": engine,
        "sessions": SessionFactory,
        "workspace": workspace,
        "project_id": project_id,
        "state": state_manager,
        "checkpoints": checkpoint_manager,
        "executor": executor,
    }


def test_successful_execution():
    with tempfile.TemporaryDirectory() as temp:
        env = create_test_environment(Path(temp))

        try:
            project_id = env["project_id"]
            state = env["state"]
            executor = env["executor"]
            workspace = env["workspace"]
            sessions = env["sessions"]

            task = state.register_task(
                project_id=project_id,
                task_key="research.collect_facts",
                module="research",
                operation="collect_facts",
                max_attempts=3,
            )

            def research_handler(context):
                assert context.project["id"] == project_id
                assert context.task["status"] == "running"

                output = context.workspace_path / "research.json"

                output.write_text(
                    '{"facts": ["Constantinople fell in 1453"]}',
                    encoding="utf-8",
                )

                return TaskResult(
                    output_data={"facts_count": 1},
                    artifacts=[
                        ArtifactOutput(
                            asset_type="research",
                            relative_path="research.json",
                            metadata={"format": "json"},
                        )
                    ],
                )

            executor.register_handler(
                "research",
                "collect_facts",
                research_handler,
            )

            result = executor.execute_task(
                project_id=project_id,
                task_key="research.collect_facts",
            )

            assert result.output_data["facts_count"] == 1

            persisted_task = state.get_task(
                project_id,
                "research.collect_facts",
            )

            assert persisted_task["status"] == "completed"
            assert persisted_task["attempts"] == 1
            assert persisted_task["output_data"]["facts_count"] == 1

            project = state.get_project(project_id)
            assert project["status"] == "completed"

            with sessions() as session:
                assets = session.scalars(
                    select(Asset).where(
                        Asset.project_id == project_id
                    )
                ).all()

                checkpoints = session.scalars(
                    select(Checkpoint).where(
                        Checkpoint.project_id == project_id
                    )
                ).all()

                assert len(assets) == 1
                assert len(checkpoints) == 1

                asset = assets[0]
                checkpoint = checkpoints[0]

                assert asset.task_id == task["id"]
                assert asset.status == "available"
                assert asset.relative_path == "research.json"
                assert asset.size_bytes > 0
                assert len(asset.checksum_sha256) == 64

                assert checkpoint.task_id == task["id"]
                assert checkpoint.checkpoint_key == (
                    "task:research.collect_facts:"
                    "outputs_registered"
                )

                assert checkpoint.payload["output_data"] == {
                    "facts_count": 1
                }

            verification = env["checkpoints"].verify_asset(
                project_id,
                "research.json",
            )

            assert verification["valid"] is True

            print("PASS: Real task execution")
            print("PASS: Task and project persisted as completed")
            print("PASS: Asset and checkpoint persisted")
            print("PASS: SHA-256 integrity verification")

        finally:
            env["engine"].dispose()


def test_handler_failure_and_retry():
    with tempfile.TemporaryDirectory() as temp:
        env = create_test_environment(Path(temp))

        try:
            project_id = env["project_id"]
            state = env["state"]
            executor = env["executor"]

            state.register_task(
                project_id=project_id,
                task_key="research.retry_test",
                module="research",
                operation="retry_test",
                max_attempts=2,
            )

            calls = {"count": 0}

            def handler(context):
                calls["count"] += 1

                if calls["count"] == 1:
                    raise RuntimeError("Temporary research failure")

                return TaskResult(
                    output_data={"recovered": True}
                )

            executor.register_handler(
                "research",
                "retry_test",
                handler,
            )

            try:
                executor.execute_task(
                    project_id,
                    "research.retry_test",
                )
            except RuntimeError as exc:
                assert "Temporary research failure" in str(exc)
            else:
                raise AssertionError(
                    "First execution should fail."
                )

            failed = state.get_task(
                project_id,
                "research.retry_test",
            )

            assert failed["status"] == "failed"
            assert failed["attempts"] == 1

            ready_keys = {
                task["task_key"]
                for task in state.get_ready_tasks(project_id)
            }

            assert "research.retry_test" in ready_keys

            result = executor.execute_task(
                project_id,
                "research.retry_test",
            )

            assert result.output_data["recovered"] is True

            completed = state.get_task(
                project_id,
                "research.retry_test",
            )

            assert completed["status"] == "completed"
            assert completed["attempts"] == 2
            assert calls["count"] == 2

            print("PASS: Real failure persisted")
            print("PASS: Failed task eligible for retry")
            print("PASS: Retry completed successfully")

        finally:
            env["engine"].dispose()


def test_missing_artifact_prevents_completion():
    with tempfile.TemporaryDirectory() as temp:
        env = create_test_environment(Path(temp))

        try:
            project_id = env["project_id"]
            state = env["state"]
            executor = env["executor"]
            sessions = env["sessions"]

            state.register_task(
                project_id=project_id,
                task_key="images.generate",
                module="image_generation",
                operation="generate",
            )

            def missing_file_handler(context):
                return TaskResult(
                    artifacts=[
                        ArtifactOutput(
                            asset_type="image",
                            relative_path="images/missing.png",
                        )
                    ]
                )

            executor.register_handler(
                "image_generation",
                "generate",
                missing_file_handler,
            )

            try:
                executor.execute_task(
                    project_id,
                    "images.generate",
                )
            except FileNotFoundError:
                pass
            else:
                raise AssertionError(
                    "Missing artifact should fail."
                )

            task = state.get_task(
                project_id,
                "images.generate",
            )

            assert task["status"] == "failed"

            with sessions() as session:
                assets = session.scalars(
                    select(Asset).where(
                        Asset.project_id == project_id
                    )
                ).all()

                checkpoints = session.scalars(
                    select(Checkpoint).where(
                        Checkpoint.project_id == project_id
                    )
                ).all()

                assert len(assets) == 0
                assert len(checkpoints) == 0

            print("PASS: Missing artifact rejected")
            print("PASS: No invalid asset/checkpoint persisted")

        finally:
            env["engine"].dispose()


def test_dependency_execution_order():
    with tempfile.TemporaryDirectory() as temp:
        env = create_test_environment(Path(temp))

        try:
            project_id = env["project_id"]
            state = env["state"]
            executor = env["executor"]

            state.register_task(
                project_id=project_id,
                task_key="research.collect",
                module="research",
                operation="collect",
            )

            state.register_task(
                project_id=project_id,
                task_key="scripting.write",
                module="scripting",
                operation="write",
                depends_on=["research.collect"],
            )

            executor.register_handler(
                "research",
                "collect",
                lambda context: TaskResult(
                    output_data={"research_done": True}
                ),
            )

            executor.register_handler(
                "scripting",
                "write",
                lambda context: TaskResult(
                    output_data={"script_done": True}
                ),
            )

            ready = state.get_ready_tasks(project_id)
            ready_keys = {task["task_key"] for task in ready}

            assert "research.collect" in ready_keys
            assert "scripting.write" not in ready_keys

            executor.execute_task(
                project_id,
                "research.collect",
            )

            ready = state.get_ready_tasks(project_id)
            ready_keys = {task["task_key"] for task in ready}

            assert "scripting.write" in ready_keys

            executor.execute_task(
                project_id,
                "scripting.write",
            )

            summary = state.get_project_summary(project_id)

            assert summary["completed_tasks"] == 2
            assert summary["progress_percent"] == 100.0
            assert summary["project"]["status"] == "completed"

            print("PASS: Dependencies enforced")
            print("PASS: Two-stage pipeline completed")

        finally:
            env["engine"].dispose()


if __name__ == "__main__":
    test_successful_execution()
    test_handler_failure_and_retry()
    test_missing_artifact_prevents_completion()
    test_dependency_execution_order()

    print("\nALL REAL DATABASE INTEGRATION TESTS PASSED!")
