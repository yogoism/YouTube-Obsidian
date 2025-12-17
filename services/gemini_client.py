"""Gemini API クライアント。外部リクエストを集約し、テストでモックしやすくする。"""

from __future__ import annotations

import base64
import random
import time
from typing import Protocol, Any, Callable

import requests


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

    def summarize_audio(self, mp3_bytes: bytes, prompt: str) -> str:
        """音声バイト列を Gemini へ投げ、要約テキストを返す"""
        parts = self._build_parts(mp3_bytes)
        payload = {"contents": [{"role": "user", "parts": parts + [{"text": prompt}]}]}

        for retry in range(5):
            res = self.session.post(
                self.gen_url, params={"key": self.api_key}, json=payload, timeout=300
            )
            if self.debug and res.status_code != 200:
                print(f"[Gemini debug] status={res.status_code} body={res.text[:300]}")
            if res.status_code in (429, 503):
                wait = (2**retry) + random.uniform(0, 3)
                self._notify(f"Gemini {res.status_code} → {wait:.1f}s wait")
                time.sleep(wait)
                continue
            res.raise_for_status()
            return res.json()["candidates"][0]["content"]["parts"][0]["text"]

        raise RuntimeError("Gemini API failed after 5 retries")

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
