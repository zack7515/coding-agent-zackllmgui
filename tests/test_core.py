# -*- coding: utf-8 -*-
"""core/ 各模組的介面測試。

跟 test_serve.py 的分工：那一份走 HTTP，驗的是「整條路通不通」；
這一份直接呼叫，驗的是**拆出去時新長出來的那些參數**。
repo_map(files, rel)、skills_usable(have)、skill_live(body, run, allowed, build)
這幾個介面是為了不讓 core 反過來 import serve 才有的，只有 serve.py 的
薄殼在用 —— 沒有人直接驗過它們，換句話說那幾個參數傳錯了也不會有測試變紅。

    python -m pytest tests/test_core.py -q
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import cmdrisk, repomap, skills, workspace   # noqa: E402


# ── repo_map：拆出去之後改成收 (files, rel) ────────────────────────── #

def test_repo_map_takes_files_and_rel_from_outside():
    """地圖不自己走檔案系統，走訪與轉相對路徑都由呼叫端給。

    這是拆出去時改的介面：原本它直接用 ws_walk()／ws_rel()，
    那樣 repomap 就要認識工作區。現在傳什麼就畫什麼。
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "calc.py").write_text(
            "def add(a, b):\n    return a + b\n\n\nclass Sum:\n    pass\n",
            encoding="utf-8")
        (root / "app.js").write_text(
            "function boot() {}\nconst run = () => {};\nconst PORT = 8080;\n",
            encoding="utf-8")
        (root / "notes.txt").write_text("沒有符號可抓\n", encoding="utf-8")

        files = sorted(root.iterdir())
        out = repomap.repo_map(files, lambda p: str(p.relative_to(root)))

        assert "calc.py：add, Sum" in out, out
        # 抓的是「可以呼叫的東西」：箭頭函式算，純數值不算
        assert "app.js：boot, run" in out, out
        assert "PORT" not in out, "常數值不該進地圖，那是雜訊"
        assert "notes.txt" in out and "notes.txt：" not in out, "抓不到符號就只列檔名"
        assert repomap.repo_map([], lambda p: "") == "", "沒有檔案就不要生一份空標題出來"


def test_repo_map_cache_key_includes_size():
    """同一格 mtime 內改兩次也要重算。

    ext4 上連著兩次寫入連 st_mtime_ns 都可能一樣，只有大小不同。
    快取鍵少了大小，模型連下兩次 edit_file 之後拿到的是舊地圖。
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        f = root / "m.py"
        rel = lambda p: str(p.relative_to(root))       # noqa: E731

        f.write_text("def one():\n    pass\n", encoding="utf-8")
        first = repomap.repo_map([f], rel)
        assert "one" in first

        st = f.stat()
        f.write_text("def one():\n    pass\n\n\ndef two():\n    pass\n", encoding="utf-8")
        os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns))   # 時間戳硬設成一樣
        assert f.stat().st_mtime_ns == st.st_mtime_ns, "前提沒成立，這個測試就沒在驗東西"

        again = repomap.repo_map([f], rel)
        assert "two" in again, "mtime 沒變就不重算 —— 大小沒進快取鍵"


# ── skills：借工具清單、借指令執行 ──────────────────────────────── #

def test_skills_usable_filters_by_the_tool_names_it_is_given():
    """用得動的那幾份是拿「現在有哪些工具」篩出來的，不是 skills 自己去問。

    篩掉的條件只看**認得的**工具名：MCP 那些不在 TOOL_SCHEMAS 裡，
    不能因為它們沒開就把一份 skill 判死。
    """
    every = skills.skills_list()
    assert every, "專案裡的 skills 一份都沒讀到"

    every_name = {s["name"] for s in skills.skills_usable({t for s in every for t in s["tools"]})}
    assert every_name == {s["name"] for s in every}, "工具全開時應該一份都不篩掉"

    # 一支工具都沒有：只留下沒有要求工具的那幾份
    none = {s["name"] for s in skills.skills_usable(set())}
    assert none == {s["name"] for s in every if not s["tools"]}, none

    # 認不得的工具名不算數
    fake = skills.skills_usable({"mcp__whatever"})
    assert {s["name"] for s in fake} == none, "沒開的 MCP 工具不該影響篩選"


def test_skill_live_runs_commands_through_the_callbacks_it_is_handed():
    """!`指令` 的放行與執行都是外面給的，skills 自己不碰 shell。

    這兩個 callback 就是拆出去時長出來的介面：allowed 決定准不准，
    build 決定怎麼跑。skills 只負責找出要跑什麼、把輸出貼回去。
    """
    body = "先看狀態：\n\n!`git status`\n\n再看分支：\n\n!`git branch`\n"
    asked, ran = [], []

    def allowed(cmd):
        asked.append(cmd)
        return "" if cmd == "git status" else "這條不在白名單裡"

    def build(name, args):
        ran.append(args["command"])
        return (["printf", "MARKER-OUT"], None, False, "")

    with tempfile.TemporaryDirectory() as tmp:
        workspace.SESSIONS[""].ws = Path(tmp)
        try:
            out = skills.skill_live(body, True, allowed, build)
            assert asked == ["git status", "git branch"], asked
            assert ran == ["git status"], "被擋下來的指令不能跑到 build"
            assert "MARKER-OUT" in out, out
            assert "這條不在白名單裡" in out, "擋下來的理由要寫進去，不能安靜跳過"

            # run=False：一條都不跑，而且要講明為什麼
            asked.clear()
            ran.clear()
            out = skills.skill_live(body, False, allowed, build)
            assert ran == [] and asked == [], "run=False 還在跑指令"
            assert "不代跑指令" in out, out
        finally:
            workspace.SESSIONS[""].ws = None


# ── cmdrisk：純函式，沒有任何外部相依 ─────────────────────────────── #

def test_command_risk_is_pure():
    """指令風險判斷不碰檔案系統也不碰工作區 —— 拆出去的第一個理由。"""
    assert cmdrisk.command_risk("ls -la")[0] == "ok"
    assert cmdrisk.command_risk("rm -rf /")[0] == "block"
    assert cmdrisk.command_risk("ls && rm -rf ~")[0] == "block", "分隔符後面也要看"
    assert cmdrisk.canon("  git   status  ") == "git status"


# ── workspace：安全邊界 ─────────────────────────────────────────── #

def test_ws_path_is_the_only_boundary():
    """路徑逃逸、機密檔案、封鎖目錄都在這一支擋掉。"""
    import pytest
    with tempfile.TemporaryDirectory() as tmp:
        workspace.SESSIONS[""].ws = Path(tmp)
        try:
            (Path(tmp) / "ok.py").write_text("x = 1\n", encoding="utf-8")
            assert workspace.ws_path("ok.py").name == "ok.py"
            for bad in ("../outside.py", "~/secret", ".env", ".git/config",
                        ".zackllmgui-rules.json"):
                with pytest.raises((PermissionError, FileNotFoundError)):
                    workspace.ws_path(bad, must_exist=False)
        finally:
            workspace.SESSIONS[""].ws = None
