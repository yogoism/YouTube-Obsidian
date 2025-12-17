import types
import base64

import pytest


def make_response(status, text="", json_data=None):
    class Resp:
        def __init__(self):
            self.status_code = status
            self.text = text

        def raise_for_status(self):
            if self.status_code >= 400:
                raise Exception(f"{self.status_code}: {self.text}")

        def json(self):
            return json_data

    return Resp()


def test_retries_then_succeeds(monkeypatch):
    from services.gemini_client import GeminiClient

    calls = []

    def fake_post(url, params=None, json=None, **kwargs):
        calls.append((url, params, json))
        if len(calls) == 1:
            return make_response(429, "quota")
        return make_response(
            200,
            json_data={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]},
        )

    client = GeminiClient(api_key="k", model="m", session=types.SimpleNamespace(post=fake_post))

    with monkeypatch.context() as m:
        m.setattr("time.sleep", lambda s: None)
        assert client.summarize_audio(b"123", "prompt") == "ok"

    assert calls[0][0].endswith(":generateContent")
    assert calls[1][0].endswith(":generateContent")
    assert len(calls) == 2


def test_large_audio_uses_upload_first(monkeypatch):
    from services.gemini_client import GeminiClient

    uploads = []
    generations = []

    def fake_post(url, params=None, json=None, headers=None, data=None, **kwargs):
        if "upload" in url:
            uploads.append((url, headers, data))
            return make_response(200, json_data={"file": {"uri": "files/123"}})
        generations.append(json)
        return make_response(
            200,
            json_data={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]},
        )

    client = GeminiClient(api_key="k", model="m", session=types.SimpleNamespace(post=fake_post))

    big_bytes = b"x" * (21 * 1024 * 1024)

    with monkeypatch.context() as m:
        m.setattr("time.sleep", lambda s: None)
        assert client.summarize_audio(big_bytes, "prompt") == "ok"

    assert uploads, "upload endpoint should be called for >20MB"
    assert uploads[0][1]["Content-Type"] == "audio/mp3"
    assert generations[0]["contents"][0]["parts"][0]["file_data"]["file_uri"] == "files/123"


def test_inline_audio_base64(monkeypatch):
    from services.gemini_client import GeminiClient

    generations = []

    def fake_post(url, params=None, json=None, headers=None, data=None, **kwargs):
        if "upload" in url:
            pytest.fail("Should not upload for small payloads")
        generations.append(json)
        return make_response(
            200,
            json_data={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]},
        )

    client = GeminiClient(api_key="k", model="m", session=types.SimpleNamespace(post=fake_post))

    payload = b"abc"
    with monkeypatch.context() as m:
        m.setattr("time.sleep", lambda s: None)
        assert client.summarize_audio(payload, "prompt") == "ok"

    data = generations[0]["contents"][0]["parts"][0]["inline_data"]["data"]
    assert base64.b64decode(data) == payload


def test_missing_api_key_raises():
    from services.gemini_client import GeminiClient

    with pytest.raises(ValueError):
        GeminiClient(api_key="", model="m")
