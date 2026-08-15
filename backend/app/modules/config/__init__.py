"""配置模块对外接口。"""

from app.modules.config.store import (
    load_config,
    load_provider_config,
    save_config,
)

__all__ = ["load_config", "load_provider_config", "save_config"]
