
from pathlib import Path
from uuid import uuid4

from backend.tests.helpers.test_environment import create_test_environment


def main():
    env = create_test_environment()
    state, checkpoints, _ = env.create_managers()

    project = state.create_project(
        name="Checkpoint Recovery Test",
        topic="Testing HIVE pipeline recovery",
    )

    project_id = project["id"]
    workspace = Path(project["workspace_path"])
    workspace.mkdir(parents=True, exist_ok=True)

    print(f"Project: {project_id}")

    # Simulate outputs from different pipeline stages.
    test_files = {
        "research/research.json": b'{"facts": ["Historical fact"]}',
        "scripts/script.txt": b"Historical documentary narration.",
        "narration/segment_001.wav": b"TEST_AUDIO_BYTES",
        "music/section_001.wav": b"TEST_MUSIC_BYTES",
    }

    for relative_path, content in test_files.items():
        file_path = workspace / relative_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(content)

        asset = checkpoints.register_asset(
            project_id=project_id,
            asset_type=relative_path.split("/")[0],
            relative_path=relative_path,
            checkpoint_key=f"saved:{relative_path}",
        )

        assert asset["status"] == "available"

        verification = checkpoints.verify_asset(
            project_id=project_id,
            relative_path=relative_path,
        )

        assert verification["valid"] is True

        print(f"PASS: {relative_path}")

    # Verify that registering the same file is idempotent.
    first = checkpoints.register_asset(
        project_id=project_id,
        asset_type="research",
        relative_path="research/research.json",
    )

    second = checkpoints.register_asset(
        project_id=project_id,
        asset_type="research",
        relative_path="research/research.json",
    )

    assert first["id"] == second["id"]
    print("PASS: Duplicate registration")

    # Simulate corruption.
    corrupted_file = workspace / "research/research.json"
    corrupted_file.write_bytes(b"CORRUPTED")

    verification = checkpoints.verify_asset(
        project_id=project_id,
        relative_path="research/research.json",
    )

    assert verification["valid"] is False
    assert verification["status"] == "invalid"

    print("PASS: Corruption detection")

    # Verify a standalone checkpoint.
    checkpoints.create_checkpoint(
        project_id=project_id,
        checkpoint_key="research_batch_completed",
        payload={"batch": 3, "sources_collected": 15},
    )

    latest = checkpoints.get_latest_checkpoint(
        project_id=project_id,
        checkpoint_key="research_batch_completed",
    )

    assert latest is not None
    assert latest["payload"]["sources_collected"] == 15

    print("PASS: Standalone checkpoint")

    # Verify missing files are rejected.
    try:
        checkpoints.register_asset(
            project_id=project_id,
            asset_type="image",
            relative_path="images/missing.png",
        )
        raise AssertionError("Missing file was accepted")
    except FileNotFoundError:
        print("PASS: Missing file rejected")

    print("\nALL CHECKPOINT MANAGER TESTS PASSED")

    env.close()

if __name__ == "__main__":
    main()
