from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from pathlib import Path

BASE = Path(__file__).resolve().parent
DB_PATH = BASE / os.getenv("DB_PATH", "catalog.db")

BACKUP_DIR = os.getenv("BACKUP_DIR", "backups")
BACKUP_KEEP = int(os.getenv("BACKUP_KEEP", "20") or "20")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def db_query(sql: str, params: tuple | list = ()) -> list[sqlite3.Row]:
    with closing(connect()) as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur.fetchall()


def db_exec(sql: str, params: tuple | list = ()) -> int:
    with closing(connect()) as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        conn.commit()
        return cur.lastrowid


def get_setting(key: str) -> str | None:
    rows = db_query("SELECT value FROM settings WHERE key=?", (key,))
    return rows[0]["value"] if rows else None


def set_setting(key: str, value: str) -> None:
    db_exec(
        """INSERT INTO settings(key,value)
           VALUES(?,?)
           ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
        (key, value),
    )


def init_db() -> None:
    with closing(connect()) as conn:
        cur = conn.cursor()
        cur.executescript(
            """
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS categories (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT    NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS products (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id INTEGER,
    name        TEXT    NOT NULL,
    description TEXT,
    price       INTEGER NOT NULL DEFAULT 0,
    stock       INTEGER NOT NULL DEFAULT 0,
    is_active   INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS product_categories (
    product_id  INTEGER NOT NULL,
    category_id INTEGER NOT NULL,
    PRIMARY KEY (product_id, category_id),
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE,
    FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS photos (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL,
    file_id    TEXT    NOT NULL,
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS product_variants (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL,
    name       TEXT    NOT NULL,
    stock      INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS users (
    user_id    INTEGER PRIMARY KEY,
    username   TEXT,
    first_name TEXT,
    last_name  TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS editors (
    user_id       INTEGER PRIMARY KEY,
    username      TEXT,
    is_active     INTEGER NOT NULL DEFAULT 1,
    perm_cats     INTEGER NOT NULL DEFAULT 1,
    perm_prods    INTEGER NOT NULL DEFAULT 1,
    perm_photos   INTEGER NOT NULL DEFAULT 1,
    perm_links    INTEGER NOT NULL DEFAULT 1,
    perm_welcome  INTEGER NOT NULL DEFAULT 1,
    perm_reserve  INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    scope      TEXT    NOT NULL,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""
        )
        conn.commit()

    # дефолты настроек
    defaults = {
        "welcome_html": "Каталог. Нажмите «Открыть витрину».",
        "auto_delete_user": "1",
        "auto_delete_admin": "0",
        "reserve_enabled": "1",
        "reserve_text": "Забронировать",
        "reserve_mode": "wa",  # wa | raw
        "reserve_phone": "",
        "reserve_msg_tpl": "Здравствуйте, хочу забронировать украшение {name} (ID: {id}, Цена: {price})",
        "reserve_url": "https://example.com/reserve?pid={id}&name={name}&price={price}",
        "links_json": "[]",
    }
    for k, v in defaults.items():
        if get_setting(k) is None:
            set_setting(k, v)


# инициализация при импорте
init_db()
