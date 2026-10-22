"""第二階段：穿搭紀錄、衣服使用次數、統計，以及對應的文字指令。"""
from datetime import date, datetime, timedelta

import pytest

from stylemate_ai import outfits, stats, wardrobe
from stylemate_ai.config import Config
from stylemate_ai.engine import Actor, Engine
from stylemate_ai.excel_store import UserStore
from stylemate_ai.schemas import ClothingItem
from stylemate_ai.wardrobe import NotFound, WardrobeError

NOW = datetime(2026, 10, 23, 14, 5, 33)
TODAY = NOW.date()
ALICE, BOB = Actor(1001, "Alice"), Actor(2002, "Bob")


def garment(name="白襯衫", **kw):
    base = dict(name=name, category="top", colors=["白"], material="棉", style="簡約", pattern="素面",
                seasons=["春", "夏"], warmth=2, formality=3, confidence="high")
    base.update(kw)
    return ClothingItem(**base)


@pytest.fixture
def cfg(tmp_path):
    return Config("t", 1, "haiku", (tmp_path / "data").resolve())


@pytest.fixture
def store(cfg):
    return UserStore(cfg.data_dir, 1001, retries=2, delay=0)


def add(store, *items, when=NOW):
    return [c["衣服ID"] for c in wardrobe.add_items(store, store.user_id, list(items), None, when)]


# ------------------------------------------------------------------ 日期解析

@pytest.mark.parametrize("token,expected", [
    ("今天", TODAY), ("昨天", TODAY - timedelta(days=1)), ("前天", TODAY - timedelta(days=2)),
    ("明天", TODAY + timedelta(days=1)), ("後天", TODAY + timedelta(days=2)),
    ("2026-10-20", date(2026, 10, 20)), ("10/20", date(2026, 10, 20)), ("10月20日", date(2026, 10, 20)),
])
def test_parse_day_word(token, expected):
    assert outfits.parse_day_word(token, TODAY) == expected


@pytest.mark.parametrize("token", ["白襯衫", "2026-13-40", "13/45", "", "上班"])
def test_parse_day_word_rejects_other_text(token):
    assert outfits.parse_day_word(token, TODAY) is None


# ------------------------------------------------------------------ 穿搭紀錄

def test_log_outfit_updates_wear_counts_and_history(store):
    a, b = add(store, garment("白襯衫"), garment("牛仔褲", category="bottom"))
    rec = outfits.log_outfit(store, 1001, [a, b], TODAY, NOW, occasion="上班")
    assert rec["衣服ID清單"] == f"{a},{b}" and rec["來源"] == "自選" and rec["狀態"] == "正常"
    items = {r["衣服ID"]: r["使用次數"] for r in wardrobe.list_items(store)["items"]}
    assert items == {a: 1, b: 1}
    outfits.log_outfit(store, 1001, [a], TODAY, NOW)  # 同一天可以記多次，每次都算
    assert wardrobe.get_item(store, a)["使用次數"] == 2 and wardrobe.get_item(store, b)["使用次數"] == 1
    hist = [h for h in store.read("修改歷史") if h["對象"] == "穿搭紀錄"]
    assert [h["動作"] for h in hist] == ["新增", "新增"]


def test_log_outfit_backdate_ok_future_and_too_old_rejected(store):
    a, = add(store, garment())
    outfits.log_outfit(store, 1001, [a], TODAY - timedelta(days=5), NOW)
    with pytest.raises(WardrobeError, match="未來"):
        outfits.log_outfit(store, 1001, [a], TODAY + timedelta(days=1), NOW)
    with pytest.raises(WardrobeError, match="一年"):
        outfits.log_outfit(store, 1001, [a], TODAY - timedelta(days=400), NOW)


def test_log_outfit_rejects_bad_ids_deleted_and_limits(store):
    a, b = add(store, garment(), garment("牛仔褲"))
    wardrobe.soft_delete(store, 1001, b, NOW)
    with pytest.raises(NotFound):
        outfits.log_outfit(store, 1001, [a, "ZZZZ"], TODAY, NOW)
    with pytest.raises(NotFound):
        outfits.log_outfit(store, 1001, [b], TODAY, NOW)  # 已刪除的衣服不能被記進新的穿搭
    with pytest.raises(WardrobeError):
        outfits.log_outfit(store, 1001, [], TODAY, NOW)
    with pytest.raises(WardrobeError):
        outfits.log_outfit(store, 1001, [a] * 3 + [f"X{i}" for i in range(11)], TODAY, NOW)
    assert store.read("穿搭紀錄") == []  # 失敗的都沒有寫入


def test_duplicate_ids_in_one_outfit_count_once(store):
    a, = add(store, garment())
    outfits.log_outfit(store, 1001, [a, a, a], TODAY, NOW)
    assert wardrobe.get_item(store, a)["使用次數"] == 1


def test_list_outfits_window_order_and_names(store):
    a, b = add(store, garment("白襯衫"), garment("牛仔褲", category="bottom"))
    outfits.log_outfit(store, 1001, [a], TODAY - timedelta(days=29), NOW)
    outfits.log_outfit(store, 1001, [b], TODAY - timedelta(days=30), NOW)   # 超出 30 天
    outfits.log_outfit(store, 1001, [a, b], TODAY, NOW)
    got = outfits.list_outfits(store, TODAY, 30)
    assert [r["日期物件"] for r in got] == [TODAY, TODAY - timedelta(days=29)]
    assert got[0]["衣服"] == [(a, "白襯衫"), (b, "牛仔褲")]
    assert len(outfits.list_outfits(store, TODAY, 7)) == 1 and len(outfits.list_outfits(store, TODAY, 365)) == 3


def test_modify_delete_restore_outfit_with_history(store):
    a, b = add(store, garment("白襯衫"), garment("牛仔褲", category="bottom"))
    oid = outfits.log_outfit(store, 1001, [a], TODAY, NOW)["紀錄ID"]
    later = NOW + timedelta(hours=1)
    outfits.modify_outfit(store, 1001, oid, {"日期": TODAY - timedelta(days=1), "場合": "約會", "衣服": [a, b]}, "記錯了", later)
    rec = outfits.get_outfit(store, oid)
    assert rec["日期"] == (TODAY - timedelta(days=1)).isoformat() and rec["場合"] == "約會" and len(rec["衣服"]) == 2
    outfits.soft_delete_outfit(store, 1001, oid, later)
    assert wardrobe.get_item(store, a)["使用次數"] == 0          # 刪除後不再計入次數
    with pytest.raises(NotFound):
        outfits.get_outfit(store, oid)
    outfits.restore_outfit(store, 1001, oid, later)
    assert wardrobe.get_item(store, a)["使用次數"] == 1
    hist = [h["動作"] for h in store.read("修改歷史") if h["對象ID"] == oid]
    assert hist == ["新增", "修改", "刪除", "復原"]
    with pytest.raises(WardrobeError):
        outfits.restore_outfit(store, 1001, oid, later)
    with pytest.raises(WardrobeError, match="未來"):
        outfits.modify_outfit(store, 1001, oid, {"日期": TODAY + timedelta(days=3)}, "x", later)
    with pytest.raises(NotFound):
        outfits.modify_outfit(store, 1001, oid, {"衣服": ["ZZZZ"]}, "x", later)


def test_outfits_of_different_users_are_separate(cfg):
    sa, sb = UserStore(cfg.data_dir, 1, retries=2, delay=0), UserStore(cfg.data_dir, 2, retries=2, delay=0)
    a, = add(sa, garment("A的"))
    oid = outfits.log_outfit(sa, 1, [a], TODAY, NOW)["紀錄ID"]
    with pytest.raises(NotFound):
        outfits.log_outfit(sb, 2, [a], TODAY, NOW)          # B 不能用 A 的衣服 ID
    with pytest.raises(NotFound):
        outfits.get_outfit(sb, oid)
    with pytest.raises(NotFound):
        outfits.soft_delete_outfit(sb, 2, oid, NOW)
    assert outfits.list_outfits(sb, TODAY) == []


# ------------------------------------------------------------------ 統計

def test_usage_rate_and_most_worn(store):
    ids = add(store, *[garment(f"衣{i}") for i in range(4)])
    for _ in range(3):
        outfits.log_outfit(store, 1001, [ids[0]], TODAY, NOW)
    outfits.log_outfit(store, 1001, [ids[1]], TODAY - timedelta(days=2), NOW)
    s = stats.compute(store, TODAY, 30)
    assert s.total == 4 and s.worn == 2 and s.usage_rate == 0.5 and s.outfit_count == 4
    assert s.most_worn == [(ids[0], "衣0", 3), (ids[1], "衣1", 1)]
    s7 = stats.compute(store, TODAY, 1)
    assert s7.worn == 1 and s7.usage_rate == 0.25 and s7.most_worn == [(ids[0], "衣0", 3)]


def test_idle_over_60_days_and_never_worn(store):
    old = NOW - timedelta(days=100)
    a = add(store, garment("很久沒穿"), when=old)[0]
    b = add(store, garment("從未穿過"), when=old)[0]
    c = add(store, garment("剛買的"), when=NOW - timedelta(days=5))[0]
    d = add(store, garment("常穿"), when=old)[0]
    outfits.log_outfit(store, 1001, [a], TODAY - timedelta(days=70), NOW)
    outfits.log_outfit(store, 1001, [d], TODAY - timedelta(days=1), NOW)
    s = stats.compute(store, TODAY, 30)
    idle = {i.name: i for i in s.idle}
    assert set(idle) == {"很久沒穿", "從未穿過"}                       # 剛買的、常穿 不在清單裡
    assert idle["很久沒穿"].days == 70 and not idle["很久沒穿"].never_worn
    assert idle["從未穿過"].days == 100 and idle["從未穿過"].never_worn
    assert [i.name for i in s.idle][0] == "從未穿過"                    # 最久的排前面
    assert c not in [i.item_id for i in s.idle]


def test_idle_boundary_is_60_days(store):
    a = add(store, garment("剛好60"), when=NOW - timedelta(days=200))[0]
    b = add(store, garment("59天"), when=NOW - timedelta(days=200))[0]
    outfits.log_outfit(store, 1001, [a], TODAY - timedelta(days=60), NOW)
    outfits.log_outfit(store, 1001, [b], TODAY - timedelta(days=59), NOW)
    assert [i.name for i in stats.compute(store, TODAY, 30).idle] == ["剛好60"]


def test_style_and_color_shares_are_weighted_by_wears_not_by_item_count(store):
    a = add(store, garment("簡約白", style="簡約", colors=["白"]))[0]
    b, c = add(store, garment("街頭黑1", style="街頭", colors=["黑"]), garment("街頭黑2", style="街頭", colors=["黑"]))
    for _ in range(3):
        outfits.log_outfit(store, 1001, [a], TODAY, NOW)       # 簡約風只有 1 件，但穿了 3 次
    outfits.log_outfit(store, 1001, [b], TODAY, NOW)
    s = stats.compute(store, TODAY, 30)
    assert dict(s.styles) == {"簡約": 0.75, "街頭": 0.25}
    assert dict(s.colors) == {"白": 0.75, "黑": 0.25}


def test_deleted_outfits_and_deleted_garments(store):
    a, b = add(store, garment("A"), garment("B"))
    o1 = outfits.log_outfit(store, 1001, [a], TODAY, NOW)["紀錄ID"]
    outfits.log_outfit(store, 1001, [b], TODAY, NOW)
    outfits.soft_delete_outfit(store, 1001, o1, NOW)             # 被刪的穿搭不算
    wardrobe.soft_delete(store, 1001, b, NOW)                    # 被刪的衣服不在總件數與使用率裡
    s = stats.compute(store, TODAY, 30)
    assert s.total == 1 and s.worn == 0 and s.usage_rate == 0 and s.outfit_count == 1 and s.most_worn == []


def test_not_enough_data():
    empty = stats.Stats(days=30, total=0, worn=0, usage_rate=0, outfit_count=0)
    assert not empty.enough_data
    assert not stats.Stats(days=30, total=3, worn=0, usage_rate=0, outfit_count=0).enough_data


def test_learned_preferences(store):
    a = add(store, garment("簡約白", style="簡約", colors=["白"]))[0]
    outfits.log_outfit(store, 1001, [a], TODAY, NOW)
    assert stats.learned_preferences(store, TODAY) == {"styles": ["簡約"], "colors": ["白"]}


# ------------------------------------------------------------------ 文字指令

def make_engine(cfg):
    return Engine(cfg, clock=lambda: NOW, retries=2, delay=0, advanced=True)  # 進階功能預設關閉，這裡明確開啟來測


def consent(e, actor, mid=1):
    out = e.consent_prompt(actor)
    e.registry.register(out.pending, mid)
    e.resolve(out.pending, "agree", actor.user_id)


def open_public(e, actor, mid=900):
    out = e.run_command(actor, "!統計")
    assert out.pending and out.pending.kind == "public_confirm"
    e.registry.register(out.pending, mid)
    e.resolve(out.pending, "confirm", actor.user_id)


def test_wear_command_by_name_and_id_and_day(cfg):
    e = make_engine(cfg)
    consent(e, ALICE)
    a, b = add(e.store(1001), garment("白襯衫"), garment("牛仔褲", category="bottom"))
    res = e.run_command(ALICE, f"!穿搭 白襯衫 #{b.lower()} 昨天 場合:上班")
    assert "已記錄" in res.public and "白襯衫" not in res.public and "2026-10-22" in res.public  # 沒公開：不洩漏名稱
    rec = outfits.list_outfits(e.store(1001), TODAY)[0]
    assert rec["衣服ID清單"] == f"{a},{b}" and rec["場合"] == "上班" and rec["日期物件"] == TODAY - timedelta(days=1)
    open_public(e, ALICE)
    shown = e.run_command(ALICE, "!穿搭 白襯衫")
    assert shown.public.startswith("🔓") and "白襯衫" in shown.public
    assert wardrobe.get_item(e.store(1001), a)["使用次數"] == 2


def test_wear_command_errors_are_friendly(cfg):
    e = make_engine(cfg)
    consent(e, ALICE)
    add(e.store(1001), garment("白襯衫"))
    assert "用法" in e.run_command(ALICE, "!穿搭").public
    assert "用法" in e.run_command(ALICE, "!穿搭 昨天").public
    assert "找不到「紅帽子」" in e.run_command(ALICE, "!穿搭 紅帽子").public
    assert "未來" in e.run_command(ALICE, "!穿搭 白襯衫 明天").public
    assert e.store(1001).read("穿搭紀錄") == []


def test_wear_ambiguous_name_asks_to_choose_then_logs(cfg):
    e = make_engine(cfg)
    consent(e, ALICE)
    s = e.store(1001)
    a, b = add(s, garment("白襯衫"), garment("藍襯衫", colors=["藍"]))
    hidden = e.run_command(ALICE, "!穿搭 襯衫")           # 沒公開：不列出名稱，請改用編號
    assert hidden.pending is None and "2 件都符合" in hidden.public and "白襯衫" not in hidden.public
    open_public(e, ALICE)
    out = e.run_command(ALICE, "!穿搭 襯衫 昨天")
    assert out.pending.kind == "choose_item" and "白襯衫" in out.public and "藍襯衫" in out.public
    e.registry.register(out.pending, 77)
    assert e.resolve(out.pending, "pick:1", BOB.user_id).denied
    res = e.resolve_text(out.pending, "2", ALICE.user_id)
    assert "已記錄 2026-10-22" in res.public
    assert outfits.list_outfits(s, TODAY)[0]["衣服ID清單"] == b
    assert out.pending.closed


def test_choose_item_cancel_and_bad_number(cfg):
    e = make_engine(cfg)
    consent(e, ALICE)
    add(e.store(1001), garment("白襯衫"), garment("藍襯衫"))
    open_public(e, ALICE)
    out = e.run_command(ALICE, "!刪除 襯衫")
    assert out.pending.kind == "choose_item"
    e.registry.register(out.pending, 78)
    bad = e.resolve(out.pending, "pick:9", ALICE.user_id)
    assert "編號不對" in bad.public and not out.pending.closed
    assert "取消" in e.resolve_text(out.pending, "取消", ALICE.user_id).public
    assert wardrobe.active_count(e.store(1001)) == 2


def test_view_and_delete_work_with_names(cfg):
    e = make_engine(cfg)
    consent(e, ALICE)
    add(e.store(1001), garment("獨一無二的西裝"))
    open_public(e, ALICE)
    assert "獨一無二的西裝" in e.run_command(ALICE, "!查看 西裝").public
    out = e.run_command(ALICE, "!刪除 西裝")
    assert out.pending.kind == "confirm_delete" and "獨一無二的西裝" in out.public


def test_outfit_history_and_stats_are_gated_and_show_banner(cfg):
    e = make_engine(cfg)
    consent(e, ALICE)
    a, = add(e.store(1001), garment("秘密襯衫"))
    outfits.log_outfit(e.store(1001), 1001, [a], TODAY, NOW, occasion="面試")
    for cmd in ("!穿搭紀錄", "!統計"):
        gate = e.run_command(ALICE, cmd)
        assert gate.pending.kind == "public_confirm" and "秘密襯衫" not in gate.public
    open_public(e, ALICE)
    hist = e.run_command(ALICE, "!穿搭紀錄 30天")
    assert hist.public.startswith("🔓") and "秘密襯衫" in hist.public and "面試" in hist.public and f"#{outfits.list_outfits(e.store(1001), TODAY)[0]['紀錄ID']}" in hist.public
    st = e.run_command(ALICE, "!統計")
    assert "使用率 100%" in st.public and "最常穿：秘密襯衫（1 次）" in st.public


def test_private_mode_skips_public_warning_and_banner(cfg):
    e = make_engine(cfg)
    consent(e, ALICE)
    a, = add(e.store(1001), garment("私密襯衫"))
    out = e.run_command(ALICE, "!衣櫃", private=True)
    assert out.pending is None and "私密襯衫" in out.public and not out.public.startswith("🔓")
    assert not e.public_mode.active(ALICE.user_id)
    wear = e.run_command(ALICE, "!穿搭 私密襯衫", private=True)
    assert "私密襯衫" in wear.public                                  # 私人回覆：可以直接顯示名稱


def test_stats_empty_and_period_argument(cfg):
    e = make_engine(cfg)
    consent(e, ALICE)
    open_public(e, ALICE)
    assert "資料還不夠" in e.run_command(ALICE, "!統計").public
    a, = add(e.store(1001), garment("襯衫"))
    outfits.log_outfit(e.store(1001), 1001, [a], TODAY - timedelta(days=40), NOW)
    assert "資料還不夠" in e.run_command(ALICE, "!統計 30天").public
    assert "最近 90 天" in e.run_command(ALICE, "!統計 90天").public


def test_edit_delete_restore_outfit_commands(cfg):
    e = make_engine(cfg)
    consent(e, ALICE)
    s = e.store(1001)
    a, b = add(s, garment("白襯衫"), garment("牛仔褲", category="bottom"))
    e.run_command(ALICE, "!穿搭 白襯衫")
    oid = outfits.list_outfits(s, TODAY)[0]["紀錄ID"]
    res = e.run_command(ALICE, f"!改穿搭 #{oid.lower()} 日期=昨天 場合=約會 衣服=白襯衫,牛仔褲 原因:記錯了")
    assert "已更新" in res.public
    rec = outfits.get_outfit(s, oid)
    assert rec["日期"] == "2026-10-22" and rec["場合"] == "約會" and len(rec["衣服"]) == 2
    assert "看不懂日期" in e.run_command(ALICE, f"!改穿搭 {oid} 日期=亂寫").public
    assert "不能修改" in e.run_command(ALICE, f"!改穿搭 {oid} 密碼=1").public
    ask = e.run_command(ALICE, f"!刪除穿搭 {oid}")
    e.registry.register(ask.pending, 80)
    assert wardrobe.get_item(s, a)["使用次數"] == 1                       # 確認前沒刪
    assert "已刪除穿搭紀錄" in e.resolve_text(ask.pending, "確認", ALICE.user_id).public
    assert wardrobe.get_item(s, a)["使用次數"] == 0
    assert "已復原" in e.run_command(ALICE, f"!復原穿搭 {oid}").public
    assert wardrobe.get_item(s, a)["使用次數"] == 1
    assert "找不到" in e.run_command(ALICE, "!刪除穿搭 O9999").public


def test_other_user_cannot_touch_my_outfit_records(cfg):
    e = make_engine(cfg)
    consent(e, ALICE, 1)
    consent(e, BOB, 2)
    a, = add(e.store(1001), garment("A的襯衫"))
    e.run_command(ALICE, "!穿搭 A的襯衫")
    oid = outfits.list_outfits(e.store(1001), TODAY)[0]["紀錄ID"]
    for cmd in (f"!改穿搭 {oid} 場合=x", f"!刪除穿搭 {oid}", f"!復原穿搭 {oid}"):
        assert "找不到" in e.run_command(BOB, cmd).public
    assert "找不到「A的襯衫」" in e.run_command(BOB, "!穿搭 A的襯衫").public
    assert len(outfits.list_outfits(e.store(1001), TODAY)) == 1
