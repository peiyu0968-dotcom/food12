from food_agent.analyzer import AnalysisResult
from food_agent.replies import format_reply


def test_food_reply_has_everything_and_daily_total():
    text = format_reply(AnalysisResult(True, "雞腿便當", ["白飯", "炸雞腿"], 450, 820), 1350)
    for part in ("這是食物", "雞腿便當", "白飯、炸雞腿", "450 公克", "820 大卡", "當日已攝取熱量：1,350 大卡"):
        assert part in text


def test_non_food_reply_says_what_it_is_and_still_has_total():
    text = format_reply(AnalysisResult(False, "馬克杯", [], None, None), 800)
    assert "不是食物" in text and "馬克杯" in text and "當日已攝取熱量：800 大卡" in text


def test_not_recorded_warning():
    text = format_reply(AnalysisResult(True, "飯", [], 100, 200), None, recorded=False)
    assert "沒有記進 Excel" in text
