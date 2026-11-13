
from datetime import datetime, timezone
from typing import Any, Callable

from backend.app.config import settings
from backend.app.core.logger import logger, configure_logging
from backend.app.core.process_lock_manager import ProcessLockManager
from backend.app.core.recovery_manager import RecoveryManager
from backend.app.database.session import init_db

class StartupManager:
    """
    Coordinates HIVE startup and prepares recovery for incomplete projects.

    StartupManager does not execute pipeline tasks. It prepares the
    application, discovers incomplete projects, and produces recovery plans.

    The process lock remains held until shutdown() is called.
    """

    def __init__(
        self,
        process_lock: ProcessLockManager | None = None,
        recovery_manager: RecoveryManager | None = None,
        database_initializer: Callable = init_db,
        logging_configurator: Callable = configure_logging,
    ):
        self.process_lock = process_lock or ProcessLockManager()
        self.recovery_manager = recovery_manager or RecoveryManager()
        self.database_initializer = database_initializer
        self.logging_configurator = logging_configurator

        self._started = False
        self._startup_report: dict[str, Any] | None = None

    # -----------------------------------------------------
    # PROPERTIES
    # -----------------------------------------------------

    @property
    def is_started(self) -> bool:
        return self._started

    @property
    def startup_report(self) -> dict[str, Any] | None:
        return self._startup_report

    # -----------------------------------------------------
    # STARTUP
    # -----------------------------------------------------

    def start(self) -> dict[str, Any]:
        """
        Initialize HIVE and prepare recovery plans.

        Startup sequence:
        1. Ensure application directories exist.
        2. Acquire the exclusive process lock.
        3. Initialize logging.
        4. Initialize the database.
        5. Discover incomplete projects.
        6. Prepare recovery for each incomplete project.
        7. Build and display the startup report.

        If startup fails, the process lock is released.
        """

        if self._started:
            return self._startup_report

        started_at = datetime.now(timezone.utc)

        try:
            # 1. Ensure required directories exist.
            settings.ensure_directories()

            # 2. Acquire the exclusive process lock.
            self.process_lock.acquire()

            # 3. Configure logging after acquiring the lock.
            self.logging_configurator()

            logger.info("HIVE startup initiated")

            logger.info(
                "Process lock acquired | session_id={}",
                self.process_lock.session_id,
            )

            logger.info("Application directories verified")

            # 4. Initialize the database.
            self.database_initializer()

            logger.info("Database initialized")

            # 5. Discover incomplete projects.
            incomplete_projects = (
                self.recovery_manager.inspect_incomplete_projects()
            )

            logger.info(
                "Incomplete projects discovered | count={}",
                len(incomplete_projects),
            )

            # 6. Prepare recovery for every incomplete project.
            recovery_results = []

            for project in incomplete_projects:
                project_id = project["project_id"]

                logger.info(
                    "Preparing project recovery | project_id={} | name={}",
                    project_id,
                    project["name"],
                )

                result = self.recovery_manager.prepare_recovery(
                    project_id=project_id,
                    process_confirmed_stopped=True,
                )

                recovery_results.append(result)

            # 7. Build the startup report.
            self._startup_report = self._build_report(
                started_at=started_at,
                incomplete_projects=incomplete_projects,
                recovery_results=recovery_results,
            )

            self._started = True

            logger.info(
                "HIVE startup completed | projects={} | ready_tasks={}",
                self._startup_report["projects_inspected"],
                self._startup_report["ready_tasks"],
            )

            self.display_report()

            return self._startup_report

        except Exception:
            logger.exception("HIVE startup failed")

            # Release the lock only if this instance acquired it.
            if self.process_lock.is_acquired:
                self.process_lock.release()

            self._started = False
            self._startup_report = None

            raise

    # -----------------------------------------------------
    # STARTUP REPORT
    # -----------------------------------------------------

    @staticmethod
    def _build_report(
        started_at: datetime,
        incomplete_projects: list[dict],
        recovery_results: list[dict],
    ) -> dict[str, Any]:

        ready_tasks = []
        task_updates = []
        blocked_projects = []

        for result in recovery_results:
            project = result.get("project", {})
            project_name = project.get("name", "Unknown project")

            if result.get("recovery_status") == "blocked":
                blocked_projects.append(
                    {
                        "project_name": project_name,
                        "reason": result.get("reason"),
                    }
                )

            for task in result.get("ready_tasks", []):
                ready_tasks.append(
                    {
                        "project_id": project.get("id"),
                        "project_name": project_name,
                        "task_key": task["task_key"],
                        "module": task["module"],
                        "operation": task["operation"],
                    }
                )

            for update in result.get("task_updates", []):
                task_updates.append(
                    {
                        "project_id": project.get("id"),
                        "project_name": project_name,
                        "task_key": update["task_key"],
                        "reason": update.get("reason"),
                    }
                )

        return {
            "started_at": started_at.isoformat(),
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "projects_inspected": len(incomplete_projects),
            "recovery_plans_created": len(recovery_results),
            "ready_tasks": len(ready_tasks),
            "updated_tasks": len(task_updates),
            "blocked_projects": blocked_projects,
            "ready_task_details": ready_tasks,
            "task_updates": task_updates,
            "recovery_results": recovery_results,
        }

    def display_report(self) -> None:
        """Display a concise startup and recovery summary."""

        report = self._startup_report

        if report is None:
            return

        print("\n========== HIVE STARTUP ==========")
        print("Status: READY")

        print(
            "Incomplete projects inspected:",
            report["projects_inspected"],
        )

        print(
            "Recovery plans prepared:",
            report["recovery_plans_created"],
        )

        print(
            "Tasks requiring execution:",
            report["ready_tasks"],
        )

        print(
            "Tasks marked for recovery:",
            report["updated_tasks"],
        )

        if report["ready_task_details"]:
            print("\nNEXT TASKS TO EXECUTE:")

            for task in report["ready_task_details"]:
                print(
                    f"  {task['project_name']} | "
                    f"{task['task_key']} | "
                    f"{task['module']}"
                )

        if report["blocked_projects"]:
            print("\nBLOCKED PROJECTS:")

            for project in report["blocked_projects"]:
                print(
                    f"  {project['project_name']} | "
                    f"{project['reason']}"
                )

        if not report["ready_task_details"]:
            print("\nNo tasks are currently ready for execution.")

        print("\nHIVE STARTUP COMPLETE")
        print("=================================\n")

    # -----------------------------------------------------
    # SHUTDOWN
    # -----------------------------------------------------

    def shutdown(self) -> None:
        """Release the process lock and mark HIVE as stopped."""

        if not self.process_lock.is_acquired:
            self._started = False
            self._startup_report = None
            return

        logger.info("HIVE shutdown initiated")

        try:
            self.process_lock.release()

        finally:
            self._started = False
            self._startup_report = None

        logger.info("HIVE shutdown completed")

    # -----------------------------------------------------
    # CONTEXT MANAGER SUPPORT
    # -----------------------------------------------------

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.shutdown()
        return False
