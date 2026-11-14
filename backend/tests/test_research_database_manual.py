
import pytest

from sqlalchemy import inspect, select, func
from sqlalchemy.exc import IntegrityError

from backend.tests.helpers.test_environment import (
    create_test_environment,
)

from backend.app.database.models import Project
from backend.app.database.research_models import (
    ResearchRun,
    ResearchQuestion,
    ResearchSource,
    ResearchEvidence,
    ResearchClaim,
    ClaimEvidence,
)


@pytest.fixture
def env():
    environment = create_test_environment()

    try:
        yield environment
    finally:
        environment.close()


def create_project(session, env):
    project = Project(
        name="Research Database Test",
        topic="The Fall of the Roman Empire",
        workspace_path=str(env.workspace_root),
    )

    session.add(project)
    session.flush()

    return project


def create_research_graph(session, env):
    """Create a complete research relationship graph."""

    project = create_project(session, env)

    run = ResearchRun(
        project_id=project.id,
        run_number=1,
        topic=project.topic,
        scope="Investigate the decline of the Western Roman Empire",
        objectives=[
            "Identify major historical events",
            "Investigate competing explanations",
        ],
    )

    session.add(run)
    session.flush()

    question = ResearchQuestion(
        research_run_id=run.id,
        question="What contributed to the decline of the empire?",
        priority=1,
    )

    source = ResearchSource(
        research_run_id=run.id,
        source_key="test-source-001",
        source_type="book",
        title="A Test History Book",
        author="Test Historian",
        reliability="high",
        status="retrieved",
    )

    session.add_all([question, source])
    session.flush()

    evidence = ResearchEvidence(
        source_id=source.id,
        evidence_type="quote",
        evidence_text="Political instability weakened imperial authority.",
        locator="Chapter 4, page 42",
    )

    claim = ResearchClaim(
        research_run_id=run.id,
        question_id=question.id,
        claim_text=(
            "Political instability contributed "
            "to the decline of the empire."
        ),
        verification_status="supported",
        evidence_strength="moderate",
    )

    session.add_all([evidence, claim])
    session.flush()

    link = ClaimEvidence(
        claim_id=claim.id,
        evidence_id=evidence.id,
        relationship_type="supports",
        explanation="The passage directly supports the claim.",
    )

    session.add(link)
    session.flush()

    return project, run, question, source, evidence, claim, link


def test_all_database_tables_exist(env):
    table_names = set(inspect(env.engine).get_table_names())

    expected_tables = {
        "projects",
        "tasks",
        "assets",
        "checkpoints",
        "research_runs",
        "research_questions",
        "research_sources",
        "research_evidence",
        "research_claims",
        "claim_evidence",
    }

    assert expected_tables.issubset(table_names)

    assert len(table_names) == 10


def test_research_relationships_work(env):
    with env.session_factory() as session:
        (
            project,
            run,
            question,
            source,
            evidence,
            claim,
            link,
        ) = create_research_graph(session, env)

        session.commit()

        run_id = run.id
        session.expunge_all()

        loaded_run = session.get(ResearchRun, run_id)

        assert loaded_run is not None
        assert loaded_run.project.id == project.id

        assert len(loaded_run.questions) == 1
        assert len(loaded_run.sources) == 1
        assert len(loaded_run.claims) == 1

        loaded_source = loaded_run.sources[0]

        assert len(loaded_source.evidence) == 1
        assert loaded_source.evidence[0].id == evidence.id

        loaded_claim = loaded_run.claims[0]

        assert len(loaded_claim.evidence_links) == 1

        loaded_link = loaded_claim.evidence_links[0]

        assert loaded_link.relationship_type == "supports"
        assert loaded_link.evidence.id == evidence.id


def test_multiple_evidence_records_can_support_one_claim(env):
    with env.session_factory() as session:
        (
            project,
            run,
            question,
            source,
            evidence,
            claim,
            link,
        ) = create_research_graph(session, env)

        second_source = ResearchSource(
            research_run_id=run.id,
            source_key="test-source-002",
            source_type="paper",
            title="A Second Test Source",
            reliability="high",
            status="retrieved",
        )

        session.add(second_source)
        session.flush()

        second_evidence = ResearchEvidence(
            source_id=second_source.id,
            evidence_type="paraphrase",
            evidence_text=(
                "A second independent source describes "
                "political instability."
            ),
        )

        session.add(second_evidence)
        session.flush()

        second_link = ClaimEvidence(
            claim_id=claim.id,
            evidence_id=second_evidence.id,
            relationship_type="supports",
        )

        session.add(second_link)
        session.commit()

        session.refresh(claim)

        assert len(claim.evidence_links) == 2


def test_invalid_claim_status_is_rejected(env):
    with env.session_factory() as session:
        project = create_project(session, env)

        run = ResearchRun(
            project_id=project.id,
            run_number=1,
            topic=project.topic,
        )

        session.add(run)
        session.flush()

        invalid_claim = ResearchClaim(
            research_run_id=run.id,
            claim_text="This claim has an invalid status.",
            verification_status="probably_true",
            evidence_strength="unknown",
        )

        session.add(invalid_claim)

        with pytest.raises(IntegrityError):
            session.commit()

        session.rollback()


def test_duplicate_source_key_is_rejected(env):
    with env.session_factory() as session:
        project = create_project(session, env)

        run = ResearchRun(
            project_id=project.id,
            run_number=1,
            topic=project.topic,
        )

        session.add(run)
        session.flush()

        source_one = ResearchSource(
            research_run_id=run.id,
            source_key="duplicate-key",
            source_type="website",
            title="First Source",
        )

        session.add(source_one)
        session.commit()

        source_two = ResearchSource(
            research_run_id=run.id,
            source_key="duplicate-key",
            source_type="website",
            title="Duplicate Source",
        )

        session.add(source_two)

        with pytest.raises(IntegrityError):
            session.commit()

        session.rollback()


def test_duplicate_claim_evidence_relationship_is_rejected(env):
    with env.session_factory() as session:
        (
            project,
            run,
            question,
            source,
            evidence,
            claim,
            link,
        ) = create_research_graph(session, env)

        duplicate_link = ClaimEvidence(
            claim_id=claim.id,
            evidence_id=evidence.id,
            relationship_type="supports",
        )

        session.add(duplicate_link)

        with pytest.raises(IntegrityError):
            session.commit()

        session.rollback()


def test_research_run_deletion_cascades(env):
    with env.session_factory() as session:
        (
            project,
            run,
            question,
            source,
            evidence,
            claim,
            link,
        ) = create_research_graph(session, env)

        run_id = run.id

        session.commit()

        session.delete(run)
        session.commit()

        assert session.scalar(
            select(func.count()).select_from(ResearchRun)
        ) == 0

        assert session.scalar(
            select(func.count()).select_from(ResearchQuestion)
        ) == 0

        assert session.scalar(
            select(func.count()).select_from(ResearchSource)
        ) == 0

        assert session.scalar(
            select(func.count()).select_from(ResearchEvidence)
        ) == 0

        assert session.scalar(
            select(func.count()).select_from(ResearchClaim)
        ) == 0

        assert session.scalar(
            select(func.count()).select_from(ClaimEvidence)
        ) == 0

        # Deleting research must not delete its documentary project.
        assert session.get(Project, project.id) is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
