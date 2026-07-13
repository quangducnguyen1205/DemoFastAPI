import json
import logging
import sys

from app import models as _models  # noqa: F401
from app.bootstrap.relay import build_result_publisher, build_result_relay_service
from app.config.settings import settings
from app.core.database import SessionLocal
from app.core.schema import initialize_database_schema


def main() -> int:
    logging.basicConfig(
        level=settings.LOG_LEVEL,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    initialize_database_schema()

    publisher = build_result_publisher()
    db = SessionLocal()
    try:
        result = build_result_relay_service(db, publisher).relay_once(
            enabled=settings.PROCESSING_OUTBOX_RELAY_ENABLED
        )
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
