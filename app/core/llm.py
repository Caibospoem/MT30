"""LLM 抽象层（模式沿用 compliance-platform，mock 任务改为提交分析）。

1. 双模式：mock（规则引擎，无需 Key）/ OpenAI 兼容协议（GLM / OpenAI / vLLM 均可用）。
2. 稳定性：超时 + 指数退避重试（覆盖超时 / 429 / 5xx）；response_format 触发 400 时
   自动去参重试（部分 GLM 网关不支持该参数）。
3. json_mode 输出统一走 extract_json 鲁棒提取。
"""
from __future__ import annotations

import json
import logging
import random
import re
import time
from functools import wraps
from typing import Any, Callable, Optional

import httpx

from ..config import get_settings

logger = logging.getLogger("ckp.llm")


class LLMError(Exception):
    """LLM 调用最终失败的统一异常。"""


def _with_retry(max_retries: int, base_delay: float = 0.8):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            last_exc: Optional[Exception] = None
            for attempt in range(1, max_retries + 1):
                try:
                    return fn(*args, **kwargs)
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    last_exc = exc
                except httpx.HTTPStatusError as exc:
                    code = exc.response.status_code
                    if code == 429 or code >= 500:
                        last_exc = exc
                    else:
                        raise
                delay = base_delay * (2 ** (attempt - 1)) * (1 + random.random() * 0.3)
                logger.warning("LLM 调用失败（第 %d 次）: %s，%.1fs 后重试", attempt, last_exc, delay)
                time.sleep(delay)
            raise LLMError(f"LLM 重试 {max_retries} 次后仍失败: {last_exc}") from last_exc
        return wrapper
    return decorator


def extract_json(text: str) -> Optional[Any]:
    """从 LLM 输出中鲁棒提取 JSON：直解 → 剥代码围栏 → 平衡扫描首个对象/数组。"""
    if text is None:
        return None
    raw = text.strip()
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        pass
    fenced = re.sub(r"```[a-zA-Z]*\s*|\s*```", "", raw)
    try:
        return json.loads(fenced.strip())
    except (json.JSONDecodeError, ValueError):
        pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start = fenced.find(opener)
        if start < 0:
            continue
        depth = 0
        in_str = False
        escape = False
        for i in range(start, len(fenced)):
            ch = fenced[i]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    candidate = fenced[start:i + 1]
                    try:
                        return json.loads(candidate)
                    except (json.JSONDecodeError, ValueError):
                        try:
                            return json.loads(re.sub(r",\s*([}\]])", r"\1", candidate))
                        except (json.JSONDecodeError, ValueError):
                            break
    return None


# ───────────────────────────── mock 引擎 ─────────────────────────────

class MockBackend:
    """规则引擎：不依赖外部 Key，保证无 Key 环境全流程可跑通（演示/测试用）。"""

    def chat(self, task: str, system: str, user: str, json_mode: bool = False) -> str:
        if task == "commit_analysis":
            return json.dumps(mock_commit_analysis(user), ensure_ascii=False)
        if json_mode:
            return json.dumps({"reply": "mock"}, ensure_ascii=False)
        return "mock"

    def close(self) -> None:  # pragma: no cover
        pass


# 标签枚举 → 关键词（与方案 5.2 的固定枚举一致；正则大小写不敏感）
_TAG_KEYWORDS: list[tuple[str, str]] = [
    ("权限", r"PreAuthorize|Secured|hasRole|hasAuthority|permission|auth|token|shiro|spring-security"),
    ("接口", r"Controller|@.*Mapping|RestController|api|feign|dto|vo\b"),
    ("配置", r"application[-\w]*\.(yml|yaml|properties)|\.xml$|bootstrap|\.env|config"),
    ("数据库", r"\.sql$|ALTER\s+TABLE|CREATE\s+TABLE|mapper|mybatis|jpa|migration|flyway"),
    ("前端", r"\.(vue|js|ts|jsx|tsx|html|css)$|element|axios"),
    ("测试", r"test|Test|spec|junit|mockmvc"),
    ("性能", r"cache|redis|batch|threadpool|ThreadPool|async|@Async|limit|pagehelper|分页|批量|缓存"),
    ("安全", r"encrypt|decrypt|password|xss|sql.?inject|desensit|脱敏|加密"),
    ("重构", r"refactor|rename|move|extract|拆分|重构"),
    ("缺陷修复", r"fix|bug|null\s*check|NPE|exception|catch|越界|空指针|修复"),
]


def _detect_tags(text: str, paths: list[str]) -> list[str]:
    joined = text.lower()
    tags: list[str] = []
    for tag, pattern in _TAG_KEYWORDS:
        if re.search(pattern, joined, re.I) or any(re.search(pattern, p, re.I) for p in paths):
            tags.append(tag)
    return tags or ["重构"]


def _modules_of(paths: list[str]) -> list[str]:
    """业务模块推导：Java 工程取包路径倒数第二段（业务层），资源文件取一级子目录。"""
    mods: list[str] = []
    for p in paths:
        parts = p.replace("\\", "/").split("/")
        if "src" in parts:
            i = parts.index("src")
            if len(parts) > i + 2 and parts[i + 2] in ("java", "kotlin"):
                pkg = [x for x in parts[i + 3:-1] if x]  # 去掉文件名的包路径
                if len(pkg) >= 3:
                    mods.append(pkg[-2])  # com/demo/order/service → order
                    continue
                if pkg:
                    mods.append(pkg[-1])
                    continue
            if len(parts) > i + 2 and parts[i + 2] == "resources":
                sub = [x for x in parts[i + 3:-1] if x]
                mods.append(sub[0] if sub else "resources")  # db/migration → db
                continue
        if len(parts) > 1:
            mods.append(parts[0])
    seen: list[str] = []
    for m in mods:
        if m and m not in seen and m not in ("com", "org", "cn", "net"):
            seen.append(m)
    return seen[:5]


_COMMON_WORDS = {
    "public", "private", "return", "import", "void", "static", "class", "final",
    "string", "override", "springframework", "annotation", "requestbody",
    "postmapping", "getmapping", "restcontroller", "service", "result",
}

# 技术标识符 → 中文要点（mock 引擎的跨语言检索桥梁；真实 LLM 不需要）
_GLOSSES = [
    (r"cache|caffeine|redis|ehcache", "缓存"),
    (r"export", "导出"),
    (r"permission|auth|token|security", "权限校验"),
    (r"batch|threadpool|async|executor", "异步批处理"),
    (r"timeout", "超时配置"),
    (r"alter|migration|flyway", "数据库结构变更"),
]


def _key_tokens(diff_text: str, limit: int = 5) -> list[str]:
    """从新增行提取业务标识符（ CamelCase / 下划线命名优先）。"""
    counter: dict[str, int] = {}
    for ln in diff_text.splitlines():
        if not ln.startswith("+") or ln.startswith("+++"):
            continue
        for tok in re.findall(r"[A-Za-z_][A-Za-z0-9_]{4,}", ln):
            if tok.lower() in _COMMON_WORDS:
                continue
            counter[tok] = counter.get(tok, 0) + 1

    def score(t: str) -> int:
        camel = sum(1 for c in t[1:] if c.isupper())
        return (camel + (1 if "_" in t else 0)) * 10 + counter[t]

    ranked = sorted(counter.items(), key=lambda kv: -score(kv[0]))
    return [t for t, _ in ranked[:limit]]


def _glosses(diff_text: str) -> list[str]:
    joined = diff_text.lower()
    return [g for pat, g in _GLOSSES if re.search(pat, joined)]


def _core_changes(files: list[dict]) -> list[dict]:
    """从 diff 中提取每个关键文件的一句话变更：优先取新增行里的注释/方法签名。"""
    changes: list[dict] = []
    for f in files[:5]:
        added = [ln.strip()[1:].strip() for ln in f.get("diff", "").splitlines() if ln.startswith("+") and not ln.startswith("+++")]
        removed_n = sum(1 for ln in f.get("diff", "").splitlines() if ln.startswith("-") and not ln.startswith("---"))
        key_line = ""
        for ln in added:
            if ln.startswith(("public ", "private ", "@", "CREATE", "ALTER", "def ", "class ")):
                key_line = ln[:80]
                break
        if not key_line and added:
            key_line = next((ln for ln in added if len(ln.strip()) > 8), added[0] if added else "")[:80]
        action = {"A": "新增", "M": "修改", "D": "删除", "R": "重命名"}.get(f.get("change_type", "M"), "修改")
        desc = f"{action}（+{f.get('additions', 0)}/-{removed_n}）"
        if key_line:
            desc += f"，关键行：{key_line}"
        changes.append({"file": f["path"], "change": desc, "why": ""})
    return changes


def mock_commit_analysis(user_json: str) -> dict:
    """mock 分析器：基于 diff 统计与关键词的确定性总结（不臆造业务，模板化输出）。"""
    try:
        payload = json.loads(user_json)
    except (json.JSONDecodeError, ValueError):
        payload = {}
    files: list[dict] = payload.get("files", [])
    paths = [f.get("path", "") for f in files]
    diff_text = payload.get("diff_text", "")
    message = payload.get("message", "").strip() or "（无提交说明）"
    tags = _detect_tags(diff_text + " " + " ".join(paths), paths)
    modules = _modules_of(paths)
    additions = sum(f.get("additions", 0) for f in files)
    deletions = sum(1 for ln in diff_text.splitlines() if ln.startswith("-") and not ln.startswith("---"))

    main_file = paths[0] if paths else "-"
    tokens = _key_tokens(diff_text)
    points = _glosses(diff_text)
    summary = (
        f"本次提交变更 {len(files)} 个文件（约 +{additions}/-{deletions}），"
        f"主要改动位于 {main_file}；涉及模块：{'、'.join(modules) if modules else '未识别'}；"
        f"内容特征标签：{'、'.join(tags)}。"
    )
    if tokens:
        summary += f"关键改动：{'、'.join(tokens)}。"
    if points:
        summary += f"技术要点：{'、'.join(points)}。"
    background = f"提交说明为「{message}」，由规则引擎根据变更内容归纳。"
    impact = ""
    if "数据库" in tags:
        impact += "含数据库结构变更，需关注上下游兼容与回滚脚本。"
    if "配置" in tags:
        impact += "含配置变更，多环境部署需同步。"
    if "权限" in tags:
        impact += "涉及权限控制，需验证未授权访问被正确拦截。"
    if not impact:
        impact = "对现有功能影响范围有限，建议回归主流程。"
    risk_note = ""
    if deletions > 0 and "缺陷修复" not in tags:
        risk_note = "存在删除性变更，请确认被删除逻辑无其他调用方。"
    reusable_note = ""
    if "权限" in tags:
        reusable_note = "包含权限校验相关写法，可作为同类接口的参考实现。"
    if "性能" in tags:
        reusable_note = (reusable_note + "；" if reusable_note else "") + "包含性能优化手段，可复用于同类慢查询/批处理场景。"
    return {
        "summary": summary,
        "background": background,
        "core_changes": _core_changes(files),
        "modules": modules,
        "impact": impact,
        "tags": tags,
        "risk_note": risk_note,
        "reusable_note": reusable_note,
    }


# ─────────────────────────── OpenAI 兼容后端 ───────────────────────────

class OpenAICompatBackend:
    """OpenAI /chat/completions 兼容协议（GLM：https://open.bigmodel.cn/api/paas/v4）。"""

    def __init__(self, api_key: str, base_url: str, model: str, timeout: int):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    @_with_retry(3)
    def _post(self, payload: dict) -> dict:
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()

    def chat(self, task: str, system: str, user: str, json_mode: bool = False) -> str:
        messages = ([{"role": "system", "content": system}] if system else []) + [
            {"role": "user", "content": user}
        ]
        payload: dict = {"model": self.model, "messages": messages, "temperature": 0.2}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        try:
            data = self._post(payload)
        except httpx.HTTPStatusError as exc:
            if json_mode and exc.response.status_code == 400:
                # 网关不支持 response_format：去参重试，靠 prompt 约束输出 JSON
                payload.pop("response_format", None)
                data = self._post(payload)
            else:
                raise
        return data["choices"][0]["message"]["content"]

    def close(self) -> None:  # pragma: no cover
        pass


# ─────────────────────────── 客户端门面 ───────────────────────────

class LLMClient:
    def __init__(self) -> None:
        s = get_settings()
        self.provider = s.llm_provider
        if s.llm_provider in ("glm", "openai"):
            key = s.zhipu_api_key if s.llm_provider == "glm" else s.openai_api_key
            base = "https://open.bigmodel.cn/api/paas/v4" if s.llm_provider == "glm" else s.openai_base_url
            if not key:
                logger.warning("LLM provider=%s 但未配置 Key，降级为 mock 模式", s.llm_provider)
                self.provider = "mock"
                self._backend: Any = MockBackend()
            else:
                self._backend = OpenAICompatBackend(key, base, s.llm_model, s.llm_timeout)
        else:
            self._backend = MockBackend()

    def chat(self, task: str, system: str = "", user: str = "", json_mode: bool = False) -> Any:
        result = self._backend.chat(task, system, user, json_mode)
        if json_mode:
            extracted = extract_json(result)
            if extracted is None:
                raise LLMError(f"JSON 提取失败，原始输出前 120 字: {str(result)[:120]}")
            return extracted
        return result

    def close(self) -> None:
        self._backend.close()


_CLIENT: LLMClient | None = None


def get_llm() -> LLMClient:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = LLMClient()
    return _CLIENT
