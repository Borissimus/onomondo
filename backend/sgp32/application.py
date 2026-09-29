import logging

from fastapi import FastAPI
from sgp32_common.security import configure_logging

from sgp32.api import create_app
from sgp32.config import Settings
from sgp32.mqtt import is_ready
from sgp32.storage import Database


def application() -> FastAPI:
    configure_logging()
    try:
        settings = Settings()
        db = Database(settings.database_url.get_secret_value())
        return create_app(settings, db, dependencies_ready=lambda: is_ready(db))
    except Exception:
        logging.error("startup_configuration_invalid")
        raise SystemExit(1) from None
