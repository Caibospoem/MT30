"""全局配置：统一从环境变量 / .env 读取（模式与 compliance-platform 一致）。"""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    # LLM（glm 未配置 key 时自动降级 mock，全流程仍可跑通）
    llm_provider: str = "mock"  # mock | glm | openai
    zhipu_api_key: str = ""
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "glm-4-flash"
    llm_timeout: int = 60
    llm_max_retries: int = 3

    # Embedding（local: 特征哈希 n-gram，零依赖；openai: GLM embedding-3）
    embedding_provider: str = "local"  # local | openai
    embedding_model: str = "embedding-3"

    # 数据库（M1 默认 SQLite，生产切 PostgreSQL：改 DB_URL 即可）
    db_url: str = f"sqlite:///{BASE_DIR / 'data' / 'app.db'}"

    # 采集
    sync_max_commits: int = 200        # 单次同步提交上限（防大仓库回灌拖死接口）
    diff_file_line_limit: int = 400    # 单文件 diff 预算（行）
    diff_total_token_budget: int = 8000  # 单提交 diff 总预算（近似 token = 字符数/2）

    # 检索
    retrieval_top_k: int = 5
    retrieval_min_score: float = 0.05          # local-hash 嵌入的分数阈值
    retrieval_min_score_semantic: float = 0.33  # embedding-3 实测：无关题封顶0.295 / 相关top2最低0.374，取中间

    @property
    def effective_min_score(self) -> float:
        """阈值随嵌入模型自适应：两族模型的分数尺度不可比。"""
        return self.retrieval_min_score_semantic if self.embedding_provider == "openai" else self.retrieval_min_score

    # App
    app_host: str = "0.0.0.0"
    app_port: int = 8100

    # 向量库目录（测试时可重定向到临时目录）
    vector_store_dir: str = ""

    @property
    def vector_dir(self) -> Path:
        if self.vector_store_dir:
            return Path(self.vector_store_dir)
        return BASE_DIR / "data" / "vector_store"


@lru_cache
def get_settings() -> Settings:
    return Settings()
