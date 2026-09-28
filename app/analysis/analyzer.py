"""提交内容分析模块：diff → LLM 结构化知识（对应技术方案 5.2）。

输出 Schema（与方案一致）：
  summary        ≤120字 变更总结（检索主文本）
  background     业务背景
  core_changes   [{file, change, why}]
  modules        涉及模块
  impact         业务影响
  tags           固定枚举标签
  risk_note      风险提示
  reusable_note  可复用经验

LLM 失败/解析失败 → 抛 AnalysisError，由同步流程标记 commit.process_status=failed。
"""
from __future__ import annotations

import json
import logging

from ..core.llm import LLMError, get_llm
from ..preprocessing.diff_filter import prepare_commit_payload

logger = logging.getLogger("ckp.analysis")

TAG_ENUM = ["权限", "接口", "配置", "数据库", "前端", "测试", "性能", "安全", "重构", "缺陷修复"]

ANALYSIS_SYSTEM = """你是资深研发专家，负责把 Git 提交的代码变更转化为可沉淀、可检索的研发知识。
要求：
1. 只依据给出的 diff 与提交说明归纳，不得臆造未出现的信息；
2. summary 不超过 120 字，说明"做了什么、改在哪儿、为了什么"；
3. tags 只能从固定枚举中选取：权限、接口、配置、数据库、前端、测试、性能、安全、重构、缺陷修复；
4. core_changes 最多列 5 个关键文件，change 用一句话描述，why 说明动机（diff 无法推断时留空）；
5. 严格输出 JSON 对象，字段：summary, background, core_changes, modules, impact, tags, risk_note, reusable_note；
6. 全部使用中文（文件名/代码标识符除外）。"""


class AnalysisError(Exception):
    pass


def _validate(result: dict) -> dict:
    """schema 校验 + 清洗：标签收枚举、字段类型兜底。"""
    if not isinstance(result, dict):
        raise AnalysisError("分析输出不是 JSON 对象")
    summary = str(result.get("summary") or "").strip()
    if not summary:
        raise AnalysisError("summary 为空")
    tags = [t for t in (result.get("tags") or []) if t in TAG_ENUM]
    core_changes = result.get("core_changes") or []
    if not isinstance(core_changes, list):
        core_changes = []
    cleaned_changes = []
    for item in core_changes[:5]:
        if isinstance(item, dict) and item.get("file"):
            cleaned_changes.append({
                "file": str(item["file"]),
                "change": str(item.get("change", ""))[:200],
                "why": str(item.get("why", ""))[:200],
            })
    return {
        "summary": summary[:200],
        "background": str(result.get("background", ""))[:500],
        "core_changes": cleaned_changes,
        "modules": [str(m) for m in (result.get("modules") or [])][:10],
        "impact": str(result.get("impact", ""))[:500],
        "tags": tags,
        "risk_note": str(result.get("risk_note", ""))[:300],
        "reusable_note": str(result.get("reusable_note", ""))[:300],
    }


def analyze_commit(raw_commit, settings) -> tuple[dict, dict, bool]:
    """分析单个提交。返回 (分析结果, 使用的 payload, 是否截断)。

    raw_commit: collector.git_collector.RawCommit
    """
    payload = prepare_commit_payload(raw_commit, settings)
    if not payload["files"]:
        # 全部文件被过滤（噪声/格式化）→ 无分析价值
        raise AnalysisError("所有文件均为噪声或纯格式化变更，跳过分析")

    user = json.dumps(payload, ensure_ascii=False)
    try:
        result = get_llm().chat(task="commit_analysis", system=ANALYSIS_SYSTEM, user=user, json_mode=True)
    except LLMError as exc:
        raise AnalysisError(f"LLM 分析失败: {exc}") from exc
    return _validate(result), payload, any(f.get("diff_truncated") for f in payload["files"])
