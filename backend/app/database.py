from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def _make_engine():
    url = get_settings().database_url
    is_sqlite = url.startswith("sqlite")
    options = {"check_same_thread": False, "timeout": 5} if is_sqlite else {}
    db_engine = create_engine(url, connect_args=options, pool_pre_ping=True)
    if is_sqlite:
        @event.listens_for(db_engine, "connect")
        def configure_sqlite_connection(connection, _record):
            cursor = connection.cursor()
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()
    return db_engine


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def upgrade_schema() -> None:
    """Apply safe additive SQLite changes for databases created by earlier scaffold revisions."""
    if engine.dialect.name != "sqlite":
        return
    additive_columns = {
        "stream_sessions": {
            "overlay_token_hash": "VARCHAR(64)",
            "featured_interaction_group_id": "INTEGER",
        },
        "chat_messages": {
            "moderation_ai_checked": "BOOLEAN NOT NULL DEFAULT 0",
        },
        "twitch_credentials": {
            "auth_session_hash": "VARCHAR(64)",
            "auth_session_expires_at": "DATETIME",
        },
        "broadcaster_settings": {
            "native_twitch_polls_enabled": "BOOLEAN NOT NULL DEFAULT 0",
        },
        "poll_suggestions": {
            "approved_challenge_id": "INTEGER REFERENCES challenges(id)",
        },
    }
    with engine.begin() as connection:
        tables = set(inspect(connection).get_table_names())
        for table, columns in additive_columns.items():
            if table not in tables:
                continue
            existing = {column["name"] for column in inspect(connection).get_columns(table)}
            for column, sql_type in columns.items():
                if column not in existing:
                    connection.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {sql_type}'))
