"""存照片：判斷真正的圖片格式、清洗檔名、避免撞名。

本檔含一段防護：
  防護 3：衣服名稱拿去當檔名前先清洗（放在 save_photo() 裡）
框外那一行是「沒有防護」時的預設行為，所以把整段註解掉，程式仍能執行。
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from pathlib import Path

MAX_SHORT_NAME = 20
FORBIDDEN_CHARS = set('/\\:*?"<>|')
WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def detect_image_suffix(data: bytes) -> str | None:
    """看檔頭判斷真正的格式，不信上傳者的副檔名。不是支援的圖片就回 None。"""
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    return None


def sanitize_filename_part(name: str) -> str:
    """把衣服名稱洗成可以安全放進檔名的短字串。"""
    chars = []
    for ch in name:
        if ch in FORBIDDEN_CHARS or unicodedata.category(ch).startswith("C"):
            chars.append("_")  # 路徑符號、冒號等，以及控制字元、看不見的特殊字元
        else:
            chars.append(ch)
    text = "".join(chars)
    text = re.sub(r"\.{2,}", "_", text)  # 連續的點（..）
    text = re.sub(r"\s+", " ", text).strip(" .")
    text = text[:MAX_SHORT_NAME].strip(" .")
    if text.split(".")[0].upper() in WINDOWS_RESERVED:
        text = "_" + text
    return text or "衣服"


def _assert_inside(path: Path, folder: Path) -> None:
    if not path.resolve().is_relative_to(folder.resolve()):
        raise ValueError(f"檔案路徑跑到照片資料夾外面了：{path}")


def save_photo(photos_dir: Path, taken_at: datetime, clothing_name: str, data: bytes) -> Path:
    """存成「YYYY-MM-DD hh-mm-ss 名稱.副檔名」；同一秒撞名就加 -2、-3。回傳存好的路徑。
    photos_dir 必須是某位使用者自己的照片資料夾。"""
    suffix = detect_image_suffix(data) or ".jpg"
    stamp = taken_at.strftime("%Y-%m-%d %H-%M-%S")
    photos_dir.mkdir(parents=True, exist_ok=True)

    safe_name = clothing_name  # 沒有防護時：原樣使用 claude 回來的名稱

    # ==== 防護 3：衣服名稱當檔名前先清洗　開始 ====
    # 清掉 / \ .. 冒號等會讓路徑亂跑的字，最後再確認檔案真的落在這位使用者的照片資料夾裡面。
    safe_name = sanitize_filename_part(clothing_name)
    _assert_inside(photos_dir / f"{stamp} {safe_name}{suffix}", photos_dir)
    # ==== 防護 3 結束 ====

    counter = 1
    while True:
        tail = "" if counter == 1 else f"-{counter}"
        path = photos_dir / f"{stamp} {safe_name}{tail}{suffix}"
        try:
            with open(path, "xb") as f:  # 只在檔案不存在時才建立，絕不覆蓋別人的照片
                f.write(data)
            return path
        except FileExistsError:
            counter += 1


def delete_photo_file(photos_dir: Path, path: Path) -> bool:
    """刪除一張照片；只允許刪除這位使用者照片資料夾裡的檔案。"""
    _assert_inside(path, photos_dir)
    if path.exists():
        path.unlink()
        return True
    return False
