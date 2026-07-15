import unittest
from unittest.mock import MagicMock, call, patch

from sqlalchemy import create_engine, inspect

from app.bootstrap import api as api_bootstrap
from app.bootstrap import consumer as consumer_bootstrap
from app.core import schema
from app.core import celery_app as celery_app_module
from app.core.database import Base
from app.relays import processing_outbox_auto_relay


class PostgreSqlSchemaInitializationLockTest(unittest.TestCase):
    def _postgresql_bind(self):
        bind = MagicMock()
        bind.dialect.name = "postgresql"
        connection = MagicMock()
        bind.connect.return_value.__enter__.return_value = connection
        return bind, connection

    def test_postgresql_initialization_holds_the_session_lock_through_creation_and_upgrade(self) -> None:
        bind, connection = self._postgresql_bind()
        order: list[str] = []

        def execute(statement, *_args, **_kwargs):
            sql = str(statement)
            if "pg_advisory_lock" in sql:
                order.append("lock")
            elif "pg_advisory_unlock" in sql:
                order.append("unlock")

        connection.execute.side_effect = execute
        with (
            patch.object(
                Base.metadata,
                "create_all",
                side_effect=lambda **_kwargs: order.append("create_all"),
            ) as create_all,
            patch.object(
                schema,
                "ensure_processing_outbox_recovery_schema",
                side_effect=lambda _bind: order.append("upgrade"),
            ) as upgrade,
        ):
            schema.initialize_database_schema(bind)

        self.assertEqual(order, ["lock", "create_all", "upgrade", "unlock"])
        create_all.assert_called_once_with(bind=connection)
        upgrade.assert_called_once_with(connection)
        self.assertEqual(
            connection.execute.call_args_list[0],
            call(
                unittest.mock.ANY,
                {"lock_key": schema._POSTGRES_SCHEMA_INITIALIZATION_LOCK_KEY},
            ),
        )
        self.assertEqual(
            connection.execute.call_args_list[-1],
            call(
                unittest.mock.ANY,
                {"lock_key": schema._POSTGRES_SCHEMA_INITIALIZATION_LOCK_KEY},
            ),
        )
        self.assertEqual(connection.commit.call_count, 2)
        connection.rollback.assert_not_called()

    def test_postgresql_initialization_releases_the_lock_after_failure(self) -> None:
        bind, connection = self._postgresql_bind()
        executed_sql: list[str] = []
        connection.execute.side_effect = lambda statement, *_args, **_kwargs: executed_sql.append(str(statement))

        with patch.object(Base.metadata, "create_all", side_effect=RuntimeError("creation failed")):
            with self.assertRaisesRegex(RuntimeError, "creation failed"):
                schema.initialize_database_schema(bind)

        self.assertTrue(any("pg_advisory_lock" in statement for statement in executed_sql))
        self.assertTrue(any("pg_advisory_unlock" in statement for statement in executed_sql))
        connection.rollback.assert_called_once_with()
        self.assertEqual(connection.commit.call_count, 1)

    def test_sqlite_initialization_does_not_open_a_postgresql_lock_connection(self) -> None:
        bind = MagicMock()
        bind.dialect.name = "sqlite"
        with (
            patch.object(Base.metadata, "create_all") as create_all,
            patch.object(schema, "ensure_processing_outbox_recovery_schema") as upgrade,
        ):
            schema.initialize_database_schema(bind)

        bind.connect.assert_not_called()
        create_all.assert_called_once_with(bind=bind)
        upgrade.assert_called_once_with(bind)

    def test_repeated_sqlite_initialization_remains_idempotent(self) -> None:
        bind = create_engine("sqlite+pysqlite:///:memory:")
        try:
            schema.initialize_database_schema(bind)
            schema.initialize_database_schema(bind)
            self.assertIn("processing_outbox_events", inspect(bind).get_table_names())
        finally:
            bind.dispose()


class SchemaInitializerEntrypointTest(unittest.TestCase):
    def test_api_consumer_worker_and_auto_relay_keep_delegating_to_the_canonical_initializer(self) -> None:
        with patch.object(api_bootstrap, "initialize_database_schema") as api_initializer:
            api_bootstrap.initialize_api_database()
        api_initializer.assert_called_once_with()

        consumer_runner = MagicMock()
        with (
            patch.object(consumer_bootstrap, "initialize_database_schema") as consumer_initializer,
            patch(
                "app.consumers.asset_processing_consumer.AssetProcessingKafkaConsumer",
                return_value=consumer_runner,
            ),
            patch.object(consumer_bootstrap.signal, "signal"),
        ):
            consumer_bootstrap.run_processing_consumer()
        consumer_initializer.assert_called_once_with()
        consumer_runner.run_forever.assert_called_once_with()

        with patch.object(celery_app_module, "initialize_database_schema") as worker_initializer:
            celery_app_module.initialize_worker_database_schema()
        worker_initializer.assert_called_once_with()

        shutdown_requested = MagicMock()
        shutdown_requested.is_set.return_value = True
        publisher = MagicMock()
        with (
            patch.object(processing_outbox_auto_relay.settings, "PROCESSING_OUTBOX_AUTO_RELAY_ENABLED", True),
            patch.object(processing_outbox_auto_relay.settings, "PROCESSING_RESULT_PUBLISHER_ENABLED", True),
            patch.object(processing_outbox_auto_relay, "initialize_database_schema") as relay_initializer,
            patch.object(processing_outbox_auto_relay, "Event", return_value=shutdown_requested),
            patch.object(processing_outbox_auto_relay.signal, "signal"),
            patch.object(processing_outbox_auto_relay, "build_result_publisher", return_value=publisher),
        ):
            self.assertEqual(processing_outbox_auto_relay.main(), 0)
        relay_initializer.assert_called_once_with()
        publisher.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
