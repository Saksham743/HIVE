
from __future__ import annotations

import subprocess
import sys
import time

from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------
# PATHS
# ---------------------------------------------------------

ROOT_DIR = Path(__file__).resolve().parents[1]

REPORT_DIR = ROOT_DIR / "logs" / "regression"


# ---------------------------------------------------------
# TEST SUITES
# ---------------------------------------------------------

TEST_SUITES = [
    ("State Manager", "backend.tests.test_state_manager_manual"),
    ("Checkpoint Manager", "backend.tests.test_checkpoint_manager_manual"),
    ("Recovery Manager", "backend.tests.test_recovery_manager_manual"),
    ("Process Lock Manager", "backend.tests.test_process_lock_manager_manual"),
    ("Pipeline Executor", "backend.tests.test_pipeline_executor_manual"),
    ("Pipeline Integration", "backend.tests.test_pipeline_integration_manual"),
    ("Pipeline Runner", "backend.tests.test_pipeline_runner_manual"),
    ("Startup Manager", "backend.tests.test_startup_manager_manual"),
    (
        "Startup Recovery Integration",
        "backend.tests.test_startup_recovery_integration_manual",
    ),
]


# ---------------------------------------------------------
# EXECUTION
# ---------------------------------------------------------

def run_suite(name: str, module: str) -> dict:
    print()
    print("=" * 65)
    print(f"RUNNING: {name}")
    print("=" * 65)

    command = [
        sys.executable,
        "-m",
        module,
    ]

    started = time.perf_counter()

    try:
        result = subprocess.run(
            command,
            cwd=ROOT_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        duration = time.perf_counter() - started

        return {
            "name": name,
            "module": module,
            "status": "PASSED" if result.returncode == 0 else "FAILED",
            "return_code": result.returncode,
            "duration": duration,
            "output": result.stdout,
        }

    except Exception as exc:
        duration = time.perf_counter() - started

        return {
            "name": name,
            "module": module,
            "status": "ERROR",
            "return_code": -1,
            "duration": duration,
            "output": repr(exc),
        }


# ---------------------------------------------------------
# REPORT GENERATION
# ---------------------------------------------------------

def generate_report(results: list[dict], started_at: datetime) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = started_at.strftime("%Y%m%d_%H%M%S")

    report_path = REPORT_DIR / f"regression_{timestamp}.txt"

    passed = sum(r["status"] == "PASSED" for r in results)
    failed = sum(r["status"] == "FAILED" for r in results)
    errors = sum(r["status"] == "ERROR" for r in results)

    total_duration = sum(r["duration"] for r in results)

    lines = [
        "=" * 75,
        "HIVE REGRESSION TEST REPORT",
        "=" * 75,
        f"Started: {started_at.isoformat(timespec='seconds')}",
        f"Python: {sys.version.split()[0]}",
        f"Project: {ROOT_DIR}",
        "",
        "SUMMARY",
        "-" * 75,
        f"Total suites: {len(results)}",
        f"Passed: {passed}",
        f"Failed: {failed}",
        f"Errors: {errors}",
        f"Total duration: {total_duration:.2f} seconds",
        "",
        "SUITE RESULTS",
        "-" * 75,
    ]

    for result in results:
        lines.extend([
            "",
            f"Suite: {result['name']}",
            f"Module: {result['module']}",
            f"Status: {result['status']}",
            f"Exit code: {result['return_code']}",
            f"Duration: {result['duration']:.2f} seconds",
            "",
            "OUTPUT",
            "-" * 75,
            result["output"],
            "",
        ])

    lines.extend([
        "=" * 75,
        "END OF REPORT",
        "=" * 75,
    ])

    report_path.write_text(
        "\n".join(lines),
        encoding="utf-8",
    )

    return report_path


# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------

def main() -> int:
    started_at = datetime.now()

    print()
    print("=" * 65)
    print("HIVE UNIFIED REGRESSION TEST RUNNER")
    print("=" * 65)

    print(f"Project: {ROOT_DIR}")
    print(f"Python: {sys.executable}")
    print(f"Suites: {len(TEST_SUITES)}")

    results = []

    for index, (name, module) in enumerate(TEST_SUITES, start=1):
        print()
        print(f"[{index}/{len(TEST_SUITES)}] {name}")

        result = run_suite(name, module)
        results.append(result)

        status = result["status"]
        duration = result["duration"]

        symbol = "PASS" if status == "PASSED" else "FAIL"

        print(f"[{symbol}] {name} | {duration:.2f}s")

        if status != "PASSED":
            print("Output preview:")
            print(result["output"][-3000:])

    report_path = generate_report(results, started_at)

    passed = sum(r["status"] == "PASSED" for r in results)
    failed = sum(r["status"] != "PASSED" for r in results)

    print()
    print("=" * 65)
    print("HIVE REGRESSION RESULTS")
    print("=" * 65)

    print(f"Total suites : {len(results)}")
    print(f"Passed       : {passed}")
    print(f"Failed       : {failed}")

    print()
    print(f"Report saved: {report_path}")

    if failed:
        print()
        print("REGRESSION FAILED")
        return 1

    print()
    print("ALL REGRESSION TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
