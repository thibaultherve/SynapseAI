from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import db_settings

engine = create_async_engine(
    db_settings.DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    pool_size=20,
    max_overflow=10,
    pool_timeout=30,
)

async_session = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


async def get_db():
    # Convention: services own commits, routes don't. We only roll back
    # here so a raised exception never leaves a half-written transaction
    # behind; successful handlers must commit explicitly in a service.
    async with async_session() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
