
from loguru import logger

from backend.app.config import settings


_configured = False


def configure_logging() -> None:
    """Configure console and persistent JSON logging."""

    global _configured

    if _configured:
        return

    settings.ensure_directories()

    logger.remove()

    # Human-readable terminal output
    logger.add(
        sink=lambda message: print(message, end=""),
        level=settings.log_level,
        colorize=True,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan> | "
            "<level>{message}</level>"
        ),
    )

    # Machine-readable persistent logs
    logger.add(
        str(settings.logs_dir / "hive.jsonl"),
        level=settings.log_level,
        rotation="10 MB",
        retention="14 days",
        compression="zip",
        serialize=True,
        encoding="utf-8",
        enqueue=False,
        backtrace=True,
        diagnose=False,
    )

    _configured = True

    logger.info("HIVE logging initialized")


__all__ = ["logger", "configure_logging"]
