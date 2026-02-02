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
    monkeypatch.setattr(main.time, "sleep", lambda s: None)

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
    monkeypatch.setattr(main.time, "sleep", lambda s: None)

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


# ========== Step 1: yt_meta() 失敗時のログ ==========
def test_process_youtube_logs_vid_on_meta_failure(monkeypatch, tmp_path, capsys):
    """yt_meta() が None を返した時にビデオIDがログに出力されること"""
    import importlib

    monkeypatch.setenv("OUTPUT_DIR_YT", str(tmp_path / "yt"))
    monkeypatch.setenv("OUTPUT_DIR_POD", str(tmp_path / "pod"))

    import main

    main = importlib.reload(main)

    monkeypatch.setattr(main, "yt_meta", lambda url: None)
    monkeypatch.setattr(main, "notify", lambda *_a, **_k: None)

    entry = SimpleNamespace(
        yt_videoid="vid_meta_fail",
        title="Meta Fail Video",
        pub_dash="2025-12-17",
        pub_slash="2025/12/17",
        author="Author",
        link="https://youtu.be/vid_meta_fail",
    )

    main.process_youtube(entry)

    captured = capsys.readouterr()
    assert "vid_meta_fail" in captured.out


# ========== Step 2: Gemini 要約失敗時の通知 ==========
def test_process_youtube_notifies_on_no_summary(monkeypatch, tmp_path):
    """Gemini 要約が None の時に notify() が呼ばれエントリタイトルを含むこと"""
    import importlib

    monkeypatch.setenv("OUTPUT_DIR_YT", str(tmp_path / "yt"))
    monkeypatch.setenv("OUTPUT_DIR_POD", str(tmp_path / "pod"))

    import main

    main = importlib.reload(main)

    monkeypatch.setattr(main, "yt_meta", lambda url: {"duration": 120})
    monkeypatch.setattr(main.time, "sleep", lambda s: None)

    notify_calls = []
    monkeypatch.setattr(main, "notify", lambda msg, **kw: notify_calls.append(msg))

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

    assert any("Test Video" in msg for msg in notify_calls)


def test_process_podcast_notifies_on_no_summary(monkeypatch, tmp_path):
    """Podcast で Gemini 要約が None の時に notify() が呼ばれエントリタイトルを含むこと"""
    import importlib

    monkeypatch.setenv("OUTPUT_DIR_YT", str(tmp_path / "yt"))
    monkeypatch.setenv("OUTPUT_DIR_POD", str(tmp_path / "pod"))

    import main

    main = importlib.reload(main)

    notify_calls = []
    monkeypatch.setattr(main, "notify", lambda msg, **kw: notify_calls.append(msg))

    class DummyResp:
        content = b"mp3bytes"

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

    assert any("Podcast Ep" in msg for msg in notify_calls)


# ========== Step 3: フィードパース失敗の検知 ==========
def test_crawl_logs_on_http_error(monkeypatch, tmp_path, capsys):
    """フィードの HTTP ステータスが 400 以上の場合にログ出力してスキップすること"""
    import importlib

    monkeypatch.setenv("OUTPUT_DIR_YT", str(tmp_path / "yt"))
    monkeypatch.setenv("OUTPUT_DIR_POD", str(tmp_path / "pod"))

    import main

    main = importlib.reload(main)

    monkeypatch.setattr(main.yaml, "safe_load", lambda _: ["https://example.com/feed"])
    monkeypatch.setattr(main.pathlib.Path, "read_text", lambda self: "")
    monkeypatch.setattr(main, "notify", lambda *_a, **_k: None)

    class FakeParsed:
        status = 404
        bozo = False
        entries = []

    monkeypatch.setattr(main.feedparser, "parse", lambda url: FakeParsed())

    main.crawl()

    captured = capsys.readouterr()
    assert "404" in captured.out


def test_crawl_warns_on_bozo_feed(monkeypatch, tmp_path, capsys):
    """bozo フラグが立っているフィードで警告ログが出つつ処理は継続すること"""
    import importlib

    monkeypatch.setenv("OUTPUT_DIR_YT", str(tmp_path / "yt"))
    monkeypatch.setenv("OUTPUT_DIR_POD", str(tmp_path / "pod"))

    import main

    main = importlib.reload(main)

    monkeypatch.setattr(main.yaml, "safe_load", lambda _: ["https://example.com/feed"])
    monkeypatch.setattr(main.pathlib.Path, "read_text", lambda self: "")
    monkeypatch.setattr(main, "notify", lambda *_a, **_k: None)

    class FakeParsed:
        status = 200
        bozo = True
        bozo_exception = "XML error"
        entries = []

    monkeypatch.setattr(main.feedparser, "parse", lambda url: FakeParsed())

    main.crawl()

    captured = capsys.readouterr()
    assert "bozo" in captured.out.lower() or "警告" in captured.out


# ========== Step 4: fetch_enclosure の例外処理 ==========
def test_process_podcast_skips_on_fetch_error(monkeypatch, tmp_path, capsys):
    """fetch_enclosure() が例外を投げた時にクラッシュせずスキップすること"""
    import importlib

    monkeypatch.setenv("OUTPUT_DIR_YT", str(tmp_path / "yt"))
    monkeypatch.setenv("OUTPUT_DIR_POD", str(tmp_path / "pod"))

    import main

    main = importlib.reload(main)

    monkeypatch.setattr(main, "notify", lambda *_a, **_k: None)

    def raise_error(entry, dest):
        raise ConnectionError("connection failed")

    monkeypatch.setattr(main, "fetch_enclosure", raise_error)

    entry = SimpleNamespace(
        title="Podcast Ep",
        pub_dash="2025-12-17",
        pub_slash="2025/12/17",
        author="Host",
        links=[{"rel": "enclosure", "href": "http://example.com/audio.mp3"}],
    )

    # クラッシュせず正常に return すること
    result = main.process_podcast(entry)

    out_files = list((tmp_path / "pod").glob("*.md"))
    assert out_files == []
    assert result is False


# ========== Step 5: yt-dlp ダウンロードのリトライ ==========
def test_process_youtube_retries_download(monkeypatch, tmp_path):
    """yt-dlp ダウンロードが3回目で成功するケース"""
    import importlib

    monkeypatch.setenv("OUTPUT_DIR_YT", str(tmp_path / "yt"))
    monkeypatch.setenv("OUTPUT_DIR_POD", str(tmp_path / "pod"))
    monkeypatch.setenv("YTDLP_RETRIES", "3")

    import main

    main = importlib.reload(main)

    monkeypatch.setattr(main, "yt_meta", lambda url: {"duration": 120})
    monkeypatch.setattr(main, "notify", lambda *_a, **_k: None)
    monkeypatch.setattr(main.time, "sleep", lambda s: None)

    attempts = []

    def fake_run(args, check, timeout):
        attempts.append(1)
        if len(attempts) < 3:
            raise main.subprocess.CalledProcessError(1, args)
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

    assert len(attempts) == 3
    out_files = list((tmp_path / "yt").glob("*.md"))
    assert len(out_files) == 1


def test_process_youtube_skips_after_all_retries_fail(monkeypatch, tmp_path):
    """全リトライ失敗でスキップすること"""
    import importlib

    monkeypatch.setenv("OUTPUT_DIR_YT", str(tmp_path / "yt"))
    monkeypatch.setenv("OUTPUT_DIR_POD", str(tmp_path / "pod"))
    monkeypatch.setenv("YTDLP_RETRIES", "3")

    import main

    main = importlib.reload(main)

    monkeypatch.setattr(main, "yt_meta", lambda url: {"duration": 120})
    monkeypatch.setattr(main, "notify", lambda *_a, **_k: None)
    monkeypatch.setattr(main.time, "sleep", lambda s: None)

    attempts = []

    def fake_run(args, check, timeout):
        attempts.append(1)
        raise main.subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(main.subprocess, "run", fake_run)

    entry = SimpleNamespace(
        yt_videoid="vid_fail",
        title="Failing Video",
        pub_dash="2025-12-17",
        pub_slash="2025/12/17",
        author="Author",
        link="https://youtu.be/vid_fail",
    )

    main.process_youtube(entry)

    assert len(attempts) == 3
    out_files = list((tmp_path / "yt").glob("*.md"))
    assert out_files == []


def test_process_youtube_retry_backoff_timing(monkeypatch, tmp_path):
    """リトライのバックオフ待ち時間が 2^(attempt-1) 秒であること"""
    import importlib

    monkeypatch.setenv("OUTPUT_DIR_YT", str(tmp_path / "yt"))
    monkeypatch.setenv("OUTPUT_DIR_POD", str(tmp_path / "pod"))
    monkeypatch.setenv("YTDLP_RETRIES", "3")

    import main

    main = importlib.reload(main)

    monkeypatch.setattr(main, "yt_meta", lambda url: {"duration": 120})
    monkeypatch.setattr(main, "notify", lambda *_a, **_k: None)

    sleep_calls = []
    monkeypatch.setattr(main.time, "sleep", lambda s: sleep_calls.append(s))

    def fake_run(args, check, timeout):
        raise main.subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(main.subprocess, "run", fake_run)

    entry = SimpleNamespace(
        yt_videoid="vid_fail",
        title="Failing Video",
        pub_dash="2025-12-17",
        pub_slash="2025/12/17",
        author="Author",
        link="https://youtu.be/vid_fail",
    )

    main.process_youtube(entry)

    # 3回リトライ: attempt 1 失敗→sleep(1), attempt 2 失敗→sleep(2), attempt 3 失敗→sleep なし
    assert sleep_calls == [1, 2]


# ========== Step 6: 処理結果サマリー ==========
def test_process_youtube_returns_true_on_success(monkeypatch, tmp_path):
    """process_youtube() が成功時に True を返すこと"""
    import importlib

    monkeypatch.setenv("OUTPUT_DIR_YT", str(tmp_path / "yt"))
    monkeypatch.setenv("OUTPUT_DIR_POD", str(tmp_path / "pod"))

    import main

    main = importlib.reload(main)

    monkeypatch.setattr(main, "yt_meta", lambda url: {"duration": 120})
    monkeypatch.setattr(main, "notify", lambda *_a, **_k: None)
    monkeypatch.setattr(main.time, "sleep", lambda s: None)

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

    result = main.process_youtube(entry)
    assert result is True


def test_process_youtube_returns_false_on_failure(monkeypatch, tmp_path):
    """process_youtube() が失敗時に False を返すこと"""
    import importlib

    monkeypatch.setenv("OUTPUT_DIR_YT", str(tmp_path / "yt"))
    monkeypatch.setenv("OUTPUT_DIR_POD", str(tmp_path / "pod"))

    import main

    main = importlib.reload(main)

    monkeypatch.setattr(main, "yt_meta", lambda url: None)
    monkeypatch.setattr(main, "notify", lambda *_a, **_k: None)

    entry = SimpleNamespace(
        yt_videoid="vid_fail",
        title="Fail Video",
        pub_dash="2025-12-17",
        pub_slash="2025/12/17",
        author="Author",
        link="https://youtu.be/vid_fail",
    )

    result = main.process_youtube(entry)
    assert result is False


def test_process_podcast_returns_true_on_success(monkeypatch, tmp_path):
    """process_podcast() が成功時に True を返すこと"""
    import importlib

    monkeypatch.setenv("OUTPUT_DIR_YT", str(tmp_path / "yt"))
    monkeypatch.setenv("OUTPUT_DIR_POD", str(tmp_path / "pod"))

    import main

    main = importlib.reload(main)

    monkeypatch.setattr(main, "notify", lambda *_a, **_k: None)

    class DummyResp:
        content = b"mp3bytes"

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

    result = main.process_podcast(entry)
    assert result is True


def test_crawl_prints_summary(monkeypatch, tmp_path, capsys):
    """crawl() 終了時に処理件数サマリーが出力・通知されること"""
    import importlib
    import time as _time

    monkeypatch.setenv("OUTPUT_DIR_YT", str(tmp_path / "yt"))
    monkeypatch.setenv("OUTPUT_DIR_POD", str(tmp_path / "pod"))

    import main

    main = importlib.reload(main)

    monkeypatch.setattr(main.yaml, "safe_load", lambda _: ["https://example.com/feed"])
    monkeypatch.setattr(main.pathlib.Path, "read_text", lambda self: "")
    monkeypatch.setattr(main.time, "sleep", lambda s: None)

    notify_calls = []
    monkeypatch.setattr(main, "notify", lambda msg, **kw: notify_calls.append(msg))

    # process_youtube が True を返すようにモック
    monkeypatch.setattr(main, "process_youtube", lambda e: True)

    # 有効なタイムスタンプ（直近1時間以内）を持つ YouTube エントリ
    recent_ts = _time.gmtime(_time.time() - 3600)

    yt_entry = SimpleNamespace(
        yt_videoid="vid123",
        title="Test Video",
        published_parsed=recent_ts,
        updated_parsed=None,
        author="Author",
        link="https://youtu.be/vid123",
    )

    class FakeParsed:
        status = 200
        bozo = False
        entries = [yt_entry]

    monkeypatch.setattr(main.feedparser, "parse", lambda url: FakeParsed())

    main.crawl()

    captured = capsys.readouterr()
    # サマリーに成功件数が含まれること
    assert "1" in captured.out
    assert "成功" in captured.out or "ok" in captured.out.lower()
    # notify にもサマリーが送られること
    assert any("1" in msg for msg in notify_calls)


# ========== RETRIES=0 クランプ ==========
def test_ytdlp_retries_zero_clamps_to_one(monkeypatch, tmp_path):
    """YTDLP_RETRIES=0 でも yt-dlp が1回実行され正常に処理されること"""
    import importlib

    monkeypatch.setenv("OUTPUT_DIR_YT", str(tmp_path / "yt"))
    monkeypatch.setenv("OUTPUT_DIR_POD", str(tmp_path / "pod"))
    monkeypatch.setenv("YTDLP_RETRIES", "0")

    import main

    main = importlib.reload(main)

    monkeypatch.setattr(main, "yt_meta", lambda url: {"duration": 120})
    monkeypatch.setattr(main, "notify", lambda *_a, **_k: None)
    monkeypatch.setattr(main.time, "sleep", lambda s: None)

    attempts = []

    def fake_run(args, check, timeout):
        attempts.append(1)
        out_path = pathlib.Path(args[5])
        out_path.write_bytes(b"mp3data")

    monkeypatch.setattr(main.subprocess, "run", fake_run)

    class DummyClient:
        def summarize_audio(self, _b, _p):
            return "result"

    monkeypatch.setattr(main, "get_gemini_client", lambda: DummyClient())

    entry = SimpleNamespace(
        yt_videoid="vid_zero",
        title="Zero Retry Video",
        pub_dash="2025-12-17",
        pub_slash="2025/12/17",
        author="Author",
        link="https://youtu.be/vid_zero",
    )

    result = main.process_youtube(entry)

    # 最低1回は実行されること
    assert len(attempts) == 1
    assert result is True
    out_files = list((tmp_path / "yt").glob("*.md"))
    assert len(out_files) == 1


def test_pod_retries_zero_clamps_to_one(monkeypatch, tmp_path):
    """POD_RETRIES=0 でも requests.get が1回実行され正常に処理されること"""
    import importlib

    monkeypatch.setenv("OUTPUT_DIR_YT", str(tmp_path / "yt"))
    monkeypatch.setenv("OUTPUT_DIR_POD", str(tmp_path / "pod"))
    monkeypatch.setenv("POD_RETRIES", "0")

    import main

    main = importlib.reload(main)

    attempts = []

    class DummyResp:
        content = b"ok"

        def raise_for_status(self): ...

    def fake_get(url, timeout):
        attempts.append(1)
        return DummyResp()

    monkeypatch.setattr(main.requests, "get", fake_get)

    entry = SimpleNamespace(links=[{"rel": "enclosure", "href": "http://example.com"}])
    dest = tmp_path / "f.mp3"

    main.fetch_enclosure(entry, dest)

    # 最低1回は実行されること
    assert len(attempts) == 1
    assert dest.read_bytes() == b"ok"


# ========== crawl() 例外ハンドリング ==========
def test_crawl_continues_after_gemini_api_error(monkeypatch, tmp_path, capsys):
    """process_youtube が RuntimeError を raise しても crawl が次のエントリへ進むこと"""
    import importlib
    import time as _time

    monkeypatch.setenv("OUTPUT_DIR_YT", str(tmp_path / "yt"))
    monkeypatch.setenv("OUTPUT_DIR_POD", str(tmp_path / "pod"))

    import main

    main = importlib.reload(main)

    monkeypatch.setattr(main.yaml, "safe_load", lambda _: ["https://example.com/feed"])
    monkeypatch.setattr(main.pathlib.Path, "read_text", lambda self: "")
    monkeypatch.setattr(main.time, "sleep", lambda s: None)

    notify_calls = []
    monkeypatch.setattr(main, "notify", lambda msg, **kw: notify_calls.append(msg))

    # エントリ2つ: 1つ目は RuntimeError、2つ目は正常
    recent_ts = _time.gmtime(_time.time() - 3600)

    entry_fail = SimpleNamespace(
        yt_videoid="vid_fail",
        title="Failing Video",
        published_parsed=recent_ts,
        updated_parsed=None,
        author="Author",
        link="https://youtu.be/vid_fail",
    )
    entry_ok = SimpleNamespace(
        yt_videoid="vid_ok",
        title="OK Video",
        published_parsed=recent_ts,
        updated_parsed=None,
        author="Author",
        link="https://youtu.be/vid_ok",
    )

    call_log = []

    def fake_process_youtube(e):
        call_log.append(e.yt_videoid)
        if e.yt_videoid == "vid_fail":
            raise RuntimeError("Gemini API 429 リトライ上限超過")
        return True

    monkeypatch.setattr(main, "process_youtube", fake_process_youtube)

    class FakeParsed:
        status = 200
        bozo = False
        entries = [entry_fail, entry_ok]

    monkeypatch.setattr(main.feedparser, "parse", lambda url: FakeParsed())

    main.crawl()

    # 両方のエントリが処理されること（1つ目の例外で止まらない）
    assert call_log == ["vid_fail", "vid_ok"]

    # エラーエントリが skip カウントされること
    captured = capsys.readouterr()
    assert "ERROR" in captured.out or "Gemini" in captured.out

    # 正常エントリが成功カウントされること（サマリーに成功1件）
    assert "成功 1件" in captured.out
    assert "スキップ 1件" in captured.out
