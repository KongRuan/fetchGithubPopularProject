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


@pytest.mark.parametrize(
    "body, key",
    [
        ('period_days: "abc"\n', "period_days"),
        ("period_days: null\n", "period_days"),
        ("period_days: true\n", "period_days"),
        ("period_days: 1.5\n", "period_days"),
        ('top_n: "abc"\n', "top_n"),
        ("top_n: null\n", "top_n"),
        ("top_n: -1\n", "top_n"),
        ("top_n: 1.5\n", "top_n"),
    ],
)
def test_bad_numeric_values_raise_config_error(tmp_path, body, key):
    """No bad input may leak ValueError/TypeError instead of ConfigError."""
    (tmp_path / "config.yaml").write_text(body, encoding="utf-8")
    with pytest.raises(ConfigError) as e:
        load_config(tmp_path / "config.yaml")
    assert key in str(e.value)


@pytest.mark.parametrize("body", ["0\n", "false\n"])
def test_falsy_non_mapping_root_is_rejected(tmp_path, body):
    """YAML `0`/`false` are scalars, not an empty config."""
    (tmp_path / "config.yaml").write_text(body, encoding="utf-8")
    with pytest.raises(ConfigError) as e:
        load_config(tmp_path / "config.yaml")
    assert "must be a mapping" in str(e.value)


def test_empty_and_comment_only_config_uses_defaults(tmp_path):
    (tmp_path / "config.yaml").write_text("", encoding="utf-8")
    assert load_config(tmp_path / "config.yaml").period_days == 30
    (tmp_path / "config.yaml").write_text("# just a comment\n", encoding="utf-8")
    assert load_config(tmp_path / "config.yaml").period_days == 30


def test_null_output_dir_falls_back_to_default(tmp_path):
    """Consistent with `github_token`: explicit null means "use the default"."""
    (tmp_path / "config.yaml").write_text("output_dir: null\n", encoding="utf-8")
    assert load_config(tmp_path / "config.yaml").output_dir == "output"


@pytest.mark.parametrize("body", ["output_dir: 123\n", 'output_dir: ""\n'])
def test_bad_output_dir_raises_config_error(tmp_path, body):
    """`123` must not stringify to a bogus directory name."""
    (tmp_path / "config.yaml").write_text(body, encoding="utf-8")
    with pytest.raises(ConfigError) as e:
        load_config(tmp_path / "config.yaml")
    assert "output_dir" in str(e.value)


def test_language_slug_slashes_are_preserved(tmp_path):
    """Trending slugs like `c%23`/`objective-c` must survive verbatim."""
    (tmp_path / "config.yaml").write_text(
        'languages: ["c%23", "f%23", "c++", "objective-c"]\n', encoding="utf-8")
    assert load_config(tmp_path / "config.yaml").languages == (
        "c%23", "f%23", "c++", "objective-c")


def test_shipped_config_loads_and_has_33_languages():
    """The real config.yaml must pass its own validator."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    s = load_config(root / "config.yaml")
    assert len(s.languages) == 33
    assert s.period_days == 30
    assert s.top_n == 20
    assert s.output_path == root / "output"
    assert s.cache_dir == root / "data" / "cache"
    assert s.results_dir == root / "data" / "results"
