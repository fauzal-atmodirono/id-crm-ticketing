from chatbot.features.chat.kb_db import _to_async_url


def test_postgres_url_upgraded() -> None:
    assert _to_async_url("postgresql://u:p@h/db") == "postgresql+psycopg://u:p@h/db"


def test_sqlite_url_upgraded() -> None:
    assert _to_async_url("sqlite:///x.db") == "sqlite+aiosqlite:///x.db"


def test_already_async_untouched() -> None:
    assert _to_async_url("postgresql+psycopg://h/db") == "postgresql+psycopg://h/db"
