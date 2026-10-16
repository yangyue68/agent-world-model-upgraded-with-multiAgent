"""生成、评审与 rollout 共用的模拟登录身份；不是生产系统认证。"""
import os

try:
    CURRENT_USER_ID = int(os.environ.get("AWM_CURRENT_USER_ID", "1"))
except ValueError as exc:
    raise ValueError("AWM_CURRENT_USER_ID 必须是整数") from exc


def environment_context():
    return {"current_user_id": CURRENT_USER_ID, "sqlite_foreign_keys_enforced_by_runtime": True}
