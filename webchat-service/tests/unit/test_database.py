from pathlib import Path

from webchat.persistence.database import Database


def test_migrations_are_repeatable(tmp_path: Path) -> None:
    database = Database(tmp_path / "webchat.sqlite3")
    database.migrate()
    database.migrate()

    with database.connect() as connection:
        tables = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]

    assert {"conversations", "messages", "turns"}.issubset(tables)
    assert journal_mode == "wal"
