import logging

from sqlalchemy import Engine, inspect, text

from app.core.database import Base, engine

logger = logging.getLogger(__name__)

_RECOVERY_COLUMNS = {
    "failure_disposition": "VARCHAR(32)",
    "recovery_cycle_count": "INTEGER NOT NULL DEFAULT 0",
    "next_recovery_at": "TIMESTAMP WITH TIME ZONE",
    "last_failure_category": "VARCHAR(128)",
    "recovery_exhausted_at": "TIMESTAMP WITH TIME ZONE",
}


def initialize_database_schema(bind: Engine = engine) -> None:
    from app import models as _models  # noqa: F401

    Base.metadata.create_all(bind=bind)
    ensure_processing_outbox_recovery_schema(bind)


def ensure_processing_outbox_recovery_schema(bind: Engine) -> None:
    inspector = inspect(bind)
    if "processing_outbox_events" not in inspector.get_table_names():
        return

    existing_columns = {column["name"] for column in inspector.get_columns("processing_outbox_events")}
    dialect = bind.dialect.name
    with bind.begin() as connection:
        for column_name, column_type in _RECOVERY_COLUMNS.items():
            if dialect == "postgresql":
                connection.execute(text(
                    f"ALTER TABLE processing_outbox_events ADD COLUMN IF NOT EXISTS {column_name} {column_type}"
                ))
            elif column_name not in existing_columns:
                portable_type = column_type.replace("TIMESTAMP WITH TIME ZONE", "TIMESTAMP")
                connection.execute(text(
                    f"ALTER TABLE processing_outbox_events ADD COLUMN {column_name} {portable_type}"
                ))

        connection.execute(text(
            """
            UPDATE processing_outbox_events
            SET failure_disposition = 'unknown',
                last_failure_category = 'historical_unclassified',
                last_error = 'historical_unclassified'
            WHERE status = 'failed'
              AND failure_disposition IS NULL
            """
        ))
        connection.execute(text(
            """
            CREATE INDEX IF NOT EXISTS idx_processing_outbox_recovery_eligibility
            ON processing_outbox_events (
                status, failure_disposition, next_recovery_at, recovery_cycle_count, created_at
            )
            """
        ))
    logger.info("processing outbox recovery schema verified")
