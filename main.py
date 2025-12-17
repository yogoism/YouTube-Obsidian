#!/usr/bin/env python3
"""YouTube RSS → Gemini 2.5 Flash → Markdown."""

import os, re, json, time, pathlib, subprocess, tempfile, calendar
import feedparser, requests, yaml
from datetime import datetime, timezone, UTC
from dotenv import load_dotenv
from services.gemini_client import GeminiClient
from prompts import PROMPT_TMPL

# ---------- 設定 ----------
# .env.local を優先的に読む（存在しない場合のみデフォルトの .env を読む）
env_path = pathlib.Path(".env.local")
load_dotenv(dotenv_path=env_path if env_path.exists() else None)
OUT_YT = pathlib.Path(os.getenv("OUTPUT_DIR_YT", "/Users/shee/YOGO/20_library/youtube"))
OUT_POD = pathlib.Path(
    os.getenv("OUTPUT_DIR_POD", "/Users/shee/YOGO/20_library/podcast")
)
for p in (OUT_YT, OUT_POD):
    p.expanduser().mkdir(parents=True, exist_ok=True)
API_KEY = os.getenv("GEMINI_API_KEY")
MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
WINDOW_HOURS = 24  # 直近何時間を見るか
DEBUG_GEMINI = os.getenv("GEMINI_DEBUG", "0") == "1"  # デバッグ時のみ詳細を出す
YTDLP_TIMEOUT = int(os.getenv("YTDLP_TIMEOUT", "900"))  # ハング防止のための上限秒数
POD_TIMEOUT = int(os.getenv("POD_TIMEOUT", "600"))  # Podcastダウンロードのタイムアウト
POD_RETRIES = int(os.getenv("POD_RETRIES", "3"))  # Podcastダウンロードのリトライ回数
_gemini_client: GeminiClient | None = None

# ---------- 通知 ----------
try:
    from pync import Notifier

    _USE_PYNC = True
except ImportError:
    _USE_PYNC = False


def notify(msg: str, title: str = "YouTube & Podcast Bot") -> None:
    if _USE_PYNC:
        try:
            Notifier.notify(msg, title=title)
            return
        except Exception:
            pass

    try:
        import subprocess, shlex

        subprocess.run(
            ["osascript", "-e", f'display notification "{msg}" with title "{title}"']
        )
    except Exception:
        print(f"[NOTIFY] {title}: {msg}")


def get_gemini_client() -> GeminiClient:
    """GeminiClient を遅延初期化（notify を参照可能な順序にする）"""
    global _gemini_client
    if _gemini_client is None:
        _gemini_client = GeminiClient(
            API_KEY, MODEL, debug=DEBUG_GEMINI, notifier=notify
        )
    return _gemini_client


# ========== ユーティリティ ==========
def sanitize_filename(text: str, max_len: int = 80) -> str:
    """ファイル名に使えない文字を削除 & 長さ制限"""
    return re.sub(r'[\\/*?:"<>|]', "", text)[:max_len]



def build_prompt(entry, *, channel: str = "") -> str:
    url = (
        getattr(entry, "link", None)
        or getattr(entry, "id", "")
        or (entry.enclosures[0].href if getattr(entry, "enclosures", []) else "")
    )

    if not channel:
        channel = (
            getattr(
                entry, "author", ""
            )  # YouTube RSS はここにチャンネル名が入ることが多い
            or getattr(entry, "itunes_author", "")  # Podcast RSS
            or "unknown"
        )

    meta = {
        "title_ja": "",  # 日本語タイトルは Gemini に生成させる
        "original_title": entry.title,
        "channel": channel,
        "url": url,
        "published": entry.pub_slash,
    }
    return PROMPT_TMPL.format(**meta)


# ========== YouTube 用 ==========
def yt_meta(url: str) -> dict | None:
    """yt-dlp -j --skip-download でメタデータ取得。失敗時はNone"""
    res = subprocess.run(
        ["yt-dlp", "-j", "--skip-download", url],
        capture_output=True,
        text=True,
        check=False,
    )

    if res.returncode != 0:
        msg = res.stderr.strip().splitlines()[-1] if res.stderr else "Unknown error"
        print(f"   - yt-dlp error ({res.returncode}) for {url}: {msg}")
        return None

    try:
        return json.loads(res.stdout)
    except json.JSONDecodeError:
        print(f"   - yt-dlp produced invalid JSON for {url}")
        return None


def yt_is_video(meta: dict) -> bool:
    """Shorts、ライブ配信、プレミア、公開前動画を除外"""
    dur, w, h = meta.get("duration", 0), meta.get("width", 0), meta.get("height", 0)
    shorts = dur <= 60 or (h and w and h > w)
    stream = (
        meta.get("is_live")
        or meta.get("was_live")
        or meta.get("live_status") in {"is_live", "was_live", "is_upcoming"}
    )
    scheduled = (
        meta.get("availability") == "scheduled"
        or meta.get("live_status") == "is_upcoming"
    )

    return (not shorts) and (not stream) and (not scheduled)


# ========== Podcast 用 ==========
def is_podcast(entry) -> bool:
    """enclosure で audio/* を持つかどうか"""
    return any(
        l.get("rel") == "enclosure" or l.get("type", "").startswith("audio/")
        for l in entry.get("links", [])
    )


def fetch_enclosure(entry, dest: pathlib.Path):
    def _rel(it):
        return it.get("rel") if isinstance(it, dict) else getattr(it, "rel", None)

    def _href(it):
        return it.get("href") if isinstance(it, dict) else getattr(it, "href", None)

    enc = next(l for l in entry.links if _rel(l) == "enclosure")
    href = _href(enc)

    last_err = None
    for attempt in range(1, POD_RETRIES + 1):
        try:
            r = requests.get(href, timeout=POD_TIMEOUT)  # 大きいファイルもあるので長めタイムアウト
            r.raise_for_status()
            dest.write_bytes(r.content)
            return
        except requests.exceptions.RequestException as e:
            last_err = e
            if attempt >= POD_RETRIES:
                break
            wait = 2 ** (attempt - 1)
            time.sleep(wait)
    raise last_err


# ========== 処理ルーチン ==========
def process_youtube(entry):
    vid = entry.yt_videoid
    url = f"https://youtu.be/{vid}"
    ymeta = yt_meta(url)
    if ymeta is None:
        return
    if not yt_is_video(ymeta):
        print(f"   - SKIP non-video {vid}")
        return

    channel_name = ymeta.get("uploader") or "unknown"

    prompt = build_prompt(entry, channel=channel_name)

    with tempfile.TemporaryDirectory() as tmpdir:
        mp3_path = pathlib.Path(tmpdir) / f"{vid}.mp3"
        subprocess.run(
            ["yt-dlp", "-x", "--audio-format", "mp3", "-o", str(mp3_path), url],
            check=True,
            timeout=YTDLP_TIMEOUT,
        )
        md = get_gemini_client().summarize_audio(mp3_path.read_bytes(), prompt)

    author = sanitize_filename(getattr(entry, "author", "unknown"), 40)
    fname = f"{entry.pub_dash}_{sanitize_filename(entry.title)}_{author}.md"
    (OUT_YT / fname).write_text(md, encoding="utf-8")
    print(f" ✔ YT  {entry.title}")
    notify(f"YouTube: {entry.title}")


def process_podcast(entry):
    channel_name = (
        getattr(entry, "author", "") or getattr(entry, "itunes_author", "") or "unknown"
    )

    prompt = build_prompt(entry, channel=channel_name)

    print(f"[download] {entry.title}")

    with tempfile.TemporaryDirectory() as tmpdir:
        mp3_path = pathlib.Path(tmpdir) / "podcast.mp3"
        fetch_enclosure(entry, mp3_path)  # ← ここで進捗バーを表示
        md = get_gemini_client().summarize_audio(mp3_path.read_bytes(), prompt)

    author = sanitize_filename(
        getattr(entry, "author", getattr(entry, "itunes_author", "unknown")), 40
    )
    fname = f"{entry.pub_dash}_{sanitize_filename(entry.title)}_{author}.md"
    (OUT_POD / fname).write_text(md, encoding="utf-8")

    print(f" ✔ Pod  {entry.title}")  # YouTube と同じ形式
    notify(f"Podcast: {entry.title}")


# ========== クロール ==========
def crawl():
    since_ts = time.time() - WINDOW_HOURS * 3600
    feeds_raw = yaml.safe_load(pathlib.Path("feeds.yaml").read_text()) or []
    feeds = [f.strip() for f in feeds_raw if isinstance(f, str) and f.strip()]
    now_utc = datetime.now(UTC)

    for feed_url in feeds:
        if not feed_url.strip():
            continue
        print(f"● {feed_url}")
        parsed = feedparser.parse(feed_url.strip())
        for e in parsed.entries:
            ts_tuple = getattr(e, "published_parsed", None) or getattr(
                e, "updated_parsed", None
            )
            if not ts_tuple or calendar.timegm(ts_tuple) < since_ts:
                continue

            pub_dt = datetime.fromtimestamp(calendar.timegm(ts_tuple), UTC)
            if pub_dt > now_utc:
                print(f"   - SKIP scheduled premiere: {e.title}")
                continue

            if pub_dt.timestamp() < since_ts:
                continue

            e.pub_dash = pub_dt.strftime("%Y-%m-%d")  # 例 2025-05-31 （ファイル名用）
            e.pub_slash = pub_dt.strftime("%Y/%m/%d")  # 例 2025/05/31 （YAML 用）

            if hasattr(e, "yt_videoid"):
                process_youtube(e)
            elif is_podcast(e):
                process_podcast(e)
            else:
                print(f"   - SKIP unknown type: {e.title}")

            time.sleep(3)  # API レート制限対策


if __name__ == "__main__":
    crawl()
