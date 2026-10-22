"""組出回覆文字。

本檔含一段防護：
  防護 7：Discord 輸出不觸發 @ 提及（放在 ALLOWED_MENTIONS 附近）
框外的 ALLOWED_MENTIONS = None 是「沒有防護」時的預設行為（Discord 預設會通知被 @ 的人），
所以把整段註解掉，程式仍能執行。

所有固定文字都不可以等於 `ready`，也不可以以 `/done` 開頭（見 tests）。
"""
from __future__ import annotations

from .schemas import AnalysisResult

MAX_MESSAGE = 1900

ALLOWED_MENTIONS = None  # 沒有防護時：衣服名稱裡的 @everyone 之類的字會真的通知別人

# ==== 防護 7：Discord 輸出不觸發 @ 提及　開始 ====
# 所有回覆都帶「不允許任何提及」的設定，衣服名稱就算是 @everyone 也只是純文字。
import discord  # noqa: E402

ALLOWED_MENTIONS = discord.AllowedMentions.none()
# ==== 防護 7 結束 ====

FAIL_TEXT = "分析失敗，請再傳一次。"
STORAGE_FAIL_TEXT = "暫時無法記錄（Excel 可能正被開著），請稍後再試。"
NOT_YOURS = "這不是你的操作。"
EXPIRED = "這個操作已經過期或完成了。"
AI_FAIL_TEXT = "我暫時沒辦法處理這個請求（AI 沒有回應或回應格式不對），請稍後再試。"

PUBLIC_WARNING = (
    "⚠️ 公開提醒\n"
    "接下來的內容（你的衣櫃、穿搭、統計或偏好）會被這個頻道的**所有成員**看到。\n"
    "要公開顯示，請按「確認公開」或回覆「確認公開」；不想公開，請按「取消」或不理會（2 分鐘後自動取消）。\n"
    "確認後 30 分鐘內可以連續公開顯示，每則結果都會標示「🔓 公開顯示中」；隨時可以輸入 `!結束公開`。\n"
    "不想公開就按「取消」，你的資料不會顯示。"
)

HELP = (
    "**StyleMate AI 指令**（在這個頻道用 `!` 開頭的指令）\n"
    "**1. 把衣服加進衣櫃**\n"
    "• 直接傳衣服照片：辨識照片裡的每一件，你再選要加入哪幾件（按按鈕，或回覆「全部」「1,3」「都不要」）\n"
    "**2. 管理你的衣櫃**\n"
    "• `!衣櫃`（可加條件：`!衣櫃 外套`、`!衣櫃 顏色:黑 風格:簡約`、`!衣櫃 2`）、`!查看 <ID或名稱>`\n"
    "• `!修改 <ID> 材質=亞麻 保暖=2 原因:洗過縮水`、`!刪除 <ID>`（可復原）、`!復原 <ID>`、`!刪圖 <ID>`\n"
    "**3. AI 幫你配衣服**\n"
    "• `!推薦 明天面試`：依場合和需求，從你衣櫃裡現有的衣服推薦一套，並說明理由；`!再來一套` 換另一套\n"
    "**其他**\n"
    "• `!刪除全部資料`：永久清除你的所有資料（要確認兩次）、`!結束公開`\n"
    "顯示你的衣櫃或推薦前，我會先提醒「頻道所有人都看得到」。每個按鈕都可以改用「回覆那則訊息」的方式操作。"
)

NOT_AVAILABLE = "這個功能目前沒有開放。現在提供三件事：傳照片加入衣櫃、管理衣櫃（`!衣櫃`）、AI 推薦穿搭（`!推薦`）。輸入 `!說明` 看用法。"

HELP_ADVANCED = (
    "**StyleMate AI 指令**（進階功能已開啟）\n"
    "**衣櫃**\n"
    "• 直接傳衣服照片：辨識照片裡的每一件，你再選要加入哪幾件\n"
    "• `!衣櫃`（可加條件：`!衣櫃 外套`、`!衣櫃 顏色:黑 風格:簡約`、`!衣櫃 2`）、`!查看 <ID或名稱>`\n"
    "• `!修改 <ID> 材質=亞麻 保暖=2 原因:洗過縮水`、`!刪除 <ID>`、`!復原 <ID>`、`!刪圖 <ID>`\n"
    "**穿搭**\n"
    "• `!穿搭 白襯衫 牛仔褲 [昨天|10/20] [場合:上班]`：記錄穿了什麼（可補記、同一天可多次）\n"
    "• `!穿搭紀錄 [30天]`、`!改穿搭 <紀錄ID> 日期=昨天 場合=上班`、`!刪除穿搭 <紀錄ID>`、`!復原穿搭 <紀錄ID>`\n"
    "• `!推薦 明天面試 [18度 下雨] [地點:台北]`：從你的衣櫃推薦一套；`!再來一套`\n"
    "• `!統計 [90天]`：使用率、最常穿、很久沒穿、常穿風格與顏色\n"
    "**設定**\n"
    "• `!設定 城市 台北`（自動查天氣）、`!偏好 喜歡深色、不穿裙子`（`!偏好 清除`）\n"
    "• `!刪除全部資料`：永久清除你的所有資料（要確認兩次）、`!結束公開`\n"
    "每個按鈕都可以改用「回覆那則訊息」的方式操作（例如回覆「全部」、「1,3」、「確認」、「穿這套」）。"
)

MENTION_HELP = "用口語聊天的功能目前沒有開放。請用 `!說明` 看指令，例如 `!推薦 明天面試`，或直接傳衣服照片。"


def clip(text: str) -> str:
    return text if len(text) <= MAX_MESSAGE else text[: MAX_MESSAGE - 1] + "…"


# ---------------------------------------------------------------- 上傳與衣櫃

def analysis_listing(result: AnalysisResult) -> str:
    lines = [f"🧥 我看到 {len(result.items)} 件："]
    for i, it in enumerate(result.items, start=1):
        lines.append(f"{i}. {it.name}｜{it.category_label}・{'、'.join(it.colors)}・{it.style}・信心{it.confidence_label}")
    if result.general_tip:
        lines.append(f"💡 {result.general_tip}")
    lines.append("請按下面的按鈕選擇，或回覆這則訊息：`全部`、`1,3`、`都不要`（10 分鐘內有效，沒選就什麼都不會儲存）")
    return clip("\n".join(lines))


def not_clothing(what: str) -> str:
    what = what or "別的東西"
    return f"這看起來不是衣服，是{what}。衣櫃只收衣服照片，這張沒有儲存。"


BLURRY = "照片看不清楚，沒辦法判斷。換個光線或角度再傳一次吧？這張沒有儲存。"


def added_public(n: int) -> str:
    return f"✅ 已加入 {n} 件"


def closet(page: dict, filters: str, banner: str, *, show_wear: bool = True) -> str:
    if page["all_active"] == 0:
        return banner + "你的衣櫃還是空的。傳一張衣服照片來開始吧！"
    if page["total"] == 0:
        return banner + f"沒有符合「{filters}」的衣服。"
    head = f"👗 你的衣櫃（{page['total']} 件" + (f"，條件：{filters}" if filters else "") + f"）第 {page['page']}/{page['pages']} 頁"
    lines = [banner + head]
    for r in page["items"]:
        lines.append(
            f"#{r['衣服ID']} {r['名稱']}｜{r['種類']}・{r['顏色']}・{r['風格']}" + (f"｜穿過 {r['使用次數']} 次" if show_wear else "")
        )
    if page["pages"] > page["page"]:
        lines.append(f"下一頁：`!衣櫃 {page['page'] + 1}`")
    return clip("\n".join(lines))


def detail(item: dict, history: list[dict], banner: str, *, show_wear: bool = True) -> str:
    deleted = item["狀態"] == "已刪除"
    lines = [
        banner + f"#{item['衣服ID']} {item['名稱']}" + ("（已刪除，可用 `!復原` 救回）" if deleted else ""),
        f"種類：{item['種類']}｜顏色：{item['顏色']}｜材質：{item['材質']}",
        f"風格：{item['風格']}｜圖案：{item['圖案']}｜適合季節：{item['適合季節']}",
        f"保暖程度：{item['保暖程度']}/5｜正式程度：{item['正式程度']}/5｜AI 信心：{item['AI信心']}",
        f"備註：{item['備註'] or '（無）'}｜品牌：{item['品牌'] or '（無）'}",
        f"入庫：{item['入庫時間']}｜最後修改：{item['最後修改時間']}" + (f"｜穿過 {item['使用次數']} 次" if show_wear else ""),
        "照片：" + ("附在下面" if item["照片位置"] else "（沒有）"),
    ]
    if history:
        lines.append("修改歷史（最近）：")
        for h in history:
            lines.append(f"• {h['修改時間']} {h['動作']}" + (f"：{h['變更欄位']}" if h["變更欄位"] else ""))
    return clip("\n".join(lines))


# ---------------------------------------------------------------- 第二階段：穿搭紀錄與統計

WEEKDAYS = "一二三四五六日"


def outfit_list(records: list[dict], days: int, banner: str) -> str:
    if not records:
        return banner + f"最近 {days} 天還沒有穿搭紀錄。用 `!穿搭 白襯衫 牛仔褲` 記錄一次吧！"
    lines = [banner + f"👔 最近 {days} 天的穿搭（{len(records)} 筆）"]
    for r in records:
        d = r["日期物件"]
        names = "、".join(name for _, name in r["衣服"])
        extra = f"｜{r['場合']}" if r.get("場合") else ""
        extra += f"｜{r['天氣']}" if r.get("天氣") else ""
        lines.append(f"• {d.isoformat()}（週{WEEKDAYS[d.weekday()]}）#{r['紀錄ID']}：{names}{extra}")
    return clip("\n".join(lines))


def stats_text(s, banner: str) -> str:
    if not s.enough_data:
        return banner + "資料還不夠，先記錄幾天穿搭吧！（`!穿搭 白襯衫 牛仔褲`）"
    lines = [
        banner + f"📊 你的衣櫃統計（最近 {s.days} 天）",
        f"衣櫃共 {s.total} 件，穿過 {s.worn} 件 → 使用率 {s.usage_rate * 100:.0f}%（穿搭紀錄 {s.outfit_count} 筆）",
    ]
    if s.most_worn:
        lines.append("最常穿：" + "、".join(f"{name}（{n} 次）" for _, name, n in s.most_worn))
    if s.idle:
        parts = []
        for it in s.idle:
            parts.append(f"{it.name}（入庫後從未穿過，已 {it.days} 天）" if it.never_worn else f"{it.name}（已 {it.days} 天）")
        lines.append("很久沒穿（超過 60 天）：" + "、".join(parts))
    else:
        lines.append("很久沒穿：沒有超過 60 天沒穿的衣服 👍")
    if s.styles:
        lines.append("常穿風格：" + "、".join(f"{k} {v * 100:.0f}%" for k, v in s.styles))
    if s.colors:
        lines.append("常穿顏色：" + "、".join(f"{k} {v * 100:.0f}%" for k, v in s.colors))
    return clip("\n".join(lines))


# ---------------------------------------------------------------- 第三個核心功能：AI 推薦穿搭

def reco_text(reco, outfit_lines: list[str], banner: str) -> str:
    head = banner + "👔 " + (reco.request.occasion or "日常")
    if reco.weather is not None:
        head += f"｜{reco.weather.summary()}"
    lines = [head]
    if reco.complete:
        lines += outfit_lines
        lines.append(f"💬 理由：{reco.reason}")
    else:
        lines.append(f"目前湊不出完整的一套：{reco.reason}")
    if reco.missing:
        lines.append("🛍️ 如果想更適合，可以考慮添購：" + "；".join(f"{t}（{why}）" if why else t for t, why in reco.missing))
    if reco.note:
        lines.append(f"📝 {reco.note}")
    if reco.complete:
        lines.append("想換一套？按「再來一套」或回覆「再來一套」。")
    return clip("\n".join(lines))
