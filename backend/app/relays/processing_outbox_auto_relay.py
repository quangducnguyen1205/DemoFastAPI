import logging
import signal
from threading import Event

from app import models as _models  # noqa: F401
from app.config.settings import settings
from app.core.database import Base, SessionLocal, engine
from app.services.processing_outbox_publisher import build_processing_outbox_publisher
from app.services.processing_outbox_relay import run_processing_outbox_relay_once

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    logging.basicConfig(
        level=settings.LOG_LEVEL,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def main() -> int:
    _configure_logging()
    if not settings.PROCESSING_OUTBOX_AUTO_RELAY_ENABLED:
        logger.error(
            "processing outbox auto relay is disabled; set PROCESSING_OUTBOX_AUTO_RELAY_ENABLED=true"
        )
        return 1

    Base.metadata.create_all(bind=engine)
    shutdown_requested = Event()

    def _request_shutdown(signum, _frame) -> None:
        logger.info("processing outbox auto relay shutdown requested signal=%s", signum)
        shutdown_requested.set()

    signal.signal(signal.SIGINT, _request_shutdown)
    signal.signal(signal.SIGTERM, _request_shutdown)

    publisher = build_processing_outbox_publisher()
    try:
        logger.info(
            "processing outbox auto relay started interval_seconds=%s batch_size=%s",
            settings.PROCESSING_OUTBOX_AUTO_RELAY_INTERVAL_SECONDS,
            settings.PROCESSING_OUTBOX_AUTO_RELAY_BATCH_SIZE,
        )
        while not shutdown_requested.is_set():
            db = SessionLocal()
            try:
                result = run_processing_outbox_relay_once(
                    db,
                    publisher=publisher,
                    enabled=settings.PROCESSING_OUTBOX_AUTO_RELAY_ENABLED,
                    batch_size=settings.PROCESSING_OUTBOX_AUTO_RELAY_BATCH_SIZE,
                )
            finally:
                db.close()

            if result.claimed or result.retried or result.failed:
                logger.info(
                    "processing outbox auto relay iteration claimed=%s published=%s retried=%s failed=%s skipped=%s",
                    result.claimed,
                    result.published,
                    result.retried,
                    result.failed,
                    result.skipped,
                )

            shutdown_requested.wait(settings.PROCESSING_OUTBOX_AUTO_RELAY_INTERVAL_SECONDS)
    finally:
        close = getattr(publisher, "close", None)
        if close is not None:
            close()
        logger.info("processing outbox auto relay stopped")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
