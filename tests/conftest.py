"""pytest 公共夹具：临时数据库 / 临时向量库 / 演示 Git 仓库。

注意：环境变量必须在首次导入 app.* 之前设置（get_settings 与引擎均为单例）。
"""
from __future__ import annotations

import os
import shutil
import tempfile

_TMP = tempfile.mkdtemp(prefix="ckp-test-")
os.environ["DB_URL"] = f"sqlite:///{_TMP}/test.db"
os.environ["VECTOR_STORE_DIR"] = os.path.join(_TMP, "vectors")

import pytest  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.db import Base  # noqa: E402
from app.vectorstore import reset_vector_store  # noqa: E402

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from scripts.make_demo_repo import main as make_demo_repo_main  # noqa: E402


@pytest.fixture()
def db():
    engine = create_engine(f"sqlite:///{_TMP}/t_{os.getpid()}.db",
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = Session()
    yield session
    session.close()
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture(autouse=True)
def _fresh_vector_store():
    reset_vector_store()
    yield
    reset_vector_store()
    vdir = os.path.join(_TMP, "vectors")
    if os.path.exists(vdir):
        shutil.rmtree(vdir, ignore_errors=True)


@pytest.fixture()
def demo_repo(tmp_path):
    target = str(tmp_path / "demo_repo")
    make_demo_repo_main(target)
    return target


@pytest.fixture()
def synced_repo(db, demo_repo):
    """已同步未确认的仓库（5 条待确认条目）。"""
    from app.collector.sync import sync_repository
    from app.models.tables import Repository

    repo = Repository(name="demo", git_url=demo_repo, default_branch="main", language="java")
    db.add(repo)
    db.flush()
    stats = sync_repository(db, repo, mode="backfill")
    db.commit()
    return repo, stats
