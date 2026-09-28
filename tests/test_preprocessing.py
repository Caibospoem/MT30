"""预处理模块测试：噪声过滤 / 格式化识别 / 预算控制。"""
from app.preprocessing.diff_filter import (
    is_formatting_only,
    is_noise_path,
    prepare_commit_payload,
)
from tests.collector_fixtures import make_raw_commit


def test_noise_paths():
    assert is_noise_path("web/package-lock.json")
    assert is_noise_path("service/target/classes/App.class")
    assert is_noise_path("web/dist/bundle.min.js")
    assert is_noise_path("src/main/java/OrderDTOGenerated.java")
    assert not is_noise_path("src/main/java/com/demo/OrderService.java")
    assert not is_noise_path("src/main/resources/application.yml")


def test_formatting_only_detection():
    ws_only = "+\n+   \n-\n"
    comments = "+ // just a comment\n+ /* block */\n- # python comment\n"
    assert is_formatting_only(ws_only)
    assert is_formatting_only(comments)
    assert not is_formatting_only("+ if (orderNo.isEmpty()) { return null; }\n")


def test_payload_filters_noise_and_truncates():
    big = "+" + "x = 1\n" * 600  # 600 行，超过 400 行上限
    commit = make_raw_commit(files={
        "src/main/java/App.java": "package a;\n+ new logic line\n",
        "web/package-lock.json": "+locked\n",
        "src/Big.java": big,
    })
    payload = prepare_commit_payload(commit, type("S", (), {
        "diff_file_line_limit": 400, "diff_total_token_budget": 8000})())

    paths = [f["path"] for f in payload["files"]]
    assert "web/package-lock.json" not in paths
    assert "src/main/java/App.java" in paths
    assert "src/Big.java" in paths
    assert payload["files_skipped_noise"] == ["web/package-lock.json"]

    big_entry = next(f for f in payload["files"] if f["path"] == "src/Big.java")
    assert big_entry["diff_truncated"]
    assert len(big_entry["diff"].splitlines()) <= 402  # 400 行 + 截断标记


def test_total_budget_stop():
    commit = make_raw_commit(files={
        f"src/A{i}.java": "+" + "line\n" * 300 for i in range(6)
    })
    payload = prepare_commit_payload(commit, type("S", (), {
        "diff_file_line_limit": 400, "diff_total_token_budget": 2000})())  # 2000 token ≈ 4000 字符
    used_chars = sum(len(f["diff"]) for f in payload["files"])
    assert used_chars <= 4100  # 不超过预算（留少量截断余量）
