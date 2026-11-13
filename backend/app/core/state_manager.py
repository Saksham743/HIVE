from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.core.logger import logger
from backend.app.database import session
from backend.app.database.models import (
    Project,
    Task,
    utc_now,
)
from backend.app.database.session import SessionLocal


class StateManager:
    """Manages persistent project and task execution state."""

    def __init__(
    self,
    session_factory=SessionLocal,
    workspace_root: Path | None = None,
    ):
        self.session_factory = session_factory
        self.workspace_root = Path(
            workspace_root or settings.workspaces_dir
        )

    @staticmethod
    def _project_dict(project: Project) -> dict:
        return {
            "id": project.id,
            "name": project.name,
            "topic": project.topic,
            "status": project.status,
            "current_stage": project.current_stage,
            "workspace_path": project.workspace_path,
            "last_error": project.last_error,
            "created_at": project.created_at.isoformat(),
            "updated_at": project.updated_at.isoformat(),
        }

    @staticmethod
    def _task_dict(task: Task) -> dict:
        return {
            "id": task.id,
            "project_id": task.project_id,
            "task_key": task.task_key,
            "module": task.module,
            "operation": task.operation,
            "status": task.status,
            "attempts": task.attempts,
            "max_attempts": task.max_attempts,
            "depends_on": task.depends_on,
            "input_data": task.input_data,
            "output_data": task.output_data,
            "error_message": task.error_message,
        }

    
    def _refresh_project_status(self, session: Session, project_id: str) -> None:
        """Update project status based on the state of its tasks."""

        project = session.get(Project, project_id)

        if project is None:
            raise ValueError(f"Project not found: {project_id}")

        tasks = session.scalars(
            select(Task).where(Task.project_id == project_id)
        ).all()

        if not tasks:
            return

        if all(task.status in ("completed", "skipped") for task in tasks):
            project.status = "completed"
            project.current_stage = None
            project.last_error = None

        elif any(
            task.status == "failed"
            and task.attempts >= task.max_attempts
            for task in tasks
        ):
            project.status = "failed"

        elif any(task.status == "running" for task in tasks):
            project.status = "running"

        elif any(task.attempts > 0 for task in tasks):
            project.status = "running"

        else:
            project.status = "created"


    def create_project(self, name: str, topic: str) -> dict:
        """Create a project and its workspace."""

        if not name.strip() or not topic.strip():
            raise ValueError("Project name and topic are required.")

        self.workspace_root.mkdir(parents=True, exist_ok=True)

        project_id = str(uuid4())
        workspace = self.workspace_root / project_id

        workspace.mkdir(parents=True, exist_ok=False)

        try:
            with self.session_factory() as session:
                project = Project(
                    id=project_id,
                    name=name.strip(),
                    topic=topic.strip(),
                    status="created",
                    workspace_path=str(workspace.resolve()),
                )

                session.add(project)
                session.commit()
                session.refresh(project)

                result = self._project_dict(project)

            logger.info(
                "Project created | project_id={} | topic={}",
                project_id,
                topic,
            )

            return result

        except Exception:
            if workspace.exists():
                workspace.rmdir()
            raise

    def register_task(
        self,
        project_id: str,
        task_key: str,
        module: str,
        operation: str,
        input_data: dict | None = None,
        depends_on: list[str] | None = None,
        max_attempts: int = 3,
    ) -> dict:
        """Register a task. Repeated identical registration is safe."""

        if not task_key.strip():
            raise ValueError("Task key cannot be empty.")

        if not module.strip() or not operation.strip():
            raise ValueError("Module and operation are required.")

        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1.")

        dependencies = depends_on or []

        if task_key in dependencies:
            raise ValueError("A task cannot depend on itself.")

        if len(dependencies) != len(set(dependencies)):
            raise ValueError("Duplicate task dependencies are not allowed.")

        with self.session_factory() as session:
            project = session.get(Project, project_id)

            if project is None:
                raise ValueError(f"Project not found: {project_id}")

            existing = session.scalar(
                select(Task).where(
                    Task.project_id == project_id,
                    Task.task_key == task_key,
                )
            )

            if existing:
                same_definition = (
                    existing.module == module
                    and existing.operation == operation
                    and existing.input_data == (input_data or {})
                    and existing.depends_on == dependencies
                    and existing.max_attempts == max_attempts
                )

                if not same_definition:
                    raise ValueError(
                        f"Task '{task_key}' already exists "
                        "with a different definition."
                    )

                return self._task_dict(existing)

            task = Task(
                project_id=project_id,
                task_key=task_key,
                module=module,
                operation=operation,
                input_data=input_data or {},
                depends_on=dependencies,
                max_attempts=max_attempts,
                status="pending",
            )

            session.add(task)

            session.flush()
            self._refresh_project_status(session, project_id)

            session.commit()
            session.refresh(task)

            result = self._task_dict(task)

        logger.info(
            "Task registered | project_id={} | task={}",
            project_id,
            task_key,
        )

        return result

    def start_task(self, project_id: str, task_key: str) -> dict:
        """Start an eligible task and increment its attempt count."""

        with self.session_factory() as session:
            task = session.scalar(
                select(Task).where(
                    Task.project_id == project_id,
                    Task.task_key == task_key,
                )
            )

            if task is None:
                raise ValueError(f"Task not found: {task_key}")

            if task.status == "completed":
                raise ValueError("Completed tasks cannot be restarted.")

            if task.status == "running":
                raise ValueError("Task is already running.")

            if task.status not in ("pending", "failed"):
                raise ValueError(
                    f"Task cannot start from status: {task.status}"
                )

            if task.attempts >= task.max_attempts:
                raise ValueError("Task has exhausted its retry limit.")

            # Verify that all dependencies exist and are completed.
            if task.depends_on:
                dependencies = session.scalars(
                    select(Task).where(
                        Task.project_id == project_id,
                        Task.task_key.in_(task.depends_on),
                    )
                ).all()

                dependency_map = {
                    dependency.task_key: dependency
                    for dependency in dependencies
                }

                missing = [
                    key for key in task.depends_on
                    if key not in dependency_map
                ]

                if missing:
                    raise ValueError(
                        f"Missing dependencies: {missing}"
                    )

                incomplete = [
                    key for key in task.depends_on
                    if dependency_map[key].status != "completed"
                ]

                if incomplete:
                    raise ValueError(
                        f"Dependencies are not completed: {incomplete}"
                    )

            now = utc_now()

            task.status = "running"
            task.attempts += 1
            task.started_at = now
            task.completed_at = None
            task.error_message = None

            project = session.get(Project, project_id)
            project.status = "running"
            project.current_stage = task.module
            project.last_error = None

            session.commit()
            session.refresh(task)

            result = self._task_dict(task)

        logger.info(
            "Task started | project_id={} | task={} | attempt={}",
            project_id,
            task_key,
            result["attempts"],
        )

        return result

    def complete_task(
        self,
        project_id: str,
        task_key: str,
        output_data: dict | None = None,
    ) -> dict:
        """Mark a running task completed."""

        with self.session_factory() as session:
            task = session.scalar(
                select(Task).where(
                    Task.project_id == project_id,
                    Task.task_key == task_key,
                )
            )

            if task is None:
                raise ValueError(f"Task not found: {task_key}")

            # Safe to call again after a successful completion.
            if task.status == "completed":
                return self._task_dict(task)

            if task.status != "running":
                raise ValueError(
                    "Only running tasks can be completed."
                )

            task.status = "completed"
            task.output_data = output_data or {}
            task.completed_at = utc_now()
            task.error_message = None

            session.flush()
            self._refresh_project_status(session, project_id)

            session.commit()
            session.refresh(task)

            result = self._task_dict(task)

        logger.info(
            "Task completed | project_id={} | task={}",
            project_id,
            task_key,
        )

        return result

    def fail_task(
        self,
        project_id: str,
        task_key: str,
        error_message: str,
    ) -> dict:
        """Record a task failure and determine retry eligibility."""

        with self.session_factory() as session:
            task = session.scalar(
                select(Task).where(
                    Task.project_id == project_id,
                    Task.task_key == task_key,
                )
            )

            if task is None:
                raise ValueError(f"Task not found: {task_key}")

            if task.status != "running":
                raise ValueError(
                    "Only running tasks can be marked as failed."
                )

            task.status = "failed"
            task.error_message = error_message
            task.completed_at = None

            project = session.get(Project, project_id)

            retry_available = task.attempts < task.max_attempts

            if not retry_available:
                project.last_error = f"{task_key}: {error_message}"

            session.flush()
            self._refresh_project_status(session, project_id)

            session.commit()
            session.refresh(task)

            result = self._task_dict(task)
            result["retry_available"] = retry_available

        logger.error(
            "Task failed | project_id={} | task={} | retry_available={}",
            project_id,
            task_key,
            retry_available,
        )

        return result

    def get_ready_tasks(self, project_id: str) -> list[dict]:
        """Return pending or retryable tasks with completed dependencies."""

        with self.session_factory() as session:
            tasks = session.scalars(
                select(Task).where(
                    Task.project_id == project_id,
                    Task.status.in_(["pending", "failed"]),
                    Task.attempts < Task.max_attempts,
                ).order_by(Task.created_at, Task.task_key)
            ).all()

            completed_keys = set(
                session.scalars(
                    select(Task.task_key).where(
                        Task.project_id == project_id,
                        Task.status == "completed",
                    )
                ).all()
            )

            ready = [
                self._task_dict(task)
                for task in tasks
                if all(
                    dependency in completed_keys
                    for dependency in (task.depends_on or [])
                )
            ]

            return ready

    def get_incomplete_projects(self) -> list[dict]:
        """Find projects that have not been completed."""

        with self.session_factory() as session:
            projects = session.scalars(
                select(Project)
                .where(Project.status != "completed")
                .order_by(Project.updated_at)
            ).all()

            return [
                self._project_dict(project)
                for project in projects
            ]

    def get_project(self, project_id: str) -> dict | None:
        with self.session_factory() as session:
            project = session.get(Project, project_id)

            if project is None:
                return None

            return self._project_dict(project)

    def get_task(self, project_id: str, task_key: str) -> dict | None:
        with self.session_factory() as session:
            task = session.scalar(
                select(Task).where(
                    Task.project_id == project_id,
                    Task.task_key == task_key,
                )
            )

            if task is None:
                return None

            return self._task_dict(task)

    def get_project_summary(self, project_id: str) -> dict:
        """Return project progress counts."""

        with self.session_factory() as session:
            project = session.get(Project, project_id)

            if project is None:
                raise ValueError(f"Project not found: {project_id}")

            tasks = session.scalars(
                select(Task).where(Task.project_id == project_id)
            ).all()

            counts = {
                "pending": 0,
                "running": 0,
                "completed": 0,
                "failed": 0,
                "skipped": 0,
            }

            for task in tasks:
                counts[task.status] += 1

            total = len(tasks)
            completed = counts["completed"]

            return {
                "project": self._project_dict(project),
                "total_tasks": total,
                "completed_tasks": completed,
                "progress_percent": (
                    round(completed / total * 100, 2)
                    if total else 0
                ),
                "task_counts": counts,
            }
