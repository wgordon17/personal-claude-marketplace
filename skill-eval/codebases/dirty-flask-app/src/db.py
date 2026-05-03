# TEST FIXTURE: Contains deliberately planted vulnerabilities. See MANIFEST.md.

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from flask import g


class Base(DeclarativeBase):
    pass


_engine = None
_SessionFactory = None


def init_db(database_url):
    global _engine, _SessionFactory
    _engine = create_engine(
        database_url,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
    )
    _SessionFactory = sessionmaker(bind=_engine)
    Base.metadata.create_all(_engine)


def get_session():
    if "db_session" not in g:
        g.db_session = _SessionFactory()
    return g.db_session


def get_engine():
    return _engine


def create_tables():
    if _engine is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    Base.metadata.create_all(_engine)


def drop_tables():
    if _engine is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    Base.metadata.drop_all(_engine)


def reset_db(database_url):
    drop_tables()
    init_db(database_url)
