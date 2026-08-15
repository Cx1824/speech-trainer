"""档案模块对外接口。"""

from app.modules.profile.store import (
    create_profile,
    delete_profile,
    get_profile,
    list_profiles,
    update_profile,
)

__all__ = [
    "create_profile",
    "update_profile",
    "delete_profile",
    "list_profiles",
    "get_profile",
]
