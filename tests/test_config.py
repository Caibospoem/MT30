"""配置层测试：检索阈值随嵌入 provider 自适应。"""
from app.config import Settings


def test_min_score_adapts_to_provider():
    """hash 与 embedding-3 分数尺度不可比：0.05 只适用于 hash，语义模型用实测阈值。"""
    s_local = Settings(embedding_provider="local", _env_file=None)
    s_openai = Settings(embedding_provider="openai", _env_file=None)
    assert s_local.effective_min_score == s_local.retrieval_min_score == 0.05
    assert s_openai.effective_min_score == s_openai.retrieval_min_score_semantic == 0.33
