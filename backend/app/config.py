"""应用配置加载。

所有配置项从环境变量读取，支持 .env 文件。
后端运行时配置（如 AI API 密钥）优先读取数据库中用户保存的配置，
未配置时回退到环境变量默认值。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend 根目录（app/ 的上级），保证相对路径与启动 cwd 无关
BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """全局配置。"""

    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # 应用
    app_env: str = Field(default="development")
    app_host: str = Field(default="127.0.0.1")
    app_port: int = Field(default=8000)
    app_log_level: str = Field(default="INFO")

    # CORS
    cors_origins: str = Field(
        default="http://localhost:5178,http://127.0.0.1:5178,http://localhost:5179,http://127.0.0.1:5179"
    )
    allowed_hosts: str = Field(default="localhost,127.0.0.1,[::1]")

    # 数据（默认绝对路径，避免受启动目录影响）
    database_url: str = Field(
        default=f"sqlite+aiosqlite:///{BACKEND_DIR / 'data' / 'speech_trainer.db'}"
    )
    upload_dir: str = Field(default=str(BACKEND_DIR / "uploads"))
    report_dir: str = Field(default=str(BACKEND_DIR / "output"))
    # 生产测试包可由 FastAPI 直接托管已构建前端，避免 Windows 运行时依赖 Node.js。
    # 留空时保持开发模式现状，由 Vite 单独提供前端。
    frontend_dist_dir: str = Field(default="")

    # LLM
    llm_provider: str = Field(default="deepseek")
    llm_base_url: str = Field(default="https://api.deepseek.com")
    llm_api_key: str = Field(default="")
    llm_model: str = Field(default="deepseek-v4-pro")
    llm_temperature: float = Field(default=0.7)

    # ASR
    asr_provider: str = Field(default="sherpa_onnx")
    asr_base_url: str = Field(default="")
    asr_api_key: str = Field(default="")
    asr_api_secret: str = Field(default="")
    asr_model: str = Field(
        default="sherpa-onnx-streaming-zipformer-zh-int8-2025-06-30"
    )

    # TTS
    tts_provider: str = Field(default="custom")
    tts_base_url: str = Field(default="")
    tts_api_key: str = Field(default="")
    tts_api_secret: str = Field(default="")
    tts_voice: str = Field(default="")
    tts_speed: float = Field(default=1.0)

    @field_validator("app_env")
    @classmethod
    def _normalize_env(cls, v: str) -> str:
        return v.lower()

    @property
    def cors_origin_list(self) -> List[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def allowed_host_list(self) -> List[str]:
        return [host.strip() for host in self.allowed_hosts.split(",") if host.strip()]

    @property
    def is_dev(self) -> bool:
        return self.app_env == "development"

    @property
    def frontend_dist_path(self) -> Path | None:
        raw = self.frontend_dist_dir.strip()
        if not raw:
            return None
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = BACKEND_DIR / path
        return path.resolve()

    def ensure_dirs(self) -> None:
        """确保运行时目录存在。"""
        for d in (self.upload_dir, self.report_dir):
            Path(d).mkdir(parents=True, exist_ok=True)
        # SQLite 数据库目录
        if self.database_url.startswith("sqlite"):
            db_path = self.database_url.split("///")[-1]
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """单例 Settings。"""
    s = Settings()
    s.ensure_dirs()
    return s
