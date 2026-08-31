#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 frontend/ 組回單一檔案 zackllmgui.html。

    python build.py            # 需要時才重組
    python build.py --force    # 一定重組
    python build.py --check    # 只檢查是不是最新的（CI 用，過期回傳碼 1）

為什麼是「組回單一檔案」而不是 <script src>：
單一檔案能直接用 file:// 打開、能整份複製給別人、不需要任何伺服器 ——
那是這個專案的賣點之一。拆成多個檔案只是為了好維護，
不該把使用者那一端的性質也一起改掉。

serve.py 啟動時會自己呼叫這裡，所以平常不必手動跑。
把 frontend/ 刪掉也不影響 —— serve.py 找不到就直接用現成的 zackllmgui.html。
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "frontend"
OUT = HERE / "zackllmgui.html"
STYLE_MARK = "/*{{STYLE}}*/\n"
SCRIPT_MARK = "/*{{SCRIPT}}*/\n"


def sources() -> list:
    """所有來源檔，順序就是串接的順序（js 依檔名排序，所以編號是有意義的）。"""
    return [SRC / "index.html", SRC / "style.css"] + sorted((SRC / "js").glob("*.js"))


def render() -> str:
    index = (SRC / "index.html").read_text(encoding="utf-8")
    css = (SRC / "style.css").read_text(encoding="utf-8")
    js = "".join(f.read_text(encoding="utf-8") for f in sorted((SRC / "js").glob("*.js")))
    for mark in (STYLE_MARK, SCRIPT_MARK):
        if mark not in index:
            raise SystemExit(f"frontend/index.html 少了 {mark.strip()} 標記")
    return index.replace(STYLE_MARK, css).replace(SCRIPT_MARK, js)


def stale() -> bool:
    """產出內容與來源不同就要重組；不依賴下載／解壓後不可靠的 mtime。"""
    if not SRC.is_dir():
        return False
    if not OUT.exists():
        return True
    return OUT.read_text(encoding="utf-8") != render()


def build(force: bool = False) -> bool:
    """需要時重組。回傳有沒有真的寫檔。"""
    if not SRC.is_dir():
        return False
    if not force and not stale():
        return False
    page = render()
    if OUT.exists() and OUT.read_text(encoding="utf-8") == page:
        OUT.touch()                     # 內容一樣就只更新時間，避免每次啟動都寫檔
        return False
    OUT.write_text(page, encoding="utf-8")
    return True


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if "--check" in sys.argv:
        if stale():
            print("zackllmgui.html 比 frontend/ 舊，請跑 python build.py")
            return 1
        print("zackllmgui.html 是最新的")
        return 0
    if build("--force" in sys.argv):
        lines = OUT.read_text(encoding="utf-8").count("\n") + 1
        print(f"已組出 {OUT.name}（{lines:,} 行 / {OUT.stat().st_size / 1024:.0f} KB）")
    else:
        print(f"{OUT.name} 已是最新，沒有動它")
    return 0


if __name__ == "__main__":
    sys.exit(main())
