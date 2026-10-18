from src.rank.cache import Cache


def test_write_and_read_text(tmp_path):
    cache = Cache(tmp_path)
    cache.write_text("pages/a.html", "<html>héllo</html>")
    assert cache.exists("pages/a.html")
    assert cache.read_text("pages/a.html") == "<html>héllo</html>"


def test_json_roundtrip(tmp_path):
    cache = Cache(tmp_path)
    cache.write_json("est/x.json", {"gain": 12})
    assert cache.read_json("est/x.json") == {"gain": 12}


def test_missing_json_returns_none(tmp_path):
    assert Cache(tmp_path).read_json("nope.json") is None


def test_corrupt_json_returns_none(tmp_path):
    cache = Cache(tmp_path)
    cache.write_text("bad.json", "{not json")
    assert cache.read_json("bad.json") is None


def test_key_can_contain_directories(tmp_path):
    cache = Cache(tmp_path)
    cache.write_text("pages/deep/nested.html", "x")
    assert (tmp_path / "pages" / "deep" / "nested.html").is_file()


def test_read_json_returns_none_when_truncated_mid_multibyte(tmp_path):
    """A crashed write can cut a CJK char in half; that means re-fetch."""
    cache = Cache(tmp_path)
    (tmp_path / "trunc.json").write_bytes(b'{"\xe8\xb6\x8b\xe5\x8a\xbf": "\xe8')
    assert cache.read_json("trunc.json") is None


def test_exists_does_not_create_directories(tmp_path):
    """Probing for a missing entry must be a pure read."""
    assert Cache(tmp_path).exists("nope/x.json") is False
    assert not (tmp_path / "nope").exists()


def test_exists_returns_false_when_parent_component_is_a_file(tmp_path):
    cache = Cache(tmp_path)
    cache.write_text("file.txt", "x")
    assert cache.exists("file.txt/x.html") is False
