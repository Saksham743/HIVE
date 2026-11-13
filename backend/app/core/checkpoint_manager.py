
import hashlib
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from backend.app.database.models import (
    Asset,
    Checkpoint,
    Project,
    Task,
)
from backend.app.database.session import SessionLocal


class CheckpointManager:
    """
    Handles asset registration, integrity verification,
    and persistent project checkpoints.
    """

    def __init__(self, session_factory: sessionmaker = SessionLocal):
        self.session_factory = session_factory

    # ---------------------------------------------------------
    # INTERNAL HELPERS
    # ---------------------------------------------------------

    @staticmethod
    def _calculate_checksum(file_path: Path) -> str:
        """Calculate SHA-256 without loading the entire file into memory."""

        sha256 = hashlib.sha256()

        with file_path.open("rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                sha256.update(chunk)

        return sha256.hexdigest()

    @staticmethod
    def _resolve_asset_path(
        workspace_path: str,
        relative_path: str,
    ) -> Path:
        """Resolve an asset path and prevent escaping the project workspace."""

        relative = Path(relative_path)

        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Asset path must be relative to the project workspace.")

        workspace = Path(workspace_path).resolve()
        resolved = (workspace / relative).resolve()

        try:
            resolved.relative_to(workspace)
        except ValueError:
            raise ValueError("Asset path escapes the project workspace.")

        return resolved

    @staticmethod
    def _asset_dict(asset: Asset) -> dict:
        return {
            "id": asset.id,
            "project_id": asset.project_id,
            "task_id": asset.task_id,
            "asset_type": asset.asset_type,
            "relative_path": asset.relative_path,
            "status": asset.status,
            "checksum_sha256": asset.checksum_sha256,
            "size_bytes": asset.size_bytes,
            "metadata": asset.asset_metadata,
        }

    @staticmethod
    def _checkpoint_dict(checkpoint: Checkpoint) -> dict:
        return {
            "id": checkpoint.id,
            "project_id": checkpoint.project_id,
            "task_id": checkpoint.task_id,
            "checkpoint_key": checkpoint.checkpoint_key,
            "payload": checkpoint.payload,
            "created_at": checkpoint.created_at,
        }

    # ---------------------------------------------------------
    # ASSET REGISTRATION
    # ---------------------------------------------------------

    def register_asset(
        self,
        project_id: str,
        asset_type: str,
        relative_path: str,
        task_id: str | None = None,
        metadata: dict | None = None,
        checkpoint_key: str | None = None,
        checkpoint_payload: dict | None = None,
    ) -> dict:
        """
        Register a generated file.

        The file must already exist and pass basic validation.
        Re-registering the same project/path updates the existing record.

        An optional checkpoint can be saved in the same DB transaction.
        """

        session = self.session_factory()

        try:
            project = session.get(Project, project_id)

            if not project:
                raise ValueError(f"Project not found: {project_id}")

            if task_id:
                task = session.get(Task, task_id)

                if not task or task.project_id != project_id:
                    raise ValueError("Task does not belong to this project.")

            file_path = self._resolve_asset_path(
                project.workspace_path,
                relative_path,
            )

            if not file_path.is_file():
                raise FileNotFoundError(
                    f"Asset file does not exist: {file_path}"
                )

            size_bytes = file_path.stat().st_size

            if size_bytes <= 0:
                raise ValueError("Cannot register an empty asset file.")

            checksum = self._calculate_checksum(file_path)

            asset = session.scalar(
                select(Asset).where(
                    Asset.project_id == project_id,
                    Asset.relative_path == relative_path,
                )
            )

            if asset is None:
                asset = Asset(
                    project_id=project_id,
                    task_id=task_id,
                    asset_type=asset_type,
                    relative_path=relative_path,
                )
                session.add(asset)

            asset.task_id = task_id
            asset.asset_type = asset_type
            asset.status = "available"
            asset.checksum_sha256 = checksum
            asset.size_bytes = size_bytes
            asset.asset_metadata = metadata or {}

            # Flush to obtain the asset ID before committing.
            session.flush()

            # Optional checkpoint, saved atomically with the asset record.
            if checkpoint_key:
                checkpoint = Checkpoint(
                    project_id=project_id,
                    task_id=task_id,
                    checkpoint_key=checkpoint_key,
                    payload={
                        **(checkpoint_payload or {}),
                        "asset_id": asset.id,
                        "relative_path": relative_path,
                    },
                )
                session.add(checkpoint)

            session.commit()
            session.refresh(asset)

            return self._asset_dict(asset)

        except Exception:
            session.rollback()
            raise

        finally:
            session.close()

    # ---------------------------------------------------------
    # ASSET VERIFICATION
    # ---------------------------------------------------------

    def verify_asset(
        self,
        project_id: str,
        relative_path: str,
    ) -> dict:
        """
        Verify that an existing asset is present and unchanged.

        Marks the database record invalid if the file is missing,
        empty, or has a different checksum.
        """

        session = self.session_factory()

        try:
            asset = session.scalar(
                select(Asset).where(
                    Asset.project_id == project_id,
                    Asset.relative_path == relative_path,
                )
            )

            if asset is None:
                raise ValueError(
                    f"Asset is not registered: {relative_path}"
                )

            # Never restore an intentionally deleted asset.
            if asset.status == "deleted":
                result = self._asset_dict(asset)
                result["valid"] = False
                result["reason"] = "Asset is marked as deleted."
                return result

            project = session.get(Project, project_id)

            if not project:
                raise ValueError(f"Project not found: {project_id}")

            file_path = self._resolve_asset_path(
                project.workspace_path,
                relative_path,
            )

            valid = False

            if file_path.is_file():
                size_bytes = file_path.stat().st_size

                if size_bytes > 0:
                    checksum = self._calculate_checksum(file_path)

                    valid = (
                        checksum == asset.checksum_sha256
                        and size_bytes == asset.size_bytes
                    )

            if valid:
                asset.status = "available"
            else:
                asset.status = "invalid"

            session.commit()
            session.refresh(asset)

            result = self._asset_dict(asset)
            result["valid"] = valid

            return result

        except Exception:
            session.rollback()
            raise

        finally:
            session.close()

    # ---------------------------------------------------------
    # CHECKPOINT CREATION
    # ---------------------------------------------------------

    def create_checkpoint(
        self,
        project_id: str,
        checkpoint_key: str,
        payload: dict | None = None,
        task_id: str | None = None,
    ) -> dict:
        """Persist a completed milestone or recovery checkpoint."""

        session = self.session_factory()

        try:
            project = session.get(Project, project_id)

            if not project:
                raise ValueError(f"Project not found: {project_id}")

            if task_id:
                task = session.get(Task, task_id)

                if not task or task.project_id != project_id:
                    raise ValueError("Task does not belong to this project.")

            checkpoint = Checkpoint(
                project_id=project_id,
                task_id=task_id,
                checkpoint_key=checkpoint_key,
                payload=payload or {},
            )

            session.add(checkpoint)
            session.commit()
            session.refresh(checkpoint)

            return self._checkpoint_dict(checkpoint)

        except Exception:
            session.rollback()
            raise

        finally:
            session.close()

    # ---------------------------------------------------------
    # CHECKPOINT RETRIEVAL
    # ---------------------------------------------------------

    def get_latest_checkpoint(
        self,
        project_id: str,
        checkpoint_key: str,
        task_id: str | None = None,
    ) -> dict | None:
        """Retrieve the most recent matching checkpoint."""

        session = self.session_factory()

        try:
            query = select(Checkpoint).where(
                Checkpoint.project_id == project_id,
                Checkpoint.checkpoint_key == checkpoint_key,
            )

            if task_id is None:
                query = query.where(Checkpoint.task_id.is_(None))
            else:
                query = query.where(Checkpoint.task_id == task_id)

            checkpoint = session.scalar(
                query.order_by(
                    Checkpoint.created_at.desc(),
                    Checkpoint.id.desc(),
                ).limit(1)
            )

            if checkpoint is None:
                return None

            return self._checkpoint_dict(checkpoint)

        finally:
            session.close()
