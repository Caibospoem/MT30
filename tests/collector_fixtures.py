"""共享测试工具：构造 RawCommit / RawFile。"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.collector.git_collector import RawCommit, RawFile  # noqa: E402


def make_raw_commit(hash_: str = "abc123", message: str = "修复问题", files: dict | None = None):
    raw_files = []
    for path, diff in (files or {}).items():
        additions = sum(1 for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++"))
        deletions = sum(1 for line in diff.splitlines() if line.startswith("-") and not line.startswith("---"))
        change = "A" if "+" in diff and additions and deletions == 0 else "M"
        raw_files.append(RawFile(path=path, old_path=None, change_type=change, diff=diff,
                                 additions=additions, deletions=deletions))
    return RawCommit(
        hash=hash_, author="demo-dev", author_email="demo@demo.com",
        committed_at=datetime(2026, 9, 25, 10, 0, 0), message=message,
        parents=["p1"], files=raw_files,
    )
