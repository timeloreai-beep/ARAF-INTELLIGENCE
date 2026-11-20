from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from backend.app.config import get_settings
from backend.app.integrations.database import normalize_async_database_url
from backend.app.models import Base


config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_urls() -> list[str]:
    settings = get_settings()
    urls = [
        normalize_async_database_url(raw)
        for raw in (settings.supabase_database_url, settings.neon_database_url)
        if raw
    ]
    if not urls:
        raise RuntimeError(
            "Set SUPABASE_DATABASE_URL or NEON_DATABASE_URL before running migrations"
        )
    return list(dict.fromkeys(urls))


def run_migrations_offline() -> None:
    context.configure(
        url=_database_urls()[0],
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    base_configuration = config.get_section(config.config_ini_section) or {}
    for url in _database_urls():
        configuration = dict(base_configuration)
        configuration["sqlalchemy.url"] = url
        connectable = async_engine_from_config(
            configuration,
            prefix="sqlalchemy.",
            poolclass=pool.NullPool,
            connect_args={"ssl": "require"},
        )
        try:
            async with connectable.connect() as connection:
                await connection.run_sync(do_run_migrations)
        finally:
            await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
