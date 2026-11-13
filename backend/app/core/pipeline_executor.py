
from dataclasses import dataclass, field
from pathlib import Path
import pathlib
from typing import Callable

from backend.app.core.state_manager import StateManager
from backend.app.core.checkpoint_manager import CheckpointManager
from backend.app.core.logger import logger


@dataclass(frozen=True)
class TaskContext:
    """Information a module needs to execute one task."""

    project: dict
    task: dict
    workspace_path: pathlib.Path


@dataclass(frozen=True)
class ArtifactOutput:
    """A file produced by a task."""

    asset_type: str
    relative_path: str
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class TaskResult:
    """Structured result returned by a task handler."""

    output_data: dict = field(default_factory=dict)
    artifacts: list[ArtifactOutput] = field(default_factory=list)


TaskHandler = Callable[[TaskContext], TaskResult]


class HandlerNotRegisteredError(Exception):
    """Raised when no handler exists for a task."""


class PipelineExecutor:
    """
    Dispatches pipeline tasks to registered module handlers.
    """

    def __init__(
        self,
        state_manager: StateManager | None = None,
        checkpoint_manager: CheckpointManager | None = None,
    ):
        self.state_manager = state_manager or StateManager()
        self.checkpoint_manager = (
            checkpoint_manager or CheckpointManager()
        )

        self._handlers: dict[tuple[str, str], TaskHandler] = {}

    # ---------------------------------------------------------
    # HANDLER REGISTRY
    # ---------------------------------------------------------

    def register_handler(
        self,
        module: str,
        operation: str,
        handler: TaskHandler,
    ) -> None:
        """Register a handler for a module and operation."""

        if not module.strip() or not operation.strip():
            raise ValueError("Module and operation are required.")

        if not callable(handler):
            raise TypeError("Handler must be callable.")

        key = (module.strip(), operation.strip())

        if key in self._handlers:
            raise ValueError(
                f"Handler already registered for "
                f"'{key[0]}.{key[1]}'."
            )

        self._handlers[key] = handler

        logger.info(
            "Pipeline handler registered | module={} | operation={}",
            module,
            operation,
        )

    def get_handler(
        self,
        module: str,
        operation: str,
    ) -> TaskHandler:
        """Retrieve the handler responsible for a task."""

        key = (module, operation)
        handler = self._handlers.get(key)

        if handler is None:
            raise HandlerNotRegisteredError(
                f"No handler registered for "
                f"'{module}.{operation}'."
            )

        return handler

    # ---------------------------------------------------------
    # TASK CONTEXT
    # ---------------------------------------------------------

    def _build_task_context(
        self,
        project_id: str,
        task_key: str,
    ) -> TaskContext:
        """Retrieve project and task information for a handler."""

        project = self.state_manager.get_project(project_id)

        if project is None:
            raise ValueError(f"Project not found: {project_id}")

        task = self.state_manager.get_task(
            project_id=project_id,
            task_key=task_key,
        )

        if task is None:
            raise ValueError(f"Task not found: {task_key}")

        workspace_path = pathlib.Path(project["workspace_path"]).resolve()

        if not workspace_path.exists():
            raise FileNotFoundError(
                f"Project workspace does not exist: {workspace_path}"
            )

        return TaskContext(
            project=project,
            task=task,
            workspace_path=workspace_path,
        )

    
    # ---------------------------------------------------------
    # TASK PREPARATION
    # ---------------------------------------------------------

    def _prepare_task_execution(
        self,
        project_id: str,
        task_key: str,
    ) -> tuple[TaskContext, TaskHandler]:
        """
        Validate a task, resolve its handler, and mark it as running.

        Returns the task context and its registered handler.
        """

        # 1. Retrieve project and task.
        context = self._build_task_context(
            project_id=project_id,
            task_key=task_key,
        )

        task = context.task

        # 2. Resolve handler BEFORE changing task state.
        handler = self.get_handler(
            module=task["module"],
            operation=task["operation"],
        )

        # 3. Ask StateManager to start the task.
        # StateManager validates dependencies and retry limits.
        self.state_manager.start_task(
            project_id=project_id,
            task_key=task_key,
        )

        # 4. Refresh context so the handler receives the
        # latest task state, including status="running".
        context = self._build_task_context(
            project_id=project_id,
            task_key=task_key,
        )

        logger.info(
            "Pipeline task started | project_id={} | task_key={} | attempt={}",
            project_id,
            task_key,
            context.task["attempts"],
        )

        return context, handler
    
    # ---------------------------------------------------------
    # TASK EXECUTION
    # ---------------------------------------------------------

    def execute_task(
        self,
        project_id: str,
        task_key: str,
    ) -> TaskResult:
        """
        Execute a pipeline task through its complete lifecycle.

        Workflow:
        1. Prepare and start task.
        2. Execute handler.
        3. Validate and register artifacts.
        4. Create checkpoint.
        5. Mark task completed.
        6. Record failures when any execution stage fails.
        """

        # Preparation happens before the failure-handling block.
        # A task that never started must not be marked failed here.
        context, handler = self._prepare_task_execution(
            project_id=project_id,
            task_key=task_key,
        )

        try:
            # 1. Execute handler.
            logger.info(
                "Executing pipeline handler | project_id={} | task_key={}",
                project_id,
                task_key,
            )

            result = handler(context)

            # 2. Validate handler result.
            if not isinstance(result, TaskResult):
                raise TypeError(
                    f"Handler for '{task_key}' must return "
                    f"TaskResult, received {type(result).__name__}."
                )

            if not isinstance(result.output_data, dict):
                raise TypeError(
                    "TaskResult.output_data must be a dictionary."
                )

            # 3. Validate and register artifacts.
            registered_assets = self._validate_and_register_artifacts(
                project_id=project_id,
                context=context,
                result=result,
            )

            # 4. Create a checkpoint after all artifacts
            # have been registered successfully.
            checkpoint_payload = {
                "task_key": task_key,
                "module": context.task["module"],
                "operation": context.task["operation"],
                "output_data": result.output_data,
                "registered_assets": [
                    {
                        "asset_type": asset["asset_type"],
                        "relative_path": asset["relative_path"],
                    }
                    for asset in registered_assets
                ],
            }

            self.checkpoint_manager.create_checkpoint(
                project_id=project_id,
                checkpoint_key=f"task:{task_key}:outputs_registered",
                payload=checkpoint_payload,
                task_id=context.task["id"],
            )

            logger.info(
                "Pipeline task checkpoint created | "
                "project_id={} | task_key={}",
                project_id,
                task_key,
            )

            # 5. Mark task completed only after registration
            # and checkpoint creation have succeeded.
            self.state_manager.complete_task(
                project_id=project_id,
                task_key=task_key,
                output_data=result.output_data,
            )

            logger.info(
                "Pipeline task completed | project_id={} | task_key={}",
                project_id,
                task_key,
            )

            return result

        except Exception as exc:
            # 6. Record failure from any execution stage.
            error_message = f"{type(exc).__name__}: {exc}"

            try:
                failure_info = self.state_manager.fail_task(
                    project_id=project_id,
                    task_key=task_key,
                    error_message=error_message,
                )

                logger.error(
                    "Pipeline task failed | project_id={} | "
                    "task_key={} | retry_available={}",
                    project_id,
                    task_key,
                    failure_info.get("retry_available", False),
                )

            except Exception:
                # Preserve the original exception if failure
                # persistence also encounters an error.
                logger.exception(
                    "Could not persist task failure | "
                    "project_id={} | task_key={}",
                    project_id,
                    task_key,
                )

            raise


    
    # ---------------------------------------------------------
    # ARTIFACT VALIDATION AND REGISTRATION
    # ---------------------------------------------------------

    def _validate_and_register_artifacts(
        self,
        project_id: str,
        context: TaskContext,
        result: TaskResult,
    ) -> list[dict]:
        """
        Validate every artifact before registering any of them.

        Ensures:
        - The result contains a valid artifact list.
        - Every artifact has a valid definition.
        - Paths remain inside the project workspace.
        - Every file exists and is non-empty.
        - Duplicate artifact paths are rejected.

        Validated artifacts are registered through CheckpointManager.
        """

        if not isinstance(result, TaskResult):
            raise TypeError("Expected a TaskResult.")

        if not isinstance(result.artifacts, list):
            raise TypeError("TaskResult.artifacts must be a list.")

        workspace_path = context.workspace_path.resolve()

        validated_artifacts = []
        seen_paths = set()

        # -----------------------------------------------------
        # PHASE 1: VALIDATE ALL ARTIFACTS
        # -----------------------------------------------------

        for index, artifact in enumerate(result.artifacts):

            if not isinstance(artifact, ArtifactOutput):
                raise TypeError(
                    f"Artifact at index {index} must be "
                    f"an ArtifactOutput."
                )

            if (
                not isinstance(artifact.asset_type, str)
                or not artifact.asset_type.strip()
            ):
                raise ValueError(
                    f"Artifact at index {index} has an invalid asset_type."
                )

            if (
                not isinstance(artifact.relative_path, str)
                or not artifact.relative_path.strip()
            ):
                raise ValueError(
                    f"Artifact at index {index} has an invalid relative_path."
                )

            if not isinstance(artifact.metadata, dict):
                raise TypeError(
                    f"Artifact metadata at index {index} must be a dictionary."
                )

            relative_path = Path(artifact.relative_path)

            if relative_path.is_absolute():
                raise ValueError(
                    f"Absolute artifact paths are not allowed: "
                    f"{artifact.relative_path}"
                )

            # Resolve the path to prevent traversal and symlink escapes.
            resolved_path = (
                workspace_path / relative_path
            ).resolve()

            try:
                resolved_path.relative_to(workspace_path)
            except ValueError:
                raise ValueError(
                    f"Artifact path escapes project workspace: "
                    f"{artifact.relative_path}"
                )

            normalized_path = str(resolved_path).casefold()

            if normalized_path in seen_paths:
                raise ValueError(
                    f"Duplicate artifact path: {artifact.relative_path}"
                )

            seen_paths.add(normalized_path)

            if not resolved_path.exists():
                raise FileNotFoundError(
                    f"Artifact does not exist: {artifact.relative_path}"
                )

            if not resolved_path.is_file():
                raise ValueError(
                    f"Artifact is not a regular file: "
                    f"{artifact.relative_path}"
                )

            if resolved_path.stat().st_size == 0:
                raise ValueError(
                    f"Artifact is empty: {artifact.relative_path}"
                )

            validated_artifacts.append(artifact)

        # -----------------------------------------------------
        # PHASE 2: REGISTER VALIDATED ARTIFACTS
        # -----------------------------------------------------

        registered_assets = []

        for artifact in validated_artifacts:

            asset = self.checkpoint_manager.register_asset(
                project_id=project_id,
                asset_type=artifact.asset_type,
                relative_path=artifact.relative_path,
                task_id=context.task["id"],
                metadata=artifact.metadata,
            )

            registered_assets.append(asset)

            logger.info(
                "Pipeline artifact registered | project_id={} | "
                "task_key={} | asset_type={} | path={}",
                project_id,
                context.task["task_key"],
                artifact.asset_type,
                artifact.relative_path,
            )

        return registered_assets

    @property
    def registered_handlers(self) -> list[str]:
        """Return registered handler identifiers."""

        return [
            f"{module}.{operation}"
            for module, operation in sorted(self._handlers)
        ]
