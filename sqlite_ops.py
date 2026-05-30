import sqlite3
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from config import DB_PATH

log = logging.getLogger(__name__)


@contextmanager
def get_connection(db_path: Path = DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: Path = DB_PATH) -> None:
    """Create all tables if they don't exist."""
    with get_connection(db_path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS collection (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                scryfall_id      TEXT NOT NULL,
                name             TEXT NOT NULL,
                set_code         TEXT,
                collector_number TEXT,
                foil             INTEGER NOT NULL DEFAULT 0,
                condition        TEXT,
                language         TEXT,
                quantity         INTEGER NOT NULL DEFAULT 1,
                purchase_price   REAL,
                date_added       TEXT NOT NULL,
                date_updated     TEXT NOT NULL,
                UNIQUE (scryfall_id, foil, condition, language)
            );

            CREATE TABLE IF NOT EXISTS sync_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                source      TEXT NOT NULL,
                added       INTEGER NOT NULL DEFAULT 0,
                updated     INTEGER NOT NULL DEFAULT 0,
                unchanged   INTEGER NOT NULL DEFAULT 0,
                timestamp   TEXT NOT NULL
            );
        """)
    log.info("Database initialised at %s", db_path)


def upsert_collection_row(conn: sqlite3.Connection, row: dict) -> str:
    """Insert or update a collection row. Returns 'added', 'updated', or 'unchanged'."""
    now = datetime.now(timezone.utc).isoformat()

    existing = conn.execute(
        """
        SELECT id, quantity, purchase_price, condition, language
        FROM collection
        WHERE scryfall_id = ? AND foil = ? AND condition = ? AND language = ?
        """,
        (row["scryfall_id"], row["foil"], row["condition"], row["language"]),
    ).fetchone()

    if existing is None:
        conn.execute(
            """
            INSERT INTO collection
                (scryfall_id, name, set_code, collector_number, foil,
                 condition, language, quantity, purchase_price, date_added, date_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["scryfall_id"], row["name"], row["set_code"],
                row["collector_number"], row["foil"], row["condition"],
                row["language"], row["quantity"], row["purchase_price"],
                now, now,
            ),
        )
        return "added"

    changed = (
        existing["quantity"] != row["quantity"]
        or existing["purchase_price"] != row["purchase_price"]
    )
    if not changed:
        return "unchanged"

    conn.execute(
        """
        UPDATE collection
        SET quantity = ?, purchase_price = ?, date_updated = ?
        WHERE id = ?
        """,
        (row["quantity"], row["purchase_price"], now, existing["id"]),
    )
    return "updated"


def write_sync_log(conn: sqlite3.Connection, source: str, added: int, updated: int, unchanged: int) -> None:
    conn.execute(
        "INSERT INTO sync_log (source, added, updated, unchanged, timestamp) VALUES (?, ?, ?, ?, ?)",
        (source, added, updated, unchanged, datetime.now(timezone.utc).isoformat()),
    )
