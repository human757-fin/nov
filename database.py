"""Small SQLite store for per-server bot settings."""

import sqlite3
import ssl
from contextlib import closing
from functools import lru_cache
from pathlib import Path

import config

DB_PATH = Path(__file__).parent / "catgirl.sqlite3"
DEFAULT_PREFIX = "!"


class _Connection:
    def __init__(self, raw, engine: str):
        self.raw = raw
        self.engine = engine

    def execute(self, query: str, params=()):
        if self.engine == "sqlite":
            return self.raw.execute(query, params)
        cursor = self.raw.cursor()
        cursor.execute(query.replace("?", "%s"), params)
        return cursor

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if self.engine == "sqlite":
            return self.raw.__exit__(exc_type, exc_value, traceback)
        if exc_type is None:
            self.raw.commit()
        else:
            self.raw.rollback()
        return False

    def close(self):
        self.raw.close()


def _connect() -> _Connection:
    engine = config.DATABASE_ENGINE
    if engine in {"mysql", "mariadb"}:
        required = {
            "DB_HOST": config.DATABASE_HOST,
            "DB_NAME": config.DATABASE_NAME,
            "DB_USERNAME": config.DATABASE_USERNAME,
            "DB_PASSWORD": config.DATABASE_PASSWORD,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise RuntimeError(
                "Missing database environment settings: " + ", ".join(missing)
            )
        try:
            import pymysql
        except ImportError as error:
            raise RuntimeError(
                "MySQL/MariaDB support needs PyMySQL; install the project requirements."
            ) from error

        tls_options = {}
        if config.DATABASE_TLS:
            tls_context = ssl.create_default_context(cafile=config.DATABASE_TLS_CA)
            tls_options["ssl"] = tls_context
        else:
            tls_options["ssl_disabled"] = True
        raw = pymysql.connect(
            host=config.DATABASE_HOST,
            port=config.DATABASE_PORT,
            user=config.DATABASE_USERNAME,
            password=config.DATABASE_PASSWORD,
            database=config.DATABASE_NAME,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=10,
            autocommit=False,
            **tls_options,
        )
        return _Connection(raw, "mysql")
    if engine not in {"sqlite", ""}:
        raise RuntimeError("DB_ENGINE must be sqlite, mysql, or mariadb.")
    raw = sqlite3.connect(DB_PATH)
    raw.row_factory = sqlite3.Row
    return _Connection(raw, "sqlite")


def initialize() -> None:
    with closing(_connect()) as connection, connection:
        # Serialize schema creation/migrations because Pterodactyl starts the
        # web panel and bot as separate processes against the same database.
        is_mysql = connection.engine == "mysql"
        lock_name = "nov_bot_schema"
        if is_mysql:
            lock = connection.execute(
                "SELECT GET_LOCK(?, 10) AS acquired", (lock_name,)
            ).fetchone()
            if lock is None or lock["acquired"] != 1:
                raise RuntimeError("Could not acquire the database schema lock.")
        else:
            connection.execute("BEGIN IMMEDIATE")

        try:
            if is_mysql:
                connection.execute(
                    """CREATE TABLE IF NOT EXISTS guild_settings (
                        guild_id BIGINT NOT NULL PRIMARY KEY,
                        command_prefix VARCHAR(5) NOT NULL DEFAULT '!',
                        welcome_channel_id BIGINT NULL,
                        welcome_message VARCHAR(4096) NOT NULL DEFAULT 'Welcome {mention} to {server}! You are member #{member_count}.',
                        welcome_title VARCHAR(256) NOT NULL DEFAULT 'Welcome to {server}!',
                        welcome_color VARCHAR(7) NOT NULL DEFAULT '#FF9ECF'
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""
                )
                guild_columns = {
                    row["Field"]
                    for row in connection.execute("SHOW COLUMNS FROM guild_settings").fetchall()
                }
                guild_migrations = {
                    "command_prefix": "VARCHAR(5) NOT NULL DEFAULT '!'",
                    "welcome_channel_id": "BIGINT NULL",
                    "welcome_message": "VARCHAR(4096) NOT NULL DEFAULT 'Welcome {mention} to {server}! You are member #{member_count}.'",
                    "welcome_title": "VARCHAR(256) NOT NULL DEFAULT 'Welcome to {server}!'",
                    "welcome_color": "VARCHAR(7) NOT NULL DEFAULT '#FF9ECF'",
                }
                for column, definition in guild_migrations.items():
                    if column not in guild_columns:
                        connection.execute(
                            f"ALTER TABLE guild_settings ADD COLUMN {column} {definition}"
                        )
                connection.execute(
                    """CREATE TABLE IF NOT EXISTS feature_suggestions (
                        id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        guild_id BIGINT NOT NULL,
                        user_id BIGINT NOT NULL,
                        submitter VARCHAR(256) NOT NULL,
                        suggestion TEXT NOT NULL,
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        status VARCHAR(16) NOT NULL DEFAULT 'pending',
                        status_note VARCHAR(500) NULL,
                        status_updated_by BIGINT NULL,
                        status_updated_at TIMESTAMP NULL,
                        INDEX feature_suggestions_guild_id_id (guild_id, id)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""
                )
                columns = {
                    row["Field"]
                    for row in connection.execute(
                        "SHOW COLUMNS FROM feature_suggestions"
                    ).fetchall()
                }
                migrations = {
                    "status": "VARCHAR(16) NOT NULL DEFAULT 'pending'",
                    "status_note": "VARCHAR(500) NULL",
                    "status_updated_by": "BIGINT NULL",
                    "status_updated_at": "TIMESTAMP NULL",
                }
            else:
                connection.execute(
                    """CREATE TABLE IF NOT EXISTS guild_settings (
                        guild_id INTEGER PRIMARY KEY,
                        command_prefix TEXT NOT NULL DEFAULT '!',
                        welcome_channel_id INTEGER,
                        welcome_message TEXT NOT NULL DEFAULT 'Welcome {mention} to {server}! You are member #{member_count}.',
                        welcome_title TEXT NOT NULL DEFAULT 'Welcome to {server}!',
                        welcome_color TEXT NOT NULL DEFAULT '#FF9ECF'
                    )"""
                )
                guild_columns = {
                    row["name"]
                    for row in connection.execute("PRAGMA table_info(guild_settings)")
                }
                guild_migrations = {
                    "command_prefix": "TEXT NOT NULL DEFAULT '!'",
                    "welcome_channel_id": "INTEGER",
                    "welcome_message": "TEXT NOT NULL DEFAULT 'Welcome {mention} to {server}! You are member #{member_count}.'",
                    "welcome_title": "TEXT NOT NULL DEFAULT 'Welcome to {server}!'",
                    "welcome_color": "TEXT NOT NULL DEFAULT '#FF9ECF'",
                }
                for column, definition in guild_migrations.items():
                    if column not in guild_columns:
                        connection.execute(
                            f"ALTER TABLE guild_settings ADD COLUMN {column} {definition}"
                        )
                connection.execute(
                    """CREATE TABLE IF NOT EXISTS feature_suggestions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        guild_id INTEGER NOT NULL,
                        user_id INTEGER NOT NULL,
                        submitter TEXT NOT NULL,
                        suggestion TEXT NOT NULL,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        status TEXT NOT NULL DEFAULT 'pending',
                        status_note TEXT,
                        status_updated_by INTEGER,
                        status_updated_at TEXT
                    )"""
                )
                columns = {
                    row["name"]
                    for row in connection.execute(
                        "PRAGMA table_info(feature_suggestions)"
                    )
                }
                migrations = {
                    "status": "TEXT NOT NULL DEFAULT 'pending'",
                    "status_note": "TEXT",
                    "status_updated_by": "INTEGER",
                    "status_updated_at": "TEXT",
                }
            for column, definition in migrations.items():
                if column not in columns:
                    connection.execute(
                        f"ALTER TABLE feature_suggestions ADD COLUMN {column} {definition}"
                    )
            if is_mysql:
                connection.execute(
                    """CREATE TABLE IF NOT EXISTS ticket_settings (
                        guild_id BIGINT NOT NULL PRIMARY KEY,
                        panel_channel_id BIGINT NULL,
                        panel_message_id BIGINT NULL,
                        log_channel_id BIGINT NULL,
                        admin_role_ids TEXT NULL
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""
                )
                connection.execute(
                    """CREATE TABLE IF NOT EXISTS ticket_categories (
                        guild_id BIGINT NOT NULL,
                        category_key VARCHAR(32) NOT NULL,
                        position INTEGER NOT NULL,
                        name VARCHAR(100) NOT NULL,
                        emoji VARCHAR(64) NULL,
                        description VARCHAR(100) NOT NULL,
                        parent_category_id BIGINT NULL,
                        PRIMARY KEY (guild_id, category_key),
                        INDEX ticket_categories_order (guild_id, position)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""
                )
                connection.execute(
                    """CREATE TABLE IF NOT EXISTS tickets (
                        id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        guild_id BIGINT NOT NULL,
                        channel_id BIGINT NULL,
                        user_id BIGINT NOT NULL,
                        category_key VARCHAR(32) NOT NULL,
                        category_name VARCHAR(100) NOT NULL,
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        status VARCHAR(16) NOT NULL DEFAULT 'provisioning',
                        close_reason VARCHAR(100) NULL,
                        closed_by BIGINT NULL,
                        closed_at TIMESTAMP NULL,
                        INDEX tickets_open_lookup (guild_id, user_id, status),
                        INDEX tickets_channel_lookup (channel_id)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""
                )
            else:
                connection.execute(
                    """CREATE TABLE IF NOT EXISTS ticket_settings (
                        guild_id INTEGER PRIMARY KEY,
                        panel_channel_id INTEGER,
                        panel_message_id INTEGER,
                        log_channel_id INTEGER,
                        admin_role_ids TEXT NOT NULL DEFAULT ''
                    )"""
                )
                connection.execute(
                    """CREATE TABLE IF NOT EXISTS ticket_categories (
                        guild_id INTEGER NOT NULL,
                        category_key TEXT NOT NULL,
                        position INTEGER NOT NULL,
                        name TEXT NOT NULL,
                        emoji TEXT,
                        description TEXT NOT NULL,
                        parent_category_id INTEGER,
                        PRIMARY KEY (guild_id, category_key)
                    )"""
                )
                connection.execute(
                    """CREATE TABLE IF NOT EXISTS tickets (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        guild_id INTEGER NOT NULL,
                        channel_id INTEGER,
                        user_id INTEGER NOT NULL,
                        category_key TEXT NOT NULL,
                        category_name TEXT NOT NULL,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        status TEXT NOT NULL DEFAULT 'provisioning',
                        close_reason TEXT,
                        closed_by INTEGER,
                        closed_at TEXT
                    )"""
                )
        finally:
            if is_mysql:
                connection.execute("SELECT RELEASE_LOCK(?)", (lock_name,)).fetchone()


def get_guild_settings(guild_id: int) -> dict:
    with closing(_connect()) as connection, connection:
        row = connection.execute(
            "SELECT * FROM guild_settings WHERE guild_id = ?", (guild_id,)
        ).fetchone()
    settings = {
        "guild_id": guild_id,
        "command_prefix": DEFAULT_PREFIX,
        "welcome_channel_id": None,
        "welcome_message": "Welcome {mention} to {server}! You are member #{member_count}.",
        "welcome_title": "Welcome to {server}!",
        "welcome_color": "#FF9ECF",
    }
    if row:
        settings.update(
            {key: value for key, value in dict(row).items() if value is not None}
        )
    return settings


@lru_cache(maxsize=1024)
def get_command_prefix(guild_id: int) -> str:
    with closing(_connect()) as connection, connection:
        row = connection.execute(
            "SELECT command_prefix FROM guild_settings WHERE guild_id = ?",
            (guild_id,),
        ).fetchone()
    return row["command_prefix"] if row and row["command_prefix"] else DEFAULT_PREFIX


def set_command_prefix(guild_id: int, prefix: str) -> None:
    with closing(_connect()) as connection, connection:
        insert_statement = (
            "INSERT IGNORE INTO guild_settings (guild_id) VALUES (?)"
            if connection.engine == "mysql"
            else "INSERT OR IGNORE INTO guild_settings (guild_id) VALUES (?)"
        )
        connection.execute(insert_statement, (guild_id,))
        connection.execute(
            "UPDATE guild_settings SET command_prefix = ? WHERE guild_id = ?",
            (prefix, guild_id),
        )
    get_command_prefix.cache_clear()


def get_dashboard_guild_ids() -> list[int]:
    """Return guilds with saved settings or suggestions for the management panel."""
    with closing(_connect()) as connection, connection:
        rows = connection.execute(
            """SELECT guild_id FROM guild_settings
            UNION SELECT guild_id FROM feature_suggestions
            UNION SELECT guild_id FROM ticket_settings
            ORDER BY guild_id"""
        ).fetchall()
    return [row["guild_id"] for row in rows]


def get_dashboard_suggestions(guild_id: int, limit: int = 100) -> list[dict]:
    """Return recent suggestions and review fields for the management panel."""
    with closing(_connect()) as connection, connection:
        rows = connection.execute(
            """SELECT id, user_id, submitter, suggestion, created_at, status, status_note
            FROM feature_suggestions WHERE guild_id = ?
            ORDER BY id DESC LIMIT ?""",
            (guild_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def update_guild_setting(guild_id: int, setting: str, value) -> None:
    allowed = {
        "welcome_channel_id",
        "welcome_message",
        "welcome_title",
        "welcome_color",
    }
    if setting not in allowed:
        raise ValueError(f"Unsupported setting: {setting}")
    with closing(_connect()) as connection, connection:
        insert_statement = (
            "INSERT IGNORE INTO guild_settings (guild_id) VALUES (?)"
            if connection.engine == "mysql"
            else "INSERT OR IGNORE INTO guild_settings (guild_id) VALUES (?)"
        )
        connection.execute(insert_statement, (guild_id,))
        connection.execute(
            f"UPDATE guild_settings SET {setting} = ? WHERE guild_id = ?",
            (value, guild_id),
        )


def add_suggestion(guild_id: int, user_id: int, submitter: str, suggestion: str) -> int:
    with closing(_connect()) as connection, connection:
        cursor = connection.execute(
            """INSERT INTO feature_suggestions (guild_id, user_id, submitter, suggestion)
            VALUES (?, ?, ?, ?)""",
            (guild_id, user_id, submitter, suggestion),
        )
        return cursor.lastrowid


def count_suggestions(guild_id: int) -> int:
    with closing(_connect()) as connection, connection:
        row = connection.execute(
            "SELECT COUNT(*) AS total FROM feature_suggestions WHERE guild_id = ?",
            (guild_id,),
        ).fetchone()
    return row["total"]


def get_suggestions(guild_id: int, limit: int, offset: int) -> list[dict]:
    with closing(_connect()) as connection, connection:
        rows = connection.execute(
            """SELECT id, user_id, submitter, suggestion, created_at
            FROM feature_suggestions WHERE guild_id = ?
            ORDER BY id DESC LIMIT ? OFFSET ?""",
            (guild_id, limit, offset),
        ).fetchall()
    return [dict(row) for row in rows]


def count_public_features(guild_id: int) -> int:
    with closing(_connect()) as connection, connection:
        row = connection.execute(
            """SELECT COUNT(*) AS total FROM feature_suggestions
            WHERE guild_id = ? AND status IN ('approved', 'implemented')""",
            (guild_id,),
        ).fetchone()
    return row["total"]


def get_public_features(guild_id: int, limit: int, offset: int) -> list[dict]:
    with closing(_connect()) as connection, connection:
        rows = connection.execute(
            """SELECT id, suggestion, status, status_updated_at
            FROM feature_suggestions
            WHERE guild_id = ? AND status IN ('approved', 'implemented')
            ORDER BY CASE status WHEN 'approved' THEN 0 ELSE 1 END,
                COALESCE(status_updated_at, created_at) DESC, id DESC
            LIMIT ? OFFSET ?""",
            (guild_id, limit, offset),
        ).fetchall()
    return [dict(row) for row in rows]


def count_user_suggestions(guild_id: int, user_id: int) -> int:
    with closing(_connect()) as connection, connection:
        row = connection.execute(
            """SELECT COUNT(*) AS total FROM feature_suggestions
            WHERE guild_id = ? AND user_id = ?""",
            (guild_id, user_id),
        ).fetchone()
    return row["total"]


def get_user_suggestions(
    guild_id: int, user_id: int, limit: int, offset: int
) -> list[dict]:
    with closing(_connect()) as connection, connection:
        rows = connection.execute(
            """SELECT id, suggestion, created_at, status, status_note, status_updated_at
            FROM feature_suggestions WHERE guild_id = ? AND user_id = ?
            ORDER BY id DESC LIMIT ? OFFSET ?""",
            (guild_id, user_id, limit, offset),
        ).fetchall()
    return [dict(row) for row in rows]


def set_suggestion_status(
    guild_id: int,
    suggestion_id: int,
    status: str,
    note: str | None,
    reviewer_id: int,
) -> dict | None:
    allowed = {"pending", "approved", "denied", "implemented"}
    if status not in allowed:
        raise ValueError(f"Unsupported suggestion status: {status}")
    with closing(_connect()) as connection, connection:
        row = connection.execute(
            """SELECT id, user_id, submitter, suggestion, status
            FROM feature_suggestions WHERE guild_id = ? AND id = ?""",
            (guild_id, suggestion_id),
        ).fetchone()
        if row is None:
            return None
        previous_status = row["status"]
        connection.execute(
            """UPDATE feature_suggestions
            SET status = ?, status_note = ?, status_updated_by = ?,
                status_updated_at = CURRENT_TIMESTAMP
            WHERE guild_id = ? AND id = ?""",
            (status, note, reviewer_id, guild_id, suggestion_id),
        )
    result = dict(row)
    result["previous_status"] = previous_status
    result["status"] = status
    result["status_note"] = note
    return result


def get_ticket_config(guild_id: int) -> dict:
    with closing(_connect()) as connection, connection:
        row = connection.execute(
            "SELECT * FROM ticket_settings WHERE guild_id = ?", (guild_id,)
        ).fetchone()
        categories = connection.execute(
            """SELECT category_key, position, name, emoji, description, parent_category_id
            FROM ticket_categories WHERE guild_id = ? ORDER BY position""",
            (guild_id,),
        ).fetchall()
    result = {
        "guild_id": guild_id,
        "panel_channel_id": None,
        "panel_message_id": None,
        "log_channel_id": None,
        "admin_role_ids": [],
        "categories": [dict(category) for category in categories],
    }
    if row:
        saved = dict(row)
        result.update(saved)
        result["admin_role_ids"] = [
            int(role_id)
            for role_id in (saved.get("admin_role_ids") or "").split(",")
            if role_id.isdigit()
        ]
    return result


def get_all_ticket_configs() -> list[dict]:
    with closing(_connect()) as connection, connection:
        rows = connection.execute(
            "SELECT guild_id FROM ticket_settings ORDER BY guild_id"
        ).fetchall()
    return [get_ticket_config(int(row["guild_id"])) for row in rows]


def save_ticket_config(
    guild_id: int,
    panel_channel_id: int | None,
    log_channel_id: int | None,
    admin_role_ids: list[int],
    categories: list[dict],
) -> None:
    roles_value = ",".join(str(role_id) for role_id in admin_role_ids)
    with closing(_connect()) as connection, connection:
        insert_statement = (
            "INSERT IGNORE INTO ticket_settings (guild_id) VALUES (?)"
            if connection.engine == "mysql"
            else "INSERT OR IGNORE INTO ticket_settings (guild_id) VALUES (?)"
        )
        connection.execute(insert_statement, (guild_id,))
        connection.execute(
            """UPDATE ticket_settings SET panel_channel_id = ?, log_channel_id = ?,
            admin_role_ids = ? WHERE guild_id = ?""",
            (panel_channel_id, log_channel_id, roles_value, guild_id),
        )
        connection.execute("DELETE FROM ticket_categories WHERE guild_id = ?", (guild_id,))
        for position, category in enumerate(categories):
            connection.execute(
                """INSERT INTO ticket_categories
                (guild_id, category_key, position, name, emoji, description, parent_category_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    guild_id,
                    str(position),
                    position,
                    category["name"],
                    category["emoji"] or None,
                    category["description"],
                    category["parent_category_id"],
                ),
            )


def set_ticket_panel_message(guild_id: int, message_id: int | None) -> None:
    with closing(_connect()) as connection, connection:
        connection.execute(
            "UPDATE ticket_settings SET panel_message_id = ? WHERE guild_id = ?",
            (message_id, guild_id),
        )


def get_open_ticket(guild_id: int, user_id: int) -> dict | None:
    with closing(_connect()) as connection, connection:
        row = connection.execute(
            """SELECT * FROM tickets WHERE guild_id = ? AND user_id = ?
            AND status IN ('provisioning', 'open') ORDER BY id DESC LIMIT 1""",
            (guild_id, user_id),
        ).fetchone()
    return dict(row) if row else None


def create_ticket(guild_id: int, user_id: int, category_key: str, category_name: str) -> int:
    with closing(_connect()) as connection, connection:
        cursor = connection.execute(
            """INSERT INTO tickets (guild_id, user_id, category_key, category_name)
            VALUES (?, ?, ?, ?)""",
            (guild_id, user_id, category_key, category_name),
        )
        return int(cursor.lastrowid)


def set_ticket_channel(ticket_id: int, channel_id: int) -> None:
    with closing(_connect()) as connection, connection:
        connection.execute(
            "UPDATE tickets SET channel_id = ?, status = 'open' WHERE id = ?",
            (channel_id, ticket_id),
        )


def delete_ticket(ticket_id: int) -> None:
    with closing(_connect()) as connection, connection:
        connection.execute("DELETE FROM tickets WHERE id = ?", (ticket_id,))


def get_ticket_by_channel(channel_id: int) -> dict | None:
    with closing(_connect()) as connection, connection:
        row = connection.execute(
            "SELECT * FROM tickets WHERE channel_id = ?", (channel_id,)
        ).fetchone()
    return dict(row) if row else None


def close_ticket(ticket_id: int, closed_by: int, reason: str) -> dict | None:
    with closing(_connect()) as connection, connection:
        row = connection.execute(
            "SELECT * FROM tickets WHERE id = ? AND status = 'open'", (ticket_id,)
        ).fetchone()
        if row is None:
            return None
        connection.execute(
            """UPDATE tickets SET status = 'closed', close_reason = ?,
            closed_by = ?, closed_at = CURRENT_TIMESTAMP WHERE id = ?""",
            (reason, closed_by, ticket_id),
        )
    result = dict(row)
    result.update({"status": "closed", "close_reason": reason, "closed_by": closed_by})
    return result
