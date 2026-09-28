"""数据库会话管理。M1 用 SQLite，切 PostgreSQL 仅需修改 DATABASE_URL。"""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


_engine = None
_SessionLocal = None


def get_engine():
    global _engine, _SessionLocal
    if _engine is None:
        url = get_settings().db_url
        if url.startswith("sqlite:///"):
            from pathlib import Path

            Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(url, connect_args={"check_same_thread": False} if url.startswith("sqlite") else {})
        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    return _engine


def init_db() -> None:
    from .models import tables  # noqa: F401  确保模型注册

    get_engine()
    Base.metadata.create_all(_engine)


def get_session() -> Iterator[Session]:
    """FastAPI 依赖：请求级会话。"""
    get_engine()
    db = _SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
