import os
import sqlite3
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", override=True)


def storage_dir():
    folder = Path(
        os.getenv("HELPDESK_STORAGE_DIR", "local-storage")
    )

    if not folder.is_absolute():
        folder = BASE_DIR / folder

    folder.mkdir(parents=True, exist_ok=True)
    return folder


def open_database():
    connection = sqlite3.connect(
        storage_dir() / "users.sqlite"
    )

    connection.execute("""
        CREATE TABLE IF NOT EXISTS preferences (
            user_id TEXT PRIMARY KEY,
            language TEXT NOT NULL
        )
    """)

    connection.commit()
    return connection


def save_language(user_id, language):
    if not language.strip():
        raise ValueError("Language cannot be empty.")

    connection = open_database()

    try:
        connection.execute(
            """
            INSERT INTO preferences (user_id, language)
            VALUES (?, ?)
            ON CONFLICT(user_id)
            DO UPDATE SET language = excluded.language
            """,
            (user_id, language.strip()),
        )
        connection.commit()
    finally:
        connection.close()


def load_language(user_id):
    connection = open_database()

    try:
        row = connection.execute(
            "SELECT language FROM preferences WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    finally:
        connection.close()

    return row[0] if row else None