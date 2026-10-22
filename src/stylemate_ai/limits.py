"""額度、頻率與檔案保護。

本檔含一段防護：
  防護 5：額度、頻率與檔案保護（放在檔案下半部）
框外三個函式是「沒有防護」時的預設行為（不限制），所以把整段註解掉，程式仍能執行。
"""
from __future__ import annotations

import math
import time
from collections import defaultdict, deque
from typing import Callable

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
ALLOWED_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".gif")
WINDOW_SECONDS = 60.0
RATE_LIMITS = {"upload": 3, "ai": 5}  # 每位使用者每分鐘；ai = 推薦與口語指令（之後階段使用）
MAX_INFLIGHT = 3  # 每位使用者同時「排隊中或等待選擇」的工作上限


class Limiter:
    """記錄每位使用者最近的動作與目前進行中的工作。只記數字，不記任何內容。"""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._events: dict[tuple[str, int], deque[float]] = defaultdict(deque)
        self._inflight: dict[int, int] = defaultdict(int)

    def hits_in_window(self, kind: str, user_id: int) -> tuple[int, float]:
        """回傳（最近一分鐘內的次數, 最早那次還要多久才滑出視窗）。"""
        now = self._clock()
        events = self._events[(kind, user_id)]
        while events and now - events[0] >= WINDOW_SECONDS:
            events.popleft()
        wait = (WINDOW_SECONDS - (now - events[0])) if events else 0.0
        return len(events), wait

    def record(self, kind: str, user_id: int) -> None:
        self._events[(kind, user_id)].append(self._clock())

    def inflight(self, user_id: int) -> int:
        return self._inflight[user_id]

    def acquire(self, user_id: int) -> None:
        self._inflight[user_id] += 1

    def release(self, user_id: int) -> None:
        self._inflight[user_id] = max(0, self._inflight[user_id] - 1)

    def reset_user(self, user_id: int) -> None:
        self._inflight.pop(user_id, None)


def check_attachment(filename: str, content_type: str | None, size: int) -> str | None:
    return None  # 沒有防護時：不檢查


def check_rate(limiter: Limiter, user_id: int, kind: str) -> str | None:
    return None  # 沒有防護時：不限制


def check_inflight(limiter: Limiter, user_id: int) -> str | None:
    return None  # 沒有防護時：不限制


# ==== 防護 5：額度、頻率與檔案保護　開始 ====
# 圖片 10 MB 以內、只收 jpg／png／webp／gif；每人每分鐘上傳與 AI 請求有上限；
# 每人同時進行中的工作最多 3 件。超過時回一句明確原因，不處理、不存任何東西。
def check_attachment(filename: str, content_type: str | None, size: int) -> str | None:  # noqa: F811
    if size > MAX_UPLOAD_BYTES:
        return "圖片超過 10 MB，請壓縮後再傳。"
    looks_ok = (content_type or "").startswith("image/") or filename.lower().endswith(ALLOWED_EXTENSIONS)
    if not looks_ok:
        return "只收 jpg、png、webp、gif 圖片。"
    return None


def check_rate(limiter: Limiter, user_id: int, kind: str) -> str | None:  # noqa: F811
    limit = RATE_LIMITS[kind]
    count, wait = limiter.hits_in_window(kind, user_id)
    if count >= limit:
        return f"你傳太快了（每分鐘最多 {limit} 次），請 {math.ceil(wait)} 秒後再試。"
    limiter.record(kind, user_id)
    return None


def check_inflight(limiter: Limiter, user_id: int) -> str | None:  # noqa: F811
    if limiter.inflight(user_id) >= MAX_INFLIGHT:
        return f"你還有 {MAX_INFLIGHT} 件在處理或等你選擇，請先處理完再傳。"
    return None
# ==== 防護 5 結束 ====
