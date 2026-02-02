"""Gemini API クライアント。外部リクエストを集約し、テストでモックしやすくする。"""

from __future__ import annotations

import base64
import random
import time
from typing import Protocol, Any, Callable

import requests

from services.text_validator import is_repetitive

# --- 生成パラメータ ---
_BASE_TEMPERATURE = 0.7
_TEMPERATURE_STEP = 0.3  # リトライごとの温度エスカレーション幅
_MAX_CONTENT_RETRIES = 3

_BASE_GENERATION_CONFIG: dict[str, Any] = {
    "temperature": _BASE_TEMPERATURE,
    "topP": 0.95,
    "topK": 40,
    "maxOutputTokens": 8192,
    "frequencyPenalty": 0.3,
    "presencePenalty": 0.1,
}

# finishReason の分類
_ACCEPT_REASONS = {"STOP", "MAX_TOKENS", None}  # None = finishReason が応答に含まれないケース


class _Session(Protocol):
    def post(self, url: str, **kwargs: Any): ...


class GeminiClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        session: _Session | None = None,
        upload_url: str | None = None,
        gen_url: str | None = None,
        debug: bool = False,
        notifier: Callable[[str], None] | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("GEMINI_API_KEY が設定されていません")
        self.api_key = api_key
        self.model = model
        self.session = session or requests
        base = "https://generativelanguage.googleapis.com"
        self.upload_url = upload_url or f"{base}/upload/v1beta/files"
        self.gen_url = gen_url or f"{base}/v1beta/models/{model}:generateContent"
        self.debug = debug
        self._notify = notifier or (lambda _msg: None)

    def summarize_audio(self, mp3_bytes: bytes, prompt: str) -> str | None:
        """音声バイト列を Gemini へ投げ、要約テキストを返す。

        安全フィルタで本文が欠落した場合は即座に None を返す。
        finishReason 異常や反復テキスト検出時は温度を上げて最大3回リトライする。
        """
        audio_parts = self._build_parts(mp3_bytes)
        contents = [{"role": "user", "parts": audio_parts + [{"text": prompt}]}]

        for attempt in range(_MAX_CONTENT_RETRIES):
            temperature = min(_BASE_TEMPERATURE + attempt * _TEMPERATURE_STEP, 2.0)
            gen_config = {**_BASE_GENERATION_CONFIG, "temperature": temperature}
            payload = {"contents": contents, "generationConfig": gen_config}

            text, finish_reason = self._request(payload)

            # 安全フィルタ（text が None）→ 即終了
            if text is None:
                self._notify("Gemini 応答に本文がありませんでした (parts missing)")
                return None

            # finishReason が異常 → 温度を上げてリトライ
            if finish_reason not in _ACCEPT_REASONS:
                self._notify(
                    f"Gemini finishReason={finish_reason} → リトライ ({attempt + 1}/{_MAX_CONTENT_RETRIES})"
                )
                continue

            # 反復テキスト検出 → 温度を上げてリトライ
            if is_repetitive(text):
                self._notify(
                    f"Gemini 反復テキスト検出 → リトライ ({attempt + 1}/{_MAX_CONTENT_RETRIES})"
                )
                continue

            return text

        # リトライ尽きた
        self._notify("Gemini コンテンツ品質リトライ上限に到達")
        return None

    def _request(self, payload: dict[str, Any]) -> tuple[str | None, str | None]:
        """HTTP リトライ込みで Gemini API を呼び出す。(text, finishReason) を返す。"""
        for retry in range(5):
            res = self.session.post(
                self.gen_url, params={"key": self.api_key}, json=payload, timeout=300
            )
            if self.debug:
                print(f"[Gemini debug] status={res.status_code} body={res.text[:300]}")
            if res.status_code in (429, 503):
                wait = (2**retry) + random.uniform(0, 3)
                self._notify(f"Gemini {res.status_code} → {wait:.1f}s wait")
                time.sleep(wait)
                continue
            res.raise_for_status()
            data = res.json()
            text = self._extract_text(data)
            finish_reason = self._extract_finish_reason(data)
            return text, finish_reason

        raise RuntimeError("Gemini API failed after 5 retries")

    def _extract_text(self, data: dict[str, Any]) -> str | None:
        """候補からテキストを安全に取り出す。欠落時は None。"""
        try:
            candidates = data.get("candidates") or []
            if not candidates:
                return None
            content = candidates[0].get("content") or {}
            parts = content.get("parts") or []
            if not parts or not isinstance(parts, list):
                return None
            first = parts[0]
            if not isinstance(first, dict):
                return None
            return first.get("text")
        except (KeyError, TypeError, IndexError):
            return None

    def _extract_finish_reason(self, data: dict[str, Any]) -> str | None:
        """候補から finishReason を安全に取り出す。"""
        try:
            candidates = data.get("candidates") or []
            if not candidates:
                return None
            return candidates[0].get("finishReason")
        except (KeyError, TypeError, IndexError):
            return None

    def _build_parts(self, mp3_bytes: bytes) -> list[dict[str, dict[str, str]]]:
        if len(mp3_bytes) > 20 * 1024 * 1024:
            up = self.session.post(
                self.upload_url,
                params={"key": self.api_key, "uploadType": "media"},
                headers={"Content-Type": "audio/mp3"},
                data=mp3_bytes,
                timeout=300,
            )
            up.raise_for_status()
            file_uri = up.json()["file"]["uri"]
            return [{"file_data": {"file_uri": file_uri}}]

        return [
            {
                "inline_data": {
                    "mime_type": "audio/mp3",
                    "data": base64.b64encode(mp3_bytes).decode(),
                }
            }
        ]
