"""text_validator の単体テスト。反復ループ検出の各層を検証する。"""

from services.text_validator import is_repetitive


# --- 短いテキストは常に False ---
class TestShortText:
    def test_empty_string(self):
        assert is_repetitive("") is False

    def test_short_normal(self):
        assert is_repetitive("こんにちは") is False


# --- 層A: 同一部分文字列の連続反復 ---
class TestConsecutiveRepetition:
    def test_same_phrase_repeated_4_times(self):
        """同一フレーズが4回以上連続すれば反復と判定"""
        text = "こんにちは。" * 10
        assert is_repetitive(text) is True

    def test_three_repeats_is_ok(self):
        """3回連続は閾値未満なので正常"""
        text = "これはテスト。" * 3
        assert is_repetitive(text) is False


# --- 層B: 文レベルのユニーク率 ---
class TestSentenceUniqueness:
    def test_many_duplicate_sentences(self):
        """同一文が大量に繰り返されるとユニーク率が低下して反復と判定"""
        # 20文中19文が同一 → ユニーク率 = 2/20 = 0.1
        text = "今日は良い天気です。" * 19 + "明日も晴れるでしょう。"
        assert is_repetitive(text) is True

    def test_diverse_sentences(self):
        """十分に多様な文は正常"""
        sentences = [f"これは{i}番目の文です。" for i in range(10)]
        text = "".join(sentences)
        assert is_repetitive(text) is False


# --- 層C: n-gram 多様性 ---
class TestNgramDiversity:
    def test_monotonous_characters(self):
        """同一文字の羅列は n-gram 多様性が極端に低い"""
        text = "あ" * 200
        assert is_repetitive(text) is True

    def test_normal_japanese_text(self):
        """通常の日本語テキストは正常"""
        text = (
            "大規模言語モデルは自然言語処理の分野で急速に発展しています。"
            "これらのモデルは膨大なテキストデータから学習し、人間のような文章を生成できます。"
            "しかし、時として同じフレーズを繰り返すという問題が発生することがあります。"
        )
        assert is_repetitive(text) is False


# --- 境界値 ---
class TestEdgeCases:
    def test_exactly_20_chars_not_short(self):
        """ちょうど20文字は短文扱いしない（層Cで単調さを検出）"""
        text = "あ" * 20  # 単調テキスト → 層Cで反復判定
        assert is_repetitive(text) is True

    def test_19_chars_is_short(self):
        """19文字は短文扱いでスキップ（検査しない）"""
        text = "あ" * 19
        assert is_repetitive(text) is False

    def test_mixed_repetitive_and_normal(self):
        """前半が反復でも全体としては正常なケース"""
        repetitive_part = "テスト。" * 3
        normal_part = "".join(f"段落{i}の内容はここに書かれています。" for i in range(5))
        text = repetitive_part + normal_part
        assert is_repetitive(text) is False
