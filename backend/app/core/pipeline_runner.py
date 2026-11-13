
from backend.app.core.pipeline_executor import PipelineExecutor
from backend.app.core.logger import logger


class PipelineRunner:
    """
    Coordinates execution of all ready tasks in a project.

    Responsibilities:
    - Discover tasks whose dependencies are satisfied.
    - Execute tasks through PipelineExecutor.
    - Discover newly unlocked tasks after execution.
    - Continue unrelated tasks after failures.
    - Prevent repeated attempts of the same task in one run.
    - Return a structured execution summary.

    Execution is sequential in this version.
    """

    def __init__(self, executor: PipelineExecutor):
        if not isinstance(executor, PipelineExecutor):
            raise TypeError(
                "PipelineRunner requires a PipelineExecutor instance."
            )

        self.executor = executor
        self.state_manager = executor.state_manager

    # ---------------------------------------------------------
    # PROJECT EXECUTION
    # ---------------------------------------------------------

    def run_project(self, project_id: str) -> dict:
        """
        Run all currently executable tasks for a project.

        Each task is attempted at most once during a single run.

        Failed tasks that remain eligible for retry are deferred
        until the next run. This prevents infinite retry loops.

        Returns a structured execution summary.
        """

        if not isinstance(project_id, str) or not project_id.strip():
            raise ValueError("A valid project_id is required.")

        project = self.state_manager.get_project(project_id)

        if project is None:
            raise ValueError(f"Project not found: {project_id}")

        logger.info(
            "Pipeline run started | project_id={} | topic={}",
            project_id,
            project["topic"],
        )

        attempted_task_keys = set()
        task_results = []

        tasks_succeeded = 0
        tasks_failed = 0
        tasks_errored = 0

        # -----------------------------------------------------
        # EXECUTION LOOP
        # -----------------------------------------------------

        while True:

            # Always refresh ready tasks.
            # Successful tasks may have unlocked new dependencies.
            ready_tasks = self.state_manager.get_ready_tasks(
                project_id
            )

            # Find the next task not yet attempted in this run.
            next_task = next(
                (
                    task
                    for task in ready_tasks
                    if task["task_key"] not in attempted_task_keys
                ),
                None,
            )

            # No more executable tasks for this run.
            if next_task is None:
                break

            task_key = next_task["task_key"]
            attempts_before = next_task["attempts"]

            # Mark before execution to prevent repeated attempts
            # if execution fails and the task remains retryable.
            attempted_task_keys.add(task_key)

            logger.info(
                "Pipeline runner dispatching task | "
                "project_id={} | task_key={}",
                project_id,
                task_key,
            )

            # -------------------------------------------------
            # EXECUTE TASK
            # -------------------------------------------------

            try:
                result = self.executor.execute_task(
                    project_id=project_id,
                    task_key=task_key,
                )

                tasks_succeeded += 1

                task_results.append({
                    "task_key": task_key,
                    "status": "completed",
                    "attempts": attempts_before + 1,
                    "output_data": result.output_data,
                    "error": None,
                })

                logger.info(
                    "Pipeline runner task succeeded | "
                    "project_id={} | task_key={}",
                    project_id,
                    task_key,
                )

            except Exception as exc:

                # Inspect persistent state to distinguish an actual
                # execution failure from a preparation/configuration
                # error, such as a missing registered handler.
                latest_task = self.state_manager.get_task(
                    project_id=project_id,
                    task_key=task_key,
                )

                attempts_after = (
                    latest_task["attempts"]
                    if latest_task is not None
                    else attempts_before
                )

                task_status = (
                    latest_task["status"]
                    if latest_task is not None
                    else "unknown"
                )

                execution_started = attempts_after > attempts_before

                if task_status == "failed" and execution_started:
                    outcome = "failed"
                    tasks_failed += 1
                else:
                    outcome = "error"
                    tasks_errored += 1

                error_message = f"{type(exc).__name__}: {exc}"

                task_results.append({
                    "task_key": task_key,
                    "status": outcome,
                    "attempts": attempts_after,
                    "output_data": None,
                    "error": error_message,
                })

                logger.error(
                    "Pipeline runner task did not complete | "
                    "project_id={} | task_key={} | status={} | error={}",
                    project_id,
                    task_key,
                    outcome,
                    error_message,
                )

                # Continue the loop.
                # Other independent tasks may still be executable.

        # -----------------------------------------------------
        # FINAL PROJECT SUMMARY
        # -----------------------------------------------------

        project_summary = self.state_manager.get_project_summary(
            project_id
        )

        project_status = project_summary["project"]["status"]

        if project_status == "completed":
            run_status = "completed"
        elif project_status == "failed":
            run_status = "failed"
        else:
            run_status = "incomplete"

        summary = {
            "project_id": project_id,
            "run_status": run_status,
            "tasks_attempted": len(attempted_task_keys),
            "tasks_succeeded": tasks_succeeded,
            "tasks_failed": tasks_failed,
            "tasks_errored": tasks_errored,
            "task_results": task_results,
            "project_summary": project_summary,
        }

        logger.info(
            "Pipeline run finished | project_id={} | "
            "status={} | attempted={} | succeeded={} | "
            "failed={} | errors={}",
            project_id,
            run_status,
            len(attempted_task_keys),
            tasks_succeeded,
            tasks_failed,
            tasks_errored,
        )

        return summary
