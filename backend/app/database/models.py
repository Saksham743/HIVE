
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


class Project(Base):
    __tablename__ = "projects"

    __table_args__ = (
        CheckConstraint(
            "status IN ('created', 'running', 'paused', 'completed', 'failed')",
            name="ck_project_status",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=generate_id
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    topic: Mapped[str] = mapped_column(Text, nullable=False)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="created", index=True
    )
    current_stage: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )

    workspace_path: Mapped[str] = mapped_column(
        Text, nullable=False
    )

    project_metadata: Mapped[dict] = mapped_column(
        "metadata", JSON, nullable=False, default=dict
    )

    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    tasks: Mapped[list["Task"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    assets: Mapped[list["Asset"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    checkpoints: Mapped[list["Checkpoint"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class Task(Base):
    __tablename__ = "tasks"

    __table_args__ = (
        UniqueConstraint(
            "project_id", "task_key", name="uq_project_task_key"
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed', 'skipped')",
            name="ck_task_status",
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

    task_key: Mapped[str] = mapped_column(String(200), nullable=False)
    module: Mapped[str] = mapped_column(String(100), nullable=False)
    operation: Mapped[str] = mapped_column(String(200), nullable=False)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", index=True
    )

    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3
    )

    input_data: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict
    )
    output_data: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict
    )

    depends_on: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )

    error_message: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    project: Mapped["Project"] = relationship(back_populates="tasks")
    assets: Mapped[list["Asset"]] = relationship(back_populates="task")
    checkpoints: Mapped[list["Checkpoint"]] = relationship(
        back_populates="task"
    )


class Asset(Base):
    __tablename__ = "assets"

    __table_args__ = (
        UniqueConstraint(
            "project_id", "relative_path",
            name="uq_project_asset_path",
        ),
        CheckConstraint(
            "status IN ('pending', 'available', 'invalid', 'deleted')",
            name="ck_asset_status",
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
    task_id: Mapped[str | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    asset_type: Mapped[str] = mapped_column(String(100), nullable=False)
    relative_path: Mapped[str] = mapped_column(Text, nullable=False)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )

    checksum_sha256: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    size_bytes: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )

    asset_metadata: Mapped[dict] = mapped_column(
        "metadata", JSON, nullable=False, default=dict
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    project: Mapped["Project"] = relationship(back_populates="assets")
    task: Mapped["Task | None"] = relationship(back_populates="assets")


class Checkpoint(Base):
    __tablename__ = "checkpoints"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=generate_id
    )
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    task_id: Mapped[str | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    checkpoint_key: Mapped[str] = mapped_column(
        String(200), nullable=False
    )
    payload: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    project: Mapped["Project"] = relationship(
        back_populates="checkpoints"
    )
    task: Mapped["Task | None"] = relationship(
        back_populates="checkpoints"
    )
