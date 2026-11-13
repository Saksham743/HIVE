
import tempfile
from pathlib import Path

from backend.app.core.pipeline_executor import (
    PipelineExecutor,
    HandlerNotRegisteredError,
    ArtifactOutput,
    TaskResult,
)


# ---------------------------------------------------------
# FAKE MANAGERS
# ---------------------------------------------------------

class FakeStateManager:
    def __init__(self):
        self.task_started = False
        self.failure_info = None

        # Give each fake project its own real, isolated workspace.
        # Keep the TemporaryDirectory object alive for as long as this
        # fake manager exists so the directory is not removed too early.
        self._workspace_tempdir = tempfile.TemporaryDirectory(
            prefix="hive-fake-workspace-"
        )
        self.workspace = Path(self._workspace_tempdir.name)

        self.project = {
            "id": "test-project-id",
            "name": "Test Project",
            "workspace_path": str(self.workspace),
        }

        self.task = {
            "id": "test-task-id",
            "project_id": "test-project-id",
            "task_key": "research.collect_facts",
            "module": "research",
            "operation": "collect_facts",
            "status": "pending",
            "attempts": 0,
        }

    def get_project(self, project_id):
        if project_id != self.project["id"]:
            return None

        return self.project.copy()

    def get_task(self, project_id, task_key):
        if (
            project_id != self.task["project_id"]
            or task_key != self.task["task_key"]
        ):
            return None

        return self.task.copy()

    def start_task(self, project_id, task_key):
        if self.task_started:
            raise ValueError("Task already started.")

        self.task_started = True
        self.task["status"] = "running"
        self.task["attempts"] += 1

    def fail_task(self, project_id, task_key, error_message):
        self.task["status"] = "failed"
        self.task["error_message"] = error_message

        self.failure_info = {
            "retry_available": self.task["attempts"] < 3
        }

        return self.failure_info

    def complete_task(self, project_id, task_key, output_data=None):
        if self.task["status"] != "running":
            raise ValueError("Task is not running.")

        self.task["status"] = "completed"
        self.task["output_data"] = output_data or {}


class FakeCheckpointManager:
    def __init__(self):
        self.registered_assets = []
        self.checkpoints = []

    def register_asset(
        self,
        project_id,
        asset_type,
        relative_path,
        task_id=None,
        metadata=None,
    ):
        asset = {
            "project_id": project_id,
            "asset_type": asset_type,
            "relative_path": relative_path,
            "task_id": task_id,
            "metadata": metadata or {},
            "status": "available",
        }

        self.registered_assets.append(asset)

        return asset

    def create_checkpoint(
        self,
        project_id,
        checkpoint_key,
        payload=None,
        task_id=None,
    ):
        checkpoint = {
            "project_id": project_id,
            "checkpoint_key": checkpoint_key,
            "payload": payload or {},
            "task_id": task_id,
        }

        self.checkpoints.append(checkpoint)

        return checkpoint


# ---------------------------------------------------------
# TEST HELPERS
# ---------------------------------------------------------

def create_executor():
    state_manager = FakeStateManager()

    executor = PipelineExecutor(
        state_manager=state_manager,
        checkpoint_manager=FakeCheckpointManager(),
    )

    return executor, state_manager


def dummy_handler(context):
    return TaskResult(
        output_data={
            "message": "Research completed successfully"
        }
    )


# ---------------------------------------------------------
# CONTEXT CONSTRUCTION TEST
# ---------------------------------------------------------

def test_context_construction():
    executor, _ = create_executor()

    context = executor._build_task_context(
        project_id="test-project-id",
        task_key="research.collect_facts",
    )

    assert context.project["id"] == "test-project-id"
    assert context.task["task_key"] == "research.collect_facts"
    assert context.workspace_path.is_absolute()
    assert context.workspace_path.is_dir()

    print("PASS: Task context construction")


# ---------------------------------------------------------
# TASK PREPARATION TESTS
# ---------------------------------------------------------

def test_successful_task_preparation():
    executor, state_manager = create_executor()

    executor.register_handler(
        "research",
        "collect_facts",
        dummy_handler,
    )

    context, handler = executor._prepare_task_execution(
        project_id="test-project-id",
        task_key="research.collect_facts",
    )

    assert callable(handler)
    assert context.task["status"] == "running"
    assert context.task["attempts"] == 1
    assert state_manager.task_started is True

    print("PASS: Successful task preparation")


def test_missing_handler_does_not_start_task():
    executor, state_manager = create_executor()

    try:
        executor._prepare_task_execution(
            project_id="test-project-id",
            task_key="research.collect_facts",
        )
    except HandlerNotRegisteredError:
        pass
    else:
        raise AssertionError("Expected missing handler error.")

    assert state_manager.task_started is False

    print("PASS: Missing handler prevents task start")


def test_missing_project_does_not_start_task():
    executor, state_manager = create_executor()

    try:
        executor._prepare_task_execution(
            project_id="missing-project",
            task_key="research.collect_facts",
        )
    except ValueError:
        pass
    else:
        raise AssertionError("Expected missing project error.")

    assert state_manager.task_started is False

    print("PASS: Missing project prevents task start")


# ---------------------------------------------------------
# HANDLER EXECUTION TESTS
# ---------------------------------------------------------

def test_successful_handler_execution():
    executor, state_manager = create_executor()

    executor.register_handler(
        "research",
        "collect_facts",
        dummy_handler,
    )

    result = executor.execute_task(
        project_id="test-project-id",
        task_key="research.collect_facts",
    )

    assert isinstance(result, TaskResult)

    assert result.output_data["message"] == (
        "Research completed successfully"
    )

    assert state_manager.task["status"] == "completed"
    assert state_manager.task["attempts"] == 1

    checkpoints = executor.checkpoint_manager.checkpoints

    assert len(checkpoints) == 1

    checkpoint = checkpoints[0]

    assert checkpoint["checkpoint_key"] == (
        "task:research.collect_facts:outputs_registered"
    )

    assert checkpoint["payload"]["output_data"] == result.output_data

    print("PASS: Complete task execution lifecycle")


def test_handler_exception_records_failure():
    executor, state_manager = create_executor()

    def failing_handler(context):
        raise RuntimeError("Simulated research failure")

    executor.register_handler(
        "research",
        "collect_facts",
        failing_handler,
    )

    try:
        executor.execute_task(
            project_id="test-project-id",
            task_key="research.collect_facts",
        )
    except RuntimeError as exc:
        assert str(exc) == "Simulated research failure"
    else:
        raise AssertionError("Expected handler exception.")

    assert state_manager.task["status"] == "failed"

    assert "Simulated research failure" in (
        state_manager.task["error_message"]
    )

    assert state_manager.failure_info == {
        "retry_available": True
    }

    print("PASS: Handler failure is recorded")


def test_invalid_handler_result_records_failure():
    executor, state_manager = create_executor()

    def invalid_handler(context):
        return {"message": "This is not a TaskResult"}

    executor.register_handler(
        "research",
        "collect_facts",
        invalid_handler,
    )

    try:
        executor.execute_task(
            project_id="test-project-id",
            task_key="research.collect_facts",
        )
    except TypeError:
        pass
    else:
        raise AssertionError("Expected invalid result TypeError.")

    assert state_manager.task["status"] == "failed"
    assert "TaskResult" in state_manager.task["error_message"]

    assert state_manager.failure_info == {
        "retry_available": True
    }

    print("PASS: Invalid handler result is rejected")


# ---------------------------------------------------------
# ARTIFACT VALIDATION TESTS
# ---------------------------------------------------------

def test_successful_artifact_registration():
    with tempfile.TemporaryDirectory() as temp_dir:
        executor, state_manager = create_executor()

        workspace = Path(temp_dir)
        state_manager.project["workspace_path"] = str(workspace)

        artifact_file = workspace / "research.json"

        artifact_file.write_text(
            '{"facts": ["Fact 1", "Fact 2"]}',
            encoding="utf-8",
        )

        context = executor._build_task_context(
            project_id="test-project-id",
            task_key="research.collect_facts",
        )

        result = TaskResult(
            output_data={"facts_count": 2},
            artifacts=[
                ArtifactOutput(
                    asset_type="research",
                    relative_path="research.json",
                    metadata={"format": "json"},
                )
            ],
        )

        registered = executor._validate_and_register_artifacts(
            project_id="test-project-id",
            context=context,
            result=result,
        )

        assert len(registered) == 1
        assert registered[0]["asset_type"] == "research"
        assert registered[0]["relative_path"] == "research.json"

        print("PASS: Successful artifact registration")


def test_missing_artifact_is_rejected():
    with tempfile.TemporaryDirectory() as temp_dir:
        executor, state_manager = create_executor()

        state_manager.project["workspace_path"] = temp_dir

        context = executor._build_task_context(
            project_id="test-project-id",
            task_key="research.collect_facts",
        )

        result = TaskResult(
            artifacts=[
                ArtifactOutput(
                    asset_type="research",
                    relative_path="missing.json",
                )
            ]
        )

        try:
            executor._validate_and_register_artifacts(
                "test-project-id",
                context,
                result,
            )
        except FileNotFoundError:
            pass
        else:
            raise AssertionError("Expected missing artifact error.")

        assert not executor.checkpoint_manager.registered_assets

        print("PASS: Missing artifact rejected")


def test_empty_artifact_is_rejected():
    with tempfile.TemporaryDirectory() as temp_dir:
        executor, state_manager = create_executor()

        workspace = Path(temp_dir)
        state_manager.project["workspace_path"] = temp_dir

        (workspace / "empty.json").write_text(
            "",
            encoding="utf-8",
        )

        context = executor._build_task_context(
            project_id="test-project-id",
            task_key="research.collect_facts",
        )

        result = TaskResult(
            artifacts=[
                ArtifactOutput(
                    asset_type="research",
                    relative_path="empty.json",
                )
            ]
        )

        try:
            executor._validate_and_register_artifacts(
                "test-project-id",
                context,
                result,
            )
        except ValueError:
            pass
        else:
            raise AssertionError("Expected empty artifact error.")

        assert not executor.checkpoint_manager.registered_assets

        print("PASS: Empty artifact rejected")


def test_artifact_path_traversal_is_rejected():
    with tempfile.TemporaryDirectory() as temp_dir:
        executor, state_manager = create_executor()

        state_manager.project["workspace_path"] = temp_dir

        context = executor._build_task_context(
            project_id="test-project-id",
            task_key="research.collect_facts",
        )

        result = TaskResult(
            artifacts=[
                ArtifactOutput(
                    asset_type="research",
                    relative_path="../outside.json",
                )
            ]
        )

        try:
            executor._validate_and_register_artifacts(
                "test-project-id",
                context,
                result,
            )
        except ValueError:
            pass
        else:
            raise AssertionError("Expected path traversal error.")

        assert not executor.checkpoint_manager.registered_assets

        print("PASS: Artifact path traversal rejected")


def test_all_artifacts_validated_before_registration():
    with tempfile.TemporaryDirectory() as temp_dir:
        executor, state_manager = create_executor()

        workspace = Path(temp_dir)
        state_manager.project["workspace_path"] = temp_dir

        (workspace / "valid.json").write_text(
            '{"valid": true}',
            encoding="utf-8",
        )

        context = executor._build_task_context(
            project_id="test-project-id",
            task_key="research.collect_facts",
        )

        result = TaskResult(
            artifacts=[
                ArtifactOutput(
                    asset_type="research",
                    relative_path="valid.json",
                ),
                ArtifactOutput(
                    asset_type="research",
                    relative_path="missing.json",
                ),
            ]
        )

        try:
            executor._validate_and_register_artifacts(
                "test-project-id",
                context,
                result,
            )
        except FileNotFoundError:
            pass
        else:
            raise AssertionError("Expected missing artifact error.")

        assert not executor.checkpoint_manager.registered_assets

        print("PASS: All artifacts validated before registration")


# ---------------------------------------------------------
# CHECKPOINT FAILURE TEST
# ---------------------------------------------------------

def test_checkpoint_failure_prevents_task_completion():
    executor, state_manager = create_executor()

    def failing_checkpoint(**kwargs):
        raise RuntimeError("Simulated checkpoint failure")

    executor.checkpoint_manager.create_checkpoint = failing_checkpoint

    executor.register_handler(
        "research",
        "collect_facts",
        dummy_handler,
    )

    try:
        executor.execute_task(
            project_id="test-project-id",
            task_key="research.collect_facts",
        )
    except RuntimeError as exc:
        assert str(exc) == "Simulated checkpoint failure"
    else:
        raise AssertionError("Expected checkpoint failure.")

    assert state_manager.task["status"] == "failed"

    assert "Simulated checkpoint failure" in (
        state_manager.task["error_message"]
    )

    assert state_manager.failure_info == {
        "retry_available": True
    }

    print("PASS: Checkpoint failure prevents task completion")


# ---------------------------------------------------------
# TEST RUNNER
# ---------------------------------------------------------

if __name__ == "__main__":
    test_context_construction()

    test_successful_task_preparation()
    test_missing_handler_does_not_start_task()
    test_missing_project_does_not_start_task()

    test_successful_handler_execution()
    test_handler_exception_records_failure()
    test_invalid_handler_result_records_failure()

    test_successful_artifact_registration()
    test_missing_artifact_is_rejected()
    test_empty_artifact_is_rejected()
    test_artifact_path_traversal_is_rejected()
    test_all_artifacts_validated_before_registration()

    test_checkpoint_failure_prevents_task_completion()

    print("\nALL PIPELINE EXECUTOR TESTS PASSED!")
