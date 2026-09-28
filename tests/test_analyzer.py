"""分析模块测试：mock 引擎输出合法 Schema。"""
import pytest

from app.analysis.analyzer import AnalysisError, TAG_ENUM, analyze_commit
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
