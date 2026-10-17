"""Alembic environment.

The database URL is taken from app.config rather than alembic.ini so that
migrations, the CLI, and the API can never disagree about which file they are
pointed at.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Importing models registers them on Base.metadata for autogenerate.
from app import models  # noqa: F401
from app.config import get_settings
from app.db import Base

config = context.config

if config.config_file_name is not None:
    # disable_existing_loggers defaults to True, which silences every logger
    # already configured — including the app's own. That is invisible when
    # alembic runs as its own process, but running it in-process (the schema
    # drift test does) would leave the rest of the run logging nothing.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

settings = get_settings()
settings.resolved_db_path.parent.mkdir(parents=True, exist_ok=True)
config.set_main_option("sqlalchemy.url", settings.database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # SQLite cannot ALTER most things in place; batch mode rewrites the
        # table instead, which every future migration will need.
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
