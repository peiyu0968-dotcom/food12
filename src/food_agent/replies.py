"""組出回覆文字。"""
from __future__ import annotations

from .analyzer import AnalysisResult

FAIL_TEXT = "分析失敗，請再傳一次"


def _num(value: float) -> str:
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.1f}"


def format_reply(result: AnalysisResult, daily_total: float | None, recorded: bool = True) -> str:
    lines: list[str] = []
    if result.is_food:
        lines.append(f"✅ 這是食物：{result.name}")
        lines.append(f"成份：{'、'.join(result.ingredients) if result.ingredients else '（沒有列出）'}")
        lines.append(f"重量：約 {_num(result.weight_g)} 公克")
        lines.append(f"熱量：約 {_num(result.calories_kcal)} 大卡")
    else:
        lines.append(f"❌ 你傳的不是食物，是{result.name}。")

    if daily_total is not None:
        lines.append(f"📊 當日已攝取熱量：{_num(daily_total)} 大卡")
    if not recorded:
        lines.append("⚠️ 這筆沒有記進 Excel（檔案可能正被開著），請關閉後再傳一次。")
    return "\n".join(lines)[:1900]
