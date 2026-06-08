import os
import sqlite3
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()
DATABASE_PATH = os.getenv("DATABASE_PATH", "memory.db")


def get_connection() -> sqlite3.Connection:
    try:
        connection = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS preferences (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                description TEXT DEFAULT ''
            )
            """
        )
        connection.commit()
        return connection
    except sqlite3.Error as error:
        raise RuntimeError(f"Failed to connect to database: {error}") from error


def save_preference(key: str, value: str, description: str = "") -> None:
    if not key:
        raise ValueError("Preference key must be provided")

    try:
        with get_connection() as connection:
            connection.execute(
                "INSERT INTO preferences (key, value, description) VALUES (?, ?, ?)",
                (key, value, description),
            )
    except sqlite3.IntegrityError as error:
        raise ValueError(f"Preference with key '{key}' already exists") from error
    except sqlite3.Error as error:
        raise RuntimeError(f"Failed to save preference: {error}") from error


def get_preference(key: str) -> Optional[Dict[str, str]]:
    if not key:
        raise ValueError("Preference key must be provided")

    try:
        with get_connection() as connection:
            cursor = connection.execute(
                "SELECT key, value, description FROM preferences WHERE key = ?",
                (key,),
            )
            row = cursor.fetchone()

        if row is None:
            return None

        return {"key": row[0], "value": row[1], "description": row[2]}
    except sqlite3.Error as error:
        raise RuntimeError(f"Failed to get preference: {error}") from error


def get_all_preferences() -> List[Dict[str, str]]:
    try:
        with get_connection() as connection:
            cursor = connection.execute(
                "SELECT key, value, description FROM preferences"
            )
            rows = cursor.fetchall()

        return [
            {"key": row[0], "value": row[1], "description": row[2]}
            for row in rows
        ]
    except sqlite3.Error as error:
        raise RuntimeError(f"Failed to get all preferences: {error}") from error


def update_preference(key: str, value: str) -> None:
    if not key:
        raise ValueError("Preference key must be provided")

    try:
        with get_connection() as connection:
            cursor = connection.execute(
                "UPDATE preferences SET value = ? WHERE key = ?",
                (value, key),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"Preference with key '{key}' does not exist")
    except sqlite3.Error as error:
        raise RuntimeError(f"Failed to update preference: {error}") from error


def delete_preference(key: str) -> None:
    if not key:
        raise ValueError("Preference key must be provided")

    try:
        with get_connection() as connection:
            cursor = connection.execute(
                "DELETE FROM preferences WHERE key = ?",
                (key,),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"Preference with key '{key}' does not exist")
    except sqlite3.Error as error:
        raise RuntimeError(f"Failed to delete preference: {error}") from error
