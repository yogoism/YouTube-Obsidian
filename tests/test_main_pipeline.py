import pathlib
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    # main.py import時に必要な環境変数をセットし、副作用パスを隔離
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("OUTPUT_DIR_YT", str(tmp_path / "yt"))
    monkeypatch.setenv("OUTPUT_DIR_POD", str(tmp_path / "pod"))
    yield


def test_sanitize_filename_removes_invalid():
    import main

    assert main.sanitize_filename('inva*lid:"name?') == "invalidname"


def test_sanitize_filename_removes_path_traversal():
    """../を含むファイル名がディレクトリトラバーサルしないこと"""
    import main

    assert "/" not in main.sanitize_filename("../../../etc/passwd")
    assert ".." not in main.sanitize_filename("../../../etc/passwd")
    # 結果が安全なファイル名であること
    result = main.sanitize_filename("..%2F..%2Fetc/passwd")
    assert ".." not in result


def test_sanitize_filename_dotdot_only():
    """ファイル名が .. のみの場合も安全であること"""
    import main

    result = main.sanitize_filename("..")
    assert result != ".."
    assert ".." not in result


def test_notify_escapes_double_quotes(monkeypatch):
    """notify() が " を含むメッセージで AppleScript インジェクションしないこと"""
    import importlib

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    import main

    main = importlib.reload(main)

    # pync を無効化して osascript パスに入る
    monkeypatch.setattr(main, "_USE_PYNC", False)

    captured = {}

    def fake_run(args):
        captured["args"] = args

    monkeypatch.setattr(main.subprocess, "run", fake_run)

    # ダブルクォートを含むメッセージ
    main.notify('test" & do shell script "curl evil.com', title="My Title")

    # osascript に渡される文字列にエスケープされていない " が含まれないこと
    script = captured["args"][2]
    # display notification と with title の中身だけにクォートがあるべき
    # エスケープされた \" は安全
    # 未エスケープの " が display notification "..." with title "..." 以外に無いことを確認
    assert '" & do shell script "' not in script


def test_notify_escapes_backslash_in_title(monkeypatch):
    """notify() が title に " を含む場合も安全であること"""
    import importlib

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    import main

    main = importlib.reload(main)

    monkeypatch.setattr(main, "_USE_PYNC", False)

    captured = {}

    def fake_run(args):
        captured["args"] = args

    monkeypatch.setattr(main.subprocess, "run", fake_run)

    main.notify("hello", title='Evil"Title')

    script = captured["args"][2]
    # title 内の " がエスケープされていること
    assert 'Evil"Title' not in script


def test_crawl_skips_non_http_feed_url(monkeypatch, tmp_path):
    """file:// スキームの URL がスキップされること"""
    import importlib

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("OUTPUT_DIR_YT", str(tmp_path / "yt"))
    monkeypatch.setenv("OUTPUT_DIR_POD", str(tmp_path / "pod"))

    import main

    main = importlib.reload(main)

    # feedparser.parse が呼ばれた URL を記録
    parse_calls = []

    def fake_parse(url):
        parse_calls.append(url)
        return type("P", (), {"entries": []})()

    monkeypatch.setattr(main.feedparser, "parse", fake_parse)

    # file:// URL を含む feeds.yaml を用意
    feeds_content = '- "file:///etc/passwd"\n- "https://example.com/feed"\n'
    monkeypatch.setattr(
        main.yaml, "safe_load", lambda _text: ["file:///etc/passwd", "https://example.com/feed"]
    )
    monkeypatch.setattr(main.pathlib.Path, "read_text", lambda self: feeds_content)

    main.crawl()

    # file:// URL は feedparser.parse に渡されないこと
    for url in parse_calls:
        assert not url.startswith("file://"), f"file:// URL がパースされた: {url}"
    # https URL はパースされていること
    assert any(url.startswith("https://") for url in parse_calls)


def test_yt_is_video_filters_shorts_and_live():
    import main

    assert main.yt_is_video({"duration": 10, "width": 1920, "height": 1080}) is False
    assert (
        main.yt_is_video({"duration": 120, "width": 720, "height": 1280}) is False
    )  # 縦長も除外
    assert main.yt_is_video({"duration": 120, "is_live": True}) is False
    assert main.yt_is_video({"duration": 120, "width": 1920, "height": 1080}) is True


def test_process_youtube_writes_markdown(monkeypatch, tmp_path):
    import importlib

    # このテスト用に出力先を固定し直してから再読み込み
    monkeypatch.setenv("OUTPUT_DIR_YT", str(tmp_path / "yt"))
    monkeypatch.setenv("OUTPUT_DIR_POD", str(tmp_path / "pod"))

    import main

    main = importlib.reload(main)

    # ダミーの外部依存を差し替え
    monkeypatch.setattr(main, "yt_meta", lambda url: {"duration": 120})
    monkeypatch.setattr(main, "notify", lambda *_a, **_k: None)

    def fake_run(args, check, timeout):
        out_path = pathlib.Path(args[5])
        out_path.write_bytes(b"mp3data")

    monkeypatch.setattr(main.subprocess, "run", fake_run)
    class DummyClient:
        def summarize_audio(self, _b, _p):
            return "result"

    monkeypatch.setattr(main, "get_gemini_client", lambda: DummyClient())

    entry = SimpleNamespace(
        yt_videoid="vid123",
        title="Test Video",
        pub_dash="2025-12-17",
        pub_slash="2025/12/17",
        author="Author",
        link="https://youtu.be/vid123",
    )

    main.process_youtube(entry)

    out_files = list((tmp_path / "yt").glob("*.md"))
    assert len(out_files) == 1
    assert out_files[0].read_text(encoding="utf-8") == "result"


def test_process_youtube_sets_timeout(monkeypatch, tmp_path):
    import importlib

    monkeypatch.setenv("OUTPUT_DIR_YT", str(tmp_path / "yt"))
    monkeypatch.setenv("OUTPUT_DIR_POD", str(tmp_path / "pod"))
    import main

    main = importlib.reload(main)

    monkeypatch.setattr(main, "yt_meta", lambda url: {"duration": 120})
    monkeypatch.setattr(main, "notify", lambda *_a, **_k: None)

    captured = {}

    def fake_run(args, check, timeout):
        captured["timeout"] = timeout
        out_path = pathlib.Path(args[5])
        out_path.write_bytes(b"mp3data")

    monkeypatch.setattr(main.subprocess, "run", fake_run)
    class DummyClient:
        def summarize_audio(self, _b, _p):
            return "result"

    monkeypatch.setattr(main, "get_gemini_client", lambda: DummyClient())

    entry = SimpleNamespace(
        yt_videoid="vid123",
        title="Test Video",
        pub_dash="2025-12-17",
        pub_slash="2025/12/17",
        author="Author",
        link="https://youtu.be/vid123",
    )

    main.process_youtube(entry)
    assert captured["timeout"] > 0


def test_process_podcast_writes_markdown(monkeypatch, tmp_path):
    import importlib

    monkeypatch.setenv("OUTPUT_DIR_YT", str(tmp_path / "yt"))
    monkeypatch.setenv("OUTPUT_DIR_POD", str(tmp_path / "pod"))
    import main

    main = importlib.reload(main)

    monkeypatch.setattr(main, "notify", lambda *_a, **_k: None)

    class DummyResp:
        def __init__(self):
            self.content = b"mp3bytes"

        def raise_for_status(self): ...

    monkeypatch.setattr(main.requests, "get", lambda url, timeout: DummyResp())

    class DummyClient:
        def summarize_audio(self, _b, _p):
            return "result-pod"

    monkeypatch.setattr(main, "get_gemini_client", lambda: DummyClient())

    entry = SimpleNamespace(
        title="Podcast Ep",
        pub_dash="2025-12-17",
        pub_slash="2025/12/17",
        author="Host",
        links=[{"rel": "enclosure", "href": "http://example.com/audio.mp3"}],
    )

    main.process_podcast(entry)
    out_files = list((tmp_path / "pod").glob("*.md"))
    assert len(out_files) == 1
    assert out_files[0].read_text(encoding="utf-8") == "result-pod"


def test_process_youtube_skips_when_no_summary(monkeypatch, tmp_path):
    import importlib

    monkeypatch.setenv("OUTPUT_DIR_YT", str(tmp_path / "yt"))
    monkeypatch.setenv("OUTPUT_DIR_POD", str(tmp_path / "pod"))

    import main

    main = importlib.reload(main)

    monkeypatch.setattr(main, "yt_meta", lambda url: {"duration": 120})
    monkeypatch.setattr(main, "notify", lambda *_a, **_k: None)

    def fake_run(args, check, timeout):
        out_path = pathlib.Path(args[5])
        out_path.write_bytes(b"mp3data")

    monkeypatch.setattr(main.subprocess, "run", fake_run)

    class DummyClient:
        def summarize_audio(self, _b, _p):
            return None

    monkeypatch.setattr(main, "get_gemini_client", lambda: DummyClient())

    entry = SimpleNamespace(
        yt_videoid="vid123",
        title="Test Video",
        pub_dash="2025-12-17",
        pub_slash="2025/12/17",
        author="Author",
        link="https://youtu.be/vid123",
    )

    main.process_youtube(entry)

    out_files = list((tmp_path / "yt").glob("*.md"))
    assert out_files == []


def test_process_podcast_skips_when_no_summary(monkeypatch, tmp_path):
    import importlib

    monkeypatch.setenv("OUTPUT_DIR_YT", str(tmp_path / "yt"))
    monkeypatch.setenv("OUTPUT_DIR_POD", str(tmp_path / "pod"))

    import main

    main = importlib.reload(main)

    monkeypatch.setattr(main, "notify", lambda *_a, **_k: None)

    class DummyResp:
        def __init__(self):
            self.content = b"mp3bytes"

        def raise_for_status(self): ...

    monkeypatch.setattr(main.requests, "get", lambda url, timeout: DummyResp())

    class DummyClient:
        def summarize_audio(self, _b, _p):
            return None

    monkeypatch.setattr(main, "get_gemini_client", lambda: DummyClient())

    entry = SimpleNamespace(
        title="Podcast Ep",
        pub_dash="2025-12-17",
        pub_slash="2025/12/17",
        author="Host",
        links=[{"rel": "enclosure", "href": "http://example.com/audio.mp3"}],
    )

    main.process_podcast(entry)
    out_files = list((tmp_path / "pod").glob("*.md"))
    assert out_files == []


def test_process_youtube_skips_on_download_failure(monkeypatch, tmp_path):
    """yt-dlp ダウンロード失敗時にクラッシュせずスキップすること"""
    import importlib

    monkeypatch.setenv("OUTPUT_DIR_YT", str(tmp_path / "yt"))
    monkeypatch.setenv("OUTPUT_DIR_POD", str(tmp_path / "pod"))

    import main

    main = importlib.reload(main)

    monkeypatch.setattr(main, "yt_meta", lambda url: {"duration": 120})
    monkeypatch.setattr(main, "notify", lambda *_a, **_k: None)

    def fake_run_fail(args, check, timeout):
        raise main.subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(main.subprocess, "run", fake_run_fail)

    entry = SimpleNamespace(
        yt_videoid="vid_fail",
        title="Failing Video",
        pub_dash="2025-12-17",
        pub_slash="2025/12/17",
        author="Author",
        link="https://youtu.be/vid_fail",
    )

    # クラッシュせず正常に return すること
    main.process_youtube(entry)

    # Markdown が出力されていないこと
    out_files = list((tmp_path / "yt").glob("*.md"))
    assert out_files == []


def test_process_youtube_skips_on_timeout(monkeypatch, tmp_path):
    """yt-dlp タイムアウト時にクラッシュせずスキップすること"""
    import importlib

    monkeypatch.setenv("OUTPUT_DIR_YT", str(tmp_path / "yt"))
    monkeypatch.setenv("OUTPUT_DIR_POD", str(tmp_path / "pod"))

    import main

    main = importlib.reload(main)

    monkeypatch.setattr(main, "yt_meta", lambda url: {"duration": 120})
    monkeypatch.setattr(main, "notify", lambda *_a, **_k: None)

    def fake_run_timeout(args, check, timeout):
        raise main.subprocess.TimeoutExpired(args, timeout)

    monkeypatch.setattr(main.subprocess, "run", fake_run_timeout)

    entry = SimpleNamespace(
        yt_videoid="vid_timeout",
        title="Timeout Video",
        pub_dash="2025-12-17",
        pub_slash="2025/12/17",
        author="Author",
        link="https://youtu.be/vid_timeout",
    )

    # クラッシュせず正常に return すること
    main.process_youtube(entry)

    # Markdown が出力されていないこと
    out_files = list((tmp_path / "yt").glob("*.md"))
    assert out_files == []


def test_fetch_enclosure_uses_config_timeout(monkeypatch, tmp_path):
    import importlib

    monkeypatch.setenv("OUTPUT_DIR_YT", str(tmp_path / "yt"))
    monkeypatch.setenv("OUTPUT_DIR_POD", str(tmp_path / "pod"))
    monkeypatch.setenv("POD_TIMEOUT", "5")

    import main

    main = importlib.reload(main)

    called = {}

    class DummyResp:
        content = b"ok"

        def raise_for_status(self): ...

    def fake_get(url, timeout):
        called["timeout"] = timeout
        return DummyResp()

    monkeypatch.setattr(main.requests, "get", fake_get)
    entry = SimpleNamespace(links=[{"rel": "enclosure", "href": "http://example.com"}])

    dest = tmp_path / "f.mp3"
    main.fetch_enclosure(entry, dest)
    assert called["timeout"] == 5
    assert dest.read_bytes() == b"ok"


def test_fetch_enclosure_retries(monkeypatch, tmp_path):
    import importlib

    monkeypatch.setenv("OUTPUT_DIR_YT", str(tmp_path / "yt"))
    monkeypatch.setenv("OUTPUT_DIR_POD", str(tmp_path / "pod"))
    monkeypatch.setenv("POD_RETRIES", "3")

    import main

    main = importlib.reload(main)

    attempts = []

    class DummyResp:
        content = b"ok"

        def raise_for_status(self): ...

    def fake_get(url, timeout):
        attempts.append(1)
        if len(attempts) < 3:
            raise main.requests.exceptions.Timeout()
        return DummyResp()

    monkeypatch.setattr(main.requests, "get", fake_get)
    monkeypatch.setattr(main.time, "sleep", lambda s: None)

    entry = SimpleNamespace(links=[{"rel": "enclosure", "href": "http://example.com"}])
    dest = tmp_path / "f.mp3"

    main.fetch_enclosure(entry, dest)
    assert len(attempts) == 3
    assert dest.read_bytes() == b"ok"
