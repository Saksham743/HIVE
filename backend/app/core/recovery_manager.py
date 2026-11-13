
from sqlalchemy import select

from backend.app.core.checkpoint_manager import CheckpointManager
from backend.app.core.logger import logger
from backend.app.database.models import Asset, Project, Task
from backend.app.database.session import SessionLocal


class RecoveryManager:
    """
    Inspects interrupted HIVE projects, validates their saved assets,
    and prepares a safe task-level recovery plan.

    This manager does not execute pipeline tasks.
    """

    def __init__(
        self,
        session_factory=SessionLocal,
        checkpoint_manager=None,
    ):
        self.session_factory = session_factory
        self.checkpoint_manager = (
            checkpoint_manager
            or CheckpointManager(session_factory)
        )

    # ---------------------------------------------------------
    # PROJECT DISCOVERY
    # ---------------------------------------------------------

    def inspect_incomplete_projects(self) -> list[dict]:
        """Find all projects that have not completed."""

        with self.session_factory() as session:
            projects = session.scalars(
                select(Project)
                .where(Project.status != "completed")
                .order_by(Project.updated_at)
            ).all()

            results = []

            for project in projects:
                tasks = session.scalars(
                    select(Task).where(
                        Task.project_id == project.id
                    )
                ).all()

                counts = {
                    "pending": 0,
                    "running": 0,
                    "completed": 0,
                    "failed": 0,
                    "skipped": 0,
                }

                for task in tasks:
                    counts[task.status] = (
                        counts.get(task.status, 0) + 1
                    )

                results.append({
                    "project_id": project.id,
                    "name": project.name,
                    "topic": project.topic,
                    "status": project.status,
                    "current_stage": project.current_stage,
                    "task_counts": counts,
                    "total_tasks": len(tasks),
                })

            return results

    # ---------------------------------------------------------
    # RECOVERY PREPARATION
    # ---------------------------------------------------------

    def prepare_recovery(
        self,
        project_id: str,
        process_confirmed_stopped: bool = False,
    ) -> dict:
        """
        Inspect a project and prepare its recovery plan.

        The caller must confirm that the previous HIVE process
        has stopped before any recovery state is modified.
        """

        with self.session_factory() as session:
            project = session.get(Project, project_id)

            if project is None:
                raise ValueError(
                    f"Project not found: {project_id}"
                )

            tasks = session.scalars(
                select(Task)
                .where(Task.project_id == project_id)
                .order_by(Task.created_at, Task.task_key)
            ).all()

            assets = session.scalars(
                select(Asset)
                .where(Asset.project_id == project_id)
                .order_by(Asset.created_at)
            ).all()

            project_info = {
                "id": project.id,
                "name": project.name,
                "topic": project.topic,
                "status": project.status,
            }

            task_info = [
                {
                    "id": task.id,
                    "task_key": task.task_key,
                    "module": task.module,
                    "operation": task.operation,
                    "status": task.status,
                    "attempts": task.attempts,
                    "max_attempts": task.max_attempts,
                    "depends_on": task.depends_on or [],
                    "error_message": task.error_message,
                }
                for task in tasks
            ]

            asset_info = [
                {
                    "id": asset.id,
                    "task_id": asset.task_id,
                    "asset_type": asset.asset_type,
                    "relative_path": asset.relative_path,
                    "status": asset.status,
                }
                for asset in assets
            ]

        # Do not modify task states while another HIVE process
        # might still be executing them.

        if not process_confirmed_stopped:
            return {
                "project": project_info,
                "recovery_status": "blocked",
                "reason": (
                    "The previous HIVE process has not been "
                    "confirmed stopped."
                ),
                "requires_process_confirmation": True,
                "tasks": task_info,
                "assets": asset_info,
            }

        logger.info(
            "Recovery inspection started | project_id={}",
            project_id,
        )

        # -----------------------------------------------------
        # 1. VERIFY EXISTING ASSETS
        # -----------------------------------------------------

        asset_results = []
        invalid_tasks = set()
        deleted_assets = []

        for asset in asset_info:

            # An intentionally deleted asset must not be
            # accidentally restored by verification.
            if asset["status"] == "deleted":
                deleted_assets.append(asset["relative_path"])
                continue

            result = self.checkpoint_manager.verify_asset(
                project_id=project_id,
                relative_path=asset["relative_path"],
            )

            asset_results.append(result)

            if not result["valid"] and asset["task_id"]:
                invalid_tasks.add(asset["task_id"])

        # -----------------------------------------------------
        # 2. IDENTIFY INTERRUPTED TASKS
        # -----------------------------------------------------

        interrupted_tasks = {
            task["task_key"]
            for task in task_info
            if task["status"] == "running"
        }

        # Completed tasks whose output assets are no longer
        # valid must be eligible for recovery.
        invalid_asset_task_keys = {
            task["task_key"]
            for task in task_info
            if (
                task["id"] in invalid_tasks
                and task["status"] == "completed"
            )
        }

        recovery_roots = (
            interrupted_tasks | invalid_asset_task_keys
        )

        # -----------------------------------------------------
        # 3. INVALIDATE COMPLETED DEPENDENTS
        # -----------------------------------------------------

        # If an upstream task must be repeated, completed
        # downstream tasks may contain stale results.
        # Preserve their files, but make their tasks retryable.

        task_by_key = {
            task["task_key"]: task
            for task in task_info
        }

        invalidated_tasks = set(recovery_roots)

        changed = True

        while changed:
            changed = False

            for task in task_info:
                if task["task_key"] in invalidated_tasks:
                    continue

                if task["status"] != "completed":
                    continue

                if any(
                    dependency in invalidated_tasks
                    for dependency in task["depends_on"]
                ):
                    invalidated_tasks.add(task["task_key"])
                    changed = True

        # -----------------------------------------------------
        # 4. UPDATE INTERRUPTED / INVALID TASKS
        # -----------------------------------------------------

        task_updates = []

        with self.session_factory() as session:
            project = session.get(Project, project_id)

            for task_key in invalidated_tasks:
                task = session.scalar(
                    select(Task).where(
                        Task.project_id == project_id,
                        Task.task_key == task_key,
                    )
                )

                if task is None:
                    continue

                old_status = task.status

                if task_key in interrupted_tasks:
                    reason = (
                        "Execution interrupted because the "
                        "previous HIVE process stopped."
                    )
                elif task_key in invalid_asset_task_keys:
                    reason = (
                        "One or more output assets failed "
                        "integrity verification."
                    )
                else:
                    reason = (
                        "A dependency requires recovery. "
                        "This task must be checked again."
                    )

                task.status = "failed"
                task.error_message = reason
                task.completed_at = None

                retry_available = (
                    task.attempts < task.max_attempts
                )

                task_updates.append({
                    "task_key": task.task_key,
                    "previous_status": old_status,
                    "new_status": "failed",
                    "reason": reason,
                    "attempts": task.attempts,
                    "max_attempts": task.max_attempts,
                    "retry_available": retry_available,
                })

            # Refresh project status without resetting task
            # attempt counts or changing asset records.
            all_tasks = session.scalars(
                select(Task).where(
                    Task.project_id == project_id
                )
            ).all()

            if all_tasks and all(
                task.status in ("completed", "skipped")
                for task in all_tasks
            ):
                project.status = "completed"
                project.current_stage = None
                project.last_error = None

            elif any(
                task.status == "failed"
                and task.attempts >= task.max_attempts
                for task in all_tasks
            ):
                project.status = "failed"
                project.current_stage = None

            elif any(
                task.status == "running"
                for task in all_tasks
            ):
                project.status = "running"

            elif any(
                task.status == "failed"
                and task.attempts < task.max_attempts
                for task in all_tasks
            ):
                project.status = "paused"
                project.current_stage = None

            elif any(task.attempts > 0 for task in all_tasks):
                project.status = "paused"
                project.current_stage = None

            else:
                project.status = "created"

            session.commit()

        # -----------------------------------------------------
        # 5. IDENTIFY WHAT CAN RUN NEXT
        # -----------------------------------------------------

        with self.session_factory() as session:
            ready_tasks = session.scalars(
                select(Task)
                .where(
                    Task.project_id == project_id,
                    Task.status.in_(["pending", "failed"]),
                    Task.attempts < Task.max_attempts,
                )
                .order_by(Task.created_at, Task.task_key)
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
                {
                    "task_key": task.task_key,
                    "module": task.module,
                    "operation": task.operation,
                    "attempts": task.attempts,
                    "max_attempts": task.max_attempts,
                }
                for task in ready_tasks
                if all(
                    dependency in completed_keys
                    for dependency in (task.depends_on or [])
                )
            ]

            blocked = [
                {
                    "task_key": task.task_key,
                    "blocked_by": [
                        dependency
                        for dependency in (task.depends_on or [])
                        if dependency not in completed_keys
                    ],
                }
                for task in ready_tasks
                if not all(
                    dependency in completed_keys
                    for dependency in (task.depends_on or [])
                )
            ]

            updated_project = session.get(Project, project_id)

            final_status = updated_project.status

        logger.info(
            "Recovery inspection completed | project_id={} | "
            "updated_tasks={} | ready_tasks={}",
            project_id,
            len(task_updates),
            len(ready),
        )

        return {
            "project": project_info,
            "recovery_status": "prepared",
            "project_status": final_status,
            "verified_assets": asset_results,
            "deleted_assets_skipped": deleted_assets,
            "task_updates": task_updates,
            "ready_tasks": ready,
            "blocked_tasks": blocked,
            "next_action": (
                "The Pipeline Orchestrator can now execute "
                "the ready tasks."
                if ready
                else "No tasks are currently ready to execute."
            ),
        }
