"""分析模块测试：mock 引擎输出合法 Schema。"""
import pytest

from app.analysis.analyzer import AnalysisError, TAG_ENUM, _as_list, _validate, analyze_commit
from app.config import get_settings
from tests.collector_fixtures import make_raw_commit

get_settings()  # 缓存测试环境配置


def test_mock_analysis_schema():
    commit = make_raw_commit(
        message="修改接口",
        files={"src/main/java/com/demo/controller/OrderExportController.java":
               "+ @PreAuthorize(\"hasRole('ADMIN')\")\n+ @PostMapping\n+ public Result export() {}"},
    )
    result, payload, truncated = analyze_commit(commit, get_settings())
    for key in ("summary", "background", "core_changes", "modules", "impact", "tags", "risk_note", "reusable_note"):
        assert key in result
    assert result["summary"]
    assert set(result["tags"]) <= set(TAG_ENUM)
    assert "权限" in result["tags"] and "接口" in result["tags"]
    assert truncated is False


def test_mock_analysis_tags_db_and_config():
    commit = make_raw_commit(files={
        "src/main/resources/db/migration/V12__order.sql": "+ ALTER TABLE t_order ADD COLUMN c1 INT;",
        "src/main/resources/application.yml": "+   cache-ttl-seconds: 300",
    })
    result, _, _ = analyze_commit(commit, get_settings())
    assert "数据库" in result["tags"]
    assert "配置" in result["tags"]


def test_noise_only_commit_rejected():
    commit = make_raw_commit(files={"web/package-lock.json": "+locked"})
    with pytest.raises(AnalysisError):
        analyze_commit(commit, get_settings())


def test_as_list_coerces_llm_string_drift():
    """glm-4-flash 偶发把数组输出成字符串，必须收敛而不是逐字迭代。"""
    assert _as_list("order") == ["order"]
    assert _as_list("配置、性能") == ["配置", "性能"]
    assert _as_list(["数据库"]) == ["数据库"]
    assert _as_list(None) == []
    assert _as_list({"a": "order"}) == ["order"]


def test_validate_survives_string_drift():
    """真实事故：modules/tags 被模型输出为字符串 → 单字符数组/标签全空。"""
    raw = {
        "summary": "新增订单导出接口",
        "modules": "order",
        "tags": "接口、性能、权限",
        "risk_note": "-",
    }
    result = _validate(raw)
    assert result["modules"] == ["order"]
    assert set(result["tags"]) == {"接口", "性能", "权限"}
