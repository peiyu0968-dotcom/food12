import pytest

from food_agent.config import ConfigError, load_config

GOOD = "DISCORD_TOKEN=abc\nCHANNEL_ID=123\nCLAUDE_MODEL=haiku\nDATA_DIR=./data\n"


def write_env(tmp_path, text):
    p = tmp_path / ".env"
    p.write_text(text, encoding="utf-8")
    return p


def test_loads_good_env(tmp_path):
    cfg = load_config(write_env(tmp_path, GOOD))
    assert cfg.channel_id == 123 and cfg.claude_model == "haiku"
    assert cfg.data_dir == (tmp_path / "data").resolve()


def test_missing_key(tmp_path):
    with pytest.raises(ConfigError, match="CHANNEL_ID"):
        load_config(write_env(tmp_path, "DISCORD_TOKEN=a\nCLAUDE_MODEL=h\nDATA_DIR=./d\n"))


def test_placeholder_rejected(tmp_path):
    with pytest.raises(ConfigError, match="範例"):
        load_config(write_env(tmp_path, GOOD.replace("abc", "你的bot_token")))


def test_channel_must_be_number(tmp_path):
    with pytest.raises(ConfigError, match="數字"):
        load_config(write_env(tmp_path, GOOD.replace("123", "abc")))


def test_no_env_file(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / ".env")
