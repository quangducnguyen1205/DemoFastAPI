import logging

from sqlalchemy import Connection, Engine, MetaData, Table, func, inspect, literal, select, text

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

_PROCESSING_LEASE_COLUMNS = {
    "processing_started_at": "TIMESTAMP WITH TIME ZONE",
    "lease_expires_at": "TIMESTAMP WITH TIME ZONE",
    "attempt_count": "INTEGER NOT NULL DEFAULT 0",
}

_PROCESSING_SOURCE_COLUMNS = {
    "source_type": "VARCHAR(32) NOT NULL DEFAULT 'OBJECT_STORAGE'",
    "youtube_video_id": "VARCHAR(64)",
}

_OBJECT_STORAGE_COLUMN_NAMES = (
    "storage_bucket",
    "object_key",
    "original_filename",
    "content_type",
    "size_bytes",
)

# Stable Project3 FastAPI PostgreSQL session advisory lock for schema creation and upgrades.
# This value is intentionally fixed rather than derived from Python's process-randomized hash().
_POSTGRES_SCHEMA_INITIALIZATION_LOCK_KEY = 5_126_144_801


def initialize_database_schema(bind: Engine = engine) -> None:
    from app import models as _models  # noqa: F401

    if bind.dialect.name == "postgresql":
        _initialize_postgresql_schema(bind)
        return

    Base.metadata.create_all(bind=bind)
    ensure_processing_request_source_schema(bind)
    ensure_processing_request_lease_schema(bind)
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
            ensure_processing_request_source_schema(connection)
            ensure_processing_request_lease_schema(connection)
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


def ensure_processing_request_lease_schema(bind: Engine | Connection) -> None:
    # Keep the public narrow upgrader independently usable by older startup/tests that
    # invoke it directly before querying the current ProcessingRequest ORM shape.
    ensure_processing_request_source_schema(bind)
    inspector = inspect(bind)
    if "processing_requests" not in inspector.get_table_names():
        return

    existing_columns = {
        column["name"] for column in inspector.get_columns("processing_requests")
    }
    dialect = bind.dialect.name
    if isinstance(bind, Connection):
        _apply_processing_request_lease_schema(bind, dialect, existing_columns)
    else:
        with bind.begin() as connection:
            _apply_processing_request_lease_schema(connection, dialect, existing_columns)
    logger.info("processing request lease schema verified")


def ensure_processing_request_source_schema(bind: Engine | Connection) -> None:
    inspector = inspect(bind)
    if "processing_requests" not in inspector.get_table_names():
        return

    existing_columns = {
        column["name"]: column
        for column in inspector.get_columns("processing_requests")
    }
    existing_constraints = {
        constraint["name"]
        for constraint in inspector.get_check_constraints("processing_requests")
    }
    dialect = bind.dialect.name
    if isinstance(bind, Connection):
        _apply_processing_request_source_schema(
            bind,
            dialect,
            existing_columns,
            existing_constraints,
        )
    else:
        with bind.begin() as connection:
            _apply_processing_request_source_schema(
                connection,
                dialect,
                existing_columns,
                existing_constraints,
            )
    logger.info("processing request source schema verified")


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


def _apply_processing_request_source_schema(
    connection: Connection,
    dialect: str,
    existing_columns: dict[str, dict],
    existing_constraints: set[str],
) -> None:
    if dialect == "sqlite":
        requires_rebuild = (
            not set(_PROCESSING_SOURCE_COLUMNS).issubset(existing_columns)
            or any(
                not existing_columns[column_name]["nullable"]
                for column_name in _OBJECT_STORAGE_COLUMN_NAMES
                if column_name in existing_columns
            )
            or "ck_processing_request_source_shape" not in existing_constraints
            or "ck_processing_request_youtube_video_id" not in existing_constraints
        )
        if requires_rebuild:
            _rebuild_sqlite_processing_request_source_schema(
                connection,
                set(existing_columns),
            )
        return

    for column_name, column_type in _PROCESSING_SOURCE_COLUMNS.items():
        connection.execute(text(
            f"ALTER TABLE processing_requests "
            f"ADD COLUMN IF NOT EXISTS {column_name} {column_type}"
        ))

    connection.execute(text(
        """
        UPDATE processing_requests
        SET source_type = 'OBJECT_STORAGE'
        WHERE source_type IS NULL
        """
    ))

    if dialect != "postgresql":
        return

    connection.execute(text(
        """
        ALTER TABLE processing_requests
        ALTER COLUMN source_type SET DEFAULT 'OBJECT_STORAGE',
        ALTER COLUMN source_type SET NOT NULL,
        ALTER COLUMN storage_bucket DROP NOT NULL,
        ALTER COLUMN object_key DROP NOT NULL,
        ALTER COLUMN original_filename DROP NOT NULL,
        ALTER COLUMN content_type DROP NOT NULL,
        ALTER COLUMN size_bytes DROP NOT NULL
        """
    ))
    connection.execute(text(
        """
        CREATE INDEX IF NOT EXISTS ix_processing_requests_source_type
        ON processing_requests (source_type)
        """
    ))
    connection.execute(text(
        """
        DO $phase2_source$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint constraint_record
                JOIN pg_class table_record
                  ON table_record.oid = constraint_record.conrelid
                JOIN pg_namespace schema_record
                  ON schema_record.oid = table_record.relnamespace
                WHERE constraint_record.conname = 'ck_processing_request_source_shape'
                  AND constraint_record.contype = 'c'
                  AND table_record.relname = 'processing_requests'
                  AND schema_record.nspname = current_schema()
            ) THEN
                ALTER TABLE processing_requests
                ADD CONSTRAINT ck_processing_request_source_shape CHECK (
                    (
                        source_type = 'OBJECT_STORAGE'
                        AND youtube_video_id IS NULL
                        AND storage_bucket IS NOT NULL
                        AND object_key IS NOT NULL
                        AND content_type IS NOT NULL
                        AND size_bytes IS NOT NULL
                        AND size_bytes >= 0
                    )
                    OR (
                        source_type = 'YOUTUBE'
                        AND youtube_video_id IS NOT NULL
                        AND storage_bucket IS NULL
                        AND object_key IS NULL
                        AND original_filename IS NULL
                        AND content_type IS NULL
                        AND size_bytes IS NULL
                    )
                );
            END IF;

            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint constraint_record
                JOIN pg_class table_record
                  ON table_record.oid = constraint_record.conrelid
                JOIN pg_namespace schema_record
                  ON schema_record.oid = table_record.relnamespace
                WHERE constraint_record.conname =
                      'ck_processing_request_youtube_video_id'
                  AND constraint_record.contype = 'c'
                  AND table_record.relname = 'processing_requests'
                  AND schema_record.nspname = current_schema()
            ) THEN
                ALTER TABLE processing_requests
                ADD CONSTRAINT ck_processing_request_youtube_video_id CHECK (
                    youtube_video_id IS NULL
                    OR (
                        length(youtube_video_id) BETWEEN 1 AND 64
                        AND youtube_video_id ~ '^[A-Za-z0-9_-]+$'
                    )
                );
            END IF;
        END
        $phase2_source$;
        """
    ))


def _rebuild_sqlite_processing_request_source_schema(
    connection: Connection,
    existing_column_names: set[str],
) -> None:
    from app import models

    legacy_metadata = MetaData()
    legacy_table = models.ProcessingRequest.__table__.to_metadata(
        legacy_metadata,
        name="_processing_requests_source_upgrade",
    )
    legacy_table.indexes.clear()
    for constraint in tuple(legacy_table.constraints):
        if (
            constraint.name == "ck_processing_request_youtube_video_id"
            and " ~ " in str(getattr(constraint, "sqltext", ""))
        ):
            legacy_table.constraints.remove(constraint)
    legacy_table.create(connection)

    source_table = Table(
        "processing_requests",
        MetaData(),
        autoload_with=connection,
    )

    selected_values = []
    for column in legacy_table.columns:
        if column.name == "source_type":
            if column.name in existing_column_names:
                selected_values.append(
                    func.coalesce(source_table.c.source_type, literal("OBJECT_STORAGE"))
                )
            else:
                selected_values.append(literal("OBJECT_STORAGE"))
        elif column.name == "youtube_video_id":
            selected_values.append(
                source_table.c.youtube_video_id
                if column.name in existing_column_names
                else literal(None)
            )
        elif column.name == "attempt_count" and column.name not in existing_column_names:
            selected_values.append(literal(0))
        elif column.name in existing_column_names:
            selected_values.append(source_table.c[column.name])
        else:
            selected_values.append(literal(None))

    connection.execute(
        legacy_table.insert().from_select(
            [column.name for column in legacy_table.columns],
            select(*selected_values),
        )
    )
    connection.execute(text("DROP TABLE processing_requests"))
    connection.execute(text(
        "ALTER TABLE _processing_requests_source_upgrade RENAME TO processing_requests"
    ))
    for index in models.ProcessingRequest.__table__.indexes:
        index.create(connection, checkfirst=True)


def _apply_processing_request_lease_schema(
    connection: Connection,
    dialect: str,
    existing_columns: set[str],
) -> None:
    for column_name, column_type in _PROCESSING_LEASE_COLUMNS.items():
        if dialect == "postgresql":
            connection.execute(text(
                f"ALTER TABLE processing_requests "
                f"ADD COLUMN IF NOT EXISTS {column_name} {column_type}"
            ))
        elif column_name not in existing_columns:
            portable_type = column_type.replace("TIMESTAMP WITH TIME ZONE", "TIMESTAMP")
            connection.execute(text(
                f"ALTER TABLE processing_requests ADD COLUMN {column_name} {portable_type}"
            ))

    connection.execute(text(
        """
        UPDATE processing_requests
        SET attempt_count = 0
        WHERE attempt_count IS NULL
        """
    ))
    connection.execute(text(
        """
        UPDATE processing_requests
        SET lease_expires_at = CURRENT_TIMESTAMP
        WHERE status = 'processing'
          AND processing_started_at IS NULL
          AND lease_expires_at IS NULL
          AND attempt_count = 0
        """
    ))

    if dialect == "postgresql":
        connection.execute(text(
            """
            ALTER TABLE processing_requests
            ALTER COLUMN attempt_count SET DEFAULT 0,
            ALTER COLUMN attempt_count SET NOT NULL
            """
        ))
        connection.execute(text(
            """
            DO $phase2$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1
                    FROM pg_constraint constraint_record
                    JOIN pg_class table_record
                      ON table_record.oid = constraint_record.conrelid
                    JOIN pg_namespace schema_record
                      ON schema_record.oid = table_record.relnamespace
                    WHERE constraint_record.conname =
                          'ck_processing_request_attempt_count_nonnegative'
                      AND constraint_record.contype = 'c'
                      AND table_record.relname = 'processing_requests'
                      AND schema_record.nspname = current_schema()
                ) THEN
                    ALTER TABLE processing_requests
                    ADD CONSTRAINT ck_processing_request_attempt_count_nonnegative
                    CHECK (attempt_count >= 0);
                END IF;

                IF NOT EXISTS (
                    SELECT 1
                    FROM pg_constraint constraint_record
                    JOIN pg_class table_record
                      ON table_record.oid = constraint_record.conrelid
                    JOIN pg_namespace schema_record
                      ON schema_record.oid = table_record.relnamespace
                    WHERE constraint_record.conname = 'ck_processing_request_lease_shape'
                      AND constraint_record.contype = 'c'
                      AND table_record.relname = 'processing_requests'
                      AND schema_record.nspname = current_schema()
                ) THEN
                    ALTER TABLE processing_requests
                    ADD CONSTRAINT ck_processing_request_lease_shape CHECK (
                        (
                            processing_started_at IS NULL
                            AND lease_expires_at IS NULL
                        )
                        OR (
                            lease_expires_at IS NOT NULL
                            AND (
                                processing_started_at IS NOT NULL
                                OR attempt_count = 0
                            )
                        )
                    );
                END IF;
            END
            $phase2$;
            """
        ))


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
                    FROM pg_constraint constraint_record
                    JOIN pg_class table_record
                      ON table_record.oid = constraint_record.conrelid
                    JOIN pg_namespace schema_record
                      ON schema_record.oid = table_record.relnamespace
                    WHERE constraint_record.conname = 'ck_processing_request_transcript_timing'
                      AND constraint_record.contype = 'c'
                      AND table_record.relname = 'processing_request_transcripts'
                      AND schema_record.nspname = current_schema()
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
