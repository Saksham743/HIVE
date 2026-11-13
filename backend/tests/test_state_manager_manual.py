from backend.tests.helpers.test_environment import create_test_environment


def main():
    env = create_test_environment()
    state, _, _ = env.create_managers()

    project = state.create_project(
        name="HIVE Recovery Test",
        topic="The Construction of the Great Pyramid",
    )

    project_id = project["id"]

    print("\nPROJECT CREATED")
    print(project)

    # Register three tasks with dependencies.
    research = state.register_task(
        project_id=project_id,
        task_key="research",
        module="research",
        operation="collect_historical_facts",
    )

    script = state.register_task(
        project_id=project_id,
        task_key="script",
        module="scripting",
        operation="generate_documentary_script",
        depends_on=["research"],
    )

    image_1 = state.register_task(
        project_id=project_id,
        task_key="image_001",
        module="image_generation",
        operation="generate_scene_image",
        depends_on=["script"],
    )

    # Registering an identical task should not create a duplicate.
    duplicate = state.register_task(
        project_id=project_id,
        task_key="research",
        module="research",
        operation="collect_historical_facts",
    )

    assert research["id"] == duplicate["id"]

    print("\nINITIAL READY TASKS")
    print([task["task_key"] for task in state.get_ready_tasks(project_id)])

    # Complete research.
    state.start_task(project_id, "research")
    state.complete_task(
        project_id,
        "research",
        {"facts_collected": 25},
    )

    print("\nREADY AFTER RESEARCH")
    print([task["task_key"] for task in state.get_ready_tasks(project_id)])

    # Complete script.
    state.start_task(project_id, "script")
    state.complete_task(
        project_id,
        "script",
        {"script_version": 1},
    )

    # Simulate an image-generation failure.
    state.start_task(project_id, "image_001")
    failure = state.fail_task(
        project_id,
        "image_001",
        "Simulated image generation interruption",
    )

    print("\nFAILURE RESULT")
    print(failure)

    print("\nREADY TASKS AFTER FAILURE")
    print([task["task_key"] for task in state.get_ready_tasks(project_id)])

    # Retry the failed image task.
    state.start_task(project_id, "image_001")
    state.complete_task(
        project_id,
        "image_001",
        {"image_path": "images/image_001.png"},
    )

    print("\nFINAL PROJECT SUMMARY")
    print(state.get_project_summary(project_id))

    incomplete_projects = state.get_incomplete_projects()

    print("\nINCOMPLETE PROJECTS")
    for item in incomplete_projects:
        print(f"{item['name']} | {item['id']} | {item['status']}")

    assert project["id"] not in [
        item["id"] for item in incomplete_projects
    ], "Completed project incorrectly appears in incomplete projects!"

    print("\nCURRENT PROJECT SUCCESSFULLY REMOVED FROM INCOMPLETE PROJECTS")

    print("\nALL STATE MANAGER TESTS PASSED")


if __name__ == "__main__":
    main()
