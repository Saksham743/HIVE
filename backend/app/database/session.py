
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from backend.app.config import settings
from backend.app.database.base import Base


is_sqlite = settings.database_url.startswith("sqlite")

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if is_sqlite else {},
    echo=settings.debug and settings.environment == "development",
)

if is_sqlite:

    @event.listens_for(engine, "connect")
    def enable_sqlite_foreign_keys(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    expire_on_commit=False,
)


def init_db() -> None:
    """Create database tables that do not already exist."""

    # Register all database models.
    import backend.app.database.models
    import backend.app.database.research_models

    settings.ensure_directories()
    Base.metadata.create_all(bind=engine)


@contextmanager
def session_scope():
    """Provide a transactional database session."""
    session = SessionLocal()

    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
