#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 zackllmgui.html 端出來，並把 /api/* 轉給 Ollama。

為什麼需要它：瀏覽器直接開 HTML 檔（file://）去打另一台的 Ollama 會被 CORS 擋，
除非在 Ollama 那台設 OLLAMA_ORIGINS。這支讓網頁和 API 變成同一個來源，
CORS 就完全不存在了。

只用 Python 標準函式庫，不需要安裝任何東西。

用法：
    python serve.py                                   # 連本機 Ollama
    python serve.py --ollama http://192.168.1.20:11434
    python serve.py --ollama 192.168.1.20:11434 --port 8080
    python serve.py --host 0.0.0.0                    # 讓同網段其他裝置也能開
    python serve.py --no-tools                        # 關掉本機工具（預設是開著的）

同時提供給網頁的幾支小 API：
    GET  /upstream   回報真正的 Ollama 位址與這支服務的能力
    POST /extract    PDF / Word 轉文字
    POST /tool       執行本機工具（預設關閉，網頁可開關，只接受本機）
    POST /tools      開關工具與檔案修改
    POST /workspace  設定模型可以讀寫的專案資料夾
    POST /preview    算出寫入前的 diff（不寫入）
    POST /restore    還原一份備份
    POST /view       讀一個檔案給介面顯示（可附上與備份的 diff）
    *    /ext        轉送到 X-Target 指定的外部 OpenAI 相容 API（只接受本機）
"""

from __future__ import annotations

import argparse
import ast
import collections
import contextlib
import difflib
import fnmatch
import io
import ipaddress
import json
import os
import re
import shlex
import shutil
import select
import socket
import subprocess
import tempfile
import time
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))          # tools/ 跟 serve.py 放在一起

import sandbox
from core import repomap, restore, sysinfo, workspace
from core.agents import (AGENTS_DIR, SUB_DEPTH_MAX, agent_chain, agent_close,
                         agent_guard, agent_open, agent_stop, agent_trace,
                         agent_types, agent_view, agents_roots, as_agent,
                         bind_agent, git_at, worktree_orphans)
from core.jobs import (BG_MAX, BG_TIMEOUT, BG_WAIT, BG_WAIT_MAX, JOBS, JOBS_LOCK,
                       _job_tail, _start_job, decode_output, jobs_state, kill_tree,
                       process_group_kwargs, tail_of)
from core import skills as _skills
from core.skills import (SKILL_CMD, SKILL_CMD_MAX, SKILL_DESC_MAX,
                         SKILL_LIST_MAX, SKILLS_DIR, parse_skill, skill_body,
                         skill_commands, skill_find, skill_trusted,
                         skills_list, skills_roots)
from core.mcp import (MCP, MCP_CONFIG, mcp_call, mcp_config_path, mcp_load,
                      mcp_start, mcp_status, mcp_stop, mcp_tool_defs, mcps)
from core.rules import (RULES_FILE, rule_match, rules_files, rules_load,
                        rules_path, rules_save)
from core.restore import (backup_file, checkpoint, journal_add, journal_for,
                          journal_path, journal_read, restore_backup,
                          rewind_to, ws_is_git)
from core.workspace import (BACKUP_DIR, DENY_DIRS, DENY_FILES, MAX_FILE_BYTES,
                            SESSIONS, SESSIONS_LOCK, SESSIONS_MAX, Session,
                            WORKTREE_DIR, WORKTREE_LINK, WORKTREE_MAX, WORKTREE_SKIP,
                            _CUR, cur, session_for, ws_langs, ws_missing_tools,
                            ws_path, ws_rel, ws_root, ws_walk)
from core.cmdrisk import RISKY_CMDS, canon, command_risk
from core.extract import extract_text
from tools import browser
from tools.schemas import TOOL_SCHEMAS
PAGE = HERE / "zackllmgui.html"

# 網頁的原始碼在 frontend/，build.py 把它組成單一檔案。
# 只有 frontend/ 存在時才需要它 —— 只複製 serve.py + zackllmgui.html 出去照樣能跑。
try:
    import build as page_build
except Exception:
    page_build = None

# Ollama 產生回應可能很久，別讓代理先斷線
UPSTREAM_TIMEOUT = 900
CHUNK = 8192
MAX_UPLOAD = 32 * 1024 * 1024      # 檔案解析的上限，避免有人把記憶體灌爆
TOOL_OUTPUT_LIMIT = 8000           # 工具結果塞回模型前先截斷，別把 context 撐爆
SHELL_TIMEOUT = 30
ALLOW_TOOLS = True                 # 預設開著；--no-tools 關掉，網頁上也隨時能切

AT_FILE_CAP = 3000                 # 輸入框打 @ 時最多列幾個檔案
# 專案自己的說明檔。收常見的通用檔名，找到第一個就用。
AGENT_FILES = ("AGENTS.md", "GROK.md", ".cursorrules")
PROJECT_MD_LIMIT = 6000


# 計畫模式住在 Session.plan["on"]：工作區、修改權限、自動模式、待辦、MCP 都跟著
# 分頁走，這一個沒跟的話，A 分頁打開計畫模式會把 B 分頁的寫入工具一起收走。
TRUST_REMOTE = False               # --trust-remote：非本機的瀏覽器也能開工具與設工作區
# 連網瀏覽（搜尋 + 開頁 + 跟連結走）。預設關著：它會讓模型主動連出去，
# 那跟「讀本機檔案」是不同性質的權限，該由使用者自己按下去。
ALLOW_BROWSER = False

# 沙盒：把 run_shell / run_tests / setup_env 關進跑不出工作區的地方。
# run_shell 是唯一跑得出去的工具 —— 檔案工具有 ws_path() 擋著，它沒有。
# 預設關：要先裝 bubblewrap／docker，而這個專案的賣點是零相依。

# 自動模式（off／read／edit／full／ws）。後端**不用它決定放不放行** ——
# 那一層在 autoApprove() 與 rule_match()。這裡只決定系統提示怎麼寫：
# 「每一次呼叫都會先讓使用者確認」那句話在自動模式下是假的。
AUTO_MODES = ("off", "read", "edit", "full", "ws")
ALLOW_SANDBOX = False

SANDBOX_BACKEND = ""               # 空的＝照 sandbox/ 的偏好順序自己挑
# 容器後端的映像檔。預設那個（python:3.13-slim）沒有編譯器，C/C++ 要自己換。
# 只從命令列給：換映像檔等於換掉沙盒裡的整個世界。
SANDBOX_IMAGE = ""
SANDBOX_GPU = False                 # 容器需額外 runtime；不無條件開，避免整個沙盒起不來
SEARCH_HITS = 80
TEST_TIMEOUT = 900
STREAM_TOOLS = {"run_shell", "run_tests"}   # 這兩支走 /run 串流，其他工具沒必要
RING_LINES = 2000                  # 串流時最多留這麼多行回灌給模型
# 輸出也要有上限，不是只有時間上限：`yes`、`find /`、跑歪的測試都會在逾時之前
# 先把瀏覽器灌爆。超過就砍掉程序，並且把原因寫進回給模型的結果裡。
MAX_RUN_BYTES = 2 * 1024 * 1024
MAX_LINE_CHARS = 4000              # 單行上限。minified JS 或 base64 一行就好幾 MB

TODO_FILE = ".zackllmgui-todos.md"



def ollama_is_local() -> bool:
    """--ollama 指到的位址是不是這台機器。

    num_thread 是套用在跑 Ollama 的那一台，所以只有兩者同機時，
    os.cpu_count() 才是那個參數的真上限。不是比字串而已 ——
    --ollama http://192.168.1.20:11434 指回自己也算本機。
    """
    host = urllib.parse.urlparse(Handler.ollama).hostname or ""
    if host in ("localhost", "127.0.0.1", "::1"):
        return True
    try:
        theirs = {ai[4][0] for ai in socket.getaddrinfo(host, None)}
        mine = {ai[4][0] for ai in socket.getaddrinfo(socket.gethostname(), None)}
        mine |= {"127.0.0.1", "::1"}
        return bool(theirs & mine)
    except OSError:
        return False


def normalize(host: str) -> str:
    host = (host or "").strip().rstrip("/")
    if not host:
        return "http://localhost:11434"
    if not host.startswith(("http://", "https://")):
        host = "http://" + host
    return host


# ══════════════════════ 工作區 ══════════════════════ #

# ── 自己的程式碼被改過沒有 ─────────────────────────────────────── #
# 網頁每次重整都是新的（build.py 每次重讀 frontend/），Python 卻凍在啟動那一刻。
# 「頁面是新的、serve.py 是舊的」這個組合已經害過兩次：一次是網頁送了新的開關
# 而舊的 serve.py 靜靜忽略，一次是沙盒的 GPU 修好了但跑著的行程還是舊模組。
# 所以記一份啟動時的狀態，網頁定期問一次，改過了就自己重開。
SRC_GLOBS = ("serve.py", "tools/*.py", "sandbox/*.py")


# ponytail: 比 mtime 不比內容雜湊。mtime 會被 touch 與 git checkout 誤觸，
#           但誤判的代價只是多重啟一次；真的嫌吵再換成讀檔算 sha1。
def source_stamp() -> str:
    bits = []
    for pattern in SRC_GLOBS:
        for f in sorted(HERE.glob(pattern)):
            try:
                bits.append(f"{f.name}:{f.stat().st_mtime_ns}")
            except OSError:
                pass
    return "|".join(bits)


SRC_STAMP = source_stamp()


def restart_self() -> None:
    """用現在的參數把自己換掉。Python 3.4+ 的 socket 預設 close-on-exec，
    所以聽著的那個 port 會在 exec 的瞬間放掉，新的行程綁得回來。"""
    os.execv(sys.executable, [sys.executable] + sys.argv)


def detect_python() -> list:
    """找出這個專案該用哪個 python 跑測試。順序：.venv → venv → uv → poetry → 系統。

    子代理的 worktree 裡先找自己的，找不到用**主 repo 的** —— `.venv` 不在版控裡，
    不會跟著 worktree 過去，不接這一段每個子代理都要重建一份一樣的環境。
    venv 的 site-packages 是絕對路徑，從哪個目錄跑都算數。
    """
    rec = getattr(_CUR, "agent", None)
    roots = [ws_root()]
    if rec and rec.get("root") and Path(rec["root"]) != roots[0]:
        roots.append(Path(rec["root"]))
    for root in roots:
        for name in (".venv", "venv"):
            for sub in ("bin/python", "Scripts/python.exe"):
                cand = root / name / sub
                if cand.exists():
                    return [str(cand)]
    root = roots[0]
    if (root / "uv.lock").exists() and shutil.which("uv"):
        return ["uv", "run", "python"]
    if (root / "poetry.lock").exists() and shutil.which("poetry"):
        return ["poetry", "run", "python"]
    return [sys.executable]


def project_md() -> tuple:
    """工作區根目錄的專案說明。回傳 (檔名, 內容)，沒有就是 ("", "")。"""
    if cur().ws is None:
        return ("", "")
    for name in AGENT_FILES:
        f = cur().ws / name
        if f.is_file():
            text = f.read_text("utf-8", errors="replace").strip()
            if len(text) > PROJECT_MD_LIMIT:
                text = text[:PROJECT_MD_LIMIT] + "\n…（專案說明過長，已截斷）"
            return (name, text)
    return ("", "")


TODO_LINE = re.compile(r"^\s*(?:\d+[.)]|[-*])\s*\[([ xX])\]\s*(.+?)\s*$")


def todo_file() -> Path | None:
    return None if cur().ws is None else ws_root() / TODO_FILE


def write_todo_file() -> None:
    """把待辦寫成工作區裡的一份 markdown。寫失敗不算錯 —— 這只是個鏡像。"""
    f = todo_file()
    if f is None:
        return
    try:
        if not cur().todos:
            if f.exists():
                f.unlink()
            cur().todo_mtime = 0.0
            return
        body = ("# 待辦（跑的時候可以直接改這個檔，改完存檔，下一輪就會同步進去）\n\n"
                + render_todos(sync=False) + "\n")
        f.write_text(body, "utf-8")
        cur().todo_mtime = f.stat().st_mtime
    except Exception:
        cur().todo_mtime = 0.0


def sync_todo_file() -> str:
    """使用者手改了那份 markdown 就吃回來。回一句話給模型，沒改就回空字串。

    這是 Cline 的 focus chain：跑到一半要改方向，不必打斷它，改檔案就好。
    只認 [x]／[ ] 與文字，相依關係靠文字比對接回去 ——
    使用者手打的行本來就不會帶編號，硬解析只會解出一堆垃圾。
    """
    f = todo_file()
    if f is None or not cur().todos:
        return ""
    try:
        if not f.exists() or abs(f.stat().st_mtime - cur().todo_mtime) < 0.001:
            return ""
        lines = f.read_text("utf-8", errors="replace").splitlines()
    except Exception:
        return ""
    was = {t["text"]: t.get("blocked_by", []) for t in cur().todos}
    fresh = []
    for ln in lines:
        m = TODO_LINE.match(ln)
        if not m:
            continue
        text = re.sub(r"（要等[^）]*）\s*$", "", m.group(2)).strip()[:200]
        if text:
            fresh.append({"text": text, "done": m.group(1).lower() == "x",
                          "blocked_by": [n for n in was.get(text, []) if n <= len(fresh)]})
    if not fresh or fresh == cur().todos:
        cur().todo_mtime = f.stat().st_mtime
        return ""
    cur().todos = fresh[:20]
    write_todo_file()          # 重新編號之後寫回去，檔案跟清單才是同一份
    left = sum(1 for t in cur().todos if not t["done"])
    return (f"[待辦清單] 使用者剛剛手動改了 {TODO_FILE}，這是現在的版本（還剩 {left} 項）："
            f"\n{render_todos(sync=False)}\n照這份做，不要用你記得的舊版本。")


def render_todos(sync: bool = True) -> str:
    if sync:
        sync_todo_file()
    if not cur().todos:
        return "（清單是空的）"
    out = []
    for i, t in enumerate(cur().todos, start=1):
        # 編號要給模型看得到，不然它下一次沒辦法用 blocked_by 指回來
        line = f"{i}. " + ("[x] " if t["done"] else "[ ] ") + t["text"]
        blocked = [n for n in t.get("blocked_by", [])
                   if 1 <= n <= len(cur().todos) and not cur().todos[n - 1]["done"]]
        if blocked:
            line += "（要等 " + "、".join("#" + str(n) for n in blocked) + "）"
        out.append(line)
    return "\n".join(out)


def workspace_info() -> dict:
    if cur().ws is None:
        return {"path": "", "write": False}
    root = cur().ws.resolve()
    files = 0
    for _ in ws_walk():
        files += 1
        if files > 5000:
            break
    name, _ = project_md()
    return {
        "path": str(root),
        "git": (root / ".git").exists(),
        "python": " ".join(detect_python()),
        "files": files,
        "write": cur().write,
        "project_md": name,
        "git_state": git_state(),
    }


def browse_dirs(path: str) -> dict:
    """列出某個資料夾底下的子資料夾，給「選工作區」用。

    這一支**不受工作區限制**——你正在挑的就是那個限制本身。所以它只列資料夾名稱、
    不回傳任何檔案內容，而且跟其他工具一樣只接受本機請求。
    """
    raw = str(path or "").strip()
    here = Path(raw).expanduser() if raw else Path.cwd()
    try:
        here = here.resolve()
    except OSError:
        here = Path.cwd()
    if not here.is_dir():
        here = here.parent if here.parent.is_dir() else Path.cwd()

    dirs, note = [], ""
    try:
        for child in sorted(here.iterdir(), key=lambda c: c.name.lower()):
            if not child.is_dir():
                continue
            if child.name.startswith(".") and child.name != ".config":
                continue                      # 點開頭的資料夾一律不列，選單會被洗版
            try:
                git = (child / ".git").exists()
            except OSError:
                git = False
            dirs.append({"name": child.name, "git": git})
    except (PermissionError, OSError) as e:
        note = f"讀不完這個資料夾：{e.__class__.__name__}"

    # 每一條路徑都回同一組欄位。少一個 key 的話前端就得到處判斷有沒有（踩過）。
    return {
        "path": str(here),
        "parent": "" if here.parent == here else str(here.parent),
        "dirs": dirs[:500],
        "home": str(Path.home()),
        "cwd": os.getcwd(),
        "pickable": here != Path.home().resolve() and here != Path(here.anchor),
        "error": note,
    }


def list_entries(rel: str = "") -> list:
    """工作區裡某一層的內容，給側邊的檔案樹用。受 ws_path() 限制。"""
    target = ws_path(rel, must_exist=True) if rel else ws_root()
    if not target.is_dir():
        raise NotADirectoryError(f"{rel} 不是資料夾")
    out = []
    for child in sorted(target.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower())):
        if child.is_dir():
            if child.name in DENY_DIRS:
                continue
            out.append({"name": child.name, "path": ws_rel(child), "dir": True})
        else:
            if DENY_FILES.match(child.name):
                continue
            try:
                size = child.stat().st_size
            except OSError:
                size = 0
            out.append({"name": child.name, "path": ws_rel(child),
                        "dir": False, "size": size})
        if len(out) >= 800:
            break
    return out


def make_dir(rel: str) -> str:
    """在工作區裡開一個資料夾，給介面的「新增資料夾」用。回傳它的相對路徑。

    `a/b/c` 一次開三層。邊界跟檔案工具同一道 —— ws_path() 擋 ..、絕對路徑與外連。
    """
    raw = " ".join(str(rel or "").split())
    if not raw:
        raise ValueError("要給資料夾名稱")
    target = ws_path(raw)
    if target.exists():
        raise FileExistsError(f"{ws_rel(target)} 已經在了")
    target.mkdir(parents=True)
    return ws_rel(target)


def set_workspace(path: str) -> dict:
    if not str(path or "").strip():
        cur().ws = None
        return workspace_info()
    p = Path(path).expanduser().resolve()
    if not p.is_dir():
        raise NotADirectoryError(f"{p} 不是一個資料夾")
    if p == Path.home().resolve() or p == Path(p.anchor):
        # 整個家目錄或磁碟根目錄當工作區等於沒有邊界
        raise PermissionError("請指定專案資料夾，不要用家目錄或根目錄")
    cur().ws = p
    return workspace_info()


def unified(old: str, new: str, name: str, labels=("現在", "改後")) -> str:
    diff = difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                fromfile=f"{name}（{labels[0]}）",
                                tofile=f"{name}（{labels[1]}）", n=3)
    text = "".join(diff)
    return text or "（內容沒有變化）"


# ══════════════════════ 工具（給模型呼叫） ══════════════════════ #

# ── 檔案讀過沒有／讀完之後被改過沒有 ─────────────────────────── #
# edit_file 的 old 對不上時只說「找不到要取代的內容」，模型的反應是換個字串再試，
# 但真正的原因常常是檔案在它讀過之後被改了。這裡記一份「讀的時候長什麼樣」，
# 讓錯誤訊息分得出是哪一種失敗。只換訊息不擋 —— old 要完全吻合本來就擋住了。
# ponytail: 永遠不清的 dict，一次 session 幾百筆，不值得做淘汰。
READ_STATE = {}          # 絕對路徑 -> (mtime_ns, size)


def _stamp(p: Path):
    try:
        st = p.stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def note_read(p: Path) -> None:
    READ_STATE[str(p)] = _stamp(p)


def stale_hint(p: Path) -> str:
    """old 對不上時的補充說明。沒話說就回空字串。"""
    key = str(p)
    if key not in READ_STATE:
        return "（你還沒有用 read_file 讀過這個檔案，old 是猜的。先讀一次再改）"
    if READ_STATE[key] != _stamp(p):
        return ("（你讀過之後這個檔案又被改動了 —— 可能是使用者在編輯器裡改的。"
                "你手上的內容已經過期，請重新 read_file 再改）")
    return ""


def _tool_read_file(path: str, start: int = 0, end: int = 0) -> str:
    """讀檔，可指定行範圍。回傳帶行號的內容，模型引用位置才不會亂猜。"""
    p = ws_path(path, must_exist=True)
    if p.stat().st_size > MAX_FILE_BYTES:
        raise ValueError(f"{ws_rel(p)} 超過 {MAX_FILE_BYTES // 1000}KB，請用 start/end 或 search_files")
    lines = p.read_text("utf-8", errors="replace").splitlines()
    a = max(1, int(start or 1))
    b = min(len(lines), int(end) if end else len(lines))
    body = "\n".join(f"{i}\u2192{lines[i - 1]}" for i in range(a, b + 1))
    head = (f"{ws_rel(p)}（第 {a}–{b} 行，共 {len(lines)} 行）"
            f"；每行開頭的「行號→」不是檔案內容\n")
    note_read(p)
    return head + body


def _tool_list_dir(path: str = ".") -> str:
    base = ws_path(path, must_exist=True)
    rows = []
    for item in sorted(base.iterdir(), key=lambda x: (x.is_file(), x.name.lower())):
        if item.is_dir() and item.name in DENY_DIRS:
            continue
        if item.is_file() and DENY_FILES.match(item.name):
            continue
        size = f"{item.stat().st_size:>8}" if item.is_file() else "     dir"
        rows.append(f"{size}  {item.name}" + ("/" if item.is_dir() else ""))
    return f"{ws_rel(base)}/\n" + ("\n".join(rows) or "（空資料夾）")


def glob_ok(f: Path, glob: str) -> bool:
    """檔名與相對路徑都比對一次：模型很自然會傳 "pkg/calc.py" 或 "pkg/*.py"，
    只比對 f.name 的話那兩種寫法都會掃到 0 個檔案（實測害小模型直接放棄）。"""
    if not glob:
        return True
    rel = ws_rel(f)
    return (fnmatch.fnmatch(f.name, glob) or fnmatch.fnmatch(rel, glob)
            or fnmatch.fnmatch(rel, "*/" + glob.lstrip("/")))


def rg_rows(pattern: str):
    """用 ripgrep 掃一遍，回傳 [(相對路徑, 行號, 內容)]。用不了就回 None。

    只拿 rg 當候選清單產生器，邊界還是 ws_path()：每一筆都要再過一次，
    .git／.venv／.env 不會因為換了掃描器就漏出去。
    沒裝就走下面的純 Python 迴圈 —— 跟 ruff／eslint 一樣是「裝了就用」。
    """
    # PATH 上沒有的話還可以指過去：VSCode 與幾套 agent 擴充都自帶一份 rg，
    # 但那份不在 PATH 上（實測 `rg` 只是 shell function，subprocess 看不到）。
    exe = os.environ.get("ZACKLLMGUI_RG") or shutil.which("rg")
    if not exe or not Path(exe).exists():
        return None
    cmd = [exe, "--line-number", "--no-heading", "--color", "never", "--no-messages",
           "--max-filesize", str(MAX_FILE_BYTES), "--max-count", str(SEARCH_HITS)]
    for d in DENY_DIRS:
        cmd += ["--glob", "!" + d + "/"]      # rg 預設吃 .gitignore，但工作區不一定是 git repo
    try:
        proc = subprocess.run(cmd + ["-e", pattern, "."], cwd=str(ws_root()),
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30)
    except Exception:
        return None
    # 2 = rg 不認這個 pattern（Rust 的 regex 沒有後向參照與 lookaround，Python 有）
    if proc.returncode not in (0, 1):
        return None
    rows = []
    for line in proc.stdout.decode("utf-8", "replace").splitlines():
        part = line.split(":", 2)
        if len(part) == 3:
            rows.append((Path(part[0]).as_posix(), part[1], part[2]))
    return rows


def _tool_search_files(pattern: str = "", glob: str = "") -> str:
    """在工作區裡找字串，只回命中的那幾行 —— 整檔讀進去會把 context 吃光。

    **只給 glob 不給 pattern＝照檔名找檔案。** 沒有這個的話「測試檔在哪」
    要走三四輪 list_dir，而每一輪都要模型重吃一次整份 context。
    """
    if not pattern:
        if not glob:
            raise ValueError("要給 pattern（找內容）或 glob（找檔名），至少一個")
        names = [ws_rel(f) for f in ws_walk() if glob_ok(f, glob)]
        if not names:
            return f"沒有檔名符合「{glob}」的檔案"
        names.sort()
        if len(names) > SEARCH_HITS:
            return ("\n".join(names[:SEARCH_HITS])
                    + f"\n…（共 {len(names)} 個，只顯示前 {SEARCH_HITS} 個，請縮小範圍）")
        return "\n".join(names)
    try:
        rx = re.compile(pattern)
    except re.error as e:
        raise ValueError(f"pattern 不是合法的正規表示式：{e}") from None
    hits, scanned = [], 0
    rows = rg_rows(pattern)
    if rows is not None:
        for rel, n, line in rows:
            try:
                f = ws_path(rel)             # 不開放的目錄與檔案在這裡被擋掉
            except Exception:
                continue
            if not glob_ok(f, glob):
                continue
            hits.append(f"{ws_rel(f)}:{n}: {line.strip()[:200]}")
            if len(hits) >= SEARCH_HITS:
                return "\n".join(hits) + f"\n…（只顯示前 {SEARCH_HITS} 筆，請縮小範圍）"
        return "\n".join(hits) if hits else f"沒有找到「{pattern}」"
    for f in ws_walk():
        if not glob_ok(f, glob):
            continue
        try:
            if f.stat().st_size > MAX_FILE_BYTES:
                continue
            text = f.read_text("utf-8", errors="replace")
        except OSError:
            continue
        scanned += 1
        for n, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                hits.append(f"{ws_rel(f)}:{n}: {line.strip()[:200]}")
                if len(hits) >= SEARCH_HITS:
                    return "\n".join(hits) + f"\n…（只顯示前 {SEARCH_HITS} 筆，請縮小範圍）"
    if not hits:
        return f"沒有找到「{pattern}」（掃過 {scanned} 個檔案）"
    return "\n".join(hits)


def _tool_delete_file(path: str) -> str:
    """刪掉工作區裡的一個檔案。先備份、記進 journal，所以倒得回來。

    在它之前模型只能用 `rm`，而那條沒有備份 —— 最該有還原點的操作剛好是
    唯一沒有的。只刪檔案不刪資料夾：整包刪沒辦法一份一份備份。
    """
    p = ws_path(path, must_exist=True)
    if p.is_dir():
        raise IsADirectoryError(
            f"{ws_rel(p)} 是資料夾。這支只刪單一檔案 —— 整包刪掉沒有還原點，"
            f"請自己在終端機處理。")
    mark = backup_file(p)
    journal_add("delete_file", ws_rel(p), mark, False)
    size = p.stat().st_size
    p.unlink()
    READ_STATE.pop(str(p), None)      # 刪掉了，之前讀過的狀態不算數
    return f"已刪除 {ws_rel(p)}（{size} bytes，已備份，可以還原）\n[backup]{mark}"


def _tool_write_file(path: str, content: str) -> str:
    p = ws_path(path)
    existed = p.exists()
    if existed and p.read_text("utf-8", errors="replace").strip():
        raise ValueError(f"{ws_rel(p)} 已經有內容，請用 edit_file 修改，不要整檔覆寫")
    p.parent.mkdir(parents=True, exist_ok=True)
    mark = backup_file(p) if existed else ""
    journal_add("write_file", ws_rel(p), mark, not existed)
    p.write_text(content, encoding="utf-8")
    note_read(p)          # 同上：整檔寫入之後，內容就是它自己給的
    return f"已寫入 {ws_rel(p)}（{len(content)} 字元）" + (f"\n[backup]{mark}" if mark else "")


def _indent(line: str) -> str:
    return line[:len(line) - len(line.lstrip())]


def loose_replace(text: str, old: str, new: str):
    """完全比對找不到時的退路：只忽略每行前後的空白再找一次。找不到就回 None。

    本機小模型最常寫壞的就是縮排。原本直接報錯，而模型的反應是換個字串再試，
    然後撞上連續失敗上限。**只在唯一命中時才算數** —— 猜錯一個地方比多問一輪貴。
    命中之後 new 照檔案裡實際的縮排搬過去。
    """
    if "\r" in text:
        return None                      # CRLF 的檔案別猜，行尾會被弄成混排
    want = [ln.strip() for ln in old.strip("\n").split("\n")]
    if not any(want):
        return None
    lines = text.split("\n")
    at = [i for i in range(len(lines) - len(want) + 1)
          if [ln.strip() for ln in lines[i:i + len(want)]] == want]
    if len(at) != 1:
        return None
    i = at[0]
    src, dst = _indent(old.strip("\n").split("\n")[0]), _indent(lines[i])
    body = new.split("\n")
    if src != dst:
        body = [(dst + (ln[len(src):] if src and ln.startswith(src) else ln))
                if ln.strip() else ln for ln in body]
    return "\n".join(lines[:i] + body + lines[i + len(want):])


def edit_items(old: str, new: str, replace_all, edits) -> list:
    """把兩種寫法（單組 old/new、多組 edits）收成同一種形狀。"""
    if edits:
        if not isinstance(edits, list):
            raise ValueError('edits 要是陣列：[{"old": …, "new": …}, …]')
        items = [e for e in edits if isinstance(e, dict)]
        if not items:
            raise ValueError("edits 裡面沒有東西")
        return items
    if not old:
        raise ValueError("要給 old 與 new；同一個檔案要改好幾個地方就用 edits 一次送")
    return [{"old": old, "new": new, "replace_all": replace_all}]


def apply_edits(text: str, items: list, where: str, hint=None):
    """把一組取代依序套到文字上。回傳 (新文字, 改了幾處, 提醒)。

    **全有全無**：任何一組對不上就丟 ValueError，檔案一個字都不會被動到。
    確認卡的預覽跟真正的寫入共用這一支 —— 兩份實作的話，
    卡片上的 diff 遲早會跟寫進去的東西不一樣。
    """
    total, notes = 0, []
    for n, e in enumerate(items, 1):
        tag = f"第 {n} 組：" if len(items) > 1 else ""
        one, two = str(e.get("old", "")), str(e.get("new", ""))
        if one == two:
            raise ValueError(f"{tag}old 與 new 一樣，沒有東西要改")
        count = text.count(one)
        if count == 0:
            fixed = loose_replace(text, one, two)
            if fixed is None:
                raise ValueError(f"{tag}在 {where} 裡找不到要取代的內容"
                                 f"{hint(one) if hint else ''}，請先用 read_file 確認原文")
            text, total = fixed, total + 1
            notes.append(f"{tag}縮排或行尾空白跟檔案裡的不一樣，已照檔案裡的實際內容套用")
            continue
        if count > 1 and not e.get("replace_all"):
            raise ValueError(f"{tag}要取代的內容在 {where} 出現 {count} 次，"
                             f"請多帶一些前後文讓它唯一，或設 replace_all=true")
        text = text.replace(one, two) if e.get("replace_all") else text.replace(one, two, 1)
        total += count if e.get("replace_all") else 1
    return text, total, notes


def edit_hint(p: Path, old: str) -> str:
    if re.match(r"^\s*\d+\u2192", old):
        return "（old 裡面帶了 read_file 的「行號→」前綴，那不是檔案內容）"
    return stale_hint(p)


def _tool_edit_file(path: str, old: str = "", new: str = "", replace_all: bool = False,
                    edits: list = None) -> str:
    """精確字串取代。刻意不吃 diff 也不吃行號：小模型算不對，錯了就改到別的地方。

    edits 一次送多組是為了省輪數：改五個地方本來要五輪，而每一輪都要把整包
    context 重送給 Ollama 重算一次 prefill —— 那才是本機模型真正的成本。
    """
    p = ws_path(path, must_exist=True)
    text = p.read_text("utf-8", errors="replace")
    out, count, notes = apply_edits(text, edit_items(old, new, replace_all, edits),
                                    ws_rel(p), lambda o: edit_hint(p, o))
    mark = backup_file(p)
    journal_add("edit_file", ws_rel(p), mark, False)
    p.write_text(out, encoding="utf-8")
    note_read(p)          # 自己剛寫的內容不算「被別人改過」
    return (f"已修改 {ws_rel(p)}（{count} 處）"
            + ("\n" + "\n".join(notes) if notes else "") + f"\n[backup]{mark}")


# 串接、管線、重導、命令替換：後面藏得住第二條指令，路徑掃描就不算數了。
CHAINED = re.compile(r"[;&|`\n<>]|\$\(")


def ws_scoped(command: str) -> bool:
    """這行風險指令是不是只動得到工作區裡的檔案。

    只有「工作區內全自動」那一檔在用它：決定 rm 這種指令還要不要問人。
    判斷刻意保守 —— 解析不出來的一律回 False（照樣問），寧可多問一次。
    block 那一級不走這裡：那一層是直接拒絕執行，不是問不問。
    """
    cmd = " ".join(str(command or "").split())
    if command_risk(cmd)[0] != "risky":
        return False                     # block 那級直接拒絕執行，ok 那級本來就不用問
    # 沙盒開著的話「動不動得到工作區外」不必從指令去猜：工作區以外整台唯讀、
    # 家目錄被 tmpfs 蓋掉、網路切斷，指令再怎麼串接也出不去。這一條讓
    # pip install、`a && b` 這種原本掃不動的寫法在沙盒裡也不用問。
    if ALLOW_SANDBOX:
        return True
    if cur().ws is None or CHAINED.search(cmd):
        return False
    for pattern, why, *rest in RISKY_CMDS:
        if re.search(pattern, cmd, re.I):
            # sudo、裝套件、git push、kill 動的不是檔案，路徑落在哪裡都不算工作區內
            if not (rest and rest[0]):
                return False
            break
    else:
        return False                     # 不是風險指令，輪不到這裡回答
    try:
        args = shlex.split(cmd)[1:]
    except ValueError:
        return False                     # 引號沒配對
    for tok in args:
        if tok.startswith("-"):
            continue                     # 旗標；chmod 的 755 這種會落到下面，剛好也在工作區裡
        try:
            ws_path(tok)                 # 同一支路徑限制：..、絕對路徑、symlink、.git 一律不算
        except Exception:
            return False
    return True


def _tool_run_shell(command: str, background: bool = False) -> str:
    # 走 build_command 而不是自己判斷一次：風險檢查與沙盒包裝只能有一份，
    # 兩份遲早會有一份忘了改（同步這條路本來就少人走）。
    cmd, cwd, use_shell, head = build_command("run_shell", {"command": command})
    if background:
        return _start_job(command, cmd, cwd, use_shell, head)
    proc = subprocess.run(cmd, shell=use_shell, cwd=cwd, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, timeout=SHELL_TIMEOUT)
    out = decode_output(proc.stdout, use_shell)
    return f"[exit {proc.returncode}]\n{out}"


def _tool_check_job(id: str = "", kill: bool = False, wait: int = BG_WAIT) -> str:
    """收背景指令的結果。id 留空就列出全部。

    還沒跑完就先等 wait 秒 —— 這是為了「一輪抵十輪」，不是為了省時間。
    """
    with JOBS_LOCK:
        job = JOBS.get(str(id or "").strip())
        every = list(JOBS.values())
    if not job:
        if not every:
            return "現在沒有背景指令。要丟一條的話用 run_shell 加 background=true。"
        rows = []
        for j in every:
            secs = int((j["ended"] or time.time()) - j["started"])
            state = "還在跑" if j["code"] is None else f"exit {j['code']}"
            rows.append(f"{j['id']}（{state}，{secs} 秒）：{j['cmd']}")
        head = "" if not str(id or "").strip() else f"沒有 {id} 這個 id。"
        return head + "目前的背景指令：\n" + "\n".join(rows)
    try:
        limit = max(0, min(int(wait), BG_WAIT_MAX))
    except (TypeError, ValueError):
        limit = BG_WAIT
    if not kill and job["code"] is None:
        deadline = time.time() + limit
        while job["code"] is None and time.time() < deadline:
            time.sleep(0.2)
    secs = int((job["ended"] or time.time()) - job["started"])
    if kill:
        if job["code"] is not None:
            return f"{job['id']} 本來就跑完了（exit {job['code']}）。"
        try:
            kill_tree(job["proc"])
        except Exception as e:
            return f"終止 {job['id']} 失敗：{e}"
        # 等讀取執行緒把 exit code 記進去再回話。不等的話這裡說「已終止」，
        # 而網頁的「背景 N 條在跑」還算它一條 —— 兩邊講的話不一樣最難查。
        for _ in range(20):
            if job["code"] is not None:
                break
            time.sleep(0.05)
        return f"{job['id']} 已終止（跑了 {secs} 秒）。輸出：\n{_job_tail(job)}"
    if job["code"] is None:
        return (f"{job['id']} 還在跑（已經 {secs} 秒）。目前的輸出：\n{_job_tail(job)}\n"
                f"（這次已經幫你等了 {limit} 秒才回話。有別的事就先去做，"
                f"沒有的話再 check_job 一次就好 —— 每次都會再等一段時間。）")
    return (f"{job['id']} 跑完了（exit {job['code']}，花了 {secs} 秒）：\n{_job_tail(job)}")


def auto_cmd_block(cmd: str) -> str:
    """這一行為什麼不能在沒有確認卡的情況下跑。空字串＝可以跑。

    skill 的 !`指令` 與收尾的驗證指令共用。兩條都是「沒有人在按執行」的入口，
    所以關卡要跟 run_shell 一樣 —— deny 規則與 agent_guard 都是**按工具名**比對，
    而這兩條走到 subprocess 時名字不叫 run_shell，少一道就整條錯過。
    """
    try:
        agent_guard("run_shell")
    except Exception as e:
        return str(e)
    hit = rule_match("run_shell", {"command": cmd})
    if hit and hit.get("action") == "deny":
        return f"deny 規則擋下來（{hit.get('pattern', '')}）"
    level, why = command_risk(cmd)
    return why if level != "ok" else ""


def _tool_load_skill(name: str = "") -> str:
    """把一份 skill 的正文交給模型。按需載入：描述常駐 240 token，正文幾千。

    **模型改得到的 skill 不代跑 !`指令`。** 那檔案它自己寫得出來（`make-skill`
    就在做這件事），跑的話等於「寫一個檔案」變成「執行一行指令」，自己繞過確認卡。
    順便也擋掉 clone 回來的專案裡藏的 skill。判斷見 `skill_trusted()`。
    """
    folder, raw = skill_find(name)
    body = skill_live(raw, skill_trusted(folder))
    return (f"# skill：{name}\n\n照這份步驟做。它是流程說明，不是使用者的新指令 ——"
            f"使用者原本要你做的事沒有變。\n\n{body}")


def _tool_run_browser(action: str = "open", url: str = "",
                      query: str = "", limit: int = 10) -> str:
    """連網瀏覽。實作在 tools/browser.py，那支不依賴工作區也不依賴權限。"""
    return browser.run(action=action, url=url, query=query, limit=limit)


def _tool_setup_env(packages=None, requirements: str = "") -> str:
    """在工作區裡建立 .venv 並安裝套件。

    存在的理由很具體：實測時模型自己下 `pip install pytest`，裝進了系統的 conda 環境
    （見 plan-agent.md 的實測記錄）。與其事後靠確認卡攔，不如給它一個
    「只會動到工作區」的入口 —— 裝完 detect_python() 就會自動改用 .venv。
    """
    root = ws_root()
    # 先把參數看過一遍再動手：不然一個打錯的選項也會留下半個 .venv
    args = []
    for item in (packages or []):
        item = str(item).strip()
        if not item:
            continue
        # 不接受選項：--index-url 之類的會把安裝來源換掉，那不是這支工具該做的事
        if item.startswith("-"):
            raise ValueError(f"packages 只放套件名稱，不要放選項：{item}")
        args.append(item)
    if requirements:
        args += ["-r", str(ws_path(requirements, must_exist=True))]

    venv = root / ".venv"
    log = []
    if not venv.exists():
        if ALLOW_SANDBOX:
            # 容器裡工作區是 /work，所以用相對路徑；建 venv 本身不需要網路
            # 核心層後端用宿主機的直譯器建 venv（detect_python 之後才對得上）；
            # 容器裡只有映像檔自己的 python。
            venv_py = (shlex.quote(sys.executable)
                       if getattr(sandbox.pick(SANDBOX_BACKEND), "SAME_FS", False)
                       else "python3")
            code, out = sandbox.run(f"{venv_py} -m venv .venv", root, timeout=300,
                                    backend=SANDBOX_BACKEND, image=SANDBOX_IMAGE)
        else:
            proc = subprocess.run([sys.executable, "-m", "venv", str(venv)],
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=300)
            code, out = proc.returncode, proc.stdout.decode("utf-8", "replace")
        if code != 0:
            return "建立 .venv 失敗：\n" + out
        log.append(f"已建立 {venv.name}")
    else:
        log.append(f"{venv.name} 已存在")

    pip = venv / ("Scripts/pip.exe" if os.name == "nt" else "bin/pip")
    if not pip.exists() and not ALLOW_SANDBOX:
        return "\n".join(log) + "\n找不到 venv 裡的 pip，環境可能沒建好"

    if not args:
        return "\n".join(log) + "\n（沒有指定要裝的套件）"

    if ALLOW_SANDBOX:
        # 三支工具裡只有這一支開網路 —— pip 一定要連得出去，
        # 但開放範圍就縮在「裝套件」這一步，run_shell 與 run_tests 依然斷網。
        # 沙盒裡家目錄是唯讀的，pip 會抱怨 ~/.cache/pip 寫不進去然後停用快取。
        # 那段 WARNING 會原封不動進到模型的 context 裡，而它跟任務一點關係都沒有。
        # 指到工作區底下：警告消失，重裝同一個套件也快。
        line = ("PIP_CACHE_DIR=.venv/.pip-cache .venv/bin/pip install "
                + " ".join(shlex.quote(a) for a in args))
        code, out = sandbox.run(line, root, net=True, timeout=TEST_TIMEOUT,
                                backend=SANDBOX_BACKEND, image=SANDBOX_IMAGE)
    else:
        proc = subprocess.run([str(pip), "install"] + args, cwd=str(root),
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=TEST_TIMEOUT)
        code, out = proc.returncode, proc.stdout.decode("utf-8", "replace")
    log.append(f"[pip install {' '.join(args)}]\n[exit {code}]\n" + tail_of(out, 30))
    return "\n".join(log)


def _tool_run_tests(target: str = "", k: str = "") -> str:
    """跑測試。用偵測到的 python，模型不能自己換直譯器、也不能自己裝套件。"""
    cmd = detect_python() + ["-m", "pytest", "-q", "--color=no"]
    if target:
        cmd.append(str(ws_path(target, must_exist=True)))
    if k:
        cmd += ["-k", k]
    proc = subprocess.run(cmd, cwd=str(ws_root()), stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, timeout=TEST_TIMEOUT)
    out = proc.stdout.decode("utf-8", "replace")
    return f"[{' '.join(cmd)}]\n[exit {proc.returncode}]\n" + tail_of(out)


def verify_detect() -> str:
    """猜一條「跑完就知道有沒有壞」的指令。**只是給介面預填，不會自己跑。**

    真正會跑的是使用者在介面上確認過的那個字串。**刻意不從專案裡讀設定檔** ——
    那等於 clone 回來的專案可以指定一條會自動執行的指令，跟 skill 那個洞同一條路。
    """
    ws = cur().ws
    if ws is None:
        return ""
    pkg = ws / "package.json"
    if pkg.is_file():
        try:
            meta = json.loads(pkg.read_text("utf-8", errors="replace"))
            if "test" in (meta.get("scripts") or {}):
                return "npm test"
        except (ValueError, OSError):
            pass
    if (ws / "CMakeLists.txt").is_file():
        # 已經 configure 過才給 ctest —— 沒有 build/ 的話那條指令只會回錯誤，
        # 而預填一條跑不動的指令比留白更糟
        for d in ("build", "cmake-build-debug", "out/build"):
            if (ws / d / "CTestTestfile.cmake").is_file():
                return f"cmake --build {d} && ctest --test-dir {d} --output-on-failure"
        return "cmake --build build"
    if (ws / "Makefile").is_file():
        body = (ws / "Makefile").read_text("utf-8", errors="replace")
        for target in ("test", "check"):
            if re.search(rf"^\.?{target}\s*:", body, re.M):
                return f"make {target}"
    # 沒裝 .NET 就不要預填 dotnet —— 跟 ctest 那條同一個理由，跑不動的指令比留白糟
    if shutil.which("dotnet") and (list(ws.glob("*.sln")) or list(ws.glob("*.csproj"))
                                   or list(ws.glob("*/*.csproj"))):
        return "dotnet test"
    if (ws / "tests").is_dir() or list(ws.glob("test_*.py")):
        return " ".join(shlex.quote(x) for x in detect_python()) + " -m pytest -q"
    return ""


def sandbox_state() -> dict:
    """這台機器的沙盒現況。網頁拿它決定按鈕要不要 disable、tooltip 寫什麼。

    偵測的是**跑 serve.py 這一台**（工具本來就在這台跑），不是開網頁那一台。
    """
    info = sandbox.detect()
    return dict(info, on=ALLOW_SANDBOX, backend=SANDBOX_BACKEND or info["backend"],
                gpu=SANDBOX_GPU)


def sandbox_python() -> str:
    """沙盒裡該用哪個 python，回傳可以直接放進 shell 的一段字。

    核心層（bwrap／seatbelt）的檔案系統就是宿主機的，用 detect_python() 的絕對路徑
    —— 寫死 "python" 在只有 python3 的機器上會 not found（踩過）。
    容器的 rootfs 是映像檔的，宿主機路徑進去不存在，只能用裸的 python。
    """
    if getattr(sandbox.pick(SANDBOX_BACKEND), "SAME_FS", False):
        return " ".join(shlex.quote(x) for x in detect_python())
    for sub in (".venv/bin/python", "venv/bin/python"):
        # lexists 不是 exists：容器裡建的 venv，bin/python 是指向容器內
        # /usr/local/bin/python3.x 的符號連結，那個路徑在宿主機上不存在，
        # exists() 會跟著連結去看然後回 False —— 於是永遠用不到剛裝好的 venv。
        if os.path.lexists(ws_root() / sub):
            return sub
    return "python"


def build_command(name: str, args: dict):
    """把工具呼叫轉成「要跑的指令」。回傳 (cmd, cwd, shell, 標頭)。

    /run（串流）與同步的 _tool_* 用同一份判斷，風險檢查才不會只擋到其中一條路。
    """
    if name == "run_shell":
        command = str(args.get("command", ""))
        level, why = command_risk(command)
        if level == "block":
            raise PermissionError(
                f"這行指令被擋下來了（{why}）。真的要執行請自己在終端機打，"
                f"這裡不代跑無法還原的操作。")
        if ALLOW_SANDBOX:
            # 沙盒裡沒有網路，所以 curl 把東西送出去這條路直接斷掉；
            # cwd 一樣是工作區，指令本身一個字都不用改。
            return (sandbox.wrap(command, ws_root(), backend=SANDBOX_BACKEND,
                                 image=SANDBOX_IMAGE, gpu=SANDBOX_GPU),
                    str(ws_root()), False, f"$ {command}")
        return command, (str(cur().ws) if cur().ws else None), True, f"$ {command}"
    if name == "run_tests":
        if ALLOW_SANDBOX:
            line = sandbox_python() + " -m pytest -q --color=no"
            if args.get("target"):
                line += " " + shlex.quote(ws_rel(ws_path(str(args["target"]), must_exist=True)))
            if args.get("k"):
                line += " -k " + shlex.quote(str(args["k"]))
            return (sandbox.wrap(line, ws_root(), backend=SANDBOX_BACKEND,
                                 image=SANDBOX_IMAGE, gpu=SANDBOX_GPU),
                    str(ws_root()), False, "[" + line + "]")
        cmd = detect_python() + ["-m", "pytest", "-q", "--color=no"]
        if args.get("target"):
            cmd.append(str(ws_path(str(args["target"]), must_exist=True)))
        if args.get("k"):
            cmd += ["-k", str(args["k"])]
        return cmd, str(ws_root()), False, "[" + " ".join(cmd) + "]"
    raise ValueError(f"{name} 不支援串流執行")




def _tool_fetch_url(url: str) -> str:
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("只接受 http / https 網址")
    req = urllib.request.Request(url, headers={"User-Agent": "ZackLLMGUI/1.0"})
    with urllib.request.urlopen(req, timeout=20) as res:
        raw = res.read(2 * 1024 * 1024).decode("utf-8", "replace")
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"[ \t]*\n\s*\n+", "\n\n", re.sub(r"[ \t]{2,}", " ", text)).strip()





# 系統用量。刻意不吃 psutil：這支只用標準函式庫，而 /proc 跟 nvidia-smi
# 本來就在那裡。拿不到的欄位一律不回傳，前端就不畫那一格。
# ponytail: Linux（/proc）、Windows（GetSystemTimes／GlobalMemoryStatusEx）與
#           NVIDIA（nvidia-smi）。macOS 與 AMD 只會少幾格，不會壞掉 ——
#           真的有人要再加 vm_stat / rocm-smi。
def sys_usage() -> dict:
    """core.sysinfo 的數字，加上「Ollama 是不是也在這一台」。"""
    return dict(sysinfo.sys_usage(), ollama_local=ollama_is_local())


def repo_map() -> str:
    return repomap.repo_map(ws_walk(), ws_rel) if cur().ws is not None else ""


def skills_usable() -> list:
    """現在這個狀態下用得動的 skill —— 依 tool_defs() 篩。"""
    return _skills.skills_usable({d["function"]["name"] for d in tool_defs()})


def skill_live(body: str, run: bool = True) -> str:
    """把 !`指令` 換成它現在的輸出。關卡（auto_cmd_block）與包裝（build_command）
    都在這一端，傳進去。"""
    return _skills.skill_live(body, run, auto_cmd_block, build_command)


def agent_rules() -> str:
    """依目前開放的工具拼出給模型的操作規則。

    做法參考 xai-org/grok-prompts 的 Jinja 模板與 grok-build 的工具描述：
    沒開放的功能一個字都不要提，否則小模型會去呼叫不存在的工具。
    """
    if not ALLOW_TOOLS:
        return ""
    if cur().auto == "off":
        r = ["你可以呼叫工具。每一次呼叫都會先讓使用者確認，所以：",
             "- 一次只呼叫一個工具，看到結果再決定下一步。"]
    else:
        # 自動模式下唯讀呼叫不會停下來等人，所以同一輪多送幾個是免費的。
        # 省下來的不是工具執行時間（讀本機檔案是微秒），是**模型的來回**——
        # 每多一輪就要重新吃一次整份 context，那才是幾秒鐘的東西。
        r = ["你可以呼叫工具。多數呼叫會自動放行，不會停下來等人，所以：",
             "- 讀檔、搜尋、列目錄這種唯讀的呼叫，同一輪可以一次送好幾個，"
             "不要一輪讀一個檔；改檔案與跑指令一次一個，看到結果再決定下一步。"]
    r += ["- 工具失敗時先讀懂錯誤訊息，不要用一模一樣的參數重試。"]
    if cur().ws is not None:
        r += [f"- 工作區是 {cur().ws}，所有路徑都相對於它，不要用絕對路徑或 ..。",
              "- 找東西先用 search_files 或 list_dir 定位，再用 read_file 讀那一段；"
              "不要整個檔案讀進來。",
              "- read_file 每行開頭的「行號→」是為了讓你引用位置，不是檔案內容。"]
    langs = ws_langs()
    if cur().write:
        r += ["- 修改既有檔案一律用 edit_file：old 要與檔案內容完全一致（含縮排），"
              "並帶足前後文讓它在檔案裡唯一；write_file 只用來建立新檔案。",
              "- 同一個檔案要改好幾處時用 edits 一次送完，不要一輪改一處。",
              "- 要刪檔案用 delete_file，不要用 run_shell 下 rm —— "
              "delete_file 會先備份、還原得回來，rm 不會。",
              # run_tests 有 needs:"python" 的閘門，C/C++ 專案收不到那支工具。
              # 提到名字就是把它種進 context，小模型會去呼叫清單上沒有的東西。
              "- 一次做完一件事就驗證一次，不要改一整輪才驗。"
              + ("驗證用 run_tests。" if "python" in langs else ""),
              "- 測試失敗時修的是程式，不是測試。真的認為測試寫錯，先說出來讓使用者決定。"]
    if "python" in langs:
        r.append("- 缺套件時用 setup_env 裝進工作區的 .venv，不要用 run_shell 下 pip install。")
    if "c" in langs:
        # 有 CMake 就講 CMake，沒有就不要教它建一份 —— 那是專案的決定不是這裡的。
        # 不提 run_tests，連「沒有 run_tests 可用」這種否定句也不提（理由同上）。
        r.append("- C/C++：編譯與測試用 run_shell 跑專案自己的那一套"
                 + ("（cmake -S . -B build、cmake --build build、ctest --test-dir build"
                    " --output-on-failure）。" if (cur().ws / "CMakeLists.txt").is_file()
                    else "（make、或直接 gcc／g++）。"))
        # 沙盒開著一定是 sh；沒開的話走本機 shell，Windows 上那是 cmd。
        if ALLOW_SANDBOX or os.name != "nt":
            r.append("- 要清掉建置目錄用 `rm -r build`，不要加 -f —— 加了會被擋下來。")
        else:
            r.append("- 要清掉建置目錄用 `rmdir /s build`，不要加 /q —— 加了會被擋下來。")
    if "csharp" in langs and shutil.which("dotnet"):
        r.append("- C#：編譯與測試用 run_shell 跑 dotnet build、dotnet test。"
                 "寫檔後沒有語法檢查（dotnet build 是整包編譯，每寫一個檔跑一次撐不住），"
                 "所以改完要自己 build 一次。")
    # 缺工具鏈：講清楚缺什麼，而且**不要叫它自己去裝** —— 裝 SDK 是使用者的決定
    for miss in ws_missing_tools():
        r.append(f"- 這台沒有裝 {miss['what']}（找不到 {miss['tool']}），"
                 f"所以編譯與測試都跑不動。撞到就直接告訴使用者要裝什麼，"
                 f"不要自己下載或安裝。")
    if ALLOW_SANDBOX:
        # 講的必須是實際會用到的那個後端。跟 bwrap 底下的模型說「只看得到工作區」，
        # 它會以為系統編譯器不存在，然後想辦法自己弄一份。
        try:
            same_fs = getattr(sandbox.pick(SANDBOX_BACKEND), "SAME_FS", False)
        except RuntimeError:
            same_fs = False
        who = "run_shell 與 run_tests" if "python" in langs else "run_shell"
        if same_fs:
            r.append(f"- {who} 在沙盒裡跑：工作區以外唯讀、**沒有網路**。"
                     "系統的工具鏈（gcc、cmake、node…）都還在，照常用。")
        else:
            r.append(f"- {who} 在容器裡跑：只看得到工作區、**沒有網路**，"
                     "而且**映像檔裡沒裝的東西就是沒有**（gcc、cmake 預設都沒有）。"
                     "缺工具鏈就直接說，不要自己想辦法裝。")
        if "python" in langs:
            r.append("- 要裝套件用 setup_env（三支工具裡只有它連得出去）。")
        if "c" in langs:
            # 這件事不能用機制解決（開網等於拆掉沙盒），所以它就該寫進提示詞
            r.append("- 沙盒沒有網路，所以 FetchContent、vcpkg、conan **一定會失敗**。"
                     "相依套件要由使用者先在沙盒外準備好；撞到下載失敗就直接說，"
                     "不要改 CMakeLists 想繞過去。")
    if ALLOW_BROWSER:
        r.append("- 需要查網路上的東西：不知道網址就先 run_browser 搜尋，"
                 "拿到網址再 open；open 會一併給你那一頁上的連結，順著走下去。")
    if len(TOOL_SCHEMAS) and ALLOW_TOOLS:
        r.append("- 多步驟的工作先用 todo_write 列出待辦，每完成一項就整份重送並標成完成。")
        r.append("- 需要使用者決定的事用 ask_user_question 問，不要自己猜。")
    if cur().plan["on"] and not cur().plan["approved"]:
        r.append("- 目前是計畫模式：先用 submit_plan 送出計畫，"
                 "使用者核准之前不會有修改檔案的工具可用。")
    r.append("- 做完後用三到五行說明你改了什麼、驗證結果如何，不要複述工具輸出。")

    # skills 索引：只放名字與一行描述。正文由模型自己用 load_skill 拉 ——
    # 六份描述 240 token 常駐得起，六份正文是幾千 token 而且九成用不到。
    usable = skills_usable() if ALLOW_TOOLS else []
    if usable:
        # 這段每一輪都要重送，所以是固定成本：一份 skill 多幾行，是每一次呼叫都多幾行。
        # 有些 agent 為此開了兩個設定（清單佔 context 的比例、每則描述的字數上限）；
        # 這裡直接寫死，因為本機模型的 context 小得多，可調的空間本來就不大。
        r.append("\n## 現成的做法（要用就先 load_skill 把步驟載進來）")
        r += [f"- {x['name']}：{x['description'][:SKILL_DESC_MAX]}"
              for x in usable[:SKILL_LIST_MAX]]
        if len(usable) > SKILL_LIST_MAX:
            r.append(f"- （還有 {len(usable) - SKILL_LIST_MAX} 個沒列出來，"
                     "用 load_skill 指名還是叫得到）")

    name, text = project_md()
    if text:
        r.append(f"\n## 專案說明（來自 {name}，優先於上面的通則）\n{text}")
    return "\n".join(r)


def tool_defs() -> list:
    """目前這個狀態下，可以送給模型的工具定義。

    沒開放的工具不會出現在清單裡 —— 不是讓模型呼叫了再拒絕。小模型看到工具就會想用。
    """
    out = []
    plan_ok = cur().plan["approved"] or not cur().plan["on"]
    for t in TOOL_SCHEMAS:
        if t["needs"] == "ws" and cur().ws is None:
            continue
        # run_tests 與 setup_env 是 pytest 與 .venv 專用的。C/C++ 專案裡送出去，
        # 模型會拿它們去跑一個沒有 pytest 的專案，然後花幾輪搞懂為什麼失敗。
        # 那兩件事用 run_shell 跑 cmake／ctest 本來就做得到。
        if t["needs"] == "python" and "python" not in ws_langs():
            continue
        # 還沒有背景指令的時候，check_job 是一支叫了也沒東西可收的工具。
        # 量過：一支工具的定義每一輪約 110 token，而多數對話從頭到尾沒有背景指令。
        # 這用的是既有的 needs 閘門，不是新機制 —— 見 plan-agent 2.17 為什麼只做到這裡。
        if t["needs"] == "job" and (cur().ws is None or not JOBS):
            continue
        if t["needs"] == "plan" and not cur().plan["on"]:
            continue
        if t["needs"] == "browser" and not ALLOW_BROWSER:
            continue
        if t["needs"] == "skills" and not skills_list():
            continue
        if t["needs"] == "write" and (cur().ws is None or not cur().write or not plan_ok):
            continue
        props = t["properties"]
        if t["name"] == "task":
            # 型別是 agents/ 裡的檔案，不是寫死的常數 —— 使用者加一份 md 就多一種。
            # enum 直接送給模型：讓它從清單裡挑，比在描述裡寫「可以填 x 或 y」可靠。
            kinds = agent_types()
            if not kinds:
                continue                   # 一種都沒有就不要送這支工具
            props = dict(props)
            props["type"] = dict(props.get("type", {}),
                                 enum=[k["name"] for k in kinds],
                                 description="；".join(
                                     f'{k["name"]}：{k["description"]}' for k in kinds))
        out.append({"type": "function", "function": {
            "name": t["name"], "description": t["description"],
            "parameters": {"type": "object", "properties": props,
                           "required": t.get("required", [])}}})
    return out + mcp_tool_defs()


def _tool_todo_write(items) -> str:
    """模型自己維護的待辦清單。

    幾套 agent 都有這個工具，理由一樣：跑十幾輪之後，
    模型會忘記最初的目標。把清單寫出來、每輪重看一次，它才記得自己在做什麼。
    """
    if isinstance(items, str):
        items = [line.strip("-* ") for line in items.splitlines() if line.strip()]
    if not isinstance(items, list):
        raise ValueError("items 要是字串陣列或 {text, done} 陣列")
    fresh = []
    for it in items[:20]:
        if isinstance(it, dict):
            text = str(it.get("text") or it.get("content") or "").strip()
            done = bool(it.get("done") or str(it.get("status", "")).startswith("comp"))
        else:
            text = str(it).strip()
            done = text.startswith("[x]")
            text = text.lstrip("[x] ").lstrip("[ ] ")
        blocked = []
        if isinstance(it, dict):
            raw = it.get("blocked_by") or it.get("blockedBy") or []
            if isinstance(raw, (int, str)):
                raw = [raw]
            for x in (raw if isinstance(raw, list) else [])[:5]:
                try:
                    n = int(str(x).lstrip("#"))
                except ValueError:
                    continue
                if 1 <= n <= 20:
                    blocked.append(n)
        if text:
            fresh.append({"text": text[:200], "done": done, "blocked_by": blocked})
    # 相依只能往前指，而且不能指自己 —— 模型很容易寫出 3 等 5、5 等 3 這種
    # 互相等待的清單，那種清單畫面上會顯示成「全部都被擋住」，看起來像壞掉。
    for i, t in enumerate(fresh, start=1):
        t["blocked_by"] = sorted(set(n for n in t["blocked_by"] if n < i))
    cur().todos = fresh
    write_todo_file()          # 同步那份 markdown，使用者才改得到
    left = sum(1 for t in cur().todos if not t["done"])
    return f"待辦清單已更新（還剩 {left} 項）：\n{render_todos(sync=False)}"


def _tool_submit_plan(plan: str) -> str:
    """先講計畫、人核准了才動手。核准的動作在網頁上，不在這裡。"""
    text = str(plan or "").strip()
    if not text:
        raise ValueError("plan 是空的")
    # 就地更新不要換掉整個 dict：計畫模式的開關（"on"）也住在這裡面
    cur().plan.update(text=text[:8000], approved=True)     # 網頁按下「核准」才會送到這裡
    return "計畫已核准，可以開始執行。動手前再確認一次每一步都在計畫裡。"


TOOLS = {
    "read_file": _tool_read_file,
    "delete_file": _tool_delete_file,
    "list_dir": _tool_list_dir,
    "search_files": _tool_search_files,
    "run_shell": _tool_run_shell,
    "check_job": _tool_check_job,
    "run_tests": _tool_run_tests,
    "fetch_url": _tool_fetch_url,
    "write_file": _tool_write_file,
    "edit_file": _tool_edit_file,
    "todo_write": _tool_todo_write,
    "submit_plan": _tool_submit_plan,
    "setup_env": _tool_setup_env,
    "run_browser": _tool_run_browser,
    "load_skill": _tool_load_skill,
}
WRITE_TOOLS = {"write_file", "edit_file"}
WS_TOOLS = {"read_file", "list_dir", "search_files", "run_shell", "run_tests",
            "setup_env", "check_job"} | WRITE_TOOLS


# ══════════════════════ git 整合 ══════════════════════ #

def git_run(*a, timeout: int = 60):
    return subprocess.run(["git"] + list(a), cwd=str(ws_root()), stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, timeout=timeout)


def git_state() -> dict:
    """工作區的 git 狀態。不是 repo 就回 {"repo": False}。"""
    if cur().ws is None or not (cur().ws / ".git").exists():
        return {"repo": False}
    try:
        branch = git_run("rev-parse", "--abbrev-ref", "HEAD").stdout.decode("utf-8", "replace").strip()
        porcelain = git_run("status", "--porcelain").stdout.decode("utf-8", "replace")
        stat = git_run("diff", "--stat", "HEAD").stdout.decode("utf-8", "replace").strip()
    except Exception as e:
        return {"repo": True, "error": f"{type(e).__name__}: {e}"}
    files = [ln[3:] for ln in porcelain.splitlines() if ln.strip()]
    return {"repo": True, "branch": branch, "dirty": len(files),
            "files": files[:50], "stat": stat[:4000]}


def git_action(action: str, message: str = "") -> dict:
    """commit：全部加進去再 commit。discard：改成 stash，丟掉的東西還救得回來。"""
    if cur().ws is None or not (cur().ws / ".git").exists():
        raise ValueError("工作區不是 git repo")
    if action == "status":
        return git_state()
    if action == "commit":
        msg = (message or "").strip()
        if not msg:
            raise ValueError("commit 訊息不能是空的")
        git_run("add", "-A")
        proc = git_run("commit", "-m", msg)
        out = proc.stdout.decode("utf-8", "replace").strip()
        if proc.returncode != 0:
            raise RuntimeError(out or "commit 失敗")
        return dict(git_state(), message=out)
    if action == "discard":
        # 不用 checkout -- . ：那個真的救不回來。stash 之後還能 git stash pop
        proc = git_run("stash", "push", "-u", "-m",
                       "zackllmgui " + time.strftime("%Y-%m-%d %H:%M:%S"))
        out = proc.stdout.decode("utf-8", "replace").strip()
        if proc.returncode != 0:
            raise RuntimeError(out or "stash 失敗")
        return dict(git_state(), message=out + "\n（用 git stash pop 可以救回來）")
    raise ValueError(f"不認識的動作：{action}")


# ── 改完自動檢查 ─────────────────────────────────────────────── #
# 照 aider 的 --auto-lint：模型寫完檔案，linter 的錯誤接在工具結果後面，
# 它下一輪自己修掉。比寫進系統提示求它記得可靠。三個刻意的限制：
# 1. 只跑唯讀的檢查，不跑格式化 —— 在模型背後改檔案會讓它手上的內容過期。
# 2. 沒裝就安靜跳過：加分項不該變成噪音，更不該害寫檔失敗。
# 3. 不進沙盒：檔案寫在宿主機的工作區，容器裡根本沒有這個檔案。
# ponytail: 只認 ruff、eslint 與 C/C++ 的 -fsyntax-only，寫死。
#           typecheck 要先解決「跑整包很慢」。
LINT_TIMEOUT = 20
# 單檔語法檢查一定要有真的編譯旗標：裸跑 gcc -fsyntax-only 會噴「找不到 include」，
# 那是缺 -I 不是程式碼有錯，而誤報比沒有更糟。沒有這個檔就安靜跳過。
# glob 樣式：CLion 是 cmake-build-debug／release／…，VS 的開啟資料夾模式是 out/build/<設定>/。
CC_DB = ("compile_commands.json", "build/compile_commands.json",
         "cmake-build-*/compile_commands.json", "out/build/compile_commands.json",
         "out/build/*/compile_commands.json")
# 「只檢查語法」每家寫法不一樣，先認出驅動程式是誰，認不出來就跳過。
# 樣式容得下交叉編譯器與版號：arm-none-eabi-gcc、gcc-13、clang++-18。
CC_GNU = re.compile(r"(?:^|-)(?:gcc|g\+\+|clang|clang\+\+|cc|c\+\+)(?:-[\d.]+)?$", re.I)
CC_MSVC = re.compile(r"(?:^|-)(?:cl|clang-cl)$", re.I)
CC_MSVC_OUT = re.compile(r"^[/-]F[odpe]", re.I)   # MSVC 的輸出旗標，/Zs 用不到
# 拆 command 用哪一套引號規則。獨立成常數是為了測得到 —— 直接讀 os.name 的話，
# 測試沒辦法在 Linux 上假裝自己是 Windows。
CC_POSIX = os.name != "nt"


def cc_flags(path: Path):
    """從 compile_commands.json 找出這個檔案的編譯指令。回傳 (argv, cwd) 或 None。"""
    for name in CC_DB:
        for db in sorted(ws_root().glob(name)):
            hit = _cc_row(db, path)
            if hit:
                return hit
    return None


def _cc_row(db: Path, path: Path):
    """一份 compile_commands.json 裡有沒有這個檔案。有就回 (argv, cwd)。"""
    try:
        rows = json.loads(db.read_text("utf-8", errors="replace"))
    except (ValueError, OSError):
        return None
    for r in rows:
        if Path(r.get("file", "")).resolve() != path.resolve():
            continue
        # arguments 是現成的陣列，優先用。只有 command 時才自己拆，而 shlex 的
        # POSIX 模式會把 C:\VS\bin\cl.exe 的反斜線當跳脫字元吃掉。
        argv = r.get("arguments")
        if not argv:
            argv = shlex.split(r.get("command", ""), posix=CC_POSIX)
            if not CC_POSIX:
                argv = [a.strip('"') for a in argv]
        if not argv:
            continue
        # 不用 Path().stem：Linux 上讀到 Windows 的資料庫時 PosixPath 不認得反斜線
        exe = re.split(r"[\\/]", argv[0])[-1]
        exe = exe[:-4] if exe.lower().endswith(".exe") else exe
        if CC_GNU.search(exe):
            check, msvc = "-fsyntax-only", False
        elif CC_MSVC.search(exe):
            check, msvc = "/Zs", True
        else:
            return None                # icc、tcc 之類認不得的，不要猜
        # 輸出旗標要丟掉：只檢查語法不產出東西，留著 -o／/Fo 反而會出錯
        out, skip = [], False
        for a in argv[1:]:
            if skip:
                skip = False
            elif a in ("-o", "-c") or (msvc and a.lower() in ("/c", "-c")):
                skip = a == "-o"
            elif not (msvc and CC_MSVC_OUT.match(a)):
                out.append(a)
        return ([argv[0], check] + out, r.get("directory") or str(ws_root()))
    return None


def lint_after_write(path: Path) -> str:
    """回傳要接在工具結果後面的檢查輸出。沒問題、沒裝、不認得的副檔名都回空字串。"""
    ext = path.suffix.lower()
    if ext == ".py":
        if not shutil.which("ruff"):
            # ruff 沒裝就用 ast：標準函式庫，不用開行程，而且抓的正好是模型最常
            # 寫壞的東西 —— 語法錯誤。比完全沒有檢查好太多。
            try:
                ast.parse(path.read_text("utf-8", errors="replace"), str(path))
            except SyntaxError as e:
                return ("[語法檢查] 這個檔案剛寫進去就是壞的，請修：\n"
                        f"{ws_rel(path)}:{e.lineno}: {e.msg}")
            except Exception:
                pass
            return ""
        cmd = ["ruff", "check", "--no-cache", "--quiet", ws_rel(path)]
    elif ext in repomap.C_EXT:
        # 標頭檔不在 compile_commands.json 裡（那只記翻譯單元），所以只檢查 .c/.cpp。
        # ponytail: 容器裡 configure 出來的資料庫記的是 /work/…，跟宿主機對不起來，
        #           這條線就安靜地不存在。不做路徑對映 —— 那種機器通常也沒有編譯器。
        hit = cc_flags(path) if ext not in (".h", ".hpp", ".hh", ".hxx") else None
        if not hit:
            return ""
        cmd, cwd = hit
        try:
            r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=LINT_TIMEOUT)
        except Exception:
            return ""
        if r.returncode == 0:
            return ""
        body = tail_of((r.stderr + r.stdout).strip(), 20).strip()
        body = body.replace(str(ws_root()) + os.sep, "")
        return "[語法檢查] 這個檔案編不過，請修：\n" + body if body else ""
    elif ext in (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"):
        # eslint 幾乎都裝在專案裡而不是全域，所以先找 node_modules/.bin
        local = ws_root() / "node_modules" / ".bin" / "eslint"
        exe = str(local) if local.is_file() else shutil.which("eslint")
        if not exe:
            return ""
        cmd = [exe, "--no-color", ws_rel(path)]
    else:
        return ""
    try:
        r = subprocess.run(cmd, cwd=str(ws_root()), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=LINT_TIMEOUT)
    except Exception:
        return ""
    # 只理會 exit 1（＝真的有問題）。ruff 跟 eslint 的 2 都是「linter 自己有狀況」，
    # 最常見的就是專案根本沒有 eslint 設定檔 —— 拿那個去吵模型只會害它亂改。
    if r.returncode != 1:
        return ""
    # 輸出會原封不動回給模型，所以把工作區前綴剝掉：eslint 不管你丟相對還是
    # 絕對路徑都印絕對路徑，等於每次寫檔都把主機的目錄結構餵進 context。
    body = tail_of((r.stdout + r.stderr).strip(), 20).strip()
    body = body.replace(str(ws_root()) + os.sep, "")
    if not body:
        return ""
    return f"[{Path(cmd[0]).name}] 這是剛剛寫入的檔案的檢查結果，請修掉：\n{body}"


def preview_risk(name: str, args: dict) -> str:
    """確認卡要不要標紅、自動模式能不能跳過。

    load_skill 也算在裡面：它**會跑指令**，只是名字不叫 run_shell。少了這一支
    的話 `autoApprove()` 看到 risk 一律是 "ok"，而 load_skill 又列在
    READ_ONLY_TOOLS 裡 —— 「唯讀自動」以上一次都不會問，確認卡連出現的機會都沒有。
    """
    if name == "run_shell":
        return command_risk(args.get("command", ""))[0]
    if name == "load_skill":
        try:
            folder, raw = skill_find(args.get("name", ""))
        except Exception:
            return "ok"
        will_run = [c for c in skill_commands(raw)
                    if skill_trusted(folder) and not auto_cmd_block(c)]
        return "risky" if will_run else "ok"
    return "ok"


def preview_tool(name: str, args: dict) -> str:
    """給確認卡看的東西：改檔案是 diff，跑指令是風險評估。"""
    """寫入前先算出 diff 給人看。不會碰到磁碟。"""
    if name == "load_skill":
        # 這份 skill 會不會順便跑指令，要在按下去之前就看得到
        try:
            folder, raw = skill_find(args.get("name", ""))
            cmds = skill_commands(raw)
        except Exception:
            return ""
        if not cmds:
            return ""
        why = ("" if skill_trusted(folder) else "模型改得到這份 skill，不代跑指令")

        def line(c):
            no = why or auto_cmd_block(c)
            return f"  {c}" + (f"   ⛔ 不會跑：{no}" if no else "")
        return "這份 skill 會先執行：\n" + "\n".join(line(c) for c in cmds)
    if name == "run_shell":
        level, why = command_risk(args.get("command", ""))
        box = ""
        if ALLOW_SANDBOX:
            try:
                mod = sandbox.pick(SANDBOX_BACKEND)
                box = ("🛡 會在沙盒裡跑（" + mod.NAME + "／" + mod.KIND + "）："
                       + "、".join(mod.describe()["isolation"]))
            except RuntimeError as e:
                box = f"⚠ 沙盒開著但用不了：{e}"
        if level == "ok":
            return box
        head = "⛔ 這行指令會被拒絕執行" if level == "block" else "⚠ 這行指令會改動環境"
        return f"{head}：{why}" + ("\n" + box if box else "")
    if name == "delete_file":
        p = ws_path(args.get("path", ""), must_exist=True)
        if p.is_dir():
            return f"（{ws_rel(p)} 是資料夾，這支只刪單一檔案）"
        old = p.read_text("utf-8", errors="replace")
        # 整個檔案當成「全部刪掉」的 diff：要按下去的人得看得到刪的是什麼
        return unified(old, "", ws_rel(p), ("現在", "刪除後"))
    if name == "write_file":
        p = ws_path(args.get("path", ""))
        old = p.read_text("utf-8", errors="replace") if p.exists() else ""
        return unified(old, args.get("content", ""), ws_rel(p))
    if name == "edit_file":
        p = ws_path(args.get("path", ""), must_exist=True)
        old = p.read_text("utf-8", errors="replace")
        try:
            items = edit_items(args.get("old", ""), args.get("new", ""),
                               args.get("replace_all"), args.get("edits"))
            new_text, _, notes = apply_edits(old, items, ws_rel(p))
        except ValueError as e:
            return f"（無法預覽：{e}）"
        return unified(old, new_text, ws_rel(p)) + ("\n" + "\n".join(notes) if notes else "")
    return ""


def run_tool(name: str, args: dict) -> str:
    """執行一個工具。呼叫端負責先問過使用者。"""
    agent_guard(name)          # 子代理的工具白名單。在網頁之外再擋一次是刻意的
    if name in ("ask_user_question", "task"):
        # 這兩支由網頁處理：問問題要有人在，子代理的模型迴圈也跑在瀏覽器那一端
        raise ValueError("這個工具由網頁處理，不在伺服器執行")
    if name.startswith("mcp__"):
        if not isinstance(args, dict):
            raise ValueError("args 必須是物件")
        out = mcp_call(name, args)
        if len(out) > TOOL_OUTPUT_LIMIT:
            out = out[:TOOL_OUTPUT_LIMIT] + f"\n…（已截斷，原本 {len(out)} 個字元）"
        return out
    fn = TOOLS.get(name)
    if fn is None:
        raise ValueError(f"沒有這個工具：{name}")
    if not isinstance(args, dict):
        raise ValueError("args 必須是物件")
    if name in WS_TOOLS and cur().ws is None:
        raise PermissionError("這個工具需要先設定工作區資料夾")
    if name in WRITE_TOOLS and not cur().write:
        raise PermissionError("檔案修改沒有開啟（介面：功能與工具 → 修改檔案）")
    # 跟工具白名單同一個道理：tool_defs() 那層只是「不要讓它看到」，
    # 模型幻覺出 write_file 送到 /tool 就繞過去了
    if name in WRITE_TOOLS and cur().plan["on"] and not cur().plan["approved"]:
        raise PermissionError("計畫模式：計畫還沒核准，先用 submit_plan 送出計畫")
    # deny 規則在伺服器這一端擋。只在瀏覽器擋的話，那不是邊界是提醒。
    hit = rule_match(name, args)
    if hit and hit["action"] == "deny":
        # 講清楚是哪一份規則擋的：專案跟全域各一份，指錯檔案等於叫人白找
        raise PermissionError(
            f"規則擋下來了：{hit['tool']} / {hit['pattern']}"
            + (f"（{hit['note']}）" if hit["note"] else "")
            + f"。這條在「{hit.get('scope', '')}」那一份，介面：功能與工具 → 允許規則")
    out = fn(**args)
    if name in WRITE_TOOLS:
        try:
            note = lint_after_write(ws_path(str(args.get("path", ""))))
        except Exception:
            note = ""            # 檢查出事絕對不能把已經成功的寫檔變成錯誤
        if note:
            out = out + "\n\n" + note
    if len(out) > TOOL_OUTPUT_LIMIT:
        out = out[:TOOL_OUTPUT_LIMIT] + f"\n…（已截斷，原本 {len(out)} 個字元）"
    if name != "todo_write":
        try:
            note = sync_todo_file()      # 截斷之後才接，這一段不能被截掉
        except Exception:
            note = ""
        if note:
            out = out + "\n\n" + note
    return out


# 空字串＝沒帶 Host（HTTP/1.0、非瀏覽器的呼叫），跟沒帶 Origin 同一個理由放行。
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0", ""}
# 這台機器自己的名字也算本機：手機用 http://macbook.local:5678 連進來要能用，
# 不然 --host 0.0.0.0 這條路等於被這道關卡關掉了。gethostname 不查 DNS，不會卡。
LOCAL_HOSTS |= {n.lower() for n in
                (socket.gethostname(), socket.gethostname() + ".local")}


def same_site(host: str, origin: str, method: str = "POST") -> str:
    """這個請求是不是從自己的網頁發出來的。回傳擋掉的原因，空字串＝放行。

    **沒有這一道 `_is_local()` 擋不住瀏覽器**：你逛到的任何網頁都能用一張
    `enctype="text/plain"` 的表單 POST 到 /tool —— 不觸發預檢、來源 IP 是 127.0.0.1。
    兩條：有 Origin 就要跟 Host 對得上（`null` 不算）；Host 必須是本機的名字或 IP
    —— DNS rebinding 之後 Origin 跟 Host 會一致，只有後者看得出來。
    method 影響第二條不是筆誤：GET 同源時**不帶** Origin，所以一定要看 Host。
    """
    if TRUST_REMOTE:
        # 放在最前面：反向代理預設會把 Host 改寫成後端位址（nginx 的 proxy_pass、
        # Apache 的 ProxyPreserveHost Off），那時候 Origin 跟 Host 永遠對不上。
        # 這個旗標就是「把這道門整個關掉」，不是只關一半。
        return ""
    host = (host or "").strip()
    origin = (origin or "").strip()
    if origin and origin.lower() not in ("http://" + host.lower(), "https://" + host.lower()):
        return f"這個請求來自別的網站（Origin: {origin[:60]}）"
    if not origin and method.upper() not in ("GET", "HEAD"):
        return ""
    # 大小寫、port、IPv6 的方括號一次處理掉；`localhost.` 那個結尾的點也要去掉
    name = (urllib.parse.urlsplit("//" + host).hostname or "").rstrip(".")
    if name in LOCAL_HOSTS:
        return ""
    try:
        ipaddress.ip_address(name)
    except ValueError:
        return f"Host 不是本機位址（{name[:60]}）；用網域名連進來要加 --trust-remote"
    return ""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    ollama = "http://localhost:11434"
    server_version = "ZackLLMGUI/1.0"

    def log_message(self, fmt, *args):
        # 只在出事時說話，正常請求不洗畫面
        if not str(args[1] if len(args) > 1 else "").startswith("2"):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def parse_request(self) -> bool:
        # 掛在這裡而不是每支 do_* 開頭：headers 解析完、do_* 還沒跑，而且只有一處
        # 會漏。keep-alive 同一條連線來的第二個請求也會重新跑，所以綁定不會殘留。
        ok = super().parse_request()
        if not ok:
            return ok
        _CUR.s = session_for(self.headers.get("X-Tab", ""))
        # 掛在這裡而不是 do_POST：漏掉的不只是「未來新增的方法」，現在就漏著
        # GET —— rebinding 之後 /ext 是可讀的轉發代理，/upstream 會吐工作區路徑。
        why = same_site(self.headers.get("Host", ""),
                        self.headers.get("Origin", ""), self.command)
        if why:
            # 沒讀 body 就回應了，這條連線不能留著給下一個請求用
            self.close_connection = True
            self._json({"error": why + "。這支服務只接受自己那一頁發出的請求。"}, 403)
            return False
        return ok

    # -- 回應工具 ---------------------------------------------------- #

    def _send_bytes(self, data: bytes, content_type: str, code: int = 200) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, code: int = 200) -> None:
        self._send_bytes(json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                         "application/json; charset=utf-8", code)

    def _is_local(self) -> bool:
        """工具會在這台機器上跑指令，只認本機來的請求。

        --host 0.0.0.0 時同網段任何人都連得到這支服務，這道檢查是必要的邊界。
        --trust-remote 可以放寬（例如自己用手機或另一台電腦操作），
        代價是：能開這個網頁的人就能在這台機器上執行指令。
        """
        if TRUST_REMOTE:
            return True
        return self.client_address[0] in ("127.0.0.1", "::1", "::ffff:127.0.0.1")

    # 這一次請求的 body 有沒有被讀掉。**每個 POST 都必須讀掉或吃掉**，見 _drain_body。
    _body_read = False

    def _read_body(self, limit: int = MAX_UPLOAD) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        if length > limit:
            # 不讀了，但這樣 socket 裡就留著東西 —— 只能把這條連線收掉
            self.close_connection = True
            self._body_read = True
            raise ValueError(f"內容超過上限 {limit // (1024 * 1024)}MB")
        data = self.rfile.read(length) if length else b""
        self._body_read = True
        return data

    def _drain_body(self) -> None:
        """把沒讀完的 request body 吃掉。

        不吃的話 keep-alive 的下一個請求會從殘留的 body 開始解析，症狀是
        `501 Unsupported method (\'{}GET\')`。任何一條提早 return 的路徑都會踩到
        （403、沒工作區、例外），所以統一在 do_POST 收尾做，不靠各支自己記得。
        """
        if self._body_read:
            return
        self._body_read = True
        left = int(self.headers.get("Content-Length") or 0)
        if left <= 0:
            return
        if left > MAX_UPLOAD:          # 太大就別吃了，斷線比較快
            self.close_connection = True
            return
        while left > 0:
            chunk = self.rfile.read(min(left, 65536))
            if not chunk:
                break
            left -= len(chunk)

    def _serve_page(self) -> None:
        # 改完 frontend/ 直接重新整理就看得到，不用記得先跑 build.py。
        # 組不起來就用現成的那份，別讓整個網頁打不開。
        if page_build is not None:
            try:
                page_build.build()
            except Exception as e:
                sys.stderr.write(f"frontend 組合失敗，改用現成的 {PAGE.name}：{e}\n")
        try:
            data = PAGE.read_bytes()
        except OSError:
            self._send_bytes(
                f"找不到 {PAGE.name}，請確認它和 serve.py 放在同一個資料夾。".encode("utf-8"),
                "text/plain; charset=utf-8", 500)
            return
        self._send_bytes(data, "text/html; charset=utf-8")

    # -- 代理 -------------------------------------------------------- #

    def _proxy(self, method: str) -> None:
        # 一定要走 _read_body()：它會標記「body 已經讀掉了」。自己 rfile.read()
        # 的話 _drain_body 收尾時會**再讀一次**，等於多吃掉下一個請求開頭的
        # 同樣長度。症狀是下一個請求變成 `Bad request syntax ('0.1:8899')`
        # —— 那串是 `Host: 127.0.0.1:8899` 被咬掉前半截剩下的。
        body = self._read_body() or None

        req = urllib.request.Request(
            self.ollama + self.path, data=body, method=method,
            headers={"Content-Type": self.headers.get("Content-Type", "application/json"),
                     "Accept": "application/json"},
        )
        self._pipe(req)

    def _watch_client(self, upstream, done: threading.Event) -> None:
        """瀏覽器斷線就把上游那條連線切掉。

        只有**寫入**客戶端才發得出 BrokenPipe，而模型在載入或跑 prompt eval
        的時候一個 token 都還沒出來 —— 那段時間 _pipe 卡在 read1 上，沒有東西
        可寫，按了停止也傳不到 Ollama。這條執行緒專門盯著客戶端那一頭。
        """
        sock = self.connection
        while not done.is_set():
            try:
                if not select.select([sock], [], [], 0.5)[0]:
                    continue
                # MSG_PEEK 不吃掉資料：收得到東西就不是斷線（瀏覽器不會在
                # 串流中途塞下一個請求，但別為了省一行就把那種情況當斷線）
                if sock.recv(1, socket.MSG_PEEK):
                    continue
            except OSError:
                pass                   # RST 也算斷線
            # shutdown 才叫得醒卡住的 read1，close 不一定會。
            # 私有屬性，所以包起來 —— 拿不到就退回 close()，那是現在的行為。
            try:
                upstream.fp.raw._sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            upstream.close()
            return

    def _pipe(self, req: urllib.request.Request) -> None:
        """送出請求，把回應原封不動串流回瀏覽器。"""
        target = req.full_url
        try:
            upstream = urllib.request.urlopen(req, timeout=UPSTREAM_TIMEOUT)
        except urllib.error.HTTPError as e:
            # Ollama 的錯誤原封不動轉回去，網頁才能顯示真正的訊息
            payload = e.read()
            self._send_bytes(payload or str(e.reason).encode("utf-8"),
                             e.headers.get("Content-Type", "application/json"), e.code)
            return
        except urllib.error.URLError as e:
            self._json({"error": f"無法連線到 {target} — {e.reason}"}, 502)
            return
        except OSError as e:
            self._json({"error": str(e)}, 502)
            return

        # 用 chunked 轉發，串流才不會被整包緩衝住
        self.send_response(upstream.status)
        self.send_header("Content-Type",
                         upstream.headers.get("Content-Type", "application/json"))
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        done = threading.Event()
        watch = threading.Thread(target=self._watch_client, args=(upstream, done),
                                 daemon=True)
        watch.start()
        try:
            while True:
                # 一定要用 read1：read(n) 會等到收滿 n bytes 才回來，
                # NDJSON 是一行一行慢慢吐的，用 read() 會把串流整個緩衝住，
                # 畫面上看起來就是「按了送出之後卡住不動」。
                chunk = upstream.read1(CHUNK)
                if not chunk:
                    break
                self.wfile.write(b"%x\r\n" % len(chunk))
                self.wfile.write(chunk)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass          # 使用者按了停止，正常現象
        except (OSError, ValueError):
            pass          # _watch_client 把上游切掉了，也是停止
        finally:
            done.set()
            upstream.close()

    # -- 外部 API（OpenAI 相容） -------------------------------------- #

    def _do_ext(self, method: str) -> None:
        """把請求轉給 X-Target 指定的外部 API。

        存在的理由跟 /api/* 一樣是 CORS：瀏覽器直接打 api.openai.com 會被擋。
        金鑰由瀏覽器帶在 Authorization 上，這裡只負責轉送、不落地、不記錄。
        只接受本機請求，否則綁 0.0.0.0 時就變成別人的免費跳板。
        """
        if not self._is_local():
            self._json({"error": "外部 API 轉送只允許本機呼叫。"}, 403)
            return
        target = self.headers.get("X-Target", "")
        if not target.lower().startswith(("http://", "https://")):
            self._json({"error": "X-Target 必須是完整的 http/https 位址"}, 400)
            return
        try:
            body = self._read_body() if method == "POST" else None
        except ValueError as e:
            self._json({"error": str(e)}, 413)
            return
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        auth = self.headers.get("Authorization")
        if auth:
            headers["Authorization"] = auth
        self._pipe(urllib.request.Request(target, data=body, method=method, headers=headers))

    # -- 路由 -------------------------------------------------------- #

    def do_GET(self):
        if self.path == "/upstream":
            # 網頁的狀態列要顯示真正的 Ollama 位址，不是代理自己的 port
            self._json({"upstream": self.ollama, "extract": True, "ext": self._is_local(),
                        "tools": ALLOW_TOOLS and self._is_local(),
                        "tools_local": self._is_local(),
                        "workspace": workspace_info(),
                        "tool_defs": tool_defs(),
                        "agent_rules": agent_rules(),
                        "repo_map": repo_map(),
                        "verify_hint": verify_detect(),
                        "missing_tools": ws_missing_tools(),
                        "todos": cur().todos, "jobs": jobs_state(),
                        "plan": cur().plan["on"], "browser": ALLOW_BROWSER,
                        "sandbox": ALLOW_SANDBOX, "sandbox_info": sandbox_state(),
                        "mcp": mcp_status(),
                        "agents": agent_types(),
                        "stream_tools": sorted(STREAM_TOOLS),
                        # num_thread 的上限。只有 Ollama 跟這支服務同機時才算得準 ——
                        # Ollama 的 API 沒有任何一支回報主機的核心數。
                        "cpus": os.cpu_count() or 0,
                        "ollama_local": ollama_is_local(),
                        "client": self.client_address[0], "trust_remote": TRUST_REMOTE})
        elif self.path == "/sys":
            # topbar 的用量。輕到可以每幾秒問一次（nvidia-smi 有 1.5 秒的快取）。
            # 只回給本機：這台機器有幾張卡、多少記憶體不是給同網段的人看的。
            self._json(sys_usage() if self._is_local() else {})
        elif self.path == "/alive":
            # 很輕的一支，網頁每 30 秒問一次。只回「程式碼有沒有被改過」
            self._json({"src_changed": source_stamp() != SRC_STAMP,
                        "local": self._is_local()})
        elif self.path == "/ext":
            self._do_ext("GET")
        elif self.path.startswith("/api/"):
            self._proxy("GET")
        elif self.path in ("/", "/index.html", "/zackllmgui.html", "/ollama_gui.html"):
            self._serve_page()
        elif self.path == "/favicon.ico":
            self._send_bytes(b"", "image/x-icon", 204)
        else:
            self._send_bytes(b"Not Found", "text/plain; charset=utf-8", 404)

    def do_POST(self):
        self._body_read = False
        try:
            # 跨站的請求在 parse_request 就擋掉了，走不到這裡
            self._route_post()
        finally:
            # 提早 return 的路徑（403、參數錯、例外）都不會讀 body，
            # 沒吃掉的話下一個請求就會解析到它。
            self._drain_body()

    def _route_post(self):
        if self.path.startswith("/api/"):
            self._proxy("POST")
        elif self.path == "/extract":
            self._do_extract()
        elif self.path == "/ext":
            self._do_ext("POST")
        elif self.path == "/tool":
            self._do_tool()
        elif self.path == "/verify":
            self._do_verify()
        elif self.path == "/tools":
            self._do_tools_toggle()
        elif self.path == "/workspace":
            self._do_workspace()
        elif self.path == "/preview":
            self._do_preview()
        elif self.path == "/restore":
            self._do_restore()
        elif self.path == "/view":
            self._do_view()
        elif self.path == "/run":
            self._do_run()
        elif self.path == "/git":
            self._do_git()
        elif self.path == "/mcp":
            self._do_mcp()
        elif self.path == "/browse":
            self._do_browse()
        elif self.path == "/ls":
            self._do_ls()
        elif self.path == "/skills":
            self._do_skills()
        elif self.path == "/agent":
            self._do_agent()
        elif self.path == "/rules":
            self._do_rules()
        elif self.path == "/restart":
            self._do_restart()
        elif self.path == "/journal":
            self._do_journal()
        elif self.path == "/checkpoint":
            self._do_checkpoint()
        elif self.path == "/rewind":
            self._do_rewind()
        else:
            self._send_bytes(b"Not Found", "text/plain; charset=utf-8", 404)

    def _chunk(self, obj) -> None:
        payload = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        self.wfile.write(b"%x\r\n" % len(payload))
        self.wfile.write(payload)
        self.wfile.write(b"\r\n")
        self.wfile.flush()

    def _do_run(self) -> None:
        """跑指令並且邊跑邊回傳輸出（NDJSON）。

        同步版跑 pytest 時畫面會整整卡住好幾分鐘，看不出來到底是在跑還是掛了。
        沒有 job id：使用者按停止就是把這條連線斷掉，這裡寫入失敗時順手把程序殺掉。
        """
        if not ALLOW_TOOLS or not self._is_local():
            self._json({"error": "本機工具沒有開啟，或這個請求不是從本機來的"}, 403)
            return
        try:
            req = json.loads(self._read_body(64 * 1024) or b"{}")
            if not isinstance(req, dict):
                raise ValueError("body 要是物件")
            name = req.get("name", "")
            if name not in STREAM_TOOLS:
                raise ValueError(f"{name} 不支援串流執行")
            # 子代理有自己的 worktree 時，cwd 要跟著它 —— 這一段算完 cmd/cwd 就切回來，
            # 後面的串流不需要（也不該）還掛在子代理的 Session 上
            with as_agent(str(req.get("agent", ""))):
                agent_guard(name)
                if cur().ws is None:
                    raise PermissionError("這個工具需要先設定工作區資料夾")
                args = req.get("args") or {}
                hit = rule_match(name, args)
                if hit and hit["action"] == "deny":
                    raise PermissionError(
                        f"規則擋下來了：{hit['tool']} / {hit['pattern']}"
                        + (f"（{hit['note']}）" if hit["note"] else ""))
                cmd, cwd, use_shell, head = build_command(name, args)
        except Exception as e:
            self._json({"error": f"{type(e).__name__}: {e}"}, 400)
            return

        # 二進位模式用預設的區塊緩衝，不要 bufsize=0（見 core/jobs.py 的說明）
        proc = subprocess.Popen(cmd, shell=use_shell, cwd=cwd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, **process_group_kwargs())
        limit = TEST_TIMEOUT if name == "run_tests" else SHELL_TIMEOUT
        watchdog = threading.Timer(limit, kill_tree, args=(proc,))
        watchdog.start()

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        ring = collections.deque(maxlen=RING_LINES)
        dropped = 0
        total = 0
        flooded = False
        try:
            self._chunk({"line": head})
            for line in proc.stdout:
                line = decode_output(line, use_shell).rstrip("\r\n")
                raw_chars = len(line)
                if len(line) > MAX_LINE_CHARS:
                    line = line[:MAX_LINE_CHARS] + f"…（這一行被截斷，原本 {len(line)} 字元）"
                total += raw_chars + 1
                if len(ring) == RING_LINES:
                    dropped += 1
                ring.append(line)
                self._chunk({"line": line})
                if total > MAX_RUN_BYTES:
                    # 逾時之前就先把畫面灌爆的那種指令，砍掉比等它跑完好
                    flooded = True
                    kill_tree(proc)
                    note = (f"輸出超過 {MAX_RUN_BYTES // (1024 * 1024)}MB，指令已被中止。"
                            f"請縮小範圍或把輸出導到檔案再讀。")
                    self._chunk({"line": "⛔ " + note})
                    ring.append("⛔ " + note)
                    break
            code = proc.wait()
            out = "\n".join(ring)
            if dropped:
                out = f"（前面省略 {dropped} 行）\n" + out
            self._chunk({"done": True, "code": code, "flooded": flooded,
                         "result": f"{head}\n[exit {code}]\n" + tail_of(out)})
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            kill_tree(proc)      # 使用者按了停止，別讓指令在背景跑到天荒地老
        finally:
            watchdog.cancel()
            try:
                proc.stdout.close()
            except Exception:
                pass

    def _do_browse(self) -> None:
        """挑工作區用的資料夾瀏覽。只列資料夾名稱，不碰內容。"""
        if not self._is_local():
            self._json({"error": "只允許本機呼叫"}, 403)
            return
        try:
            req = json.loads(self._read_body(8192) or b"{}")
            path = req.get("path", "") if isinstance(req, dict) else ""
            self._json(browse_dirs(path))
        except Exception as e:
            self._json({"error": f"{type(e).__name__}: {e}"}, 400)

    def _do_ls(self) -> None:
        """檔案樹的一層。跟檔案工具走同一道路徑限制。"""
        if not self._is_local():
            self._json({"error": "只允許本機呼叫"}, 403)
            return
        try:
            req = json.loads(self._read_body(8192) or b"{}")
            if cur().ws is None:
                raise PermissionError("還沒設定工作區")
            rel = req.get("path", "") if isinstance(req, dict) else ""
            # flat：輸入框打 @ 要挑檔案，那需要一份平的清單而不是一層一層點。
            # 上限擋住 node_modules 那種一萬個檔案的專案 —— 選單塞不下也沒人捲得完。
            if isinstance(req, dict) and req.get("flat"):
                files = []
                for f in ws_walk():
                    files.append(ws_rel(f))
                    if len(files) >= AT_FILE_CAP:
                        break
                self._json({"files": sorted(files), "capped": len(files) >= AT_FILE_CAP})
                return
            # 新增資料夾也走這一支：介面開完就要重讀同一層，一趟就夠
            if isinstance(req, dict) and req.get("mkdir"):
                made = make_dir(str(req["mkdir"]))
                self._json({"made": made, "path": rel, "entries": list_entries(rel)})
                return
            self._json({"path": rel, "entries": list_entries(rel)})
        except Exception as e:
            self._json({"error": f"{type(e).__name__}: {e}"}, 400)

    def _do_rules(self) -> None:
        """允許規則的讀寫。只有本機能改 —— 它決定什麼可以自動放行。"""
        if not self._is_local():
            self._json({"error": "只允許本機呼叫"}, 403)
            return
        try:
            req = json.loads(self._read_body(64 * 1024) or b"{}")
            if not isinstance(req, dict):
                raise ValueError("body 要是物件")
            action = req.get("action", "list")
            if action == "add":
                scope = "專案" if cur().ws is not None else "全域"
                r = {"tool": str(req.get("tool", "*")) or "*",
                     "pattern": str(req.get("pattern", "*")) or "*",
                     "action": str(req.get("rule", "allow")).lower(),
                     "note": str(req.get("note", ""))[:200], "scope": scope}
                if r["action"] not in ("allow", "ask", "deny"):
                    raise ValueError("rule 只接受 allow / ask / deny")
                mine = [x for x in rules_load() if x["scope"] == scope
                        # 同樣的工具＋樣式只留一條，不然清單很快就沒人看得懂
                        and not (x["tool"] == r["tool"] and x["pattern"] == r["pattern"])]
                rules_save(mine + [r], scope)
            elif action == "remove":
                rules = rules_load()
                i = int(req.get("index", -1))
                if not 0 <= i < len(rules):
                    raise ValueError("沒有這一條規則")
                gone = rules.pop(i)
                rules_save([x for x in rules if x["scope"] == gone["scope"]], gone["scope"])
            elif action != "list":
                raise ValueError("action 只接受 list / add / remove")
            self._json({"rules": rules_load(),
                        "files": [{"scope": sc, "path": str(f), "exists": f.is_file()}
                                  for sc, f in rules_files()]})
        except Exception as e:
            self._json({"error": f"{type(e).__name__}: {e}"}, 400)

    def _do_restart(self) -> None:
        """把自己換成新的行程。只有本機能叫 —— 這等於遠端重啟服務。"""
        if not self._is_local():
            self._json({"error": "只有本機可以重新啟動服務。"}, 403)
            return
        self._json({"ok": True, "pid": os.getpid()})
        try:
            self.wfile.flush()
        except Exception:
            pass
        # 先把回應送出去再換掉自己。直接在這裡 execv 的話，網頁收到的是連線被
        # 中斷，分不出「重啟了」跟「serve.py 掛了」。
        threading.Timer(0.3, restart_self).start()

    def _do_skills(self) -> None:
        try:
            req = json.loads(self._read_body(8192) or b"{}")
            name = req.get("name", "") if isinstance(req, dict) else ""
            if name:
                self._json({"name": name, "body": skill_body(name)})
            else:
                self._json({"roots": [str(r) for r in skills_roots()],
                            "skills": skills_list()})
        except Exception as e:
            self._json({"error": f"{type(e).__name__}: {e}"}, 400)

    def _do_journal(self) -> None:
        if not self._is_local():
            self._json({"error": "只允許本機呼叫"}, 403)
            return
        try:
            req = json.loads(self._read_body(8192) or b"{}")
            if cur().ws is None:
                self._json({"entries": []})
                return
            chat = req.get("chat", "") if isinstance(req, dict) else ""
            self._json({"entries": journal_for(chat), "chat": chat,
                        "total": len(journal_read())})
        except Exception as e:
            self._json({"error": f"{type(e).__name__}: {e}"}, 400)

    def _do_checkpoint(self) -> None:
        """每則提示送出前照一張相。一律回 200 —— 拍不到不能擋住送訊息，
        原因放在 skipped 裡讓介面說。"""
        if not self._is_local():
            self._json({"error": "只允許本機呼叫"}, 403)
            return
        try:
            req = json.loads(self._read_body(8192) or b"{}")
            workspace.set_cur_chat(req.get("chat", ""))
            self._json(checkpoint(str(req.get("note", "")),
                                  int(req.get("msg", -1))))
        except Exception as e:
            self._json({"skipped": f"{type(e).__name__}: {e}"})

    def _do_rewind(self) -> None:
        if not self._is_local():
            self._json({"error": "只允許本機呼叫"}, 403)
            return
        try:
            req = json.loads(self._read_body(8192) or b"{}")
            if not isinstance(req, dict) or not req.get("id"):
                raise ValueError("要指定還原點 id")
            if cur().ws is None:
                raise PermissionError("還沒設定工作區")
            self._json(rewind_to(req["id"]))
        except Exception as e:
            self._json({"error": f"{type(e).__name__}: {e}"}, 400)

    def _do_git(self) -> None:
        if not self._is_local():
            self._json({"error": "只允許本機呼叫"}, 403)
            return
        try:
            req = json.loads(self._read_body(8192) or b"{}")
            if not isinstance(req, dict):
                raise ValueError("body 要是物件")
            self._json(git_action(req.get("action", "status"), req.get("message", "")))
        except Exception as e:
            self._json({"error": f"{type(e).__name__}: {e}"}, 400)

    def _do_mcp(self) -> None:
        if not self._is_local():
            self._json({"error": "只允許本機呼叫"}, 403)
            return
        try:
            req = json.loads(self._read_body(8192) or b"{}")
            action = req.get("action", "status") if isinstance(req, dict) else "status"
            status = mcp_load() if action == "reload" else mcp_status()
            self._json(dict(status, tool_defs=tool_defs()))
        except Exception as e:
            self._json({"error": f"{type(e).__name__}: {e}"}, 400)

    # -- 附加檔案解析 ------------------------------------------------ #

    def _do_extract(self) -> None:
        name = urllib.parse.unquote(self.headers.get("X-Filename", "file.txt"))
        try:
            data = self._read_body()
            text = extract_text(name, data)
        except Exception as e:                      # 解析失敗要講人話，網頁會直接顯示
            self._json({"error": str(e)}, 400)
            return
        self._json({"name": name, "text": text, "chars": len(text)})

    # -- 工具開關（由網頁控制） --------------------------------------- #

    def _do_tools_toggle(self) -> None:
        """讓網頁自己開關工具，不必重啟服務。

        仍然只有本機能改：遠端裝置就算打開網頁也無法替這台機器開啟工具。
        """
        global ALLOW_TOOLS
        if not self._is_local():
            self._json({"error": "只有本機可以開關工具。"}, 403)
            return
        try:
            req = json.loads(self._read_body(4096) or b"{}")
            if not isinstance(req, dict):
                raise ValueError("body 要是物件，例如 {\"enabled\": true}")
        except Exception as e:
            self._json({"error": str(e)}, 400)
            return
        if "enabled" in req:
            ALLOW_TOOLS = bool(req.get("enabled"))
        if "write" in req:
            cur().write = bool(req.get("write")) and cur().ws is not None
        if "browser" in req:
            global ALLOW_BROWSER
            ALLOW_BROWSER = bool(req.get("browser"))
        if "sandbox" in req:
            global ALLOW_SANDBOX, SANDBOX_BACKEND
            want = bool(req.get("sandbox"))
            if want:
                # 開之前先真的挑一次：挑不出來就把原因原封不動丟回網頁，
                # 不要讓使用者按下去之後才在跑指令時才發現。
                sandbox.pick(str(req.get("backend", "") or SANDBOX_BACKEND))
                SANDBOX_BACKEND = str(req.get("backend", "") or SANDBOX_BACKEND)
            ALLOW_SANDBOX = want
        if "auto" in req:
            want = str(req.get("auto") or "off")
            if want not in AUTO_MODES:
                self._json({"error": f"不認得的自動模式：{want}"}, 400)
                return
            cur().auto = want
        if "plan" in req:
            cur().plan["on"] = bool(req.get("plan"))
            cur().plan["approved"] = not cur().plan["on"]
        print(f"  工具     {'已啟用' if ALLOW_TOOLS else '已關閉'}"
              f"{'，可修改檔案' if cur().write else ''}（由網頁切換）")
        self._json({"tools": ALLOW_TOOLS, "write": cur().write, "plan": cur().plan["on"],
                    "browser": ALLOW_BROWSER, "sandbox": ALLOW_SANDBOX,
                    "auto": cur().auto, "sandbox_info": sandbox_state(),
                    "tool_defs": tool_defs(), "agent_rules": agent_rules(),
                    "repo_map": repo_map(), "agents": agent_types(),
                    "missing_tools": ws_missing_tools()})

    # -- 工作區 ------------------------------------------------------ #

    def _do_workspace(self) -> None:
        """設定 / 查詢工作區。只有本機能改：工具會在這台機器上動檔案。"""
        if not self._is_local():
            self._json({"error": "只有本機可以設定工作區。"}, 403)
            return
        try:
            req = json.loads(self._read_body(8192) or b"{}")
            info = set_workspace(req.get("path", ""))
            if cur().ws is None:
                cur().write = False
                info = workspace_info()
        except Exception as e:
            self._json({"error": str(e)}, 400)
            return
        print(f"  工作區   {info.get('path') or '（未設定）'}")
        info["tool_defs"] = tool_defs()
        info["agent_rules"] = agent_rules()
        info["repo_map"] = repo_map()       # 換了工作區，地圖當然要跟著換
        info["verify_hint"] = verify_detect()
        info["agents"] = agent_types()      # 專案自己的 agents/ 會蓋掉內建的
        info["missing_tools"] = ws_missing_tools()
        self._json(info)

    def _do_preview(self) -> None:
        """算 diff 給確認卡看，不會寫入任何東西。"""
        if not ALLOW_TOOLS or not self._is_local():
            self._json({"error": "工具未啟用"}, 403)
            return
        try:
            req = json.loads(self._read_body(4 * 1024 * 1024) or b"{}")
            name = req.get("name", "")
            args = req.get("args") or {}
            with as_agent(str(req.get("agent", ""))):
                agent_guard(name)      # 預覽會把檔案內容算成 diff 送回去，一樣要擋
                diff = preview_tool(name, args)
                risk = preview_risk(name, args)
                # 只在風險指令上算一次：前端要用它決定「工作區內全自動」放不放行
                scope = "ws" if risk == "risky" and ws_scoped(args.get("command", "")) else ""
                hit = rule_match(name, args)
        except Exception as e:
            self._json({"error": f"{type(e).__name__}: {e}"}, 400)
            return
        self._json({"diff": diff, "risk": risk, "rule": hit, "scope": scope})

    def _do_verify(self) -> None:
        """跑一次收尾用的驗證指令。

        **第三條沒有確認卡的執行路徑**（另外兩條：skill 的 !`指令`、自動模式）。
        所以兩件事寫死：指令只能來自使用者在介面上打的字（專案裡的檔案一個字都不讀），
        而且照樣過 `auto_cmd_block()`。
        """
        if not ALLOW_TOOLS or not self._is_local():
            self._json({"error": "工具未啟用"}, 403)
            return
        try:
            req = json.loads(self._read_body(8192) or b"{}")
            cmd = " ".join(str(req.get("command", "")).split())
            if not cmd:
                self._json({"error": "沒有設定驗證指令"}, 400)
                return
            why = auto_cmd_block(cmd)
            if why:
                self._json({"error": f"這條指令不會自動執行：{why}"}, 400)
                return
            argv, cwd, use_shell, _ = build_command("run_shell", {"command": cmd})
            proc = subprocess.Popen(argv, shell=use_shell, cwd=cwd,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    **process_group_kwargs())
            try:
                out = proc.communicate(timeout=TEST_TIMEOUT)[0]
            except subprocess.TimeoutExpired:
                kill_tree(proc)          # sh 的孫子不殺就會留下來，見 kill_tree
                self._json({"error": f"驗證指令跑超過 {TEST_TIMEOUT} 秒已中止"}, 400)
                return
        except Exception as e:
            self._json({"error": f"{type(e).__name__}: {e}"}, 400)
            return
        self._json({"command": cmd, "exit": proc.returncode,
                    "output": tail_of(decode_output(out, use_shell))})

    def _do_view(self) -> None:
        """把工作區裡的檔案內容送給介面顯示。

        跟 read_file 不同：這是給人看的，不加行號前綴、不截斷成模型看的樣子。
        帶 backup 就順便附上「備份 → 現在」的 diff，改壞了才看得出來改了什麼。
        """
        if not self._is_local():
            self._json({"error": "只允許本機檢視檔案。"}, 403)
            return
        try:
            req = json.loads(self._read_body(8192) or b"{}")
            p = ws_path(req.get("path", ""), must_exist=True)
            if p.stat().st_size > MAX_FILE_BYTES:
                raise ValueError(f"{ws_rel(p)} 太大（{p.stat().st_size // 1000}KB），不在這裡顯示")
            text = p.read_text("utf-8", errors="replace")
            out = {"path": ws_rel(p), "text": text, "lines": text.count("\n") + 1}
            mark = str(req.get("backup") or "")
            if mark:
                root = ws_root().resolve()
                src = (root / mark).resolve()
                if (root / BACKUP_DIR) in src.parents and src.exists():
                    out["diff"] = unified(src.read_text("utf-8", errors="replace"),
                                          text, ws_rel(p), ("修改前", "現在"))
        except Exception as e:
            self._json({"error": f"{type(e).__name__}: {e}"}, 400)
            return
        self._json(out)

    def _do_restore(self) -> None:
        if not self._is_local():
            self._json({"error": "只有本機可以還原檔案。"}, 403)
            return
        try:
            req = json.loads(self._read_body(8192) or b"{}")
            restored = restore_backup(req.get("backup", ""))
        except Exception as e:
            self._json({"error": str(e)}, 400)
            return
        self._json({"restored": restored})

    # -- 工具執行 ---------------------------------------------------- #

    def _do_agent(self) -> None:
        """開／收子代理的 worktree。只有本機能用：它會在這台機器上建資料夾。"""
        if not ALLOW_TOOLS or not self._is_local():
            self._json({"error": "工具未啟用"}, 403)
            return
        try:
            req = json.loads(self._read_body(4096) or b"{}")
            act = str(req.get("action", "open"))
            if act == "open":
                self._json(agent_open(str(req.get("type", "")), str(req.get("parent", "")),
                                      str(req.get("chat", "")), str(req.get("task", ""))))
            elif act == "close":
                self._json(agent_close(str(req.get("id", "")), bool(req.get("force"))))
            elif act == "stop":
                self._json(agent_stop(str(req.get("id", "")), str(req.get("why", ""))))
            elif act == "trace":
                self._json(agent_trace(str(req.get("id", ""))))
            elif act == "list":
                self._json({"agents": [agent_view(v) for v in cur().agents.values()],
                            "orphans": worktree_orphans(),
                            "types": agent_types(), "depth_max": SUB_DEPTH_MAX})
            else:
                raise ValueError(f"不認得的動作：{act}")
        except Exception as e:
            self._json({"error": f"{type(e).__name__}: {e}"}, 400)

    def _do_tool(self) -> None:
        if not ALLOW_TOOLS:
            self._json({"error": "工具未啟用，請在右側面板打開「讓模型呼叫本機工具」。"}, 403)
            return
        if not self._is_local():
            self._json({"error": "工具只允許本機呼叫。"}, 403)
            return
        try:
            req = json.loads(self._read_body(1024 * 1024) or b"{}")
            workspace.set_cur_chat(req.get("chat", ""))
            with as_agent(str(req.get("agent", ""))):
                out = run_tool(req.get("name", ""), req.get("args") or {})
        except subprocess.TimeoutExpired:
            self._json({"error": f"執行超過 {SHELL_TIMEOUT} 秒已中止"}, 400)
            return
        except Exception as e:
            self._json({"error": f"{type(e).__name__}: {e}"}, 400)
            return
        self._json({"result": out, "todos": cur().todos, "plan": cur().plan,
                    "jobs": jobs_state(), "tool_defs": tool_defs()})

    def do_DELETE(self):
        # /api/delete 會刪掉 Ollama 的模型。--host 0.0.0.0 時同網段任何人都連得到
        # 這支服務，而 DELETE 走不到 do_POST 的那一道 —— 這裡自己擋。
        self._body_read = False
        try:
            if not self._is_local():
                self._json({"error": "只接受本機請求。"}, 403)
            elif self.path.startswith("/api/"):
                self._proxy("DELETE")
            else:
                self._send_bytes(b"Not Found", "text/plain; charset=utf-8", 404)
        finally:
            self._drain_body()


def build_server(ollama: str, bind: str, port: int) -> ThreadingHTTPServer:
    Handler.ollama = normalize(ollama)
    server = ThreadingHTTPServer((bind, port), Handler)
    server.daemon_threads = True
    return server


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        description="ZackLLMGUI 啟動器（同時代理 Ollama API，避開 CORS）")
    parser.add_argument("--ollama", default=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
                        help="Ollama 位址，例如 http://192.168.1.20:11434")
    parser.add_argument("--port", type=int, default=5678, help="本機服務的 port（預設 5678）")
    parser.add_argument("--host", default="127.0.0.1",
                        help="綁定位址；要讓同網段其他裝置連進來就填 0.0.0.0")
    parser.add_argument("--no-browser", action="store_true", help="不要自動開瀏覽器")
    parser.add_argument("--workspace", default="",
                        help="模型可以讀寫的專案資料夾。預設是啟動 serve.py 的目錄，"
                             "隨時可以在網頁的設定 → 工作區裡改。")
    parser.add_argument("--trust-remote", action="store_true",
                        help="讓非本機的瀏覽器也能設定工作區與開啟工具。"
                             "等於把這台機器的 shell 開放給連得到這個網頁的人，自己斟酌。")
    parser.add_argument("--allow-write", action="store_true",
                        help="啟動時就允許修改檔案（仍然每次都要在網頁上確認）")
    parser.add_argument("--no-tools", action="store_true",
                        help="啟動時關閉本機工具。預設是開著的（仍然只接受本機請求、"
                             "每次執行前網頁都會先問你），網頁上隨時能再切換。")
    parser.add_argument("--sandbox", nargs="?", const="auto", default=None,
                        metavar="後端",
                        help="啟動時就把 run_shell / run_tests 關進沙盒。"
                             "不給值就自己挑（Linux 用 bubblewrap、macOS 用 sandbox-exec、"
                             "Windows 用 Docker Desktop）；也可以指定 bwrap／seatbelt／container。"
                             "預設關，網頁上隨時能開。")
    parser.add_argument("--sandbox-image", default="", metavar="映像檔",
                        help="容器後端要用哪個映像檔（預設 python:3.13-slim）。"
                             "那一個裡面沒有編譯器，所以 C/C++ 專案要換成有工具鏈的，"
                              "例如 gcc:14 或自己 build 一個。只影響容器後端，"
                              "bubblewrap／sandbox-exec 用的是你機器上原本的工具鏈。")
    parser.add_argument("--sandbox-gpu", action="store_true",
                        help="把 GPU 接進容器沙盒（Docker 需要 NVIDIA Container Toolkit，"
                             "映像檔仍須包含工作負載需要的 CUDA runtime）。")
    args = parser.parse_args()

    global ALLOW_TOOLS, TRUST_REMOTE, ALLOW_SANDBOX, SANDBOX_BACKEND, SANDBOX_IMAGE, SANDBOX_GPU
    SANDBOX_IMAGE = args.sandbox_image
    SANDBOX_GPU = args.sandbox_gpu
    ALLOW_TOOLS = not args.no_tools
    if args.sandbox:
        want = "" if args.sandbox == "auto" else args.sandbox
        try:
            mod = sandbox.pick(want)
            ALLOW_SANDBOX, SANDBOX_BACKEND = True, mod.NAME
            print(f"  沙盒     {mod.NAME}（{mod.KIND}）")
        except RuntimeError as e:
            print(f"（--sandbox 開不起來：{e}）", file=sys.stderr)
    TRUST_REMOTE = args.trust_remote
    # 預設把啟動目錄當工作區：多數人就是在專案裡開這支程式。
    # 家目錄或根目錄會被 set_workspace 擋掉，那就留空讓使用者自己在網頁上選。
    try:
        set_workspace(args.workspace or os.getcwd())
        cur().write = args.allow_write
    except Exception as e:
        if args.workspace:
            print(f"工作區設定失敗：{e}", file=sys.stderr)
            return 1
        print(f"（沒有預設工作區：{e}）")

    ollama = normalize(args.ollama)
    try:
        server = build_server(ollama, args.host, args.port)
    except OSError as e:
        print(f"無法在 {args.host}:{args.port} 啟動：{e}", file=sys.stderr)
        print("換一個 port 試試：python serve.py --port 8888", file=sys.stderr)
        return 1

    url = f"http://{'127.0.0.1' if args.host in ('0.0.0.0', '') else args.host}:{args.port}/"
    if page_build is not None:
        try:
            if page_build.build():
                print(f"已從 frontend/ 重新組出 {PAGE.name}")
        except Exception as e:
            print(f"frontend 組合失敗，改用現成的 {PAGE.name}：{e}", file=sys.stderr)

    print("ZackLLMGUI 已啟動")
    print(f"  網頁     {url}")
    print(f"  轉給     {ollama}")
    if args.host == "0.0.0.0":
        print("  已綁定 0.0.0.0，同網段的其他裝置也能開這個網址")
    print("  工具     " + ("已啟用（預設）" if ALLOW_TOOLS else "已關閉（--no-tools）") +
          "，只接受本機請求，每次執行前網頁都會先問你")
    if cur().ws:
        print(f"  工作區   {cur().ws}" + ("（可修改檔案）" if cur().write else "（唯讀）"))
        # 上一次跑到一半就關掉的話，登記沒了但 worktree 還在磁碟上。開機講一次，
        # 不然它們會安靜地積在專案裡 —— 只講不動，分支上可能有還沒收的成果。
        try:
            left = worktree_orphans()
        except Exception:
            left = []
        for o in left:
            print(f"  子代理   {o['id']} 還留著：{o['branch']}"
                  + (f"（{o['changes']} 個未提交的改動）" if o["changes"] else "")
                  + " —— 網頁的 /agents 可以收")
    if TRUST_REMOTE:
        print("  注意     --trust-remote：連得到這個網頁的人都能在這台機器上執行指令")
    try:
        status = mcp_load()
        for srv in status.get("servers", []):
            note = srv["error"] or f"{len(srv['tools'])} 支工具"
            print(f"  MCP      {srv['name']}：{note}")
    except Exception as e:
        print(f"  MCP      載入失敗：{e}")
    print("按 Ctrl+C 結束")

    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已結束")
    finally:
        mcp_stop()
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
