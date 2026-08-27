#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""驗證 skills/ 底下的每一份 SKILL.md。

    python tests/test_skills.py                 # 驗 skills/
    python tests/test_skills.py 別的資料夾       # 驗自己的

用途是「把寫好的 skill 丟進資料夾，跑一下看支不支援」。最有用的一項檢查是
`tools:` 列出來的工具在這個版本裡到底存不存在 —— 那是「支不支援」的真正意思。

格式的規格在 skills/README.md，這支是它的可執行版本。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import serve

DESC_LIMIT = 200        # 每一輪都會送出，所以短
BODY_LIMIT = 8000       # 超過就該拆成兩個 skill
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def est_tokens(text: str) -> int:
    """跟介面上的用量條同一套估法：CJK 一字 1 token、其餘四字元 1 token。"""
    cjk = len(re.findall(r"[　-鿿＀-￯]", text))
    return cjk + max(0, len(text) - cjk) // 4


def parse_skill(md: str) -> tuple:
    """回傳 (中繼資料, 正文)。沒有 frontmatter 就丟 ValueError。"""
    if not md.startswith("---"):
        raise ValueError("開頭要有 --- 夾起來的中繼資料")
    end = md.find("\n---", 3)
    if end < 0:
        raise ValueError("中繼資料沒有結尾的 ---")
    meta = {}
    for line in md[3:end].strip().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            raise ValueError(f"中繼資料這一行看不懂：{line.strip()[:40]}")
        k, v = line.split(":", 1)
        meta[k.strip()] = v.strip()
    return meta, md[end + 4:].strip()


def known_tools() -> list:
    """這個版本有哪些工具。含要開工作區、要開寫入的那些，skill 只是引用名字。"""
    names = [t["name"] for t in serve.TOOL_SCHEMAS]
    return sorted(set(names) | {"ask_user_question", "load_skill"})


def check_one(folder: Path, tools: list) -> tuple:
    """回傳 (ok, 訊息, 描述字數)。"""
    md_file = folder / "SKILL.md"
    if not md_file.is_file():
        return False, "沒有 SKILL.md", 0
    try:
        meta, body = parse_skill(md_file.read_text("utf-8", errors="replace"))
    except ValueError as e:
        return False, str(e), 0

    problems = []
    name = meta.get("name", "")
    if not name:
        problems.append("缺 name")
    elif not NAME_RE.match(name):
        problems.append(f"name「{name}」只能用小寫英數與 -")
    elif name != folder.name:
        problems.append(f"name「{name}」跟資料夾名稱「{folder.name}」不一樣")

    desc = meta.get("description", "")
    if not desc:
        problems.append("缺 description —— 模型只憑這一行決定要不要載入")
    elif len(desc) > DESC_LIMIT:
        problems.append(f"description {len(desc)} 字，超過 {DESC_LIMIT}")
    elif "\n" in desc:
        problems.append("description 要寫成一行")

    want = [t.strip() for t in meta.get("tools", "").split(",") if t.strip()]
    missing = [t for t in want if t not in tools]
    if missing:
        problems.append(f"tools 裡的 {'、'.join(missing)} 不存在"
                        f"（可用的有：{'、'.join(tools)}）")

    if not body:
        problems.append("正文是空的")
    elif len(body) > BODY_LIMIT:
        problems.append(f"正文 {len(body)} 字，超過 {BODY_LIMIT} —— 拆成兩個 skill")

    # 正文引用到的檔案要真的在
    for label, rel in re.findall(r"\[([^\]]*)\]\(([^)]+)\)", body):
        if rel.startswith(("http://", "https://", "#", "..")):
            continue
        if not (folder / rel).exists():
            problems.append(f"正文引用的 {rel} 不存在")

    if problems:
        return False, "；".join(problems), len(desc)
    return True, f"{len(want)} 個工具 · 描述 {len(desc)} 字 · 正文 {len(body):,} 字", len(desc)


def main() -> int:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "skills"
    if not target.is_dir():
        print(f"找不到資料夾 {target}")
        return 1

    folders = sorted(d for d in target.iterdir()
                     if d.is_dir() and not d.name.startswith("_"))
    if not folders:
        print(f"{target} 底下還沒有 skill。複製 _template 一份開始寫，"
              f"格式見 skills/README.md")
        return 0

    tools = known_tools()
    ok_count = desc_total = 0
    width = max(len(d.name) for d in folders)
    for folder in folders:
        ok, note, desc_len = check_one(folder, tools)
        print(("✅ " if ok else "❌ ") + folder.name.ljust(width + 2) + note)
        ok_count += ok
        desc_total += desc_len

    print(f"\n{ok_count} 個可用" +
          (f"、{len(folders) - ok_count} 個有問題" if ok_count < len(folders) else ""))
    print(f"描述總量 {desc_total} 字 ≈ {est_tokens('中' * desc_total)} tokens"
          f"（每一輪都會送出）")
    if desc_total > 3000:
        print("⚠ 描述加起來太多了，考慮把不常用的移出 skills/")
    return 0 if ok_count == len(folders) else 1


if __name__ == "__main__":
    sys.exit(main())
