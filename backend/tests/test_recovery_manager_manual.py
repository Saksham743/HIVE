
from pathlib import Path
from uuid import uuid4

from backend.app.database.models import Asset
from backend.tests.helpers.test_environment import create_test_environment

def create_test_asset(workspace: Path, relative_path: str, content: str):
    path = workspace / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def run_tests():
    print("\n========== HIVE RECOVERY MANAGER TESTS ==========\n")

    env = create_test_environment()
    state, checkpoint, recovery = env.create_managers()

    # -----------------------------------------------------
    # TEST 1: Create an isolated project
    # -----------------------------------------------------

    project = state.create_project(
        name=f"Recovery Manager Test {uuid4().hex[:8]}",
        topic="Recovery system integration test",
    )

    project_id = project["id"]
    workspace = Path(project["workspace_path"])

    print("[PASS] Test project created")

    # -----------------------------------------------------
    # TEST 2: Register a dependency chain
    # -----------------------------------------------------

    tasks = [
        ("research", "research", "collect_facts", []),
        ("script", "scripting", "generate_script", ["research"]),
        ("narration", "narration", "generate_voice", ["script"]),
        ("music", "music", "generate_music", ["narration"]),
    ]

    for key, module, operation, dependencies in tasks:
        state.register_task(
            project_id=project_id,
            task_key=key,
            module=module,
            operation=operation,
            depends_on=dependencies,
            max_attempts=3,
        )

    print("[PASS] Dependency chain registered")

    # -----------------------------------------------------
    # TEST 3: Complete research and script
    # -----------------------------------------------------

    research_path = create_test_asset(
        workspace,
        "research/research.json",
        '{"facts": ["fact one"]}',
    )

    script_path = create_test_asset(
        workspace,
        "scripts/script.txt",
        "This is a test documentary script.",
    )

    state.start_task(project_id, "research")

    checkpoint.register_asset(
        project_id=project_id,
        task_id=state.get_task(project_id, "research")["id"],
        asset_type="research",
        relative_path="research/research.json",
    )

    state.complete_task(
        project_id,
        "research",
        {"asset": "research/research.json"},
    )

    state.start_task(project_id, "script")

    checkpoint.register_asset(
        project_id=project_id,
        task_id=state.get_task(project_id, "script")["id"],
        asset_type="script",
        relative_path="scripts/script.txt",
    )

    state.complete_task(
        project_id,
        "script",
        {"asset": "scripts/script.txt"},
    )

    print("[PASS] Research and script completed")

    # -----------------------------------------------------
    # TEST 4: Simulate interrupted narration
    # -----------------------------------------------------

    state.start_task(project_id, "narration")

    print("[PASS] Narration marked as running")

    # -----------------------------------------------------
    # TEST 5: Block recovery without process confirmation
    # -----------------------------------------------------

    blocked_result = recovery.prepare_recovery(
        project_id=project_id,
        process_confirmed_stopped=False,
    )

    assert blocked_result["recovery_status"] == "blocked"

    print("[PASS] Recovery blocked without process confirmation")

    # -----------------------------------------------------
    # TEST 6: Prepare recovery after process stopped
    # -----------------------------------------------------

    result = recovery.prepare_recovery(
        project_id=project_id,
        process_confirmed_stopped=True,
    )

    assert result["recovery_status"] == "prepared"

    print("[PASS] Recovery preparation successful")

    # -----------------------------------------------------
    # TEST 7: Preserve valid completed assets
    # -----------------------------------------------------

    research_check = checkpoint.verify_asset(
        project_id,
        "research/research.json",
    )

    script_check = checkpoint.verify_asset(
        project_id,
        "scripts/script.txt",
    )

    assert research_check["valid"] is True
    assert script_check["valid"] is True

    assert research_path.exists()
    assert script_path.exists()

    print("[PASS] Valid completed assets preserved")

    # -----------------------------------------------------
    # TEST 8: Verify interrupted task recovery
    # -----------------------------------------------------

    narration = state.get_task(project_id, "narration")

    assert narration["status"] == "failed"
    assert narration["attempts"] == 1

    print("[PASS] Interrupted narration identified")

    # -----------------------------------------------------
    # TEST 9: Verify dependency-aware readiness
    # -----------------------------------------------------

    ready_keys = {
        task["task_key"]
        for task in result["ready_tasks"]
    }

    assert "narration" in ready_keys
    assert "music" not in ready_keys

    print("[PASS] Dependency-aware task readiness")

    # -----------------------------------------------------
    # TEST 10: Corrupt a completed asset
    # -----------------------------------------------------

    # This test uses a separate project so the first
    # recovery test's completed tasks remain untouched.

    corruption_project = state.create_project(
        name=f"Corruption Test {uuid4().hex[:8]}",
        topic="Asset corruption test",
    )

    corruption_id = corruption_project["id"]
    corruption_workspace = Path(
        corruption_project["workspace_path"]
    )

    state.register_task(
        corruption_id,
        "research",
        "research",
        "collect_facts",
    )

    create_test_asset(
        corruption_workspace,
        "research/research.json",
        '{"original": true}',
    )

    state.start_task(corruption_id, "research")

    checkpoint.register_asset(
        project_id=corruption_id,
        task_id=state.get_task(
            corruption_id, "research"
        )["id"],
        asset_type="research",
        relative_path="research/research.json",
    )

    state.complete_task(
        corruption_id,
        "research",
        {"asset": "research/research.json"},
    )

    corrupted_file = (
        corruption_workspace / "research/research.json"
    )

    corrupted_file.write_text(
        '{"corrupted": true}',
        encoding="utf-8",
    )

    corruption_result = recovery.prepare_recovery(
        project_id=corruption_id,
        process_confirmed_stopped=True,
    )

    corrupted_task = state.get_task(
        corruption_id,
        "research",
    )

    assert corrupted_task["status"] == "failed"

    print("[PASS] Corrupted asset triggers task recovery")

    # -----------------------------------------------------
    # TEST 11: Check retry exhaustion
    # -----------------------------------------------------

    retry_project = state.create_project(
        name=f"Retry Limit Test {uuid4().hex[:8]}",
        topic="Retry limit integration test",
    )

    retry_id = retry_project["id"]

    state.register_task(
        retry_id,
        "research",
        "research",
        "collect_facts",
        max_attempts=1,
    )

    state.start_task(retry_id, "research")
    state.fail_task(
        retry_id,
        "research",
        "Simulated first-attempt failure",
    )

    retry_result = recovery.prepare_recovery(
        project_id=retry_id,
        process_confirmed_stopped=True,
    )

    assert retry_result["ready_tasks"] == []

    print("[PASS] Exhausted retry limit respected")

    
    # -----------------------------------------------------
    # TEST 12: Detect missing completed asset
    # -----------------------------------------------------

    missing_project = state.create_project(
        name=f"Missing Asset Test {uuid4().hex[:8]}",
        topic="Missing asset recovery test",
    )

    missing_id = missing_project["id"]
    missing_workspace = Path(missing_project["workspace_path"])

    state.register_task(
        missing_id,
        "research",
        "research",
        "collect_facts",
    )

    missing_path = create_test_asset(
        missing_workspace,
        "research/research.json",
        '{"facts": ["original"]}',
    )

    state.start_task(missing_id, "research")

    checkpoint.register_asset(
        project_id=missing_id,
        task_id=state.get_task(missing_id, "research")["id"],
        asset_type="research",
        relative_path="research/research.json",
    )

    state.complete_task(
        missing_id,
        "research",
        {"asset": "research/research.json"},
    )

    missing_path.unlink()

    missing_result = recovery.prepare_recovery(
        project_id=missing_id,
        process_confirmed_stopped=True,
    )

    missing_task = state.get_task(missing_id, "research")

    assert missing_task["status"] == "failed"
    assert any(
        item["relative_path"] == "research/research.json"
        and item["valid"] is False
        for item in missing_result["verified_assets"]
    )

    print("[PASS] Missing asset triggers recovery")

    # -----------------------------------------------------
    # TEST 13: Deleted assets must never be restored
    # -----------------------------------------------------

    deleted_project = state.create_project(
        name=f"Deleted Asset Test {uuid4().hex[:8]}",
        topic="Deleted asset preservation test",
    )

    deleted_id = deleted_project["id"]
    deleted_workspace = Path(deleted_project["workspace_path"])

    state.register_task(
        deleted_id,
        "research",
        "research",
        "collect_facts",
    )

    create_test_asset(
        deleted_workspace,
        "research/research.json",
        '{"facts": ["preserved file"]}',
    )

    state.start_task(deleted_id, "research")

    checkpoint.register_asset(
        project_id=deleted_id,
        task_id=state.get_task(deleted_id, "research")["id"],
        asset_type="research",
        relative_path="research/research.json",
    )

    state.complete_task(
        deleted_id,
        "research",
        {"asset": "research/research.json"},
    )

    # Simulate an intentional deletion recorded in the database.
    with env.session_factory() as session:
        asset = session.query(Asset).filter_by(
            project_id=deleted_id,
            relative_path="research/research.json",
        ).one()

        asset.status = "deleted"
        session.commit()

    # Direct verification must not restore the deleted status.
    verification = checkpoint.verify_asset(
        deleted_id,
        "research/research.json",
    )

    assert verification["valid"] is False
    assert verification["status"] == "deleted"

    deleted_result = recovery.prepare_recovery(
        project_id=deleted_id,
        process_confirmed_stopped=True,
    )

    deleted_task = state.get_task(deleted_id, "research")

    assert deleted_task["status"] == "completed"
    assert "research/research.json" in (
        deleted_result["deleted_assets_skipped"]
    )

    with env.session_factory() as session:
        asset = session.query(Asset).filter_by(
            project_id=deleted_id,
            relative_path="research/research.json",
        ).one()

        assert asset.status == "deleted"

    print("[PASS] Deleted assets remain deleted")

    # -----------------------------------------------------
    # TEST 14: Invalidate completed downstream tasks
    # -----------------------------------------------------

    chain_project = state.create_project(
        name=f"Dependency Invalidation Test {uuid4().hex[:8]}",
        topic="Dependency recovery test",
    )

    chain_id = chain_project["id"]
    chain_workspace = Path(chain_project["workspace_path"])

    chain_tasks = [
        ("research", "research", "collect_facts", []),
        ("script", "scripting", "generate_script", ["research"]),
        ("narration", "narration", "generate_voice", ["script"]),
        ("music", "music", "generate_music", ["narration"]),
    ]

    for key, module, operation, dependencies in chain_tasks:
        state.register_task(
            chain_id,
            key,
            module,
            operation,
            depends_on=dependencies,
            max_attempts=3,
        )

    # Complete every task and register its output asset.
    for key, module, operation, dependencies in chain_tasks:

        relative_path = f"{key}/{key}.txt"

        create_test_asset(
            chain_workspace,
            relative_path,
            f"Valid output for {key}",
        )

        task = state.get_task(chain_id, key)

        state.start_task(chain_id, key)

        checkpoint.register_asset(
            project_id=chain_id,
            task_id=task["id"],
            asset_type=key,
            relative_path=relative_path,
        )

        state.complete_task(
            chain_id,
            key,
            {"asset": relative_path},
        )

    # Break only the upstream research output.
    research_file = chain_workspace / "research/research.txt"
    research_file.write_text(
        "Modified research output",
        encoding="utf-8",
    )

    chain_result = recovery.prepare_recovery(
        project_id=chain_id,
        process_confirmed_stopped=True,
    )

    expected_invalidated = {
        "research",
        "script",
        "narration",
        "music",
    }

    actual_invalidated = {
        item["task_key"]
        for item in chain_result["task_updates"]
    }

    assert actual_invalidated == expected_invalidated

    # All downstream files still exist. Recovery invalidates
    # their task results, but does not delete the files.
    for key, module, operation, dependencies in chain_tasks:
        if key == "research":
            continue

        assert (
            chain_workspace / f"{key}/{key}.txt"
        ).exists()

    # Only research can run because all downstream tasks
    # depend on it, directly or indirectly.
    chain_ready = {
        task["task_key"]
        for task in chain_result["ready_tasks"]
    }

    assert chain_ready == {"research"}

    for key, module, operation, dependencies in chain_tasks:
        assert state.get_task(chain_id, key)["status"] == "failed"

    print("[PASS] Downstream completed tasks invalidated")
    print("[PASS] Unrelated output files preserved")
    print("[PASS] Recovery resumes from upstream dependency")



    # -----------------------------------------------------
    # FINAL SUMMARY
    # -----------------------------------------------------

    print("\n========== RECOVERY TEST RESULTS ==========")

    print("Main project:", project_id)
    print("Corruption project:", corruption_id)
    print("Retry project:", retry_id)

    print("\nReady tasks:")
    for task in result["ready_tasks"]:
        print(" -", task["task_key"])

    print("\nRecovery task updates:")
    for task in result["task_updates"]:
        print(" -", task["task_key"], ":", task["reason"])

    print("\nALL RECOVERY MANAGER TESTS PASSED!")

    env.close()

if __name__ == "__main__":
    run_tests()

