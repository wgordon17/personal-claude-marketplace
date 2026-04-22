from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

_engine = None
_SessionFactory = None


def init_db(app):
    global _engine, _SessionFactory
    _engine = create_engine(
        app.config["DATABASE_URL"],
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
    )
    _SessionFactory = sessionmaker(bind=_engine)


@contextmanager
def get_db() -> Session:
    session = _SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def fetch_one(session: Session, query: str, params: dict):
    result = session.execute(text(query), params)
    return result.mappings().first()


def fetch_all(session: Session, query: str, params: dict):
    result = session.execute(text(query), params)
    return result.mappings().all()


def execute_write(session: Session, query: str, params: dict):
    result = session.execute(text(query), params)
    return result.rowcount


def paginate(session: Session, query: str, params: dict, page: int, per_page: int):
    offset = (page - 1) * per_page
    paginated_query = query + " LIMIT :limit OFFSET :offset"
    params = {**params, "limit": per_page, "offset": offset}
    return fetch_all(session, paginated_query, params)
