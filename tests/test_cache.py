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
