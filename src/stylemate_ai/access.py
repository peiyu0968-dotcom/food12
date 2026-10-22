"""使用者隔離與權限檢查。

本檔含一段防護：
  防護 6：使用者隔離與權限檢查（放在檔案下半部）
框外的兩個函式是「沒有防護」時的預設行為（不檢查），所以把整段註解掉，程式仍能執行。
"""
from __future__ import annotations

from pathlib import Path


class AccessError(Exception):
    """路徑或身分不合法。"""


def user_dir(data_dir: Path, user_id: int) -> Path:
    """某位使用者的資料夾：DATA_DIR/users/<Discord 使用者 ID>。"""
    return data_dir / "users" / str(user_id)  # 沒有防護時：不檢查直接組路徑


def is_owner(actor_id: int, owner_id: int) -> bool:
    """發起操作的人，是不是這份資料／這個待確認動作的主人。"""
    return True  # 沒有防護時：誰都算主人


# ==== 防護 6：使用者隔離與權限檢查　開始 ====
# 資料夾名稱只能是正整數的 Discord 使用者 ID，組完路徑後確認確實落在 DATA_DIR/users 底下；
# 按鈕、打字確認與資料存取，一律只認「發起者 ID 等於主人 ID」。
def user_dir(data_dir: Path, user_id: int) -> Path:  # noqa: F811
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
        raise AccessError("使用者 ID 不合法")
    base = (data_dir / "users").resolve()
    path = (base / str(user_id)).resolve()
    if path.parent != base:
        raise AccessError("使用者資料夾的位置不合法")
    return path


def is_owner(actor_id: int, owner_id: int) -> bool:  # noqa: F811
    return (
        isinstance(actor_id, int)
        and isinstance(owner_id, int)
        and not isinstance(actor_id, bool)
        and not isinstance(owner_id, bool)
        and actor_id == owner_id
    )
# ==== 防護 6 結束 ====
