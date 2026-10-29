# config.py
import os

from dotenv import load_dotenv

load_dotenv()


def _env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value is not None and value != "":
            return value
    return default


TOKEN = _env("DISCORD_TOKEN", "DISCORD_BOT_TOKEN") or None
ADMIN_ROLE_ID = int(_env("ADMIN_ROLE_ID", default="0"))
WEBUI_PASSWORD = _env(
    "WEB_PANEL_PASSWORD",
    "WEBPANEL_PASSWORD",
    "PANEL_PASSWORD",
    "WEB_PASSWORD",
    "WEBUI_PASSWORD",
)
WEBUI_HOST = _env("WEBUI_HOST", "WEB_PANEL_HOST", default="0.0.0.0")
WEBUI_PORT = int(
    _env("WEB_PANEL_PORT", "WEBUI_PORT", "WEB_PORT", "PANEL_PORT", "PORT", default="8080")
)
WEBUI_SECURE_COOKIE = _env(
    "SECURE_WEB_COOKIE", "WEB_PANEL_SECURE_COOKIE", "WEBUI_SECURE_COOKIE", default="0"
).lower() in {
    "1",
    "true",
    "yes",
}
WEBUI_PUBLIC_ORIGIN = _env("WEBUI_PUBLIC_ORIGIN").rstrip("/")
BOT_HEALTH_PORT = int(
    _env("BOT_HEALTH_PORT", "BOT_HEALTHCHECK_PORT", "HEALTHCHECK_PORT", "HEALTH_PORT", default="0")
)
BOT_HEALTH_HOST = _env("BOT_HEALTH_HOST", default="0.0.0.0")
DATABASE_ENGINE = _env("DB_ENGINE", "DATABASE_ENGINE", "DB_TYPE", "DATABASE_TYPE", default="sqlite").lower()
DATABASE_HOST = _env("DB_HOST", "DATABASE_HOST", "MYSQL_HOST")
DATABASE_PORT = int(_env("DB_PORT", "DATABASE_PORT", "MYSQL_PORT", default="3306"))
DATABASE_NAME = _env("DB_NAME", "DB_DATABASE", "DATABASE_NAME", "MYSQL_DATABASE")
DATABASE_USERNAME = _env(
    "DB_USERNAME", "DB_USER", "DATABASE_USERNAME", "DATABASE_USER", "MYSQL_USER"
)
DATABASE_PASSWORD = _env("DB_PASSWORD", "DATABASE_PASSWORD", "MYSQL_PASSWORD")
DATABASE_TLS = _env(
    "DB_TLS", "DATABASE_TLS", "DATABASE_SSL", "DB_SSL", default="0"
).lower() in {
    "1",
    "true",
    "yes",
}
DATABASE_TLS_CA = _env("DB_TLS_CA", "DATABASE_TLS_CA") or None
DEV_GUILD_ID = int(_env("DEV_GUILD_ID", "DEVELOPMENT_GUILD_ID", default="0"))
LOG_LEVEL = _env("LOG_LEVEL", default="INFO").upper()
