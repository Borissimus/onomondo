"""Secret-safe migration entry point for deployment."""

import logging
import os

from alembic import command
from alembic.config import Config
from sgp32_common.security import configure_logging

from sgp32.config import Settings


def main() -> None:
    configure_logging()
    try:
        settings = Settings()
        os.environ["DATABASE_URL"] = settings.database_url.get_secret_value()
        command.upgrade(Config("alembic.ini"), "head")
    except Exception:
        logging.error("migration_failed")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
