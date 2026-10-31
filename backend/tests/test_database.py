from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, inspect
from sqlalchemy.pool import StaticPool

from app import database
from app.config import Settings


def test_sqlite_engine_enables_wal_and_busy_timeout(monkeypatch):
    db_file = Path.cwd() / f"sqlite-wal-test-{uuid4().hex}.db"
    db_path = db_file.as_posix()
    monkeypatch.setattr(
        database, "get_settings",
        lambda: Settings(_env_file=None, database_url=f"sqlite:///{db_path}"),
    )
    engine = database._make_engine()
    try:
        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA journal_mode").scalar_one().lower() == "wal"
            assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one() == 5000
    finally:
        engine.dispose()
        db_file.unlink(missing_ok=True)
        Path(f"{db_file}-wal").unlink(missing_ok=True)
        Path(f"{db_file}-shm").unlink(missing_ok=True)


def test_additive_schema_adds_moderation_checked_marker_to_existing_chat_table(monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool)
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE chat_messages (event_id VARCHAR(128) PRIMARY KEY)")
    monkeypatch.setattr(database, "engine", engine)
    database.upgrade_schema()
    try:
        columns = {column["name"]: column for column in inspect(engine).get_columns("chat_messages")}
        assert columns["moderation_ai_checked"]["default"] == "0"
    finally:
        engine.dispose()
