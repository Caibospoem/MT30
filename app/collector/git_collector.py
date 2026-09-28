"""Git 提交采集模块（M1 用 subprocess git，零第三方依赖；后续可换 GitPython）。

职责：枚举区间内的提交（元数据 + name-status）→ 拉取整段 diff → 解析为
Commit / CommitFile 记录所需的结构化数据。过滤决策交给 preprocessing。
"""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger("ckp.collector")

_FIELD = "\x1f"
_RECORD = "\x1e"


class CollectorError(Exception):
    pass


@dataclass
class RawFile:
    path: str
    old_path: str | None
    change_type: str  # A/M/D/R
    diff: str
    additions: int = 0
    deletions: int = 0


@dataclass
class RawCommit:
    hash: str
    author: str
    author_email: str
    committed_at: datetime
    message: str
    parents: list[str] = field(default_factory=list)
    files: list[RawFile] = field(default_factory=list)

    @property
    def is_merge(self) -> bool:
        return len(self.parents) > 1


def _run_git(repo_path: str, args: list[str], timeout: int = 60) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", repo_path, *args],
            capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace",
        )
    except FileNotFoundError as exc:
        raise CollectorError("未找到 git 可执行文件，请确认已安装 git") from exc
    except subprocess.TimeoutExpired as exc:
        raise CollectorError(f"git 命令超时: git {' '.join(args)}") from exc
    if proc.returncode != 0:
        raise CollectorError(f"git 命令失败: git {' '.join(args)} → {proc.stderr.strip()[:200]}")
    return proc.stdout


def _parse_dt(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))


def list_commits(repo_path: str, branch: str | None = None, since: str | None = None,
                 until: str | None = None, limit: int = 200) -> list[dict]:
    """枚举提交元数据与文件变更类型（不取 diff，轻量）。

    分隔符置于格式串头部：git 的 name-status 文件列表跟在 meta 行之后，
    前置 \x1e 可保证切块后每个块 = [meta 行, 该提交的文件行...]。
    """
    fmt = _RECORD + _FIELD.join(["%H", "%an", "%ae", "%aI", "%s", "%P"])
    args = ["log", f"--pretty=format:{fmt}", "--name-status", f"--max-count={limit}"]
    if branch:
        args.append(branch)
    if since:
        args.append(f"--since={since}")
    if until:
        args.append(f"--until={until}")
    out = _run_git(repo_path, args)
    commits: list[dict] = []
    for block in out.split(_RECORD):
        block = block.strip("\n")
        if not block.strip():
            continue
        lines = block.splitlines()
        head = lines[0].split(_FIELD)
        if len(head) < 6:
            continue
        file_marks: list[dict] = []
        for line in lines[1:]:
            parts = line.split("\t")
            if len(parts) >= 2:
                status = parts[0][0]  # R100 → R
                entry = {"status": status, "path": parts[-1]}
                if status == "R" and len(parts) >= 3:
                    entry["old_path"] = parts[1]
                file_marks.append(entry)
        commits.append({
            "hash": head[0], "author": head[1], "author_email": head[2],
            "committed_at": _parse_dt(head[3]), "message": head[4],
            "parents": head[5].split() if head[5] else [],
            "file_marks": file_marks,
        })
    return commits


def _split_diff(full_diff: str) -> dict[str, str]:
    """把 git show 的整段 diff 按文件拆开：path → diff 文本。"""
    result: dict[str, str] = {}
    current_path: str | None = None
    buffer: list[str] = []
    for line in full_diff.splitlines():
        if line.startswith("diff --git "):
            if current_path and buffer:
                result[current_path] = "\n".join(buffer)
            tokens = line.split()
            # diff --git a/x b/x；rename 时 a/old b/new
            b_side = next((t for t in tokens if t.startswith("b/")), None)
            current_path = b_side[2:] if b_side else (tokens[-1][2:] if tokens else None)
            buffer = [line]
        elif current_path is not None:
            buffer.append(line)
    if current_path and buffer:
        result[current_path] = "\n".join(buffer)
    return result


def load_commit_detail(repo_path: str, meta: dict, unified: int = 2) -> RawCommit:
    """为单个提交补全每文件 diff 与增删行数。"""
    out = _run_git(repo_path, ["show", "--format=", f"--unified={unified}", meta["hash"]])
    diff_map = _split_diff(out)
    files: list[RawFile] = []
    for mark in meta["file_marks"]:
        path = mark["path"]
        diff = diff_map.get(path, "")
        additions = sum(1 for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++"))
        deletions = sum(1 for ln in diff.splitlines() if ln.startswith("-") and not ln.startswith("---"))
        files.append(RawFile(
            path=path, old_path=mark.get("old_path"), change_type=mark["status"],
            diff=diff, additions=additions, deletions=deletions,
        ))
    return RawCommit(
        hash=meta["hash"], author=meta["author"], author_email=meta["author_email"],
        committed_at=meta["committed_at"], message=meta["message"], parents=meta["parents"],
        files=files,
    )


def ensure_local_repo(git_url: str, dest_dir: str, branch: str | None = None) -> str:
    """本地路径直接返回；https/ssh 地址 clone 到 dest_dir（已存在则 fetch 重置）。"""
    import os
    if os.path.exists(os.path.join(git_url, ".git")) or os.path.isdir(git_url):
        return git_url
    if os.path.exists(dest_dir) and os.path.exists(os.path.join(dest_dir, ".git")):
        _run_git(dest_dir, ["fetch", "--all"])
        _run_git(dest_dir, ["reset", "--hard", "FETCH_HEAD"])
        return dest_dir
    os.makedirs(os.path.dirname(dest_dir), exist_ok=True)
    args = ["clone"] + ([f"--branch={branch}"] if branch else []) + [git_url, dest_dir]
    proc = subprocess.run(["git", *args], capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        raise CollectorError(f"clone 失败: {proc.stderr.strip()[:200]}")
    return dest_dir
