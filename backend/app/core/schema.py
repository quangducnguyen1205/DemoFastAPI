import logging

from sqlalchemy import Connection, Engine, inspect, text

from app.core.database import Base, engine

logger = logging.getLogger(__name__)

_RECOVERY_COLUMNS = {
    "failure_disposition": "VARCHAR(32)",
    "recovery_cycle_count": "INTEGER NOT NULL DEFAULT 0",
    "next_recovery_at": "TIMESTAMP WITH TIME ZONE",
    "last_failure_category": "VARCHAR(128)",
    "recovery_exhausted_at": "TIMESTAMP WITH TIME ZONE",
}

_TRANSCRIPT_TIMING_COLUMNS = {
    "start_ms": "BIGINT",
    "end_ms": "BIGINT",
}

# Stable Project3 FastAPI PostgreSQL session advisory lock for schema creation and upgrades.
# This value is intentionally fixed rather than derived from Python's process-randomized hash().
_POSTGRES_SCHEMA_INITIALIZATION_LOCK_KEY = 5_126_144_801


def initialize_database_schema(bind: Engine = engine) -> None:
    from app import models as _models  # noqa: F401

    if bind.dialect.name == "postgresql":
        _initialize_postgresql_schema(bind)
        return

    Base.metadata.create_all(bind=bind)
    ensure_processing_outbox_recovery_schema(bind)
    ensure_processing_transcript_timing_schema(bind)


def _initialize_postgresql_schema(bind: Engine) -> None:
    with bind.connect() as connection:
        lock_acquired = False
        try:
            logger.info("waiting for PostgreSQL schema initialization lock")
            connection.execute(
                text("SELECT pg_advisory_lock(:lock_key)"),
                {"lock_key": _POSTGRES_SCHEMA_INITIALIZATION_LOCK_KEY},
            )
            lock_acquired = True
            logger.info("acquired PostgreSQL schema initialization lock")

            Base.metadata.create_all(bind=connection)
            ensure_processing_outbox_recovery_schema(connection)
            ensure_processing_transcript_timing_schema(connection)
            connection.commit()
            logger.info("PostgreSQL schema initialization ready")
        except Exception:
            connection.rollback()
            raise
        finally:
            if lock_acquired:
                try:
                    connection.execute(
                        text("SELECT pg_advisory_unlock(:lock_key)"),
                        {"lock_key": _POSTGRES_SCHEMA_INITIALIZATION_LOCK_KEY},
                    )
                    connection.commit()
                    logger.info("released PostgreSQL schema initialization lock")
                except Exception:
                    connection.rollback()
                    logger.exception("failed to explicitly release PostgreSQL schema initialization lock")


def ensure_processing_outbox_recovery_schema(bind: Engine | Connection) -> None:
    inspector = inspect(bind)
    if "processing_outbox_events" not in inspector.get_table_names():
        return

    existing_columns = {column["name"] for column in inspector.get_columns("processing_outbox_events")}
    dialect = bind.dialect.name
    if isinstance(bind, Connection):
        _apply_processing_outbox_recovery_schema(bind, dialect, existing_columns)
    else:
        with bind.begin() as connection:
            _apply_processing_outbox_recovery_schema(connection, dialect, existing_columns)
    logger.info("processing outbox recovery schema verified")


def ensure_processing_transcript_timing_schema(bind: Engine | Connection) -> None:
    inspector = inspect(bind)
    if "processing_request_transcripts" not in inspector.get_table_names():
        return

    existing_columns = {
        column["name"] for column in inspector.get_columns("processing_request_transcripts")
    }
    dialect = bind.dialect.name
    if isinstance(bind, Connection):
        _apply_processing_transcript_timing_schema(bind, dialect, existing_columns)
    else:
        with bind.begin() as connection:
            _apply_processing_transcript_timing_schema(connection, dialect, existing_columns)
    logger.info("processing transcript timing schema verified")


def _apply_processing_transcript_timing_schema(
    connection: Connection,
    dialect: str,
    existing_columns: set[str],
) -> None:
    for column_name, column_type in _TRANSCRIPT_TIMING_COLUMNS.items():
        if dialect == "postgresql":
            connection.execute(text(
                f"ALTER TABLE processing_request_transcripts "
                f"ADD COLUMN IF NOT EXISTS {column_name} {column_type}"
            ))
        elif column_name not in existing_columns:
            connection.execute(text(
                f"ALTER TABLE processing_request_transcripts ADD COLUMN {column_name} {column_type}"
            ))

    if dialect == "postgresql":
        connection.execute(text(
            """
            DO $phase1$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1
                    FROM pg_constraint
                    WHERE conname = 'ck_processing_request_transcript_timing'
                ) THEN
                    ALTER TABLE processing_request_transcripts
                    ADD CONSTRAINT ck_processing_request_transcript_timing CHECK (
                        (start_ms IS NULL AND end_ms IS NULL)
                        OR (
                            start_ms IS NOT NULL
                            AND end_ms IS NOT NULL
                            AND start_ms >= 0
                            AND end_ms >= start_ms
                        )
                    );
                END IF;
            END
            $phase1$;
            """
        ))


def _apply_processing_outbox_recovery_schema(
    connection: Connection,
    dialect: str,
    existing_columns: set[str],
) -> None:
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
