"""Asynchronous Database Session & Engine configuration."""

from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from app.config import settings
from app.models.base import Base


def get_engine(url: str | None = None) -> AsyncEngine:
    db_url = url or settings.DATABASE_URL
    return create_async_engine(
        db_url,
        echo=False,
        future=True,
    )


engine = get_engine()
async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for yielding transactional async database sessions."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db(custom_engine: AsyncEngine | None = None) -> None:
    """Create all tables in the database."""
    import app.models  # noqa: F401
    import app.audit.models  # noqa: F401
    target_engine = custom_engine or engine
    async with target_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
