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
