# -*- coding: utf-8 -*-
"""專案地圖：有哪些檔案、每個檔案裡有什麼，先算好放進系統提示。

模型每接到一個任務都要先摸清專案：list_dir、search_files、read_file 來回三五輪，
而每一輪的成本是**重吃一次整份 context**。那幾輪買到的東西是固定的，所以先給它。
aider 的 repo map 是同一個想法，這裡刻意做得更小：只列頂層符號，不排呼叫關係
（那要 tree-sitter，排錯了比沒有更糟）。

這段只能放在對話最前面而且中途不要變 —— 動它等於放棄 Ollama 的 prefix cache。

ponytail: 只認得 Python（ast）與 JS/TS（一條正規表示式），其他語言只列檔名。
          要加語言就往 file_symbols() 加一個分支。
"""

import ast
import re
from pathlib import Path

MAP_LIMIT = 6000           # 字元上限。這段每一輪都要重送，跟 skill 清單一樣是固定成本
MAP_FILES = 400            # 掃到這麼多檔就停
MAP_SYMS = 30              # 一個檔案最多列幾個符號（真正的煞車是 MAP_LIMIT）
MAP_BYTES = 400_000        # 比這個大的檔案不解析（解析成本不值得）
_MAP_CACHE = {}            # 檔案 -> (mtime, 符號字串)。只有改過的檔案要重解析

JS_SYM = re.compile(r"^(?:export\s+)?(?:default\s+)?(?:async\s+)?"
                    r"(?:function\s+(\w+)|class\s+(\w+)"
                    r"|(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?[(<])", re.M)


def file_symbols(p: Path) -> str:
    """一個檔案裡有哪些頂層符號。回傳空字串＝只列檔名就好。"""
    try:
        if p.stat().st_size > MAP_BYTES:
            return ""
        if p.suffix == ".py":
            tree = ast.parse(p.read_text("utf-8", errors="replace"), str(p))
            names = [n.name for n in tree.body
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
        elif p.suffix in (".js", ".mjs", ".ts", ".jsx", ".tsx"):
            names = [m.group(1) or m.group(2) or m.group(3)
                     for m in JS_SYM.finditer(p.read_text("utf-8", errors="replace"))]
        else:
            return ""
    except (SyntaxError, ValueError, OSError, RecursionError):
        return ""            # 解析不動就當作沒有符號，檔名照樣列得出來
    if not names:
        return ""
    more = f"…（共 {len(names)} 個）" if len(names) > MAP_SYMS else ""
    return ", ".join(names[:MAP_SYMS]) + more


def repo_map(files, rel) -> str:
    """files 是要列進來的路徑，rel 把它轉成顯示用的相對路徑。"""
    lines, n, cut = [], 0, False
    for f in files:
        n += 1
        if n > MAP_FILES:
            cut = True
            break
        try:
            st = f.stat()
            key = (st.st_mtime_ns, st.st_size)
        except OSError:
            continue
        # 大小要一起當鍵：mtime 有顆粒度，同一格裡改兩次（模型連著兩次
        # edit_file）拿到同一個值，ext4 上連 st_mtime_ns 都一樣
        hit = _MAP_CACHE.get(f)
        if not hit or hit[0] != key:
            hit = (key, file_symbols(f))
            _MAP_CACHE[f] = hit
        lines.append(rel(f) + ("：" + hit[1] if hit[1] else ""))
    if not lines:
        return ""
    body = "\n".join(lines)
    if len(body) > MAP_LIMIT:
        body = body[:MAP_LIMIT].rsplit("\n", 1)[0]
        cut = True
    return ("專案地圖（冒號後面是那個檔案裡的頂層符號，要看內容還是要 read_file）：\n"
            + body + ("\n…（只列出一部分，其餘用 search_files 找）" if cut else ""))
