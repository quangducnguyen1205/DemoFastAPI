import logging
import signal
import time
from threading import Event

from app import models as _models  # noqa: F401
from app.config.settings import settings
from app.core.database import SessionLocal
from app.core.schema import initialize_database_schema
from app.services.processing_outbox_publisher import build_processing_outbox_publisher
from app.services.processing_outbox_relay import run_processing_outbox_relay_once
from app.services.processing_outbox_recovery import reconcile_failed_processing_outbox_events

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    logging.basicConfig(
        level=settings.LOG_LEVEL,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def _auto_relay_configuration_is_valid() -> bool:
    if not settings.PROCESSING_OUTBOX_AUTO_RELAY_ENABLED:
        logger.error(
            "processing outbox auto relay is disabled; set PROCESSING_OUTBOX_AUTO_RELAY_ENABLED=true"
        )
        return False
    if not settings.PROCESSING_RESULT_PUBLISHER_ENABLED:
        logger.error(
            "processing outbox auto relay requires PROCESSING_RESULT_PUBLISHER_ENABLED=true"
        )
        return False
    return True


def _run_iteration(db, publisher, *, run_recovery: bool):
    recovery_result = None
    if run_recovery:
        try:
            recovery_result = reconcile_failed_processing_outbox_events(db)
        except Exception as exc:
            db.rollback()
            logger.warning(
                "processing outbox recovery iteration failed category=%s",
                type(exc).__name__,
            )
    relay_result = run_processing_outbox_relay_once(
        db,
        publisher=publisher,
        enabled=settings.PROCESSING_OUTBOX_AUTO_RELAY_ENABLED,
        batch_size=settings.PROCESSING_OUTBOX_AUTO_RELAY_BATCH_SIZE,
    )
    return recovery_result, relay_result


def main() -> int:
    _configure_logging()
    if not _auto_relay_configuration_is_valid():
        return 1

    initialize_database_schema()
    shutdown_requested = Event()

    def _request_shutdown(signum, _frame) -> None:
        logger.info("processing outbox auto relay shutdown requested signal=%s", signum)
        shutdown_requested.set()

    signal.signal(signal.SIGINT, _request_shutdown)
    signal.signal(signal.SIGTERM, _request_shutdown)

    publisher = build_processing_outbox_publisher()
    try:
        logger.info(
            "processing outbox auto relay started interval_seconds=%s batch_size=%s recovery_enabled=%s recovery_interval_seconds=%s",
            settings.PROCESSING_OUTBOX_AUTO_RELAY_INTERVAL_SECONDS,
            settings.PROCESSING_OUTBOX_AUTO_RELAY_BATCH_SIZE,
            settings.PROCESSING_OUTBOX_RECOVERY_ENABLED,
            settings.PROCESSING_OUTBOX_RECOVERY_INTERVAL_SECONDS,
        )
        next_recovery_deadline = 0.0
        while not shutdown_requested.is_set():
            monotonic_now = time.monotonic()
            run_recovery = (
                settings.PROCESSING_OUTBOX_RECOVERY_ENABLED
                and monotonic_now >= next_recovery_deadline
            )
            if run_recovery:
                next_recovery_deadline = (
                    monotonic_now + settings.PROCESSING_OUTBOX_RECOVERY_INTERVAL_SECONDS
                )
            db = SessionLocal()
            try:
                recovery_result, result = _run_iteration(db, publisher, run_recovery=run_recovery)
            finally:
                db.close()

            if recovery_result is not None and recovery_result.eligible:
                logger.info(
                    "processing outbox recovery iteration eligible=%s requeued=%s skipped=%s",
                    recovery_result.eligible,
                    recovery_result.requeued,
                    recovery_result.skipped,
                )

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
