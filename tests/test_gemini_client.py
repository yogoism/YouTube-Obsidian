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


def test_missing_parts_returns_none_and_notifies(monkeypatch):
    from services.gemini_client import GeminiClient

    notified = {}

    def fake_post(url, params=None, json=None, **kwargs):
        return make_response(
            200,
            json_data={
                "candidates": [
                    {
                        "finishReason": "SAFETY",
                        "safetyRatings": [],
                        "content": {"role": "model"},
                    }
                ],
                "promptFeedback": {"blockReason": "SAFETY"},
            },
        )

    def fake_notify(msg):
        notified["msg"] = msg

    client = GeminiClient(
        api_key="k",
        model="m",
        session=types.SimpleNamespace(post=fake_post),
        notifier=fake_notify,
    )

    with monkeypatch.context() as m:
        m.setattr("time.sleep", lambda s: None)
        assert client.summarize_audio(b"123", "prompt") is None

    assert "Gemini" in notified["msg"]


# ========== generationConfig 関連 ==========
class TestGenerationConfig:
    """payload に generationConfig が含まれていることを確認"""

    def test_payload_contains_generation_config(self, monkeypatch):
        from services.gemini_client import GeminiClient

        captured = {}

        def fake_post(url, params=None, json=None, **kwargs):
            captured["payload"] = json
            return make_response(
                200,
                json_data={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]},
            )

        client = GeminiClient(
            api_key="k", model="m", session=types.SimpleNamespace(post=fake_post)
        )
        monkeypatch.setattr("time.sleep", lambda s: None)
        client.summarize_audio(b"123", "prompt")

        cfg = captured["payload"].get("generationConfig")
        assert cfg is not None, "generationConfig がペイロードに含まれていない"
        assert "temperature" in cfg
        assert "frequencyPenalty" in cfg

    def test_frequency_penalty_is_positive(self, monkeypatch):
        from services.gemini_client import GeminiClient

        captured = {}

        def fake_post(url, params=None, json=None, **kwargs):
            captured["payload"] = json
            return make_response(
                200,
                json_data={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]},
            )

        client = GeminiClient(
            api_key="k", model="m", session=types.SimpleNamespace(post=fake_post)
        )
        monkeypatch.setattr("time.sleep", lambda s: None)
        client.summarize_audio(b"123", "prompt")

        cfg = captured["payload"]["generationConfig"]
        assert cfg["frequencyPenalty"] > 0, "frequencyPenalty は正の値であるべき"


# ========== finishReason 関連 ==========
class TestFinishReason:
    """finishReason に応じた挙動を確認"""

    def test_recitation_triggers_retry(self, monkeypatch):
        """RECITATION は温度を上げてリトライする"""
        from services.gemini_client import GeminiClient

        call_count = 0

        def fake_post(url, params=None, json=None, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return make_response(
                    200,
                    json_data={
                        "candidates": [
                            {
                                "finishReason": "RECITATION",
                                "content": {"parts": [{"text": "反復テキスト"}]},
                            }
                        ]
                    },
                )
            return make_response(
                200,
                json_data={
                    "candidates": [
                        {
                            "finishReason": "STOP",
                            "content": {"parts": [{"text": "正常テキスト"}]},
                        }
                    ]
                },
            )

        client = GeminiClient(
            api_key="k", model="m", session=types.SimpleNamespace(post=fake_post)
        )
        monkeypatch.setattr("time.sleep", lambda s: None)
        result = client.summarize_audio(b"123", "prompt")

        assert result == "正常テキスト"
        assert call_count == 3, "RECITATION で2回リトライ後に成功するはず"

    def test_stop_is_accepted(self, monkeypatch):
        """STOP は正常終了として受理"""
        from services.gemini_client import GeminiClient

        def fake_post(url, params=None, json=None, **kwargs):
            return make_response(
                200,
                json_data={
                    "candidates": [
                        {
                            "finishReason": "STOP",
                            "content": {"parts": [{"text": "正常"}]},
                        }
                    ]
                },
            )

        client = GeminiClient(
            api_key="k", model="m", session=types.SimpleNamespace(post=fake_post)
        )
        monkeypatch.setattr("time.sleep", lambda s: None)
        assert client.summarize_audio(b"123", "prompt") == "正常"

    def test_max_tokens_is_accepted(self, monkeypatch):
        """MAX_TOKENS は正常終了として受理"""
        from services.gemini_client import GeminiClient

        def fake_post(url, params=None, json=None, **kwargs):
            return make_response(
                200,
                json_data={
                    "candidates": [
                        {
                            "finishReason": "MAX_TOKENS",
                            "content": {"parts": [{"text": "長いテキスト"}]},
                        }
                    ]
                },
            )

        client = GeminiClient(
            api_key="k", model="m", session=types.SimpleNamespace(post=fake_post)
        )
        monkeypatch.setattr("time.sleep", lambda s: None)
        assert client.summarize_audio(b"123", "prompt") == "長いテキスト"

    def test_all_retries_exhausted_returns_none(self, monkeypatch):
        """コンテンツリトライが尽きたら None を返す"""
        from services.gemini_client import GeminiClient

        def fake_post(url, params=None, json=None, **kwargs):
            return make_response(
                200,
                json_data={
                    "candidates": [
                        {
                            "finishReason": "RECITATION",
                            "content": {"parts": [{"text": "反復"}]},
                        }
                    ]
                },
            )

        client = GeminiClient(
            api_key="k", model="m", session=types.SimpleNamespace(post=fake_post)
        )
        monkeypatch.setattr("time.sleep", lambda s: None)
        assert client.summarize_audio(b"123", "prompt") is None


# ========== コンテンツ品質リトライ関連 ==========
class TestContentQualityRetry:
    """反復テキスト検出によるリトライ"""

    def test_repetitive_text_triggers_retry(self, monkeypatch):
        """反復テキストを検出したらリトライし、正常テキストを返す"""
        from services.gemini_client import GeminiClient

        call_count = 0
        repetitive = "こんにちは。" * 20  # is_repetitive() が True を返すテキスト

        def fake_post(url, params=None, json=None, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return make_response(
                    200,
                    json_data={
                        "candidates": [
                            {
                                "finishReason": "STOP",
                                "content": {"parts": [{"text": repetitive}]},
                            }
                        ]
                    },
                )
            return make_response(
                200,
                json_data={
                    "candidates": [
                        {
                            "finishReason": "STOP",
                            "content": {"parts": [{"text": "正常な要約テキスト"}]},
                        }
                    ]
                },
            )

        client = GeminiClient(
            api_key="k", model="m", session=types.SimpleNamespace(post=fake_post)
        )
        monkeypatch.setattr("time.sleep", lambda s: None)
        result = client.summarize_audio(b"123", "prompt")

        assert result == "正常な要約テキスト"
        assert call_count == 2

    def test_temperature_escalates_on_retry(self, monkeypatch):
        """リトライごとに温度がエスカレートする"""
        from services.gemini_client import GeminiClient

        temperatures = []
        repetitive = "こんにちは。" * 20

        def fake_post(url, params=None, json=None, **kwargs):
            cfg = json.get("generationConfig", {})
            temperatures.append(cfg.get("temperature"))
            return make_response(
                200,
                json_data={
                    "candidates": [
                        {
                            "finishReason": "RECITATION",
                            "content": {"parts": [{"text": repetitive}]},
                        }
                    ]
                },
            )

        client = GeminiClient(
            api_key="k", model="m", session=types.SimpleNamespace(post=fake_post)
        )
        monkeypatch.setattr("time.sleep", lambda s: None)
        client.summarize_audio(b"123", "prompt")  # 全リトライ失敗 → None

        assert len(temperatures) == 3, "コンテンツリトライは最大3回"
        assert temperatures[0] < temperatures[1] < temperatures[2], "温度はエスカレートすべき"

    def test_safety_returns_none_immediately(self, monkeypatch):
        """text が None (安全フィルタ) の場合は即座に None を返しリトライしない"""
        from services.gemini_client import GeminiClient

        call_count = 0

        def fake_post(url, params=None, json=None, **kwargs):
            nonlocal call_count
            call_count += 1
            return make_response(
                200,
                json_data={
                    "candidates": [
                        {
                            "finishReason": "SAFETY",
                            "content": {"role": "model"},
                        }
                    ]
                },
            )

        client = GeminiClient(
            api_key="k", model="m", session=types.SimpleNamespace(post=fake_post)
        )
        monkeypatch.setattr("time.sleep", lambda s: None)
        result = client.summarize_audio(b"123", "prompt")

        assert result is None
        assert call_count == 1, "安全フィルタではリトライしない"

    def test_http_retry_still_works_within_content_retry(self, monkeypatch):
        """コンテンツリトライの内部で HTTP 429 リトライも正常に動作する"""
        from services.gemini_client import GeminiClient

        call_count = 0

        def fake_post(url, params=None, json=None, **kwargs):
            nonlocal call_count
            call_count += 1
            # 1回目: HTTP 429 → 内部リトライ
            if call_count == 1:
                return make_response(429, "quota")
            # 2回目: RECITATION → コンテンツリトライ
            if call_count == 2:
                return make_response(
                    200,
                    json_data={
                        "candidates": [
                            {
                                "finishReason": "RECITATION",
                                "content": {"parts": [{"text": "反復"}]},
                            }
                        ]
                    },
                )
            # 3回目: 正常
            return make_response(
                200,
                json_data={
                    "candidates": [
                        {
                            "finishReason": "STOP",
                            "content": {"parts": [{"text": "成功"}]},
                        }
                    ]
                },
            )

        client = GeminiClient(
            api_key="k", model="m", session=types.SimpleNamespace(post=fake_post)
        )
        monkeypatch.setattr("time.sleep", lambda s: None)
        result = client.summarize_audio(b"123", "prompt")

        assert result == "成功"
