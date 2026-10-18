import pytest

from src.rank.config import ConfigError, DEFAULT_LANGUAGES, Settings, load_config


def test_defaults_from_written_config(tmp_path):
    (tmp_path / "config.yaml").write_text(
        "period_days: 30\n"
        "top_n: 20\n"
        "languages: [python, rust]\n"
        'github_token: ""\n'
        "enable_ossinsight_estimate: true\n"
        "output_dir: output\n",
        encoding="utf-8",
    )
    s = load_config(tmp_path / "config.yaml")
    assert s.period_days == 30
    assert s.top_n == 20
    assert s.languages == ("python", "rust")
    assert s.github_token == ""
    assert s.enable_ossinsight_estimate is True
    assert s.trending_period == "monthly"


def test_rejects_unknown_key(tmp_path):
    (tmp_path / "config.yaml").write_text("top_n: 5\nbogus_key: 1\n", encoding="utf-8")
    with pytest.raises(ConfigError) as e:
        load_config(tmp_path / "config.yaml")
    assert "bogus_key" in str(e.value)


def test_rejects_unsupported_period(tmp_path):
    (tmp_path / "config.yaml").write_text("period_days: 45\n", encoding="utf-8")
    with pytest.raises(ConfigError) as e:
        load_config(tmp_path / "config.yaml")
    assert "period_days" in str(e.value)


def test_rejects_bad_top_n(tmp_path):
    (tmp_path / "config.yaml").write_text("top_n: 0\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(tmp_path / "config.yaml")


def test_settings_exposes_default_languages():
    assert len(DEFAULT_LANGUAGES) == 33
    assert "python" in DEFAULT_LANGUAGES
