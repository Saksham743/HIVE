from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.database.base import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def generate_id() -> str:
    return str(uuid4())


# ============================================================
# 1. RESEARCH RUNS
# ============================================================

class ResearchRun(Base):
    __tablename__ = "research_runs"

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "run_number",
            name="uq_research_project_run",
        ),
        CheckConstraint(
            "status IN ("
            "'planned', 'discovering', 'extracting', "
            "'verifying', 'synthesizing', 'completed', 'failed'"
            ")",
            name="ck_research_run_status",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=generate_id
    )

    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    run_number: Mapped[int] = mapped_column(
        Integer, nullable=False
    )

    topic: Mapped[str] = mapped_column(
        Text, nullable=False
    )

    scope: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )

    objectives: Mapped[list] = mapped_column(
        JSON, nullable=False, default=list
    )

    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="planned", index=True
    )

    summary: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    project = relationship("Project")

    questions: Mapped[list["ResearchQuestion"]] = relationship(
        back_populates="research_run",
        cascade="all, delete-orphan",
    )

    sources: Mapped[list["ResearchSource"]] = relationship(
        back_populates="research_run",
        cascade="all, delete-orphan",
    )

    claims: Mapped[list["ResearchClaim"]] = relationship(
        back_populates="research_run",
        cascade="all, delete-orphan",
    )


# ============================================================
# 2. RESEARCH QUESTIONS
# ============================================================

class ResearchQuestion(Base):
    __tablename__ = "research_questions"

    __table_args__ = (
        UniqueConstraint(
            "research_run_id",
            "question",
            name="uq_research_run_question",
        ),
        CheckConstraint(
            "status IN ("
            "'pending', 'answered', 'partially_answered', 'blocked'"
            ")",
            name="ck_research_question_status",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=generate_id
    )

    research_run_id: Mapped[str] = mapped_column(
        ForeignKey("research_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    question: Mapped[str] = mapped_column(
        Text, nullable=False
    )

    priority: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3
    )

    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="pending"
    )

    answer_summary: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    research_run = relationship(
        "ResearchRun",
        back_populates="questions",
    )

    claims: Mapped[list["ResearchClaim"]] = relationship(
        back_populates="question",
    )


# ============================================================
# 3. RESEARCH SOURCES
# ============================================================

class ResearchSource(Base):
    __tablename__ = "research_sources"

    __table_args__ = (
        UniqueConstraint(
            "research_run_id",
            "source_key",
            name="uq_research_source_key",
        ),
        CheckConstraint(
            "source_type IN ("
            "'website', 'book', 'paper', 'archive', "
            "'news', 'interview', 'other'"
            ")",
            name="ck_research_source_type",
        ),
        CheckConstraint(
            "reliability IN ('high', 'medium', 'low', 'unknown')",
            name="ck_research_source_reliability",
        ),
        CheckConstraint(
            "status IN ("
            "'discovered', 'retrieved', 'failed', 'excluded'"
            ")",
            name="ck_research_source_status",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=generate_id
    )

    research_run_id: Mapped[str] = mapped_column(
        ForeignKey("research_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Stable identifier used to avoid duplicate sources in a run.
    source_key: Mapped[str] = mapped_column(
        String(500), nullable=False
    )

    source_type: Mapped[str] = mapped_column(
        String(30), nullable=False, default="other"
    )

    title: Mapped[str] = mapped_column(
        Text, nullable=False
    )

    canonical_url: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )

    author: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )

    publisher: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )

    publication_date: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )

    accessed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    reliability: Mapped[str] = mapped_column(
        String(20), nullable=False, default="unknown"
    )

    reliability_notes: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )

    content_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="discovered"
    )

    source_metadata: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    research_run = relationship(
        "ResearchRun",
        back_populates="sources",
    )

    evidence: Mapped[list["ResearchEvidence"]] = relationship(
        back_populates="source",
        cascade="all, delete-orphan",
    )


# ============================================================
# 4. RESEARCH EVIDENCE
# ============================================================

class ResearchEvidence(Base):
    __tablename__ = "research_evidence"

    __table_args__ = (
        CheckConstraint(
            "evidence_type IN ('quote', 'paraphrase', 'data_point')",
            name="ck_research_evidence_type",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=generate_id
    )

    source_id: Mapped[str] = mapped_column(
        ForeignKey("research_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    evidence_type: Mapped[str] = mapped_column(
        String(30), nullable=False, default="quote"
    )

    # Exact extracted text. Quotes must not be silently rewritten.
    evidence_text: Mapped[str] = mapped_column(
        Text, nullable=False
    )

    surrounding_context: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )

    # Page number, paragraph, section, timestamp, archive reference, etc.
    locator: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )

    extraction_method: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    source = relationship(
        "ResearchSource",
        back_populates="evidence",
    )

    claim_links: Mapped[list["ClaimEvidence"]] = relationship(
        back_populates="evidence",
        cascade="all, delete-orphan",
    )


# ============================================================
# 5. RESEARCH CLAIMS
# ============================================================

class ResearchClaim(Base):
    __tablename__ = "research_claims"

    __table_args__ = (
        CheckConstraint(
            "verification_status IN ("
            "'supported', 'contested', 'unverified', 'contradicted'"
            ")",
            name="ck_research_claim_verification",
        ),
        CheckConstraint(
            "evidence_strength IN ("
            "'strong', 'moderate', 'weak', 'none', 'unknown'"
            ")",
            name="ck_research_claim_evidence_strength",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=generate_id
    )

    research_run_id: Mapped[str] = mapped_column(
        ForeignKey("research_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    question_id: Mapped[str | None] = mapped_column(
        ForeignKey("research_questions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    claim_text: Mapped[str] = mapped_column(
        Text, nullable=False
    )

    claim_category: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )

    verification_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="unverified", index=True
    )

    evidence_strength: Mapped[str] = mapped_column(
        String(20), nullable=False, default="unknown"
    )

    verification_notes: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    research_run = relationship(
        "ResearchRun",
        back_populates="claims",
    )

    question = relationship(
        "ResearchQuestion",
        back_populates="claims",
    )

    evidence_links: Mapped[list["ClaimEvidence"]] = relationship(
        back_populates="claim",
        cascade="all, delete-orphan",
    )


# ============================================================
# 6. CLAIM ↔ EVIDENCE RELATIONSHIP
# ============================================================

class ClaimEvidence(Base):
    __tablename__ = "claim_evidence"

    __table_args__ = (
        UniqueConstraint(
            "claim_id",
            "evidence_id",
            "relationship_type",
            name="uq_claim_evidence_relationship",
        ),
        CheckConstraint(
            "relationship_type IN ("
            "'supports', 'contradicts', 'contextualizes'"
            ")",
            name="ck_claim_evidence_relationship",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=generate_id
    )

    claim_id: Mapped[str] = mapped_column(
        ForeignKey("research_claims.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    evidence_id: Mapped[str] = mapped_column(
        ForeignKey("research_evidence.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    relationship_type: Mapped[str] = mapped_column(
        String(30), nullable=False
    )

    explanation: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    claim = relationship(
        "ResearchClaim",
        back_populates="evidence_links",
    )

    evidence = relationship(
        "ResearchEvidence",
        back_populates="claim_links",
    )