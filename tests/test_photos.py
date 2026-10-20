from datetime import datetime

import pytest

from food_agent.photos import detect_image_suffix, sanitize_filename_part, save_photo

JPG = b"\xff\xd8\xff\xe0" + b"0" * 20
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 20
WHEN = datetime(2026, 10, 20, 12, 30, 45)


@pytest.mark.parametrize("attack", [
    "../../evil", "..\\..\\Windows\\evil", "C:\\x\\y", "a/b", "a:b*c?d\"e<f>g|h",
    "CON", "nul", "COM1", "  ...  ", "", "   ", "a\x00b\nc\td", "\u202etxt.exe",
    "雞腿便當" * 50, "..", "....",
])
def test_sanitize_never_leaves_path_characters(attack):
    out = sanitize_filename_part(attack)
    assert out
    assert not any(c in out for c in '/\\:*?"<>|')
    assert ".." not in out and out == out.strip(" .") and len(out) <= 20
    assert out.upper() not in {"CON", "NUL", "PRN", "AUX", "COM1"}
    assert all(ord(c) >= 32 for c in out)


def test_normal_chinese_name_untouched():
    assert sanitize_filename_part("雞腿便當") == "雞腿便當"


def test_save_photo_filename_format(tmp_path):
    p = save_photo(tmp_path / "photos", WHEN, "雞腿便當", JPG)
    assert p.name == "2026-10-20 12-30-45 雞腿便當.jpg"
    assert p.read_bytes() == JPG


def test_same_second_collision_gets_suffix(tmp_path):
    d = tmp_path / "photos"
    names = [save_photo(d, WHEN, "便當", JPG).name for _ in range(3)]
    assert names == [
        "2026-10-20 12-30-45 便當.jpg",
        "2026-10-20 12-30-45 便當-2.jpg",
        "2026-10-20 12-30-45 便當-3.jpg",
    ]


def test_real_extension_kept_for_png(tmp_path):
    assert save_photo(tmp_path / "p", WHEN, "杯", PNG).suffix == ".png"


def test_path_traversal_name_stays_inside_photos(tmp_path):
    d = tmp_path / "photos"
    p = save_photo(d, WHEN, "../../../evil", JPG)
    assert p.resolve().is_relative_to(d.resolve())
    assert [x.name for x in tmp_path.iterdir()] == ["photos"]  # 外面沒多出任何檔案


def test_detect_suffix_uses_content_not_name():
    assert detect_image_suffix(JPG) == ".jpg"
    assert detect_image_suffix(PNG) == ".png"
    assert detect_image_suffix(b"MZ\x90\x00 not an image") is None
    assert detect_image_suffix(b"") is None
