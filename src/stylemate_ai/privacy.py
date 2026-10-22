"""隱私說明、同意紀錄、刪除自己的全部資料。"""
from __future__ import annotations

import logging
import os
import shutil
import stat
from datetime import datetime
from pathlib import Path

from . import access, excel_store, timeutil
from .excel_store import SETTINGS, UserStore

log = logging.getLogger(__name__)

CONSENT_VERSION = "1"
DELETE_ALL_PHRASE = "確認刪除全部"

NOTICE = (
    "👋 第一次使用 StyleMate AI 前，請先了解：\n"
    "• 你的衣服照片與資料會存在執行這個 bot 的電腦上，管理者看得到檔案。\n"
    "• 照片與文字會送給 Claude 做分析。\n"
    "• 你的衣櫃和別人的完全分開，別人不能查看或修改。\n"
    "• 你可以隨時用「!刪除全部資料」永久清除。\n"
    "按「我同意」或回覆「我同意」才會開始；沒同意，什麼都不會儲存。"
)


def _setting(rows: list[dict], key: str) -> str | None:
    for row in rows:
        if row.get("項目") == key:
            return None if row.get("值") is None else str(row["值"])
    return None


def has_consent(store: UserStore) -> bool:
    """有按過「我同意」，而且同意的是目前這個版本的說明。"""
    if not store.exists():
        return False
    return _setting(store.read(SETTINGS), "隱私說明版本") == CONSENT_VERSION


def record_consent(store: UserStore, now: datetime) -> None:
    """使用者同意後才建立他的資料夾與 Excel。"""
    def work(wb):
        rows = excel_store.sheet_rows(wb, SETTINGS)
        for key, value in (("隱私說明同意時間", timeutil.fmt(now)), ("隱私說明版本", CONSENT_VERSION)):
            existing = next((r for r in rows if r.get("項目") == key), None)
            if existing:
                excel_store.update_row(wb, SETTINGS, existing["_row"], {"值": value})
            else:
                excel_store.append_row(wb, SETTINGS, {"項目": key, "值": value})

    store.transact(work)


def _on_rmtree_error(func, path, _exc) -> None:
    os.chmod(path, stat.S_IWRITE)  # Windows 上唯讀檔案要先改權限才刪得掉
    func(path)


def delete_all(data_dir: Path, actor_id: int, target_user_id: int) -> bool:
    """永久刪除某位使用者的整個資料夾（照片、衣櫃、穿搭紀錄、修改歷史、設定）。
    只允許本人刪除自己的；刪除前再確認一次路徑確實是 DATA_DIR/users/<該使用者 ID>。"""
    if not access.is_owner(actor_id, target_user_id):
        return False
    folder = access.user_dir(data_dir, target_user_id)
    users_root = (data_dir / "users").resolve()
    if folder.parent != users_root or folder.name != str(target_user_id):
        raise access.AccessError("要刪除的路徑不是這位使用者自己的資料夾")
    if not folder.exists():
        return False
    shutil.rmtree(folder, onerror=_on_rmtree_error)
    log.info("DELETE_ALL user=%s at=%s", target_user_id, timeutil.fmt(timeutil.now()))  # 只記 ID 與時間，不記內容
    return True
