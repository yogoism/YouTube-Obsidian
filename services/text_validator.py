"""テキスト品質バリデータ。Gemini の反復ループ（degenerate generation）を検出する。

3層の検査で反復テキストを判定:
  A) 正規表現による連続反復検出
  B) 文レベルのユニーク率
  C) 文字 n-gram の多様性
"""

from __future__ import annotations

import re

# --- 設定値 ---
_MIN_LENGTH = 20  # これ未満は検査スキップ
_CONSECUTIVE_REPEATS = 4  # 層A: 同一部分文字列がこの回数以上連続で反復
_UNIQUE_RATIO_THRESHOLD = 0.4  # 層B: ユニーク文の割合がこれ未満で反復
_NGRAM_DIVERSITY_THRESHOLD = 0.3  # 層C: n-gram 多様性がこれ未満で反復
_NGRAM_SIZE = 10  # 層C: n-gram の文字数

# 層A: 2〜50文字の部分文字列が _CONSECUTIVE_REPEATS 回以上連続
_RE_CONSECUTIVE = re.compile(r"(.{2,50})\1{" + str(_CONSECUTIVE_REPEATS - 1) + r",}")

# 文分割用（句点・ピリオド・改行で分割）
_RE_SENTENCE_SPLIT = re.compile(r"[。．.!\n]+")


def is_repetitive(text: str) -> bool:
    """テキストが反復ループかどうかを判定する。

    短いテキスト（< 20文字）は即座に False を返す。
    """
    if len(text) < _MIN_LENGTH:
        return False

    # 層A: 同一部分文字列の連続反復
    if _RE_CONSECUTIVE.search(text):
        return True

    # 層B: 文レベルのユニーク率
    sentences = [s.strip() for s in _RE_SENTENCE_SPLIT.split(text) if s.strip()]
    if len(sentences) >= 5:
        unique_ratio = len(set(sentences)) / len(sentences)
        if unique_ratio < _UNIQUE_RATIO_THRESHOLD:
            return True

    # 層C: 文字 n-gram の多様性
    if len(text) >= _NGRAM_SIZE + 1:
        ngrams = {text[i : i + _NGRAM_SIZE] for i in range(len(text) - _NGRAM_SIZE + 1)}
        max_possible = len(text) - _NGRAM_SIZE + 1
        diversity = len(ngrams) / max_possible
        if diversity < _NGRAM_DIVERSITY_THRESHOLD:
            return True

    return False
