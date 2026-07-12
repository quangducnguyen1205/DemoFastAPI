import json
import logging
import sys

from app import models as _models  # noqa: F401
from app.config.settings import settings
from app.core.database import SessionLocal
from app.core.schema import initialize_database_schema
from app.services.processing_outbox_publisher import build_processing_outbox_publisher
from app.services.processing_outbox_relay import run_processing_outbox_relay_once


def main() -> int:
    logging.basicConfig(
        level=settings.LOG_LEVEL,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    initialize_database_schema()

    publisher = build_processing_outbox_publisher()
    db = SessionLocal()
    try:
        result = run_processing_outbox_relay_once(db, publisher=publisher)
        print(json.dumps(result.to_dict(), sort_keys=True))
        if result.disabled or result.retried or result.failed:
            return 1
        return 0
    finally:
        close = getattr(publisher, "close", None)
        if close is not None:
            close()
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
