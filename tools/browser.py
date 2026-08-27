#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""連網瀏覽：搜尋、開頁、跟著連結走。

`fetch_url` 只能抓一個你已經知道網址的頁面。這一支補的是**不知道網址的時候**：
先搜尋拿到候選、開一頁、看到連結再往下走 —— 也就是人在瀏覽器裡做的事。

只用標準函式庫。搜尋走 DuckDuckGo 的 HTML 版本（不需要 API key），
它擋人的時候會明講，不會回一頁看不懂的東西。

    python tools/browser.py search "python pathlib resolve"
    python tools/browser.py open https://example.com

這個模組刻意不 import serve —— 它跟工作區、跟權限都沒有關係，
可以單獨拿去用，也單獨測得起來。開放與否由 serve.py 的 ALLOW_BROWSER 決定。
"""

from __future__ import annotations

import html as html_mod
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
TIMEOUT = 20
MAX_BYTES = 2 * 1024 * 1024        # 再大就不是給模型讀的
MAX_TEXT = 6000                    # 回給模型的正文上限（字元）
MAX_LINKS = 30
SEARCH_URL = "https://html.duckduckgo.com/html/?q="

# 這些協定不接受。file:// 會讀到本機檔案，繞過整個工作區限制。
OK_SCHEMES = ("http", "https")


def _get(url: str) -> tuple:
    """抓一個網址，回傳 (最終網址, 內文)。跟著轉址走。"""
    parts = urllib.parse.urlparse(url)
    if parts.scheme not in OK_SCHEMES:
        raise ValueError(f"只接受 http / https，不接受 {parts.scheme or '（沒有協定）'}")
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9",
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
            raw = res.read(MAX_BYTES)
            charset = res.headers.get_content_charset() or "utf-8"
            return res.geturl(), raw.decode(charset, "replace")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} {e.reason}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"連不上：{e.reason}") from None


def to_text(doc: str) -> str:
    """HTML 轉純文字。script / style / nav / footer 先丟掉再說。"""
    doc = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", doc)
    doc = re.sub(r"(?is)<(nav|footer|header|aside)[^>]*>.*?</\1>", " ", doc)
    doc = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</h[1-6]>", "\n", doc)
    doc = re.sub(r"<[^>]+>", " ", doc)
    doc = html_mod.unescape(doc)
    doc = re.sub(r"[ \t ]{2,}", " ", doc)
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", doc).strip()


def page_title(doc: str) -> str:
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", doc)
    return html_mod.unescape(m.group(1)).strip()[:200] if m else ""


def links_of(doc: str, base: str, limit: int = MAX_LINKS) -> list:
    """頁面上的連結。同一個網址只留一次，錨點文字太短的丟掉。

    連結是這支工具存在的理由 —— 沒有它，模型讀完一頁就走不下去了。
    """
    out, seen = [], set()
    for m in re.finditer(r'(?is)<a\s[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', doc):
        href = urllib.parse.urljoin(base, m.group(1).strip())
        if urllib.parse.urlparse(href).scheme not in OK_SCHEMES:
            continue
        text = re.sub(r"\s+", " ", html_mod.unescape(re.sub(r"<[^>]+>", " ", m.group(2)))).strip()
        if len(text) < 2 or href in seen:
            continue
        seen.add(href)
        out.append({"text": text[:120], "url": href})
        if len(out) >= limit:
            break
    return out


def search(query: str, limit: int = 10) -> list:
    """搜尋，回傳 [{title, url, snippet}]。"""
    q = str(query or "").strip()
    if not q:
        raise ValueError("query 是空的")
    _, doc = _get(SEARCH_URL + urllib.parse.quote_plus(q))
    out = []
    for m in re.finditer(
            r'(?is)<a[^>]+class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>'
            r'(?:.*?class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>)?', doc):
        url = html_mod.unescape(m.group(1))
        # DuckDuckGo 會把真正的網址包在 /l/?uddg=… 裡
        if "uddg=" in url:
            url = urllib.parse.unquote(re.search(r"uddg=([^&]+)", url).group(1))
        strip = lambda x: re.sub(r"\s+", " ", html_mod.unescape(
            re.sub(r"<[^>]+>", "", x or ""))).strip()
        out.append({"title": strip(m.group(2))[:200], "url": url,
                    "snippet": strip(m.group(3))[:300]})
        if len(out) >= limit:
            break
    if not out and "anomaly" in doc.lower():
        raise RuntimeError("搜尋服務暫時擋下了這個請求（太頻繁），等一下再試")
    return out


def open_page(url: str, limit: int = MAX_LINKS) -> dict:
    final, doc = _get(url)
    text = to_text(doc)
    return {
        "url": final,
        "title": page_title(doc),
        "text": text[:MAX_TEXT] + ("\n…（內容過長，已截斷）" if len(text) > MAX_TEXT else ""),
        "links": links_of(doc, final, limit),
    }


def run(action: str = "open", url: str = "", query: str = "", limit: int = 10) -> str:
    """給模型呼叫的入口。回傳純文字，因為工具結果就是塞回對話裡的一段字。"""
    act = str(action or "open").strip().lower()
    if act == "search":
        hits = search(query, min(int(limit or 10), 20))
        if not hits:
            return f"「{query}」沒有搜到東西。換個關鍵字，或直接給我網址用 open。"
        lines = [f"「{query}」的搜尋結果："]
        for i, h in enumerate(hits, 1):
            lines.append(f"{i}. {h['title']}\n   {h['url']}"
                         + (f"\n   {h['snippet']}" if h["snippet"] else ""))
        lines.append("\n要看哪一個就用 run_browser(action=\"open\", url=…)。")
        return "\n".join(lines)

    if act == "open":
        if not url:
            raise ValueError("open 要給 url")
        page = open_page(url, min(int(limit or MAX_LINKS), MAX_LINKS))
        parts = [f"# {page['title'] or page['url']}", page["url"], "", page["text"]]
        if page["links"]:
            parts.append("\n## 這一頁上的連結")
            parts += [f"- {l['text']} → {l['url']}" for l in page["links"]]
        return "\n".join(parts)

    raise ValueError(f"action 只接受 search 或 open，不是 {action}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__.strip().splitlines()[0] + "\n\n" +
                 "  python tools/browser.py search <關鍵字>\n"
                 "  python tools/browser.py open <網址>")
    act = sys.argv[1]
    arg = " ".join(sys.argv[2:])
    print(run(act, url=arg if act == "open" else "", query=arg if act == "search" else ""))
