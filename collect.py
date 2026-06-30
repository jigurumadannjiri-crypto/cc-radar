# -*- coding: utf-8 -*-
"""
cc-radar : Claude Code 最新情報レーダー
--------------------------------------------------------------------------
Claude Code の最新情報を毎朝自動収集し、
  (1) docs/index.html + docs/data.json   … GitHub Pages 公開用 静的サイト
  (2) Gmail (SMTP/STARTTLS)              … おすすめTOP5を毎朝メール通知
を生成する。標準ライブラリのみで動作（pip不要）。

起動引数:
    python collect.py              # 全実行（収集→HTML/JSON生成→メール送信）。本日送信済みならメールはスキップ
    python collect.py --force-mail # 本日送信済みでも再送する（手動実行・workflow_dispatch用）
    python collect.py --no-mail    # メール送信なし（HTML/JSONのみ）
    python collect.py --dry-run    # 収集して結果を表示するだけ（ファイル出力なし）

環境変数:
    ANTHROPIC_API_KEY              # AI翻訳・要約（任意。無ければタイトルで判断）
    CC_RADAR_GMAIL_USER            # 送信元Gmailアドレス（任意。無ければ送信スキップ）
    CC_RADAR_GMAIL_PASS            # Gmailアプリパスワード
    CC_RADAR_INSECURE_SSL=1        # SSL検証を無効化（社内ネット用。自宅は不要）
"""

import sys
import os
import re
import ssl
import json
import html
import time
import smtplib
import datetime as dt
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parsedate_to_datetime
import urllib.request
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET

# ---- Windowsコンソールの文字化け対策（UTF-8で出力） ------------------------
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")
DOCS_DIR = os.path.join(HERE, "docs")
DATA_DIR = os.path.join(HERE, "data")
HISTORY_PATH = os.path.join(DATA_DIR, "history.json")
# 「本日もう送ったか」を記録する重複防止マーカー（gitに追跡させ、クラウドの複数回起動で共有）。
# ★多重cron（朝に4回発火）でも実際に送るのは1日1回だけにするための要。
STATE_DIR = os.path.join(HERE, "state")
LAST_SENT_PATH = os.path.join(STATE_DIR, "last_sent.txt")

ATOM_NS = {"a": "http://www.w3.org/2005/Atom"}


# ===========================================================================
#  共通ユーティリティ
# ===========================================================================
def log(msg):
    print(msg, flush=True)


def text_of(el):
    """XML要素のテキストを安全に取り出す。
    ★再現の肝: `if el or ...` ではなく `el is not None` で判定すること。
    空文字要素を真偽値で判定するとタイトルが（無題）に化ける。"""
    if el is not None and el.text is not None:
        return el.text.strip()
    return ""


def ssl_context():
    ctx = ssl.create_default_context()
    if os.environ.get("CC_RADAR_INSECURE_SSL") == "1":
        # 社内ネット等、証明書がプロキシで差し替えられる環境向け。自宅では不要。
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        log("  [warn] CC_RADAR_INSECURE_SSL=1 : SSL検証を無効化しています")
    return ctx


def http_get(url, cfg, as_bytes=False, extra_headers=None):
    """UA偽装つきHTTP GET。★独自UAだと全ソース403になるためChrome UA必須。"""
    headers = {
        "User-Agent": cfg["fetch"]["user_agent"],
        "Accept-Language": "ja,en;q=0.8",
        "Accept": "*/*",
    }
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, headers=headers)
    timeout = cfg["fetch"].get("timeout_sec", 20)
    with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()) as resp:
        raw = resp.read()
    if as_bytes:
        return raw
    # 文字コードはUTF-8前提、ダメなら寛容にデコード
    return raw.decode("utf-8", errors="replace")


def to_iso(d):
    """datetime → ISO文字列(UTC) / None → None"""
    if d is None:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.astimezone(dt.timezone.utc).isoformat()


def parse_rfc822(s):
    try:
        return parsedate_to_datetime(s)
    except Exception:
        return None


def parse_iso(s):
    if not s:
        return None
    s = s.strip()
    try:
        # PythonはZ付きを直接読めない版があるので置換
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def strip_html(s):
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


# ===========================================================================
#  収集源パーサ（type別）  — 1ソース失敗しても全体は止めない
# ===========================================================================
def fetch_atom(src, cfg):
    """GitHub Releases などのAtomフィード。"""
    items = []
    xml = http_get(src["url"], cfg)
    root = ET.fromstring(xml)
    for entry in root.findall("a:entry", ATOM_NS):
        title = text_of(entry.find("a:title", ATOM_NS))
        link_el = entry.find("a:link", ATOM_NS)
        url = link_el.get("href") if link_el is not None else ""
        updated = parse_iso(text_of(entry.find("a:updated", ATOM_NS)))
        content = text_of(entry.find("a:content", ATOM_NS))
        items.append(_mk_item(src, title, url, updated, strip_html(content)))
    return items


def fetch_changelog(src, cfg):
    """CHANGELOG.md を `## x.y.z` 見出し単位で分割。
    ★バージョン番号らしき見出しのみ採用し、アンカーリンクを生成する。"""
    items = []
    md = http_get(src["url"], cfg)
    html_base = src.get("html_base", "")
    # 見出し（##, # どちらも許容）で分割
    blocks = re.split(r"(?m)^#{1,3}\s+", md)
    for blk in blocks:
        if not blk.strip():
            continue
        first_line, _, body = blk.partition("\n")
        heading = first_line.strip()
        # バージョン番号らしき見出しのみ（例: 1.2.3 / v1.2 / 1.2）
        m = re.match(r"^v?(\d+\.\d+(?:\.\d+)?)", heading)
        if not m:
            continue
        version = m.group(1)
        anchor = re.sub(r"[^\w.-]+", "", version.replace(".", ""))
        url = f"{html_base}#{anchor}" if html_base else src["url"]
        summary = strip_html(body).strip()
        title = f"CHANGELOG {version}"
        # CHANGELOGは日付を持たないことが多い → published None（末尾寄せ）
        items.append(_mk_item(src, title, url, None, summary[:600]))
    return items


def fetch_googlenews(src, cfg):
    """Googleニュース RSS。<item> の title/link/pubDate/description/source。"""
    items = []
    xml = http_get(src["url"], cfg)
    root = ET.fromstring(xml)
    channel = root.find("channel")
    if channel is None:
        return items
    for it in channel.findall("item"):
        title = text_of(it.find("title"))
        link = text_of(it.find("link"))
        pub = parse_rfc822(text_of(it.find("pubDate")))
        desc = strip_html(text_of(it.find("description")))
        src_el = it.find("source")
        media = text_of(src_el) if src_el is not None else ""
        # Googleニュースのtitleは「記事名 - 媒体名」形式が多いので媒体名を分離
        clean_title = title
        if " - " in title and media and title.endswith(media):
            clean_title = title[: -(len(media) + 3)].strip()
        item = _mk_item(src, clean_title, link, pub, desc)
        if media:
            item["media"] = media
        items.append(item)
    return items


def fetch_reddit(src, cfg):
    """Reddit r/ClaudeAI。JSONが403になる環境(社内IP等)もあるためRSSへフォールバック。"""
    # 1) まずJSON（情報量が多い: スコア等）
    try:
        raw = http_get(src["url"], cfg)
        data = json.loads(raw)
        items = []
        for ch in data.get("data", {}).get("children", []):
            d = ch.get("data", {})
            permalink = d.get("permalink", "")
            url = "https://www.reddit.com" + permalink if permalink else d.get("url", "")
            created = d.get("created_utc")
            pub = dt.datetime.fromtimestamp(created, dt.timezone.utc) if created else None
            item = _mk_item(src, d.get("title", ""), url, pub, strip_html(d.get("selftext", ""))[:500])
            item["score_ext"] = d.get("score", 0)
            items.append(item)
        if items:
            return items
    except Exception as e:
        log(f"    [info] Reddit JSON失敗→RSSにフォールバック: {e}")
    # 2) RSS（Atom形式）フォールバック
    rss_url = src.get("rss_url") or "https://www.reddit.com/r/ClaudeAI/search.rss?q=claude%20code&restrict_sr=1&sort=new"
    xml = http_get(rss_url, cfg)
    root = ET.fromstring(xml)
    items = []
    for entry in root.findall("a:entry", ATOM_NS):
        title = text_of(entry.find("a:title", ATOM_NS))
        link_el = entry.find("a:link", ATOM_NS)
        url = link_el.get("href") if link_el is not None else ""
        pub = parse_iso(text_of(entry.find("a:updated", ATOM_NS)) or text_of(entry.find("a:published", ATOM_NS)))
        items.append(_mk_item(src, title, url, pub, ""))
    return items


def fetch_hn(src, cfg):
    """Hacker News (Algolia API)。"""
    items = []
    raw = http_get(src["url"], cfg)
    data = json.loads(raw)
    for hit in data.get("hits", []):
        title = hit.get("title") or hit.get("story_title") or ""
        url = hit.get("url") or hit.get("story_url") or ""
        if not url:
            oid = hit.get("objectID")
            url = f"https://news.ycombinator.com/item?id={oid}" if oid else ""
        pub = parse_iso(hit.get("created_at"))
        item = _mk_item(src, title, url, pub, "")
        item["score_ext"] = hit.get("points", 0)
        items.append(item)
    return items


def fetch_youtube(src, cfg):
    """YouTube検索結果ページを軽くスクレイプ。
    ★ ytInitialData の "videoId":"(11文字)" と "title":{"runs":[{"text":...}]} を拾う。"""
    items = []
    page = http_get(src["url"], cfg)
    m = re.search(r"var ytInitialData\s*=\s*(\{.*?\});</script>", page, re.S)
    if not m:
        m = re.search(r"ytInitialData\"\]\s*=\s*(\{.*?\});", page, re.S)
    seen = set()
    if m:
        try:
            data = json.loads(m.group(1))
            for vr in _iter_video_renderers(data):
                vid = vr.get("videoId")
                if not vid or vid in seen:
                    continue
                seen.add(vid)
                runs = vr.get("title", {}).get("runs", [])
                title = "".join(r.get("text", "") for r in runs)
                if not title:
                    continue
                url = f"https://www.youtube.com/watch?v={vid}"
                # チャンネル名
                ch = ""
                owner = vr.get("ownerText", {}).get("runs", [])
                if owner:
                    ch = owner[0].get("text", "")
                item = _mk_item(src, title, url, None, "")
                if ch:
                    item["media"] = ch
                items.append(item)
        except Exception as e:
            log(f"  [warn] youtube parse: {e}")
    # フォールバック: 正規表現で videoId と title を直接拾う
    if not items:
        for vm in re.finditer(r'"videoId":"([\w-]{11})".*?"title":\{"runs":\[\{"text":"(.*?)"', page):
            vid, title = vm.group(1), vm.group(2)
            if vid in seen:
                continue
            seen.add(vid)
            title = title.encode().decode("unicode_escape", errors="replace")
            items.append(_mk_item(src, title, f"https://www.youtube.com/watch?v={vid}", None, ""))
    return items


def _iter_video_renderers(obj):
    """ネストしたytInitialDataから videoRenderer を再帰探索。"""
    if isinstance(obj, dict):
        if "videoRenderer" in obj and isinstance(obj["videoRenderer"], dict):
            yield obj["videoRenderer"]
        for v in obj.values():
            yield from _iter_video_renderers(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _iter_video_renderers(v)


def fetch_rsshub(src, cfg):
    """RSSHub経由のX(Twitter)等。RSS互換として読む。不安定なので失敗時はスキップ。"""
    return fetch_googlenews(src, cfg)


def fetch_qiita(src, cfg):
    """Qiita API v2 の全文検索（JSON配列）。日本語の活用術記事を直接取得する。
    src['url'] 例: https://qiita.com/api/v2/items?query=Claude%20Code&per_page=25
    ※未認証は60req/h。失敗時は collect_all がスキップ。"""
    items = []
    data = json.loads(http_get(src["url"], cfg))
    if not isinstance(data, list):
        return items
    for it in data:
        title = it.get("title", "")
        link = it.get("url", "")
        pub = parse_iso(it.get("created_at"))
        summary = strip_html(it.get("rendered_body", "") or it.get("body", ""))[:600]
        item = _mk_item(src, title, link, pub, summary)
        likes = it.get("likes_count")
        if likes is not None:
            item["media"] = f"Qiita ♥{likes}"
            item["score_ext"] = likes   # いいね数を人気度として役立ち度スコアに反映
        items.append(item)
    return items


def fetch_note(src, cfg):
    """note の検索API(v3, JSON)。日本語の活用術記事を直接取得する。
    src['url'] 例: https://note.com/api/v3/searches?context=note&q=Claude%20Code&size=20&start=0
    ※非公式APIのため構造変化に備えて防御的に読む。読めなければ0件（=スキップ扱い）。"""
    items = []
    data = json.loads(http_get(src["url"], cfg))
    notes = (((data or {}).get("data") or {}).get("notes") or {}).get("contents")
    if not isinstance(notes, list):
        return items
    for n in notes:
        title = n.get("name") or n.get("title") or ""
        key = n.get("key") or ""
        urlname = (n.get("user") or {}).get("urlname") or ""
        link = n.get("note_url") or (f"https://note.com/{urlname}/n/{key}" if urlname and key else "")
        if not link:
            continue
        pub = parse_iso(n.get("publish_at") or n.get("publishAt") or "")
        summary = strip_html(n.get("body") or n.get("description") or "")[:600]
        item = _mk_item(src, title, link, pub, summary)
        item["media"] = "note"
        items.append(item)
    return items


def fetch_hatena(src, cfg):
    """はてなブックマークの検索フィード(RSS1.0/RDF)。サイトを問わず日本語の技術記事を横断的に拾う
    （Qiita/Zenn/noteに限らず、個人ブログ・企業テックブログ等もまとめて集約される）。
    src['url'] 例: https://b.hatena.ne.jp/search/text?q=Claude%20Code&mode=rss&sort=recent&users=1"""
    items = []
    xml = http_get(src["url"], cfg)
    root = ET.fromstring(xml)
    ns = {"rss": "http://purl.org/rss/1.0/", "dc": "http://purl.org/dc/elements/1.1/"}
    # RSS1.0(RDF)では <item> は RDF直下の兄弟要素。名前空間付きで全item検索。
    for it in root.findall(".//rss:item", ns):
        title = text_of(it.find("rss:title", ns))
        link = text_of(it.find("rss:link", ns))
        pub = parse_iso(text_of(it.find("dc:date", ns)))
        desc = strip_html(text_of(it.find("rss:description", ns)))
        items.append(_mk_item(src, title, link, pub, desc))
    return items


FETCHERS = {
    "atom": fetch_atom,
    "changelog": fetch_changelog,
    "googlenews": fetch_googlenews,
    "rss": fetch_googlenews,          # 汎用RSS2.0（Zennトピックフィード等）
    "reddit": fetch_reddit,
    "hn": fetch_hn,
    "youtube": fetch_youtube,
    "rsshub": fetch_rsshub,
    "qiita": fetch_qiita,
    "note": fetch_note,
    "hatena": fetch_hatena,           # はてブ検索＝日本語サイト横断の活用術ネット
}


def _mk_item(src, title, url, published, summary):
    return {
        "title": (title or "").strip() or "（無題）",
        "title_orig": (title or "").strip(),
        "title_ja": "",
        "url": (url or "").strip(),
        "source_id": src["id"],
        "source": src["name"],
        "source_short": src.get("short_name", src["name"]),
        "kind": src["kind"],
        "published": to_iso(published),
        "summary": summary or "",
        "summary_ja": "",
        "theme": "",
        "score": 0,
        "media": "",
        "score_ext": 0,
        "recommended": False,
    }


# ===========================================================================
#  分類・スコアリング
# ===========================================================================
def classify_theme(item, themes):
    """2軸分類のうち theme（何の話か）をキーワードで判定。MECE: 必ずどれかに入る。"""
    hay = (item["title_orig"] + " " + item["summary"]).lower()
    best_theme, best_hits = "その他", 0
    # 「その他」以外を優先評価
    for theme, conf in themes.items():
        if theme == "その他":
            continue
        hits = sum(1 for kw in conf["keywords"] if kw.lower() in hay)
        if hits > best_hits:
            best_theme, best_hits = theme, hits
    # 公式リリースは本質的に「新機能・更新」。Tips語が偶発混入しても更新扱いに寄せる。
    if item["kind"] == "公式リリース" and best_theme in ("その他", "使い方・Tips"):
        best_theme = "新機能・更新"
    return best_theme


VERSION_ONLY_RE = re.compile(r"^(?:changelog\s+)?v?\d+\.\d+(?:\.\d+)?\s*$", re.I)


def is_version_only(item):
    """『1.2.3』のようにバージョン番号だけのリリースか。おすすめでは弱める。"""
    t = item["title_orig"].strip()
    return bool(VERSION_ONLY_RE.match(t))


def score_item(item, cfg):
    themes = cfg["themes"]
    prof = cfg["interest_profile"]
    hay = (item["title_orig"] + " " + item["summary"]).lower()
    score = 0
    # テーマ由来
    score += themes.get(item["theme"], {}).get("score", 0)
    # 公式リリースボーナス
    if item["kind"] == "公式リリース":
        score += prof.get("release_bonus", 0)
    # 注目語
    for kw in prof.get("notable_terms", []):
        if kw.lower() in hay:
            score += prof.get("notable_score", 0)
    # バグ系は減点
    for kw in prof.get("bug_terms", []):
        if kw.lower() in hay:
            score -= prof.get("bug_penalty", 0)
            break
    # バージョン番号だけのリリースは減点
    if is_version_only(item):
        score -= prof.get("version_only_penalty", 0)
    # コミュニティの外部スコア（Reddit/HNのupvote）を軽く反映
    ext = item.get("score_ext", 0) or 0
    if ext >= 50:
        score += 2
    elif ext >= 10:
        score += 1
    item["score"] = score
    return score


def pick_recommendations(items, cfg):
    """読者=Claude Codeを使うチームのリーダー/PM 向けにTOPを抽出。
    新機能から最大N + Tipsから最大M + 残りをスコア上位で埋める。"""
    rc = cfg["recommend"]
    total = rc.get("total", 10)
    from_new = rc.get("from_new", 4)
    from_tips = rc.get("from_tips", 4)
    max_ver = rc.get("max_version_only", 3)

    # おすすめ枠から除外するkind（既定: 公式リリース＝英語・長文のため）。一覧には残る。
    # exclude_themes: おすすめTOPに入れないテーマ（システム開発は別枠扱いでTOPから外す）。
    exclude_kinds = set(rc.get("exclude_kinds", []))
    exclude_themes = set(rc.get("exclude_themes", []))
    pool_items = [x for x in items
                  if x["kind"] not in exclude_kinds and x["theme"] not in exclude_themes]
    by_score = sorted(pool_items, key=lambda x: x["score"], reverse=True)
    chosen, chosen_urls = [], set()
    ver_count = [0]  # クロージャから更新するためリストで保持

    def take(pool, limit):
        n = 0
        for it in pool:
            if n >= limit:
                break
            if it["url"] in chosen_urls or it["score"] <= 0:
                continue
            # ★バージョン番号だけのリリースはおすすめ枠を専有しないよう上限を設ける
            if is_version_only(it):
                if ver_count[0] >= max_ver:
                    continue
                ver_count[0] += 1
            chosen.append(it)
            chosen_urls.add(it["url"])
            n += 1

    take([x for x in by_score if x["theme"] == "新機能・更新"], from_new)
    take([x for x in by_score if x["theme"] == "使い方・Tips"], from_tips)
    # 残りをスコア上位で埋める
    for it in by_score:
        if len(chosen) >= total:
            break
        if it["url"] in chosen_urls or it["score"] <= 0:
            continue
        if is_version_only(it) and ver_count[0] >= max_ver:
            continue
        if is_version_only(it):
            ver_count[0] += 1
        chosen.append(it)
        chosen_urls.add(it["url"])

    chosen.sort(key=lambda x: x["score"], reverse=True)
    for it in chosen:
        it["recommended"] = True
    return chosen


# ===========================================================================
#  AI翻訳・要約（任意。ANTHROPIC_API_KEY があるときだけ）
# ===========================================================================
def has_japanese(s):
    return bool(re.search(r"[ぁ-んァ-ヶ一-龠]", s or ""))


def ai_enrich(items, cfg):
    """英語記事に日本語タイトル+200字要約を付与。1バッチ10件、JSON配列で一括取得。
    失敗時は元のタイトルにフォールバック（処理は止めない）。"""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    ai = cfg.get("ai", {})
    if not ai.get("enabled") or not api_key:
        log("  [info] AI翻訳・要約はスキップ（ANTHROPIC_API_KEY未設定 or 無効）")
        return
    targets = [it for it in items if not has_japanese(it["title_orig"])][: ai.get("max_items_to_enrich", 80)]
    if not targets:
        return
    log(f"  [info] AI翻訳・要約: {len(targets)}件を処理")
    batch = ai.get("batch_size", 10)
    chars = ai.get("summary_chars", 200)
    for i in range(0, len(targets), batch):
        chunk = targets[i : i + batch]
        try:
            _ai_call_batch(chunk, cfg, api_key, chars)
        except Exception as e:
            log(f"  [warn] AIバッチ失敗（フォールバック）: {e}")
        time.sleep(0.4)


def _ai_call_batch(chunk, cfg, api_key, chars):
    ai = cfg["ai"]
    themes = list(cfg["themes"].keys())
    payload_items = [
        {"i": idx, "title": it["title_orig"][:300], "summary": it["summary"][:600], "kind": it["kind"]}
        for idx, it in enumerate(chunk)
    ]
    prompt = (
        "あなたはClaude Codeの技術ニュースを日本語で要約する編集者です。"
        "各記事について、日本語タイトル(title_ja)と" + str(chars) + "字程度の日本語要約(summary_ja)、"
        "そして次のテーマのどれか1つ(theme)を返してください: " + " / ".join(themes) + "。\n"
        "出力は必ずJSON配列のみ。各要素は {\"i\":番号, \"title_ja\":\"...\", \"summary_ja\":\"...\", \"theme\":\"...\"}。\n"
        "前置きや```は不要。\n\n入力:\n" + json.dumps(payload_items, ensure_ascii=False)
    )
    body = json.dumps(
        {
            "model": ai["model"],
            "max_tokens": 2000,
            "messages": [{"role": "user", "content": prompt}],
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        ai["endpoint"],
        data=body,
        headers={
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": ai["anthropic_version"],
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=cfg["fetch"]["timeout_sec"] + 30, context=ssl_context()) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    arr = _extract_json_array(text)
    for obj in arr:
        idx = obj.get("i")
        if not isinstance(idx, int) or idx < 0 or idx >= len(chunk):
            continue
        it = chunk[idx]
        if obj.get("title_ja"):
            it["title_ja"] = obj["title_ja"].strip()
        if obj.get("summary_ja"):
            it["summary_ja"] = obj["summary_ja"].strip()
        if obj.get("theme") in cfg["themes"]:
            it["theme"] = obj["theme"]


def _extract_json_array(text):
    text = text.strip()
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        return json.loads(text[start : end + 1])
    except Exception:
        return []


# ===========================================================================
#  翻訳プロバイダの振り分け
# ===========================================================================
def enrich(items, cfg):
    """provider に応じて翻訳・要約を実施。
    google_free=無料(キー不要) / anthropic=高品質要約(要APIキー・有料) / off=なし"""
    provider = cfg.get("translate", {}).get("provider", "google_free")
    if provider == "off":
        log("  [info] 翻訳はオフ（config.translate.provider=off）")
        return
    if provider == "anthropic":
        ai_enrich(items, cfg)  # 有料・高品質要約
        return
    translate_free_enrich(items, cfg)  # 既定: 無料翻訳


def translate_free_enrich(items, cfg):
    """無料翻訳（Google無料エンドポイント→失敗時MyMemory）で英語記事を日本語化。
    タイトルを title_ja に、要約スニペットを summary_ja に入れる（原題は title_orig に保持）。"""
    tr = cfg.get("translate", {})
    max_items = tr.get("max_items", 80)
    do_summary = tr.get("translate_summary", True)
    targets = [it for it in items if not has_japanese(it["title_orig"])][:max_items]
    if not targets:
        log("  [info] 翻訳対象の英語記事なし")
        return
    log(f"  [info] 無料翻訳: {len(targets)}件を処理（Google無料→MyMemoryフォールバック）")
    cache, ok = {}, 0
    for it in targets:
        ja = _translate_cached(it["title_orig"], cache, cfg)
        if ja:
            it["title_ja"] = ja
            ok += 1
        if do_summary and it["summary"]:
            sja = _translate_cached(it["summary"][:480], cache, cfg)
            if sja:
                it["summary_ja"] = sja
        time.sleep(0.25)  # レート制限回避のため軽く間隔をあける
    log(f"  [info] 翻訳成功: {ok}/{len(targets)} 件")


def _translate_cached(text, cache, cfg):
    text = (text or "").strip()
    if not text:
        return ""
    if text in cache:
        return cache[text]
    out = _google_translate(text, cfg) or _mymemory_translate(text, cfg) or ""
    cache[text] = out
    return out


def _google_translate(text, cfg):
    """Google翻訳の無料エンドポイント（非公式・キー不要）。en/auto→ja。"""
    try:
        url = ("https://translate.googleapis.com/translate_a/single"
               "?client=gtx&sl=auto&tl=ja&dt=t&q=" + urllib.parse.quote(text))
        data = json.loads(http_get(url, cfg))
        return "".join(seg[0] for seg in data[0] if seg and seg[0]).strip()
    except Exception:
        return ""


def _mymemory_translate(text, cfg):
    """MyMemory翻訳API（無料・キー不要・匿名1日5000語程度）。フォールバック用。"""
    try:
        url = ("https://api.mymemory.translated.net/get?langpair=en|ja&q="
               + urllib.parse.quote(text[:480]))
        data = json.loads(http_get(url, cfg))
        return (data.get("responseData", {}).get("translatedText", "") or "").strip()
    except Exception:
        return ""


# ===========================================================================
#  並べ替え・重複排除
# ===========================================================================
def dedupe(items):
    """URL単位で重複排除（先勝ち）。さらに『同一タイトル＋媒体』も重複とみなす。
    ★Googleニュースは検索クエリ毎にリダイレクトURLが変わり、同じ記事が別URLになるため、
      複数の検索ソースをまたいだ同一記事の二重掲載をこの第2キーで防ぐ。"""
    seen_url, seen_title, out = set(), set(), []
    for it in items:
        ukey = it["url"] or (it["source_id"] + "|" + it["title_orig"])
        tkey = ((it.get("title_orig") or "").strip().lower(), (it.get("media") or "").strip().lower())
        if ukey in seen_url:
            continue
        if tkey[0] and tkey in seen_title:
            continue
        seen_url.add(ukey)
        if tkey[0]:
            seen_title.add(tkey)
        out.append(it)
    return out


def sort_items(items):
    """★published有無を第1キー、日付を第2キーで降順。日付不明は末尾。"""
    def keyf(it):
        p = parse_iso(it["published"])
        has = 1 if p is not None else 0
        ts = p.timestamp() if p is not None else 0
        return (has, ts)

    return sorted(items, key=keyf, reverse=True)


def mark_new(items, recent_days):
    now = dt.datetime.now(dt.timezone.utc)
    for it in items:
        p = parse_iso(it["published"])
        it["is_new"] = bool(p and (now - p).days < recent_days)


# ===========================================================================
#  出力（data.json / index.html / .nojekyll / history.json）
# ===========================================================================
def display_title(it):
    return it["title_ja"] or it["title"]


def write_outputs(items, recommended, cfg):
    os.makedirs(DOCS_DIR, exist_ok=True)
    os.makedirs(DATA_DIR, exist_ok=True)
    generated = dt.datetime.now(dt.timezone.utc).astimezone().isoformat()

    data = {
        "generated_at": generated,
        "site_title": cfg["site_title"],
        "public_url": cfg["public_url"],
        "recent_days_new": cfg.get("recent_days_new", 7),
        "themes": list(cfg["themes"].keys()),
        "kinds": sorted({it["kind"] for it in items}),
        "recommended_urls": [it["url"] for it in recommended],
        "count": len(items),
        "items": items,
    }
    with open(os.path.join(DOCS_DIR, "data.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)

    with open(os.path.join(DOCS_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(render_html(cfg))

    # GitHub PagesでJekyll処理を無効化（_で始まるファイル等の取りこぼし防止）
    open(os.path.join(DOCS_DIR, ".nojekyll"), "w").close()

    # 既読/履歴管理（収集済みURLの蓄積。実際の既読判定はブラウザのlocalStorage）
    history = {}
    if os.path.exists(HISTORY_PATH):
        try:
            with open(HISTORY_PATH, encoding="utf-8") as f:
                history = json.load(f)
        except Exception:
            history = {}
    seen = history.get("seen_urls", {})
    today = dt.date.today().isoformat()
    for it in items:
        seen.setdefault(it["url"], today)
    history["seen_urls"] = seen
    history["last_run"] = generated
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=1)

    log(f"  [ok] docs/data.json ({len(items)}件) / docs/index.html / data/history.json を出力")


def render_html(cfg):
    """認証なし静的サイト。data.json をfetchしてJSで描画する。"""
    title = html.escape(cfg["site_title"])
    return HTML_TEMPLATE.replace("__SITE_TITLE__", title)


# ===========================================================================
#  メール送信（任意。Gmail SMTP / STARTTLS / 587）
# ===========================================================================
def jst_today_str():
    """JST(日本時間)の今日の日付 YYYY-MM-DD。マーカーの基準はJSTで統一。"""
    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=9)).strftime("%Y-%m-%d")


def already_sent_today():
    """重複防止マーカーが本日(JST)になっているか。読めなければ未送信扱い。"""
    try:
        with open(LAST_SENT_PATH, encoding="utf-8") as f:
            return f.read().strip() == jst_today_str()
    except Exception:
        return False


def mark_sent_today():
    """送信成功後に本日(JST)を刻む。クラウドではこの後ワークフローがcommit/pushして共有する。"""
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(LAST_SENT_PATH, "w", encoding="utf-8") as f:
        f.write(jst_today_str() + "\n")


def send_email(recommended, items, cfg, force=False):
    em = cfg.get("email", {})
    user = os.environ.get("CC_RADAR_GMAIL_USER")
    passwd = os.environ.get("CC_RADAR_GMAIL_PASS")
    if not em.get("enabled") or not user or not passwd:
        log("  [info] メール送信スキップ（CC_RADAR_GMAIL_USER/PASS未設定 or 無効）")
        return
    # ★多重cron対策（atomic claim方式）:
    #   CIでは workflow の「本日の送信権を取得」ステップが、git push の成否で“本日の送信担当”を
    #   ただ1回だけに確定し、その結果を環境変数 CC_RADAR_SEND(yes/no/force) で渡す。ここでは従うだけ。
    #   （複数cronが同時に束ねて発火しても、push に成功した1回しか yes にならないのでレースで二重送信しない）
    #   環境変数が無い場合（ローカル実行）は従来のファイルマーカー方式で判定する。
    send_env = os.environ.get("CC_RADAR_SEND")
    if send_env is not None:
        if send_env.strip().lower() not in ("yes", "force"):
            log(f"  [info] この回は本日の送信担当ではないためスキップ（CC_RADAR_SEND={send_env}）")
            return
    elif not force and already_sent_today():
        log(f"  [info] 本日({jst_today_str()})は送信済みのためスキップ（多重cronの重複防止）")
        return
    # 宛先: 送信元(自分)を必ず含め、config.email.to と 環境変数 CC_RADAR_MAIL_TO を追加。
    # ★追加宛先(会社アドレス等)は公開リポジトリに載せないよう Secret(CC_RADAR_MAIL_TO) 推奨。
    to_cfg = em.get("to", [])
    if isinstance(to_cfg, str):
        cfg_list = [a.strip() for a in to_cfg.split(",") if a.strip()]
    else:
        cfg_list = [a for a in to_cfg if a]
    env_list = [a.strip() for a in os.environ.get("CC_RADAR_MAIL_TO", "").split(",") if a.strip()]
    recipients = []
    for a in [user] + cfg_list + env_list:
        if a and a not in recipients:
            recipients.append(a)

    top_n = em.get("top_n", 5)
    today = dt.date.today().strftime("%Y-%m-%d")
    subject = f'{em.get("subject_prefix", "[cc-radar]")} Claude Code 最新情報 {today}'

    body = _email_html(recommended[:top_n], items, cfg)
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(_email_text(recommended[:top_n], items, cfg), "plain", "utf-8"))
    msg.attach(MIMEText(body, "html", "utf-8"))

    try:
        ctx = ssl_context()
        with smtplib.SMTP(em["smtp_host"], em["smtp_port"], timeout=30) as server:
            server.starttls(context=ctx)
            server.login(user, passwd)
            server.sendmail(user, recipients, msg.as_string())
        log(f"  [ok] メール送信完了 → {len(recipients)}件の宛先: {', '.join(recipients)}")
        # CIでは workflow が送信権取得時に既にマーカーをpush済み → ここでは触らない。
        # ローカル実行（CC_RADAR_SEND 環境変数なし）のときだけ従来どおりマーカーを刻む。
        if os.environ.get("CC_RADAR_SEND") is None:
            mark_sent_today()  # ★成功時のみマーカーを刻む（同日2通目以降を抑止）
    except Exception as e:
        log(f"  [warn] メール送信失敗: {e}")


THEME_COLOR = {
    "新機能・更新": "#00A08E",
    "使い方・Tips": "#0A7A5C",
    "事例・体験談": "#FFC000",
    "周辺・エコシステム": "#0A7A5C",
    "その他": "#7a8a86",
}


def email_reldate(iso):
    p = parse_iso(iso)
    if p is None:
        return ""
    now = dt.datetime.now(dt.timezone.utc)
    days = (now - p.astimezone(dt.timezone.utc)).days
    if days <= 0:
        return "今日"
    if days == 1:
        return "昨日"
    if days < 7:
        return f"{days}日前"
    loc = p.astimezone()
    return f"{loc.month}/{loc.day}"


def _category_groups(recommended, items, cfg):
    """TOP5に出した記事を除き、テーマ別にまとめる。(theme, total件数, 表示分リスト) のリストを返す。
    email.category_themes が指定されていればそのテーマだけ・その順で出す（『使い方』中心の絞り込み）。
    email.exclude_kinds の種別（バージョンだけの公式リリース等）はメール一覧から除外する。"""
    shown = {it["url"] for it in recommended}
    em = cfg.get("email", {})
    per_max = em.get("per_theme_max", 8)
    only_themes = em.get("category_themes") or list(cfg["themes"].keys())
    skip_kinds = set(em.get("exclude_kinds", []))
    def usefulness_key(it):
        # 役立ち度(score)を最優先、同点は新しい順。
        p = parse_iso(it.get("published"))
        return (it.get("score", 0), 1 if p else 0, p.timestamp() if p else 0)

    groups = []
    for theme in only_themes:
        if theme not in cfg["themes"]:
            continue
        bucket = [it for it in items
                  if it["theme"] == theme
                  and it["url"] not in shown
                  and it["kind"] not in skip_kinds]
        if bucket:
            # ★メールのカテゴリ一覧は「役に立ちそうな順」に並べる（日付順ではなくscore順）。
            bucket.sort(key=usefulness_key, reverse=True)
            groups.append((theme, len(bucket), bucket[:per_max]))
    return groups


def _email_text(recommended, items, cfg):
    lines = ["Claude Code 最新情報 (cc-radar)", "", "■ 本日のおすすめ"]
    for i, it in enumerate(recommended, 1):
        lines.append(f"{i}. [{it['theme']}] {display_title(it)}")
        lines.append(f"   {it['url']}")
    if cfg.get("email", {}).get("include_categories", True):
        for theme, total, lst in _category_groups(recommended, items, cfg):
            lines.append("")
            lines.append(f"■ {theme}（全{total}件）")
            for it in lst:
                d = email_reldate(it["published"])
                meta = " ".join(x for x in [it["source_short"], d] if x)
                lines.append(f"・{display_title(it)}（{meta}）")
                lines.append(f"   {it['url']}")
    lines.append("")
    lines.append(f"すべての記事: {cfg['public_url']}")
    return "\n".join(lines)


def _email_html(recommended, items, cfg):
    cards = []
    for i, it in enumerate(recommended, 1):
        color = THEME_COLOR.get(it["theme"], "#00A08E")
        summ = html.escape((it["summary_ja"] or it["summary"] or "")[:160])
        cards.append(
            f'<div style="margin:0 0 14px;padding:12px 14px;border-left:4px solid {color};background:#F1F8F6;border-radius:4px;">'
            f'<div style="font-size:12px;color:{color};font-weight:bold;">{html.escape(it["theme"])} ・ {html.escape(it["source_short"])}</div>'
            f'<a href="{html.escape(it["url"])}" style="color:#0A7A5C;font-weight:bold;font-size:15px;text-decoration:none;">{i}. {html.escape(display_title(it))}</a>'
            f'<div style="font-size:13px;color:#444;margin-top:4px;">{summ}</div>'
            f"</div>"
        )

    cats = ""
    if cfg.get("email", {}).get("include_categories", True):
        blocks = []
        for theme, total, lst in _category_groups(recommended, items, cfg):
            color = THEME_COLOR.get(theme, "#0A7A5C")
            rows = []
            for it in lst:
                d = email_reldate(it["published"])
                meta = " ・ ".join(x for x in [html.escape(it["source_short"]), html.escape(d)] if x)
                rows.append(
                    f'<li style="margin:0 0 7px;">'
                    f'<a href="{html.escape(it["url"])}" style="color:#0A7A5C;text-decoration:none;font-size:14px;">{html.escape(display_title(it))}</a>'
                    f'<span style="color:#888;font-size:12px;"> ・ {meta}</span>'
                    f"</li>"
                )
            blocks.append(
                f'<h3 style="color:{color};font-size:15px;margin:20px 0 8px;border-left:4px solid {color};padding-left:8px;">'
                f'{html.escape(theme)} <span style="color:#999;font-weight:normal;font-size:12px;">（全{total}件）</span></h3>'
                f'<ul style="margin:0;padding-left:18px;">{"".join(rows)}</ul>'
            )
        if blocks:
            cats = '<h2 style="color:#0A7A5C;font-size:17px;border-bottom:1px solid #d8e8e3;padding-bottom:4px;margin-top:26px;">テーマ別の一覧</h2>' + "".join(blocks)

    btn = (
        f'<a href="{html.escape(cfg["public_url"])}" '
        f'style="display:inline-block;background:#00A08E;color:#fff;padding:10px 20px;'
        f'border-radius:6px;text-decoration:none;font-weight:bold;">すべての記事を見る →</a>'
    )
    return (
        f'<div style="font-family:Meiryo,sans-serif;max-width:640px;margin:auto;color:#222;">'
        f'<h2 style="color:#00A08E;border-bottom:2px solid #FFC000;padding-bottom:6px;">Claude Code 最新情報</h2>'
        f'<p style="color:#666;font-size:13px;">本日のおすすめTOP{len(recommended)}＋テーマ別一覧（全{len(items)}件収集）</p>'
        + "".join(cards)
        + cats
        + f'<div style="margin-top:22px;text-align:center;">{btn}</div>'
        + '<p style="color:#999;font-size:11px;margin-top:20px;">— cc-radar 自動配信</p>'
        + "</div>"
    )


# ===========================================================================
#  メイン
# ===========================================================================
def collect_all(cfg):
    all_items = []
    for src in cfg["sources"]:
        if not src.get("enabled", True):
            continue
        fetcher = FETCHERS.get(src["type"])
        if not fetcher:
            log(f"  [skip] 未対応type: {src['type']} ({src['id']})")
            continue
        try:
            log(f"  - {src['name']} を取得中…")
            got = fetcher(src, cfg)
            got = got[: cfg.get("max_items_per_feed", 25)]
            all_items.extend(got)
            log(f"    → {len(got)}件")
        except Exception as e:
            # ★1ソース失敗で全体を止めない（X/RSSHub等の不安定ソース対策）
            log(f"    [warn] {src['name']} 取得失敗（スキップ）: {e}")
    return all_items


def main():
    args = set(sys.argv[1:])
    dry_run = "--dry-run" in args
    no_mail = "--no-mail" in args or dry_run
    force_mail = "--force-mail" in args  # 手動実行時: 本日送信済みでも再送する

    log("=" * 60)
    log("cc-radar : Claude Code 最新情報を収集します")
    log("=" * 60)

    if not os.path.exists(CONFIG_PATH):
        log(f"[error] config.json が見つかりません: {CONFIG_PATH}")
        sys.exit(1)
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)

    # 1) 収集
    log("[1/5] 収集源から取得")
    items = collect_all(cfg)
    items = dedupe(items)
    log(f"  重複排除後: {len(items)}件")

    # 2) 分類（AIなし時の暫定。AIありなら後で上書きされ得る）
    log("[2/5] 分類（kind × theme）")
    for it in items:
        if not it["theme"]:
            it["theme"] = classify_theme(it, cfg["themes"])

    # 3) AI翻訳・要約（任意）
    log("[3/5] 翻訳・要約")
    enrich(items, cfg)
    # AIでthemeが付かなかったものを補完
    for it in items:
        if not it["theme"]:
            it["theme"] = classify_theme(it, cfg["themes"])

    # 4) スコア・並べ替え・おすすめ
    log("[4/5] スコアリング・並べ替え")
    for it in items:
        score_item(it, cfg)
    items = sort_items(items)
    mark_new(items, cfg.get("recent_days_new", 7))
    recommended = pick_recommendations(items, cfg)
    log(f"  おすすめ: {len(recommended)}件")

    if dry_run:
        log("[dry-run] 出力なし。おすすめ一覧:")
        for i, it in enumerate(recommended, 1):
            log(f"  {i}. ({it['score']}pt)[{it['theme']}] {display_title(it)}")
            log(f"      {it['url']}")
        log(f"\n  収集合計: {len(items)}件")
        return

    # 5) 出力＋メール
    log("[5/5] HTML/JSON生成" + ("" if no_mail else " ＋ メール送信"))
    write_outputs(items, recommended, cfg)
    if not no_mail:
        send_email(recommended, items, cfg, force=force_mail)

    log("-" * 60)
    log(f"完了。公開URL: {cfg['public_url']}")
    log(f"ローカル確認: docs/index.html （GitHub Pages公開後はpublic_urlで閲覧）")


# ===========================================================================
#  HTML テンプレート（docs/index.html）
#  配色: 緑#00A08E / 薄緑#0A7A5C / 薄赤(背景)#F1F8F6 / 黄#FFC000 のみ
#  フォント: Meiryo UI / フィルタはselect3つ / 既読はlocalStorage
# ===========================================================================
HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__SITE_TITLE__</title>
<style>
  :root{
    --green:#00A08E; --green-d:#0A7A5C; --bg:#F1F8F6; --yellow:#FFC000;
    --ink:#222; --sub:#667; --line:#d8e8e3; --read:#9aa;
  }
  *{box-sizing:border-box}
  body{margin:0;background:#fff;color:var(--ink);
    font-family:"Meiryo UI","Meiryo",sans-serif;line-height:1.6;}
  a{color:var(--green-d);text-decoration:none}
  a:hover{text-decoration:underline}
  header{background:#fff;border-bottom:3px solid var(--yellow);
    padding:14px 18px;position:sticky;top:0;z-index:10;}
  header .bar{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;
    max-width:980px;margin:auto;}
  header h1{font-size:18px;margin:0;color:var(--green);}
  header .meta{font-size:12px;color:var(--sub);}
  .wrap{max-width:980px;margin:0 auto;padding:16px 18px 60px;}
  .controls{display:flex;gap:8px;flex-wrap:wrap;align-items:center;
    margin:14px 0;}
  .controls input[type=search]{flex:1;min-width:180px;padding:8px 10px;
    border:1px solid var(--line);border-radius:6px;font-size:14px;}
  .controls select{padding:8px 10px;border:1px solid var(--line);
    border-radius:6px;font-size:14px;background:#fff;}
  .controls button{padding:8px 12px;border:1px solid var(--green);
    background:#fff;color:var(--green);border-radius:6px;font-size:13px;
    cursor:pointer;}
  .controls button:hover{background:var(--bg);}
  .sec-title{font-size:15px;font-weight:bold;color:var(--green-d);
    margin:22px 0 8px;border-left:5px solid var(--green);padding-left:8px;}
  .sec-title.reco{border-left-color:var(--yellow);}
  .card{border:1px solid var(--line);border-radius:8px;padding:12px 14px;
    margin:0 0 10px;background:#fff;transition:opacity .15s;}
  .card.reco{background:var(--bg);}
  .card.read{opacity:.5;}
  .card .top{display:flex;gap:8px;align-items:center;flex-wrap:wrap;
    margin-bottom:4px;}
  .badge{font-size:11px;font-weight:bold;color:#fff;padding:1px 8px;
    border-radius:10px;white-space:nowrap;}
  .badge.kind{background:var(--green);}
  .badge.theme{background:var(--green-d);}
  .badge.new{background:var(--yellow);color:#5a4500;}
  .card .title{font-size:15px;font-weight:bold;color:var(--green-d);
    display:block;margin:2px 0;word-break:break-word;}
  .card .summary{font-size:13px;color:#444;margin:4px 0;}
  .card .foot{display:flex;gap:10px;align-items:center;flex-wrap:wrap;
    font-size:12px;color:var(--sub);margin-top:6px;}
  .card .read-btn{margin-left:auto;border:1px solid var(--line);
    background:#fff;color:var(--sub);border-radius:5px;padding:3px 10px;
    font-size:12px;cursor:pointer;}
  .card .read-btn:hover{border-color:var(--green);color:var(--green);}
  .check{color:var(--green);font-weight:bold;}
  #more{display:block;margin:16px auto;padding:9px 22px;
    border:1px solid var(--green);background:#fff;color:var(--green);
    border-radius:6px;font-size:14px;cursor:pointer;}
  #more:hover{background:var(--bg);}
  .empty{color:var(--sub);padding:30px;text-align:center;}
  @media(max-width:640px){
    header h1{font-size:16px;}
    .controls input[type=search]{min-width:140px;}
    .controls select{flex:1;}
  }
</style>
</head>
<body>
<header>
  <div class="bar">
    <h1>📡 __SITE_TITLE__</h1>
    <span class="meta" id="meta">読み込み中…</span>
  </div>
</header>

<div class="wrap">
  <div class="controls">
    <input type="search" id="q" placeholder="キーワード検索（タイトル・要約・媒体）">
    <select id="f-theme"><option value="">テーマ：すべて</option></select>
    <select id="f-kind"><option value="">種類：すべて</option></select>
    <select id="f-read">
      <option value="">既読：すべて</option>
      <option value="unread">未読のみ</option>
      <option value="read">既読のみ</option>
    </select>
    <button id="mark-all">未読を一括既読</button>
  </div>

  <div id="reco-wrap">
    <div class="sec-title reco">⭐ 今日のおすすめ</div>
    <div id="reco"></div>
  </div>

  <div class="sec-title">すべての記事 <span id="all-count" style="font-weight:normal;color:var(--sub);font-size:13px;"></span></div>
  <div id="list"></div>
  <button id="more" style="display:none;">もっと見る</button>
  <div id="empty" class="empty" style="display:none;">該当する記事がありません。</div>
</div>

<script>
const PAGE = 50;
let DATA = null, shown = 0, filtered = [];

const LS_KEY = "cc-radar-read";
function readSet(){ try{ return new Set(JSON.parse(localStorage.getItem(LS_KEY)||"[]")); }catch(e){ return new Set(); } }
function saveRead(s){ localStorage.setItem(LS_KEY, JSON.stringify([...s])); }
let READ = readSet();

function isRead(u){ return READ.has(u); }
function setRead(u, val){
  if(val){ READ.add(u); } else { READ.delete(u); }
  saveRead(READ);
  // ★おすすめ枠と全部枠に同じURLが両方あるので、data-urlで全カードを同期
  document.querySelectorAll('[data-url="'+CSS.escape(u)+'"]').forEach(card=>{
    card.classList.toggle("read", val);
    const btn = card.querySelector(".read-btn");
    if(btn) btn.textContent = val ? "未読に戻す" : "既読にする";
  });
  updateMeta();
}

function relDate(iso){
  if(!iso) return "日付不明";
  const d = new Date(iso); if(isNaN(d)) return "日付不明";
  const now = new Date();
  const days = Math.floor((now - d)/86400000);
  if(days <= 0) return "今日";
  if(days === 1) return "昨日";
  if(days < 7) return days + "日前";
  return (d.getMonth()+1)+"/"+d.getDate();
}

function esc(s){ const d=document.createElement("div"); d.textContent=s==null?"":s; return d.innerHTML; }

function cardHTML(it, reco){
  const r = isRead(it.url);
  const title = it.title_ja || it.title;
  const summ = it.summary_ja || it.summary || "";
  const themeColor = { "新機能・更新":"#00A08E","使い方・Tips":"#0A7A5C","事例・体験談":"#FFC000","周辺・エコシステム":"#0A7A5C","その他":"#7a8a86" }[it.theme] || "#0A7A5C";
  let badges = '<span class="badge kind">'+esc(it.kind)+'</span>';
  badges += '<span class="badge theme" style="background:'+themeColor+'">'+esc(it.theme)+'</span>';
  if(it.is_new) badges += '<span class="badge new">NEW</span>';
  const orig = (it.title_ja && it.title_orig && it.title_ja!==it.title_orig)
      ? '<div style="font-size:11px;color:#99a;">'+esc(it.title_orig)+'</div>' : '';
  return ''
    + '<div class="card'+(reco?' reco':'')+(r?' read':'')+'" data-url="'+esc(it.url)+'">'
    +   '<div class="top">'+badges+'</div>'
    +   '<a class="title" href="'+esc(it.url)+'" target="_blank" rel="noopener" '
    +       'onclick="setRead(this.closest(\'.card\').dataset.url,true)">'+esc(title)+'</a>'
    +   orig
    +   (summ ? '<div class="summary">'+esc(summ)+'</div>' : '')
    +   '<div class="foot">'
    +     '<span>'+relDate(it.published)+'</span>'
    +     '<span>・'+esc(it.source_short||it.source)+(it.media?(' / '+esc(it.media)):'')+'</span>'
    +     '<button class="read-btn" onclick="toggleRead(this)">'+(r?'未読に戻す':'既読にする')+'</button>'
    +   '</div>'
    + '</div>';
}

function toggleRead(btn){
  const url = btn.closest(".card").dataset.url;
  setRead(url, !isRead(url));
}

function applyFilter(){
  const q = document.getElementById("q").value.trim().toLowerCase();
  const ft = document.getElementById("f-theme").value;
  const fk = document.getElementById("f-kind").value;
  const fr = document.getElementById("f-read").value;
  filtered = DATA.items.filter(it=>{
    if(ft && it.theme !== ft) return false;
    if(fk && it.kind !== fk) return false;
    if(fr==="read" && !isRead(it.url)) return false;
    if(fr==="unread" && isRead(it.url)) return false;
    if(q){
      const hay = ((it.title_ja||"")+" "+(it.title||"")+" "+(it.title_orig||"")+" "+(it.summary_ja||"")+" "+(it.summary||"")+" "+(it.media||"")+" "+(it.source||"")).toLowerCase();
      if(!hay.includes(q)) return false;
    }
    return true;
  });
  shown = 0;
  document.getElementById("list").innerHTML = "";
  renderMore();
}

function renderMore(){
  const list = document.getElementById("list");
  const slice = filtered.slice(shown, shown+PAGE);
  list.insertAdjacentHTML("beforeend", slice.map(it=>cardHTML(it,false)).join(""));
  shown += slice.length;
  document.getElementById("more").style.display = (shown < filtered.length) ? "block" : "none";
  document.getElementById("empty").style.display = (filtered.length===0) ? "block" : "none";
  document.getElementById("all-count").textContent = "（"+filtered.length+"件）";
}

function updateMeta(){
  if(!DATA) return;
  const unread = DATA.items.filter(it=>!isRead(it.url)).length;
  const gen = new Date(DATA.generated_at);
  const genStr = isNaN(gen)? "" : (gen.getMonth()+1)+"/"+gen.getDate()+" "+String(gen.getHours()).padStart(2,"0")+":"+String(gen.getMinutes()).padStart(2,"0");
  document.getElementById("meta").textContent =
    "最終更新 "+genStr+" ・ 全"+DATA.items.length+"件 ・ 未読"+unread+"件";
}

function fillSelect(id, values, label){
  const sel = document.getElementById(id);
  values.forEach(v=>{ const o=document.createElement("option"); o.value=v; o.textContent=v; sel.appendChild(o); });
}

function init(data){
  DATA = data;
  fillSelect("f-theme", data.themes||[]);
  fillSelect("f-kind", data.kinds||[]);
  // おすすめ
  const recoUrls = new Set(data.recommended_urls||[]);
  const recoItems = (data.recommended_urls||[]).map(u=>data.items.find(it=>it.url===u)).filter(Boolean);
  const recoBox = document.getElementById("reco");
  if(recoItems.length){
    recoBox.innerHTML = recoItems.map(it=>cardHTML(it,true)).join("");
  }else{
    document.getElementById("reco-wrap").style.display="none";
  }
  updateMeta();
  applyFilter();
  ["q","f-theme","f-kind","f-read"].forEach(id=>{
    document.getElementById(id).addEventListener("input", applyFilter);
  });
  document.getElementById("more").addEventListener("click", renderMore);
  document.getElementById("mark-all").addEventListener("click", ()=>{
    DATA.items.forEach(it=>READ.add(it.url));
    saveRead(READ);
    document.querySelectorAll(".card").forEach(c=>{
      c.classList.add("read");
      const b=c.querySelector(".read-btn"); if(b) b.textContent="未読に戻す";
    });
    updateMeta();
  });
}

fetch("data.json?_="+Date.now())
  .then(r=>r.json())
  .then(init)
  .catch(e=>{
    document.getElementById("meta").textContent="data.json を読み込めませんでした";
    document.getElementById("empty").style.display="block";
    document.getElementById("empty").textContent="data.json を読み込めません。GitHub Pages公開後、またはローカルサーバ(python -m http.server)経由で開いてください。";
  });
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
