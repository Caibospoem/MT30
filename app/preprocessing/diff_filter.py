"""预处理：噪声过滤 + diff 预算控制（对应技术方案 5.1）。

过滤规则（题目痛点：垃圾进垃圾出）：
1. merge 提交跳过（其包含的子提交已被单独分析）；
2. 构建/依赖产物剔除：lock 文件、target/、node_modules/、dist/、二进制等；
3. 生成代码剔除：*Generated*、*.min.js；
4. 纯格式化文件剔除：变更行全部为空白/注释；
5. 预算控制：单文件 diff 截断到行上限，全提交设总字符预算，按文件优先级排序后截断。
"""
from __future__ import annotations

import re

# 剔除路径规则（按子串/正则匹配，大小写不敏感）
_NOISE_PATH_PATTERNS = [
    r"(^|/)(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|poetry\.lock)$",
    r"(^|/)(node_modules|target|dist|build|out|vendor|\.idea|\.vscode)/",
    r"\.(min\.js|min\.css|class|jar|war|so|dll|bin|png|jpg|jpeg|gif|ico|pdf|zip|gz)$",
    r"(^|/)[^/]*Generated[^/]*$",
    r"(^|/)\.git/",
]

# 优先级加权：越靠前越重要，预算紧张时优先保留
_PRIORITY_PATTERNS = [
    (5, r"(Controller|Resource|Api)\.java$|api/|interface|graphql"),
    (4, r"application[-\w]*\.(yml|yaml|properties)|\.sql$|migration|flyway"),
    (3, r"Security|Auth|Permission|Filter|Interceptor"),
    (2, r"Service|Manager|Repository|Dao|Mapper"),
    (1, r"\.java$|\.py$|\.go$|\.ts$"),
]

_COMMENT_LINE = re.compile(r"^\s*(//|/?\*|#|--|<!--|\*)")


def is_noise_path(path: str) -> bool:
    lowered = path.lower()
    return any(re.search(p, lowered, re.I) for p in _NOISE_PATH_PATTERNS)


def is_formatting_only(diff_text: str) -> bool:
    """变更行（+/-）去掉首字符后全为空白或纯注释 → 判定为格式化提交。"""
    changed = [ln[1:] for ln in diff_text.splitlines() if ln.startswith(("+", "-")) and not ln.startswith(("+++", "---"))]
    if not changed:
        return True
    return all((not ln.strip()) or _COMMENT_LINE.match(ln) for ln in changed)


def _priority(path: str) -> int:
    for weight, pattern in _PRIORITY_PATTERNS:
        if re.search(pattern, path, re.I):
            return weight
    return 0


def prepare_commit_payload(raw_commit, settings) -> dict:
    """把 RawCommit 组装为分析输入；返回 payload 与过滤统计。

    payload.files 内每项: {path, change_type, additions, deletions, diff(预算内)}
    """
    kept: list[dict] = []
    dropped_noise: list[str] = []
    dropped_formatting: list[str] = []
    file_line_limit = settings.diff_file_line_limit
    total_budget = settings.diff_total_token_budget * 2  # 近似 token → 字符

    candidates = [f for f in raw_commit.files if not is_noise_path(f.path)]
    dropped_noise = [f.path for f in raw_commit.files if is_noise_path(f.path)]
    candidates.sort(key=lambda f: -_priority(f.path))

    used = 0
    for f in candidates:
        if is_formatting_only(f.diff):
            dropped_formatting.append(f.path)
            continue
        lines = f.diff.splitlines(keepends=True)
        truncated = False
        if len(lines) > file_line_limit:
            lines = lines[:file_line_limit]
            truncated = True
        text = "".join(lines)
        if used + len(text) > total_budget:
            remain = total_budget - used
            if remain < 400:  # 剩余预算太小，直接放弃后续文件
                break
            text = text[:remain]
            truncated = True
            used = total_budget
        else:
            used += len(text)
        kept.append({
            "path": f.path, "change_type": f.change_type, "old_path": f.old_path,
            "additions": f.additions, "deletions": f.deletions,
            "diff": text, "diff_truncated": truncated,
        })

    return {
        "hash": raw_commit.hash,
        "message": raw_commit.message,
        "author": raw_commit.author,
        "committed_at": raw_commit.committed_at.isoformat() if raw_commit.committed_at else "",
        "files": kept,
        "diff_text": "\n".join(f["diff"] for f in kept),
        "files_skipped_noise": dropped_noise,
        "files_skipped_formatting": dropped_formatting,
    }
