"""老師評分流程使用的 `ready` 和 `/done`：bot 一律不回應、不反應、不處理。"""
from __future__ import annotations

import re

_MENTION = re.compile(r"<@[!&]?\d+>")
_TRAILING = " \t\r\n。.!！?？,，~～"


def is_reserved(content: str | None) -> bool:
    """訊息（拿掉 @提及 與頭尾空白標點後）正好是 ready，或以 /done 開頭，就是保留字。"""
    text = _MENTION.sub("", content or "").strip(_TRAILING).lower()
    return text == "ready" or text.startswith("/done")
