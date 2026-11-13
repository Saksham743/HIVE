
import atexit
import tempfile
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from backend.app.config import settings
from backend.app.database.base import Base
from backend.app.database import models  # noqa: F401


class ManualTestEnvironment:
    """Isolated database and filesystem for manual HIVE tests."""

    def __init__(self):
        self._temporary_directory = tempfile.TemporaryDirectory(
            prefix="hive-test-"
        )
        self.root = Path(self._temporary_directory.name).resolve()

        self.database_path = self.root / "hive_test.db"
        self.workspace_root = self.root / "workspaces"
        self.workspace_root.mkdir(parents=True, exist_ok=True)

        self._closed = False

        self._assert_isolated_database()

        database_url = f"sqlite:///{self.database_path.as_posix()}"

        self.engine = create_engine(
            database_url,
            connect_args={"check_same_thread": False},
            echo=False,
        )

        @event.listens_for(self.engine, "connect")
        def enable_foreign_keys(connection, record):
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        self.session_factory = sessionmaker(
            bind=self.engine,
            autoflush=False,
            expire_on_commit=False,
        )

        Base.metadata.create_all(bind=self.engine)

        self.state = None
        self.checkpoint = None
        self.recovery = None

        atexit.register(self.close)

    def _assert_isolated_database(self):
        test_database = self.database_path.resolve()

        production_url = make_url(settings.database_url)

        if production_url.database:
            production_database = Path(
                production_url.database
            ).resolve()

            if test_database == production_database:
                raise RuntimeError(
                    "TEST SAFETY FAILURE: Test database points "
                    "to the production HIVE database."
                )

        production_workspace = Path(
            settings.workspaces_dir
        ).resolve()

        if (
            self.workspace_root == production_workspace
            or production_workspace in self.workspace_root.parents
            or self.workspace_root in production_workspace.parents
        ):
            raise RuntimeError(
                "TEST SAFETY FAILURE: Test workspace overlaps "
                "the production workspace."
            )

    def create_managers(self):
        from backend.app.core.state_manager import StateManager
        from backend.app.core.checkpoint_manager import CheckpointManager
        from backend.app.core.recovery_manager import RecoveryManager

        self.state = StateManager(
            session_factory=self.session_factory,
            workspace_root=self.workspace_root,
        )

        self.checkpoint = CheckpointManager(
            session_factory=self.session_factory,
        )

        self.recovery = RecoveryManager(
            session_factory=self.session_factory,
            checkpoint_manager=self.checkpoint,
        )

        return self.state, self.checkpoint, self.recovery

    def close(self):
        if self._closed:
            return

        self._closed = True

        if hasattr(self, "engine"):
            self.engine.dispose()

        self._temporary_directory.cleanup()


def create_test_environment():
    """Create a fresh isolated environment for one manual test suite."""
    return ManualTestEnvironment()
