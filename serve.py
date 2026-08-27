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
import difflib
import fnmatch
import io
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
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

# ── 工作區（改檔案／跑測試用，見 plan-agent.md） ──────────────────── #
WORKSPACE = None                   # Path；沒設定就沒有任何檔案工具
ALLOW_WRITE = False                # 寫入類工具要再多一道開關
BACKUP_DIR = ".zackllmgui-backup"
# 這些資料夾不讓模型碰：版控內部、虛擬環境、相依套件、備份自己
DENY_DIRS = {".git", ".hg", ".svn", ".venv", "venv", "env", "node_modules",
             "__pycache__", ".mypy_cache", ".pytest_cache", ".tox", "dist",
             "build", ".next", ".idea", ".vscode", BACKUP_DIR}
# 這些檔案不讓模型讀，免得金鑰跟著進 context
# 這裡也擋 .zackllmgui-*.json：那幾個檔案決定「什麼可以自動放行」，
# 讓模型讀得到等於讓它知道怎麼繞，寫得到就等於自己給自己開權限。
DENY_FILES = re.compile(r"^(\.env(\..*)?|.*\.env|.*\.pem|.*\.key|.*\.pfx|id_rsa.*|"
                        r".*\.p12|.*credentials.*\.json|\.npmrc|\.netrc|"
                        r"\.zackllmgui-.*\.json)$", re.I)
MAX_FILE_BYTES = 400_000           # 單檔上限，再大就不是給模型看的
AT_FILE_CAP = 3000                 # 輸入框打 @ 時最多列幾個檔案
# 專案自己的說明檔。Claude Code 讀 CLAUDE.md、Codex 與 grok-build 讀 AGENTS.md，
# 這裡三種都收，找到第一個就用。
AGENT_FILES = ("AGENTS.md", "CLAUDE.md", "GROK.md", ".cursorrules")
PROJECT_MD_LIMIT = 6000
TODOS = []                         # [{"text": ..., "done": bool}]
PLAN = {"text": "", "approved": False}
REQUIRE_PLAN = False               # 開了就要先送計畫、人核准之後才給寫入工具
TRUST_REMOTE = False               # --trust-remote：非本機的瀏覽器也能開工具與設工作區
# 連網瀏覽（搜尋 + 開頁 + 跟連結走）。預設關著：它會讓模型主動連出去，
# 那跟「讀本機檔案」是不同性質的權限，該由使用者自己按下去。
ALLOW_BROWSER = False

# 沙盒：把 run_shell / run_tests / setup_env 丟進容器跑。
# run_shell 是唯一跑得出工作區的工具 —— 檔案工具有 ws_path() 擋著，它沒有。
# 預設關的理由跟連網瀏覽一樣，但更硬：要先裝 docker 或 podman，
# 而這個專案的賣點是零相依。開得起來才顯示得出來，開不起來會講原因。
ALLOW_SANDBOX = False

SANDBOX_BACKEND = ""               # 空的＝照 sandbox/ 的偏好順序自己挑
SEARCH_HITS = 80
TEST_TIMEOUT = 900
STREAM_TOOLS = {"run_shell", "run_tests"}   # 這兩支走 /run 串流，其他工具沒必要
RING_LINES = 2000                  # 串流時最多留這麼多行回灌給模型
# 輸出也要有上限，不是只有時間上限：`yes`、`find /`、跑歪的測試都會在逾時之前
# 先把瀏覽器灌爆。超過就砍掉程序，並且把原因寫進回給模型的結果裡。
MAX_RUN_BYTES = 2 * 1024 * 1024
MAX_LINE_CHARS = 4000              # 單行上限。minified JS 或 base64 一行就好幾 MB

# ── 背景指令 ──────────────────────────────────────────────────── #
# run_shell 原本只有 30 秒：npm install、cargo build、docker build、
# 一次資料庫遷移，沒有一個跑得完。而且它是同步的，跑的時候整個 agent 迴圈
# 都卡在那裡。背景版把程序交給一條讀取執行緒，工具立刻回一個 id，
# 模型可以先去做別的，之後用 check_job 收。
#
# 這推翻了 tech.md〈長指令為什麼沒做 job API〉的結論。那個結論的前提是
# 「每次工具呼叫都要人確認，所以人一定在旁邊看著」—— 有了全自動模式，
# 前提就不成立了。
BG_TIMEOUT = 3600                  # 背景指令的上限，一小時
BG_MAX = 8                         # 同時最多這麼多條，忘了收的不會無限累積
# check_job 預設等一下再回話。實測過不等的版本：模型每兩秒問一次，
# 45 秒的指令燒掉七輪還沒收到結果 —— 提示詞寫「不要空轉」完全沒有用。
# 等待期間 CPU 是閒的，換來的是一輪抵十輪。
BG_WAIT = 20
BG_WAIT_MAX = 300
JOBS = {}                          # id -> {proc, lines, code, ...}
JOBS_LOCK = threading.Lock()
JOB_SEQ = 0

# focus chain（跟 Cline 學的）：待辦清單同時是工作區裡的一份 markdown。
# 模型跑的時候你直接編輯它，下一輪就會同步進去 —— 不用插話也能改方向。
TODO_FILE = ".zackllmgui-todos.md"

# ── MCP（Model Context Protocol）客戶端 ──────────────────────────── #
MCP_CONFIG = ".zackllmgui-mcp.json"
RULES_FILE = ".zackllmgui-rules.json"   # 允許規則：兩份都讀，專案的優先
MCP_TOOL_CAP = 12                  # 一台 server 最多收這麼多支工具，否則每輪光工具定義就燒掉幾千 token
MCP = {}                           # server 名 -> {"proc", "tools", "lock", "id", "error"}

# ── 操作紀錄與 rewind ───────────────────────────────────────────── #
# 每一次改檔案都記一行。備份本來就有了，缺的是「先後順序」——
# 沒有順序就只能一個檔一個檔還原，沒辦法「退回十分鐘前」。
JOURNAL = "journal.jsonl"          # 放在 BACKUP_DIR 底下
# 現在是哪一則對話在呼叫工具。網頁每次呼叫都會帶上來，記進 journal，
# 「紀錄」分頁才能只顯示這一則對話改過的東西。
# ponytail: 一個全域變數，靠請求順序決定。一次只跑一個工具（每一次都要人確認）
#           所以夠用；真的要平行跑工具的話得改成傳參數穿到 journal_add。
CURRENT_CHAT = ""
SKILLS_DIR = "skills"              # serve.py 旁邊；工作區有自己的就用工作區的
SKILL_BODY_LIMIT = 8000


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


# ══════════════════════ 檔案解析 ══════════════════════ #

def _pdf_text(data: bytes) -> str:
    """PDF 轉文字。優先用系統的 pdftotext，沒有才退回 pypdf。

    兩個都沒有就明講缺什麼，不要丟一個看不懂的例外。
    """
    exe = shutil.which("pdftotext")
    if exe:
        proc = subprocess.run([exe, "-layout", "-", "-"], input=data,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        if proc.returncode == 0:
            return proc.stdout.decode("utf-8", "replace")
    try:
        import pypdf
    except ImportError:
        raise RuntimeError(
            "這台機器沒有 PDF 解析工具。二選一：\n"
            "  sudo apt install poppler-utils   （Windows：choco install poppler）\n"
            "  pip install pypdf") from None
    reader = pypdf.PdfReader(io.BytesIO(data))
    return "\n\n".join((page.extract_text() or "") for page in reader.pages)


def _docx_text(data: bytes) -> str:
    """.docx / .pptx / .odt 都是 zip 裡的 XML，標籤拔掉就是文字。

    ponytail: 正規表示式拔標籤，不做樣式與表格；要完整版面就換 python-docx。
    """
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = [n for n in z.namelist()
                 if n in ("word/document.xml", "content.xml")
                 or (n.startswith("ppt/slides/slide") and n.endswith(".xml"))]
        if not names:
            raise RuntimeError("這個 zip 裡沒有找到文件內容（不是 docx / odt / pptx？）")
        parts = []
        for name in sorted(names):
            xml = z.read(name).decode("utf-8", "replace")
            xml = re.sub(r"</w:p>|</text:p>|</a:p>", "\n", xml)
            xml = re.sub(r"<[^>]+>", "", xml)
            parts.append(xml)
    text = "".join(parts)
    text = (text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
                .replace("&quot;", '"').replace("&apos;", "'"))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def extract_text(filename: str, data: bytes) -> str:
    ext = Path(filename or "").suffix.lower()
    if ext == ".pdf":
        return _pdf_text(data)
    if ext in (".docx", ".odt", ".pptx"):
        return _docx_text(data)
    return data.decode("utf-8", "replace")


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


def ws_root() -> Path:
    if WORKSPACE is None:
        raise RuntimeError("還沒設定工作區資料夾（介面：設定 → 工作區）")
    return WORKSPACE


def ws_path(rel: str, must_exist: bool = False) -> Path:
    """把相對路徑轉成工作區內的絕對路徑，並確認它真的還在工作區裡。

    resolve() 會把 symlink 一起解開，所以指向外面的連結也會在這裡被擋下來。
    這是整個檔案工具的安全邊界，不要為了方便繞過它。
    """
    root = ws_root().resolve()
    raw = str(rel if rel is not None else ".").strip()
    if raw.startswith("~"):
        raise PermissionError("路徑只能相對於工作區")
    target = (root / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()
    if target != root and root not in target.parents:
        raise PermissionError(f"路徑超出工作區：{raw}")
    parts = target.relative_to(root).parts if target != root else ()
    for part in parts:
        if part in DENY_DIRS:
            raise PermissionError(f"{part} 是不開放的資料夾")
    if parts and DENY_FILES.match(parts[-1]):
        raise PermissionError(f"{parts[-1]} 屬於機密檔案，不開放讀寫")
    if must_exist and not target.exists():
        raise FileNotFoundError(f"找不到 {raw}")
    return target


def ws_rel(p: Path) -> str:
    root = ws_root().resolve()
    return "." if p == root else str(p.relative_to(root))


def ws_walk():
    """走訪工作區，跳過封鎖目錄。"""
    root = ws_root().resolve()
    for base, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in DENY_DIRS and not d.startswith("."))
        for name in sorted(files):
            if DENY_FILES.match(name):
                continue
            yield Path(base) / name


def detect_python() -> list:
    """找出這個專案該用哪個 python 跑測試。順序：.venv → venv → uv → poetry → 系統。"""
    root = ws_root()
    for name in (".venv", "venv"):
        for sub in ("bin/python", "Scripts/python.exe"):
            cand = root / name / sub
            if cand.exists():
                return [str(cand)]
    if (root / "uv.lock").exists() and shutil.which("uv"):
        return ["uv", "run", "python"]
    if (root / "poetry.lock").exists() and shutil.which("poetry"):
        return ["poetry", "run", "python"]
    return [sys.executable]


def project_md() -> tuple:
    """工作區根目錄的專案說明。回傳 (檔名, 內容)，沒有就是 ("", "")。"""
    if WORKSPACE is None:
        return ("", "")
    for name in AGENT_FILES:
        f = WORKSPACE / name
        if f.is_file():
            text = f.read_text("utf-8", errors="replace").strip()
            if len(text) > PROJECT_MD_LIMIT:
                text = text[:PROJECT_MD_LIMIT] + "\n…（專案說明過長，已截斷）"
            return (name, text)
    return ("", "")


TODO_MTIME = 0.0                   # 上一次自己寫進去的時間戳，用來分辨「誰改的」
TODO_LINE = re.compile(r"^\s*(?:\d+[.)]|[-*])\s*\[([ xX])\]\s*(.+?)\s*$")


def todo_file() -> Path | None:
    return None if WORKSPACE is None else ws_root() / TODO_FILE


def write_todo_file() -> None:
    """把待辦寫成工作區裡的一份 markdown。寫失敗不算錯 —— 這只是個鏡像。"""
    global TODO_MTIME
    f = todo_file()
    if f is None:
        return
    try:
        if not TODOS:
            if f.exists():
                f.unlink()
            TODO_MTIME = 0.0
            return
        body = ("# 待辦（跑的時候可以直接改這個檔，改完存檔，下一輪就會同步進去）\n\n"
                + render_todos(sync=False) + "\n")
        f.write_text(body, "utf-8")
        TODO_MTIME = f.stat().st_mtime
    except Exception:
        TODO_MTIME = 0.0


def sync_todo_file() -> str:
    """使用者手改了那份 markdown 就吃回來。回一句話給模型，沒改就回空字串。

    這是 Cline 的 focus chain：跑到一半要改方向，不必打斷它，改檔案就好。
    只認 [x]／[ ] 與文字，相依關係靠文字比對接回去 ——
    使用者手打的行本來就不會帶編號，硬解析只會解出一堆垃圾。
    """
    global TODOS, TODO_MTIME
    f = todo_file()
    if f is None or not TODOS:
        return ""
    try:
        if not f.exists() or abs(f.stat().st_mtime - TODO_MTIME) < 0.001:
            return ""
        lines = f.read_text("utf-8", errors="replace").splitlines()
    except Exception:
        return ""
    was = {t["text"]: t.get("blocked_by", []) for t in TODOS}
    fresh = []
    for ln in lines:
        m = TODO_LINE.match(ln)
        if not m:
            continue
        text = re.sub(r"（要等[^）]*）\s*$", "", m.group(2)).strip()[:200]
        if text:
            fresh.append({"text": text, "done": m.group(1).lower() == "x",
                          "blocked_by": [n for n in was.get(text, []) if n <= len(fresh)]})
    if not fresh or fresh == TODOS:
        TODO_MTIME = f.stat().st_mtime
        return ""
    TODOS = fresh[:20]
    write_todo_file()          # 重新編號之後寫回去，檔案跟清單才是同一份
    left = sum(1 for t in TODOS if not t["done"])
    return (f"[待辦清單] 使用者剛剛手動改了 {TODO_FILE}，這是現在的版本（還剩 {left} 項）："
            f"\n{render_todos(sync=False)}\n照這份做，不要用你記得的舊版本。")


def render_todos(sync: bool = True) -> str:
    if sync:
        sync_todo_file()
    if not TODOS:
        return "（清單是空的）"
    out = []
    for i, t in enumerate(TODOS, start=1):
        # 編號要給模型看得到，不然它下一次沒辦法用 blocked_by 指回來
        line = f"{i}. " + ("[x] " if t["done"] else "[ ] ") + t["text"]
        blocked = [n for n in t.get("blocked_by", [])
                   if 1 <= n <= len(TODOS) and not TODOS[n - 1]["done"]]
        if blocked:
            line += "（要等 " + "、".join("#" + str(n) for n in blocked) + "）"
        out.append(line)
    return "\n".join(out)


def workspace_info() -> dict:
    if WORKSPACE is None:
        return {"path": "", "write": False}
    root = WORKSPACE.resolve()
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
        "write": ALLOW_WRITE,
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


def set_workspace(path: str) -> dict:
    global WORKSPACE
    if not str(path or "").strip():
        WORKSPACE = None
        return workspace_info()
    p = Path(path).expanduser().resolve()
    if not p.is_dir():
        raise NotADirectoryError(f"{p} 不是一個資料夾")
    if p == Path.home().resolve() or p == Path(p.anchor):
        # 整個家目錄或磁碟根目錄當工作區等於沒有邊界
        raise PermissionError("請指定專案資料夾，不要用家目錄或根目錄")
    WORKSPACE = p
    return workspace_info()


def backup_file(p: Path) -> str:
    """改檔案之前先留一份，介面上才有「還原」可以按。

    時間戳只到秒，同一秒內改同一個檔案兩次就會蓋掉前一份備份 ——
    模型連續改同一個檔案時這是常態，不是邊角情況。撞到就在後面加序號，
    第一份（也就是最原始的那一份）永遠留得住。
    """
    root = ws_root().resolve()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    rel = p.relative_to(root)
    for n in range(1, 1000):
        dst = root / BACKUP_DIR / stamp / rel
        if not dst.exists():
            break
        dst = root / BACKUP_DIR / f"{stamp}-{n}" / rel
        if not dst.exists():
            break
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(p, dst)
    return str(dst.relative_to(root))


def journal_path() -> Path:
    return ws_root().resolve() / BACKUP_DIR / JOURNAL


def journal_add(tool: str, rel: str, backup: str, created: bool) -> str:
    """記一筆改檔案的操作。回傳這一筆的 id。

    寫失敗不能讓工具跟著失敗 —— 紀錄是為了方便，不是為了正確性。
    """
    entry = {
        "id": f"{time.time():.6f}",
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "tool": tool, "path": rel, "backup": backup, "created": created,
        "chat": CURRENT_CHAT,
    }
    try:
        f = journal_path()
        f.parent.mkdir(parents=True, exist_ok=True)
        with f.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return entry["id"]


def journal_read() -> list:
    f = journal_path()
    if not f.is_file():
        return []
    out = []
    for line in f.read_text("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def journal_for(chat: str) -> list:
    """某一則對話改過的東西。

    每一筆多帶兩個數字，因為**還原一定是照時間倒著做的**：
    退回某一筆之前，那之後的所有改動都要退掉 —— 包含別則對話改的。
    只給這則對話的清單卻偷偷動到別人的東西，那是騙人；
    所以把「總共會退幾筆」與「其中幾筆是別的對話」一起送出去，確認框寫得出實話。
    """
    entries = journal_read()
    out = []
    for i, e in enumerate(entries):
        if chat and e.get("chat") and e["chat"] != chat:
            continue
        rest = entries[i:]
        out.append(dict(e, undo_count=len(rest),
                        other_chats=sum(1 for x in rest
                                        if x.get("chat") and x.get("chat") != e.get("chat"))))
    return out


def rewind_to(entry_id: str) -> dict:
    """把工作區退回「某一筆操作發生之前」的樣子。

    做法是把那一筆之後（含那一筆）的操作**反著做回去**：
    有備份就複製回來，是新建的檔案就刪掉。順序不能反 ——
    同一個檔案被改過三次時，只有從最新往回走才會停在正確的版本。
    """
    entries = journal_read()
    idx = next((i for i, e in enumerate(entries) if e.get("id") == entry_id), -1)
    if idx < 0:
        raise ValueError("找不到這個還原點")

    undone, failed = [], []
    for e in reversed(entries[idx:]):
        rel = e.get("path", "")
        try:
            target = ws_path(rel)
            if e.get("created"):
                if target.exists():
                    target.unlink()
                undone.append(f"刪除 {rel}（原本不存在）")
            elif e.get("backup"):
                restore_backup(e["backup"])
                undone.append(f"還原 {rel}")
            else:
                failed.append(f"{rel}：沒有備份可以還原")
        except Exception as ex:
            failed.append(f"{rel}：{type(ex).__name__}: {ex}")

    # 還原本身也是一次操作，記下來（但不記成可以再被 rewind 的項目）
    keep = entries[:idx]
    try:
        f = journal_path()
        f.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in keep),
                     encoding="utf-8")
    except OSError:
        pass
    return {"undone": undone, "failed": failed, "entries": keep}


def restore_backup(rel: str) -> str:
    root = ws_root().resolve()
    src = (root / rel).resolve()
    if root / BACKUP_DIR not in src.parents and (root / BACKUP_DIR) != src.parent:
        raise PermissionError("只能還原備份資料夾裡的檔案")
    if not src.exists():
        raise FileNotFoundError(f"找不到備份 {rel}")
    # .zackllmgui-backup/<時間戳>/<原本的相對路徑>
    parts = Path(rel).parts
    dest = ws_path(str(Path(*parts[2:])))
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return ws_rel(dest)


def unified(old: str, new: str, name: str, labels=("現在", "改後")) -> str:
    diff = difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                fromfile=f"{name}（{labels[0]}）",
                                tofile=f"{name}（{labels[1]}）", n=3)
    text = "".join(diff)
    return text or "（內容沒有變化）"


# ══════════════════════ 工具（給模型呼叫） ══════════════════════ #

# ── 檔案讀過沒有／讀完之後被改過沒有 ─────────────────────────── #
# edit_file 的 old 對不上時，原本只會說「找不到要取代的內容」。模型看到這句
# 的反應是「換個字串再試一次」，可是真正的原因常常是**檔案在它讀過之後被改了**
# （使用者在編輯器裡動了、或它自己剛寫過）。它會一直換字串，然後撞上前端的
# 連續失敗上限，白燒兩輪。
#
# 所以這裡記一份「讀的時候檔案長什麼樣」，錯誤訊息才分得出是哪一種失敗。
# Claude Code 是用同一份狀態直接**擋下**未讀先改；這裡不擋 —— old 要完全吻合
# 本來就擋住了錯誤的修改，多擋一層只會讓猜對的情況也不能改。這裡只換訊息。
# ponytail: 一個永遠不清的 dict，鍵是絕對路徑。上限是「讀過幾個檔案」，
#           一次 session 幾百筆，不值得做淘汰。真的要淘汰再換 OrderedDict。
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


def _tool_search_files(pattern: str, glob: str = "") -> str:
    """在工作區裡找字串，只回命中的那幾行 —— 整檔讀進去會把 context 吃光。"""
    try:
        rx = re.compile(pattern)
    except re.error as e:
        raise ValueError(f"pattern 不是合法的正規表示式：{e}") from None
    hits, scanned = [], 0
    for f in ws_walk():
        # 檔名與相對路徑都比對一次：模型很自然會傳 "pkg/calc.py" 或 "pkg/*.py"，
        # 只比對 f.name 的話那兩種寫法都會掃到 0 個檔案（實測害小模型直接放棄）。
        if glob and not (fnmatch.fnmatch(f.name, glob)
                         or fnmatch.fnmatch(ws_rel(f), glob)
                         or fnmatch.fnmatch(ws_rel(f), "*/" + glob.lstrip("/"))):
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


def _tool_edit_file(path: str, old: str, new: str, replace_all: bool = False) -> str:
    """精確字串取代。刻意不吃 diff 也不吃行號：小模型算不對，錯了就改到別的地方。"""
    p = ws_path(path, must_exist=True)
    text = p.read_text("utf-8", errors="replace")
    if old == new:
        raise ValueError("old 與 new 一樣，沒有東西要改")
    count = text.count(old)
    if count == 0:
        hint = ""
        if re.match(r"^\s*\d+\u2192", old):
            hint = "（old 裡面帶了 read_file 的「行號→」前綴，那不是檔案內容）"
        else:
            hint = stale_hint(p)
        raise ValueError(f"在 {ws_rel(p)} 裡找不到要取代的內容{hint}，請先用 read_file 確認原文")
    if count > 1 and not replace_all:
        raise ValueError(f"要取代的內容在 {ws_rel(p)} 出現 {count} 次，"
                         f"請多帶一些前後文讓它唯一，或設 replace_all=true")
    mark = backup_file(p)
    journal_add("edit_file", ws_rel(p), mark, False)
    p.write_text(text.replace(old, new) if replace_all else text.replace(old, new, 1),
                 encoding="utf-8")
    note_read(p)          # 自己剛寫的內容不算「被別人改過」
    return f"已修改 {ws_rel(p)}（{count if replace_all else 1} 處）\n[backup]{mark}"


# 一定要擋下來的：打錯一個字就回不去的那種。
# 這裡列的是「無法用備份救回來」的操作，跟 rm 掉工作區裡的檔案不同層級。
BLOCKED_CMDS = [
    (r"\brm\s+(-[a-zA-Z]*\s+)*(/|/\*|~|~/|\$HOME)(\s|$)", "rm 掉根目錄或家目錄"),
    (r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f|\brm\s+-[a-zA-Z]*f[a-zA-Z]*r", "rm -rf（請指明確切路徑，或用 git 還原）"),
    (r"\bmkfs(\.|\s)", "格式化磁碟"),
    (r"\bdd\s+[^|]*of=/dev/", "dd 寫進裝置"),
    (r">\s*/dev/(sd|nvme|hd)", "覆寫磁碟裝置"),
    (r":\(\)\s*\{\s*:\|:&\s*\}\s*;\s*:", "fork bomb"),
    (r"\b(shutdown|reboot|halt|poweroff)\b", "關機或重開機"),
    (r"\bchmod\s+-R\s+777\s+/(\s|$)", "把根目錄權限打開"),
    (r"\b(userdel|groupdel|passwd)\b", "動到系統帳號"),
    (r"\bgit\s+push\b[^|;]*--force", "強制推送（會覆蓋遠端歷史）"),
    (r"\bcurl\b[^|]*\|\s*(sudo\s+)?(ba)?sh", "把網路上的東西直接餵給 shell"),
    (r"\bwget\b[^|]*\|\s*(sudo\s+)?(ba)?sh", "把網路上的東西直接餵給 shell"),
]

# 會改動環境但救得回來的：不擋，但確認卡要標紅，自動模式也一定要問人。
RISKY_CMDS = [
    (r"\bsudo\b", "用 sudo 提權"),
    (r"\brm\b", "刪除檔案"),
    (r"\bpip\s+(install|uninstall)|\bnpm\s+(i|install|uninstall)\b|\bconda\s+(install|remove)",
     "安裝或移除套件"),
    (r"\bapt(-get)?\s+(install|remove|purge)|\byum\s+(install|remove)", "動到系統套件"),
    (r"\bgit\s+(push|reset\s+--hard|clean\s+-[a-zA-Z]*f|checkout\s+--\s)", "動到 git 歷史或工作區"),
    (r"\bmv\b|\bchmod\b|\bchown\b", "搬動檔案或改權限"),
    (r">\s*/(etc|usr|bin|boot|lib)", "寫進系統目錄"),
    (r"\bkill(all)?\b|\bpkill\b", "終止程序"),
]


def command_risk(command: str) -> tuple:
    """判斷一行指令的風險。回傳 ("block"|"risky"|"ok", 原因)。

    這是後端的判斷，前端的確認卡直接顯示它的結論 ——
    風險判斷寫兩份的話，總有一份會過期。
    """
    cmd = " ".join(str(command or "").split())
    for pattern, why in BLOCKED_CMDS:
        if re.search(pattern, cmd, re.I):
            return ("block", why)
    for pattern, why in RISKY_CMDS:
        if re.search(pattern, cmd, re.I):
            return ("risky", why)
    return ("ok", "")


def _tool_run_shell(command: str, background: bool = False) -> str:
    # 走 build_command 而不是自己判斷一次：風險檢查與沙盒包裝只能有一份，
    # 兩份遲早會有一份忘了改（同步這條路本來就少人走）。
    cmd, cwd, use_shell, head = build_command("run_shell", {"command": command})
    if background:
        return _start_job(command, cmd, cwd, use_shell, head)
    proc = subprocess.run(cmd, shell=use_shell, cwd=cwd, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, timeout=SHELL_TIMEOUT)
    out = proc.stdout.decode("utf-8", "replace")
    return f"[exit {proc.returncode}]\n{out}"


def kill_tree(proc) -> None:
    """殺掉整棵程序樹，不是只殺最上面那一個。

    `shell=True` 跑的是 `sh -c "…"`。指令一複雜，sh 就會 fork 而不是 exec，
    真正在跑的東西是 sh 的**孫子** —— `proc.kill()` 只殺得到 sh，孫子繼續跑，
    而且繼續握著 stdout 的寫入端，讀取執行緒就永遠等不到 EOF。
    （實測：check_job 說「已終止」，jobs_state 卻永遠停在「還在跑」。）
    Popen 用 start_new_session 讓它自己一個 process group，這裡整組送 SIGKILL。
    """
    try:
        os.killpg(os.getpgid(proc.pid), 9)
    except Exception:
        try:
            proc.kill()          # Windows 沒有 process group，退回去殺單一程序
        except Exception:
            pass


def _job_reader(job, proc, watchdog) -> None:
    """把程序的輸出一路讀進 ring buffer。跑完就記下 exit code。

    整支都在 try 裡：這條執行緒死掉的話 job 會永遠停在「還在跑」，
    模型就會一直去 check_job 直到輪數用完。
    """
    try:
        for line in proc.stdout:
            line = line.rstrip("\n")
            if len(line) > MAX_LINE_CHARS:
                line = line[:MAX_LINE_CHARS] + "…（這一行被截斷）"
            with JOBS_LOCK:                # 讀取端會 join 這個 deque，不能邊改邊讀
                job["lines"].append(line)
    except Exception as e:
        with JOBS_LOCK:
            job["lines"].append(f"（讀取輸出時出錯：{type(e).__name__}: {e}）")
    finally:
        watchdog.cancel()
        try:
            job["code"] = proc.wait()
        except Exception:
            job["code"] = -1
        job["ended"] = time.time()
        try:
            proc.stdout.close()
        except Exception:
            pass


def _start_job(command: str, cmd, cwd, use_shell: bool, head: str) -> str:
    global JOB_SEQ
    with JOBS_LOCK:
        alive = [j for j in JOBS.values() if j["code"] is None]
        if len(alive) >= BG_MAX:
            raise RuntimeError(
                f"背景指令已經有 {len(alive)} 條在跑（上限 {BG_MAX}）。"
                f"先用 check_job 把跑完的收掉，或用 check_job(kill=true) 終止不要的。")
        JOB_SEQ += 1
        jid = f"job{JOB_SEQ}"
        job = {"id": jid, "cmd": " ".join(str(command).split())[:200], "code": None,
               "lines": collections.deque(maxlen=RING_LINES),
               "started": time.time(), "ended": 0.0, "proc": None}
        JOBS[jid] = job
    proc = subprocess.Popen(cmd, shell=use_shell, cwd=cwd, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, bufsize=1, text=True,
                            encoding="utf-8", errors="replace",
                            start_new_session=True)
    job["proc"] = proc
    watchdog = threading.Timer(BG_TIMEOUT, kill_tree, args=(proc,))
    watchdog.daemon = True             # 不然 serve.py 要等一小時才關得掉
    watchdog.start()
    t = threading.Thread(target=_job_reader, args=(job, proc, watchdog), daemon=True)
    t.start()
    return (f"{head}\n已經丟到背景跑，id = {jid}（上限 {BG_TIMEOUT // 60} 分鐘）。\n"
            f"**現在先去做別的事**，不要空轉等它。之後用 check_job(\"{jid}\") 收結果 ——"
            f"還沒跑完的話它會告訴你已經跑了多久。")


def _job_tail(job) -> str:
    with JOBS_LOCK:
        return tail_of("\n".join(job["lines"]))


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


def jobs_state() -> list:
    """給網頁看的背景指令狀態。關掉分頁再打開，這些還在。"""
    with JOBS_LOCK:
        every = list(JOBS.values())
    return [{"id": j["id"], "cmd": j["cmd"], "code": j["code"],
             "secs": int((j["ended"] or time.time()) - j["started"])}
            for j in every]


def _tool_load_skill(name: str = "") -> str:
    """把一份 skill 的正文交給模型。

    設計上只有這一支是「按需載入」的：六份內建 skill 的描述加起來 240 token，
    常駐得起；正文全部塞進系統提示會是幾千 token，而九成的對話用不到。
    """
    body = skill_body(name)
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
                                    backend=SANDBOX_BACKEND)
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
                                backend=SANDBOX_BACKEND)
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


def sandbox_state() -> dict:
    """這台機器的沙盒現況。網頁拿它決定按鈕要不要 disable、tooltip 寫什麼。

    偵測的是**跑 serve.py 這一台**（工具本來就在這台跑），不是開網頁那一台。
    """
    info = sandbox.detect()
    return dict(info, on=ALLOW_SANDBOX, backend=SANDBOX_BACKEND or info["backend"])


def sandbox_python() -> str:
    """沙盒裡該用哪個 python，回傳可以直接放進 shell 的一段字。

    分兩種情況，因為後端分兩種：

    - **核心層**（bwrap／seatbelt）：檔案系統就是宿主機的，所以 detect_python()
      算出來的絕對路徑直接可用。這很重要 —— 這台有 python3 但沒有 python，
      寫死 "python" 會變成 `sh: python: not found`（踩過）。
    - **容器**：rootfs 是映像檔的，宿主機的路徑進去不存在，只能用相對路徑或裸的 python。
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
            return (sandbox.wrap(command, ws_root(), backend=SANDBOX_BACKEND),
                    str(ws_root()), False, f"$ {command}")
        return command, (str(WORKSPACE) if WORKSPACE else None), True, f"$ {command}"
    if name == "run_tests":
        if ALLOW_SANDBOX:
            line = sandbox_python() + " -m pytest -q --color=no"
            if args.get("target"):
                line += " " + shlex.quote(ws_rel(ws_path(str(args["target"]), must_exist=True)))
            if args.get("k"):
                line += " -k " + shlex.quote(str(args["k"]))
            return (sandbox.wrap(line, ws_root(), backend=SANDBOX_BACKEND),
                    str(ws_root()), False, "[" + line + "]")
        cmd = detect_python() + ["-m", "pytest", "-q", "--color=no"]
        if args.get("target"):
            cmd.append(str(ws_path(str(args["target"]), must_exist=True)))
        if args.get("k"):
            cmd += ["-k", str(args["k"])]
        return cmd, str(ws_root()), False, "[" + " ".join(cmd) + "]"
    raise ValueError(f"{name} 不支援串流執行")


def tail_of(out: str, keep: int = 100) -> str:
    """測試輸出動輒上千行。只回最後 keep 行，加上所有看起來像錯誤的行。"""
    lines = out.splitlines()
    if len(lines) <= keep:
        return out
    bad = [ln for ln in lines[:-keep]
           if re.search(r"(FAILED|ERROR|Traceback|assert |Exception)", ln)][-40:]
    head = ("（前面省略 %d 行，以下是其中的錯誤行）\n" % (len(lines) - keep) +
            "\n".join(bad) + "\n…\n") if bad else "（前面省略 %d 行）\n" % (len(lines) - keep)
    return head + "\n".join(lines[-keep:])


def _tool_fetch_url(url: str) -> str:
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("只接受 http / https 網址")
    req = urllib.request.Request(url, headers={"User-Agent": "ZackLLMGUI/1.0"})
    with urllib.request.urlopen(req, timeout=20) as res:
        raw = res.read(2 * 1024 * 1024).decode("utf-8", "replace")
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"[ \t]*\n\s*\n+", "\n\n", re.sub(r"[ \t]{2,}", " ", text)).strip()





def agent_rules() -> str:
    """依目前開放的工具拼出給模型的操作規則。

    做法參考 xai-org/grok-prompts 的 Jinja 模板與 grok-build 的工具描述：
    沒開放的功能一個字都不要提，否則小模型會去呼叫不存在的工具。
    """
    if not ALLOW_TOOLS:
        return ""
    r = ["你可以呼叫工具。每一次呼叫都會先讓使用者確認，所以：",
         "- 一次只呼叫一個工具，看到結果再決定下一步。",
         "- 工具失敗時先讀懂錯誤訊息，不要用一模一樣的參數重試。"]
    if WORKSPACE is not None:
        r += [f"- 工作區是 {WORKSPACE}，所有路徑都相對於它，不要用絕對路徑或 ..。",
              "- 找東西先用 search_files 或 list_dir 定位，再用 read_file 讀那一段；"
              "不要整個檔案讀進來。",
              "- read_file 每行開頭的「行號→」是為了讓你引用位置，不是檔案內容。"]
    if ALLOW_WRITE:
        r += ["- 修改既有檔案一律用 edit_file：old 要與檔案內容完全一致（含縮排），"
              "並帶足前後文讓它在檔案裡唯一；write_file 只用來建立新檔案。",
              "- 一次只改一個地方，改完立刻用 run_tests 驗證。",
              "- 測試失敗時修的是程式，不是測試。真的認為測試寫錯，先說出來讓使用者決定。"]
    if WORKSPACE is not None:
        r.append("- 缺套件時用 setup_env 裝進工作區的 .venv，不要用 run_shell 下 pip install。")
    if ALLOW_SANDBOX:
        r.append("- run_shell 與 run_tests 在容器裡跑：只看得到工作區、**沒有網路**。"
                 "要裝套件用 setup_env（只有它連得出去）。")
    if ALLOW_BROWSER:
        r.append("- 需要查網路上的東西：不知道網址就先 run_browser 搜尋，"
                 "拿到網址再 open；open 會一併給你那一頁上的連結，順著走下去。")
    if len(TOOL_SCHEMAS) and ALLOW_TOOLS:
        r.append("- 多步驟的工作先用 todo_write 列出待辦，每完成一項就整份重送並標成完成。")
        r.append("- 需要使用者決定的事用 ask_user_question 問，不要自己猜。")
    if REQUIRE_PLAN and not PLAN["approved"]:
        r.append("- 目前是計畫模式：先用 submit_plan 送出計畫，"
                 "使用者核准之前不會有修改檔案的工具可用。")
    r.append("- 做完後用三到五行說明你改了什麼、驗證結果如何，不要複述工具輸出。")

    # skills 索引：只放名字與一行描述。正文由模型自己用 load_skill 拉 ——
    # 六份描述 240 token 常駐得起，六份正文是幾千 token 而且九成用不到。
    skills = skills_list() if ALLOW_TOOLS else []
    if skills:
        r.append("\n## 現成的做法（要用就先 load_skill 把步驟載進來）")
        r += [f"- {s['name']}：{s['description']}" for s in skills]

    name, text = project_md()
    if text:
        r.append(f"\n## 專案說明（來自 {name}，優先於上面的通則）\n{text}")
    return "\n".join(r)


def tool_defs() -> list:
    """目前這個狀態下，可以送給模型的工具定義。

    沒開放的工具不會出現在清單裡 —— 不是讓模型呼叫了再拒絕。小模型看到工具就會想用。
    """
    out = []
    plan_ok = PLAN["approved"] or not REQUIRE_PLAN
    for t in TOOL_SCHEMAS:
        if t["needs"] == "ws" and WORKSPACE is None:
            continue
        if t["needs"] == "plan" and not REQUIRE_PLAN:
            continue
        if t["needs"] == "browser" and not ALLOW_BROWSER:
            continue
        if t["needs"] == "skills" and not skills_list():
            continue
        if t["needs"] == "write" and (WORKSPACE is None or not ALLOW_WRITE or not plan_ok):
            continue
        out.append({"type": "function", "function": {
            "name": t["name"], "description": t["description"],
            "parameters": {"type": "object", "properties": t["properties"],
                           "required": t.get("required", [])}}})
    return out + mcp_tool_defs()


def _tool_todo_write(items) -> str:
    """模型自己維護的待辦清單。

    Claude Code 與 grok-build 都有這個工具，理由一樣：跑十幾輪之後，
    模型會忘記最初的目標。把清單寫出來、每輪重看一次，它才記得自己在做什麼。
    """
    global TODOS
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
    TODOS = fresh
    write_todo_file()          # 同步那份 markdown，使用者才改得到
    left = sum(1 for t in TODOS if not t["done"])
    return f"待辦清單已更新（還剩 {left} 項）：\n{render_todos(sync=False)}"


def _tool_submit_plan(plan: str) -> str:
    """先講計畫、人核准了才動手。核准的動作在網頁上，不在這裡。"""
    global PLAN
    text = str(plan or "").strip()
    if not text:
        raise ValueError("plan 是空的")
    PLAN = {"text": text[:8000], "approved": True}   # 網頁按下「核准」才會送到這裡
    return "計畫已核准，可以開始執行。動手前再確認一次每一步都在計畫裡。"


TOOLS = {
    "read_file": _tool_read_file,
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


# ══════════════════════ MCP 客戶端 ══════════════════════ #
# grok-build 有一整個 xai-grok-mcp crate（stdio + HTTP、OAuth、elicitation、liveness）。
# 這裡只做最小可用的那一塊：stdio 上的 JSON-RPC，把對方的 tools/list 併進 tool_defs()。
# 守住三點：一樣要過確認卡、一樣只接受本機請求、工具數量要有上限。

def mcp_config_path() -> Path:
    """設定檔位置：工作區優先，其次是 serve.py 旁邊。"""
    if WORKSPACE is not None and (WORKSPACE / MCP_CONFIG).is_file():
        return WORKSPACE / MCP_CONFIG
    return HERE / MCP_CONFIG


def _mcp_rpc(server: str, method: str, params: dict, timeout: float = 30):
    """送一次 JSON-RPC 並等對應 id 的回覆。通知（沒有 id）直接跳過。"""
    st = MCP[server]
    proc = st["proc"]
    with st["lock"]:
        st["id"] += 1
        msg_id = st["id"]
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": msg_id,
                                     "method": method, "params": params}) + "\n")
        proc.stdin.flush()
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                raise RuntimeError(f"MCP server {server} 已結束")
            try:
                obj = json.loads(line)
            except ValueError:
                continue                      # 有些 server 會在 stdout 印雜訊
            if obj.get("id") != msg_id:
                continue                      # 通知或別人的回覆
            if "error" in obj:
                raise RuntimeError(str(obj["error"].get("message") or obj["error"]))
            return obj.get("result") or {}
    raise TimeoutError(f"MCP server {server} 超過 {timeout} 秒沒有回應")


def _mcp_notify(server: str, method: str, params: dict) -> None:
    st = MCP[server]
    with st["lock"]:
        st["proc"].stdin.write(json.dumps({"jsonrpc": "2.0", "method": method,
                                           "params": params}) + "\n")
        st["proc"].stdin.flush()


def mcp_start(name: str, spec: dict) -> None:
    proc = subprocess.Popen(
        [spec["command"]] + list(spec.get("args") or []),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        cwd=str(WORKSPACE) if WORKSPACE else None,
        env={**os.environ, **(spec.get("env") or {})},
        text=True, encoding="utf-8", bufsize=1)
    MCP[name] = {"proc": proc, "tools": [], "lock": threading.Lock(), "id": 0, "error": ""}
    _mcp_rpc(name, "initialize", {
        "protocolVersion": "2024-11-05", "capabilities": {},
        "clientInfo": {"name": "ZackLLMGUI", "version": "1.0"}}, timeout=20)
    _mcp_notify(name, "notifications/initialized", {})
    tools = (_mcp_rpc(name, "tools/list", {}, timeout=20) or {}).get("tools") or []
    keep = spec.get("tools")
    if keep:
        tools = [t for t in tools if t.get("name") in keep]
    if len(tools) > MCP_TOOL_CAP:
        # 一個檔案系統 server 就可能塞二十支工具，全送給模型等於每輪多燒好幾千 token
        tools = tools[:MCP_TOOL_CAP]
        MCP[name]["error"] = f"工具超過 {MCP_TOOL_CAP} 支，只取前面幾支（用 tools 欄位自己挑）"
    MCP[name]["tools"] = tools


def mcp_stop_all() -> None:
    for st in MCP.values():
        try:
            st["proc"].terminate()
        except Exception:
            pass
    MCP.clear()


def mcp_load() -> dict:
    """重讀設定檔並重開所有 server。回傳每一台的狀態。"""
    mcp_stop_all()
    cfg_file = mcp_config_path()
    if not cfg_file.is_file():
        return {"config": str(cfg_file), "servers": []}
    try:
        cfg = json.loads(cfg_file.read_text("utf-8"))
    except ValueError as e:
        return {"config": str(cfg_file), "servers": [], "error": f"設定檔不是合法的 JSON：{e}"}
    for name, spec in (cfg.get("servers") or {}).items():
        if not isinstance(spec, dict) or not spec.get("command"):
            continue
        try:
            mcp_start(name, spec)
        except Exception as e:
            MCP[name] = {"proc": None, "tools": [], "lock": threading.Lock(), "id": 0,
                         "error": f"{type(e).__name__}: {e}"}
    return mcp_status()


def mcp_status() -> dict:
    return {"config": str(mcp_config_path()),
            "servers": [{"name": n, "tools": [t.get("name") for t in st["tools"]],
                         "error": st["error"]} for n, st in MCP.items()]}


def mcp_tool_defs() -> list:
    """MCP 工具併進來時加前綴，免得跟本地工具或彼此撞名。"""
    out = []
    for server, st in MCP.items():
        for t in st["tools"]:
            schema = t.get("inputSchema") or {"type": "object", "properties": {}}
            out.append({"type": "function", "function": {
                "name": f"mcp__{server}__{t.get('name')}",
                "description": (t.get("description") or "")[:600],
                "parameters": schema}})
    return out


def mcp_call(full_name: str, args: dict) -> str:
    _, server, tool = full_name.split("__", 2)
    if server not in MCP or MCP[server]["proc"] is None:
        raise ValueError(f"MCP server {server} 沒有在跑")
    res = _mcp_rpc(server, "tools/call", {"name": tool, "arguments": args}, timeout=120)
    parts = []
    for item in (res.get("content") or []):
        if item.get("type") == "text":
            parts.append(item.get("text", ""))
        else:
            parts.append(f"（{item.get('type')} 內容，這裡不顯示）")
    return "\n".join(parts) or "（沒有回傳內容）"


# ══════════════════════ git 整合 ══════════════════════ #

def git_run(*a, timeout: int = 60):
    return subprocess.run(["git"] + list(a), cwd=str(ws_root()), stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, timeout=timeout)


def git_state() -> dict:
    """工作區的 git 狀態。不是 repo 就回 {"repo": False}。"""
    if WORKSPACE is None or not (WORKSPACE / ".git").exists():
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
    if WORKSPACE is None or not (WORKSPACE / ".git").exists():
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


# ══════════════════════ skills ══════════════════════ #
# 一個資料夾一份 SKILL.md，格式與範本見 skills/README.md。
# 目前只給人用（在對話框打 / 叫出來），模型端的 load_skill 還沒接。

def skills_roots() -> list:
    """要掃的 skills 資料夾，順序是 [內建, 工作區]。

    兩邊都讀，同名時工作區的贏。**不能寫成二選一** ——
    那樣的話模型照 make-skill 在專案裡寫下第一份 skill 的瞬間，
    內建那六份會全部從清單上消失（踩過）。
    """
    roots = [HERE / SKILLS_DIR]
    if WORKSPACE is not None:
        ws = WORKSPACE / SKILLS_DIR
        if ws.is_dir() and ws.resolve() != (HERE / SKILLS_DIR).resolve():
            roots.append(ws)
    return [r for r in roots if r.is_dir()]


def parse_skill(md: str) -> tuple:
    """回傳 (中繼資料, 正文)。格式壞掉就丟 ValueError。"""
    if not md.startswith("---"):
        raise ValueError("開頭要有 --- 夾起來的中繼資料")
    end = md.find("\n---", 3)
    if end < 0:
        raise ValueError("中繼資料沒有結尾的 ---")
    meta = {}
    for line in md[3:end].strip().splitlines():
        if ":" in line and not line.lstrip().startswith("#"):
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, md[end + 4:].strip()


def skills_list() -> list:
    """所有可用的 skill，只讀中繼資料（正文要另外拿）。同名時工作區的蓋掉內建的。"""
    found = {}
    for root in skills_roots():
        scope = "專案" if root != HERE / SKILLS_DIR else "內建"
        for folder in sorted(root.iterdir()):
            if not folder.is_dir() or folder.name.startswith("_"):
                continue
            f = folder / "SKILL.md"
            if not f.is_file():
                continue
            try:
                meta, _ = parse_skill(f.read_text("utf-8", errors="replace"))
            except ValueError:
                continue
            if meta.get("name") and meta.get("description"):
                found[meta["name"]] = {
                    "name": meta["name"], "description": meta["description"],
                    "scope": scope,
                    "tools": [t.strip() for t in meta.get("tools", "").split(",") if t.strip()]}
    return [found[k] for k in sorted(found)]


def skill_body(name: str) -> str:
    clean = str(name or "").strip()
    # 反著找：工作區的優先，跟 skills_list() 的覆蓋規則一致
    for root in reversed(skills_roots()):
        folder = root / clean
        # 名稱只當資料夾名用，不接受路徑；底線開頭的是範本，不給讀
        if (folder.parent != root or clean.startswith("_")
                or not (folder / "SKILL.md").is_file()):
            continue
        _, body = parse_skill((folder / "SKILL.md").read_text("utf-8", errors="replace"))
        return body[:SKILL_BODY_LIMIT]
    raise ValueError(f"沒有這個 skill：{name}")


# ══════════════════════ 允許規則 ══════════════════════ #
# 自動模式是全有全無的三段，但人真正想要的從來不是那三個，而是
# 「pytest 一律放行、git commit 要問我、secrets/ 永遠不准碰」。
# 規則檔就是把這種判斷寫下來一次，不用每天重新點一遍。
#
# 順序（第一個成立的說了算）：
#   deny 規則 > 擋掉的危險指令 > 風險指令一律問 > allow 規則 > 自動模式
#
# allow **不能**蓋過風險指令：那條保證（「危險指令自動模式也一定會問你」）
# 是寫在文件上的，不能被一個設定檔悄悄拿掉。

def rules_files() -> list:
    """[(範圍, 路徑)]。兩份都讀，專案的排在前面（第一條命中的說了算）。

    **不能寫成二選一。** skills 那邊踩過同一個坑：只要專案有了自己的一份，
    全域那份就整個消失 —— 使用者加了一條專案規則，結果全域的 deny 全部失效。
    """
    out = []
    if WORKSPACE is not None:
        out.append(("專案", WORKSPACE / RULES_FILE))
    here = HERE / RULES_FILE
    if not out or out[0][1].resolve() != here.resolve():
        out.append(("全域", here))
    return out


def rules_path(write: bool = False) -> Path:
    """要寫到哪一份：有工作區就寫專案的，沒有就寫全域的。"""
    files = rules_files()
    return files[0][1] if (write and files) else (HERE / RULES_FILE)


def rules_read_one(f: Path, scope: str) -> list:
    if not f.is_file():
        return []
    try:
        data = json.loads(f.read_text("utf-8", errors="replace"))
    except ValueError:
        return []          # 壞掉就當成沒有：規則是為了少按幾次，不能擋住整個程式
    out = []
    for r in (data.get("rules") if isinstance(data, dict) else data) or []:
        if not isinstance(r, dict):
            continue
        act = str(r.get("action", "")).lower()
        if act not in ("allow", "ask", "deny"):
            continue
        out.append({"tool": str(r.get("tool", "*")) or "*",
                    "pattern": str(r.get("pattern", "*")) or "*",
                    "action": act,
                    "note": str(r.get("note", ""))[:200],
                    "scope": scope})
    return out


def rules_load() -> list:
    out = []
    for scope, f in rules_files():
        out += rules_read_one(f, scope)
    # deny 一律排到最前面：第一條命中的說了算，禁止的不該被任何 allow 蓋掉
    return ([r for r in out if r["action"] == "deny"]
            + [r for r in out if r["action"] != "deny"])


def rules_save(rules: list, scope: str = "") -> None:
    """把某一個範圍的規則寫回它自己那一份檔案。"""
    for sc, f in rules_files():
        if scope and sc != scope:
            continue
        keep = [{k: v for k, v in r.items() if k != "scope"}
                for r in rules if r.get("scope", sc) == sc]
        if not keep and not f.is_file():
            continue
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"rules": keep}, ensure_ascii=False, indent=2) + "\n",
                     encoding="utf-8")


def rule_subject(name: str, args: dict) -> str:
    """這一次呼叫要拿什麼去比對樣式。

    指令類比指令本身、檔案類比路徑、連網類比網址 —— 都是使用者心裡
    「我要放行的是什麼」的那個東西。
    """
    if not isinstance(args, dict):
        return ""
    for key in ("command", "path", "url", "query", "target", "name"):
        if args.get(key):
            return str(args[key])
    return ""


def rule_match(name: str, args: dict) -> dict:
    """回傳命中的規則，沒有就回 None。第一條命中的說了算。"""
    subject = rule_subject(name, args)
    for r in rules_load():
        if not fnmatch.fnmatch(name, r["tool"]):
            continue
        pat = r["pattern"]
        # 路徑樣式常寫成 secrets/**，fnmatch 不認得 ** 的遞迴語意，補一個前綴比對
        if (fnmatch.fnmatch(subject, pat)
                or (pat.endswith("/**") and subject.startswith(pat[:-2]))
                or (pat.endswith("*") and subject.startswith(pat[:-1]))):
            return r
    return None


# ── 改完自動檢查 ─────────────────────────────────────────────── #
# aider 的 --auto-lint 就是這件事，而且它預設是開的：模型寫完檔案，linter 的錯誤
# 直接接在工具結果後面，它下一輪自己就修掉。比寫進系統提示求它記得可靠得多。
#
# 三個刻意的限制：
# 1. 只跑**唯讀**的檢查，不跑會改檔案的格式化（black、ruff format、prettier）。
#    在模型背後改掉檔案，它手上的內容就過期了，下一次 edit_file 的 old 會對不上。
# 2. 沒裝就安靜跳過。這是加分項，不該變成噪音，更不該害寫檔失敗。
# 3. 不進沙盒：write_file 是直接 p.write_text() 寫在宿主機的工作區，沒有走
#    sandbox.run。檔案在哪就在哪檢查 —— docker 後端的容器裡根本沒有這個檔案。
# ponytail: 只認 ruff 與 eslint 兩支，判斷寫死。要加第三支就往這裡加一個分支；
#           要 typecheck（mypy／tsc）得先解決「跑整包很慢」，那不是加一行的事。
LINT_TIMEOUT = 20


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


def preview_tool(name: str, args: dict) -> str:
    """給確認卡看的東西：改檔案是 diff，跑指令是風險評估。"""
    """寫入前先算出 diff 給人看。不會碰到磁碟。"""
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
    if name == "write_file":
        p = ws_path(args.get("path", ""))
        old = p.read_text("utf-8", errors="replace") if p.exists() else ""
        return unified(old, args.get("content", ""), ws_rel(p))
    if name == "edit_file":
        p = ws_path(args.get("path", ""), must_exist=True)
        old = p.read_text("utf-8", errors="replace")
        target = args.get("old", "")
        count = old.count(target)
        if count == 0:
            return "（無法預覽：在檔案裡找不到要取代的內容）"
        if count > 1 and not args.get("replace_all"):
            return f"（無法預覽：要取代的內容出現 {count} 次，需要 replace_all）"
        new_text = (old.replace(target, args.get("new", "")) if args.get("replace_all")
                    else old.replace(target, args.get("new", ""), 1))
        return unified(old, new_text, ws_rel(p))
    return ""


def run_tool(name: str, args: dict) -> str:
    """執行一個工具。呼叫端負責先問過使用者。"""
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
    if name in WS_TOOLS and WORKSPACE is None:
        raise PermissionError("這個工具需要先設定工作區資料夾")
    if name in WRITE_TOOLS and not ALLOW_WRITE:
        raise PermissionError("檔案修改沒有開啟（介面：功能與工具 → 修改檔案）")
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


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    ollama = "http://localhost:11434"
    server_version = "ZackLLMGUI/1.0"

    def log_message(self, fmt, *args):
        # 只在出事時說話，正常請求不洗畫面
        if not str(args[1] if len(args) > 1 else "").startswith("2"):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

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

        不吃的話，keep-alive 的下一個請求會從殘留的 body 開始解析，
        症狀就是 `501 Unsupported method ('{}GET')` —— 前一個請求的 `{}`
        黏在下一行請求前面。只要有**任何一條路徑**提早 return 就會發生：
        403（非本機）、「還沒設定工作區」、例外……每一條都算。

        所以不靠各個處理函式自己記得，統一在 do_POST 收尾時做。
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
        finally:
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
                        "todos": TODOS, "jobs": jobs_state(),
                        "plan": REQUIRE_PLAN, "browser": ALLOW_BROWSER,
                        "sandbox": ALLOW_SANDBOX, "sandbox_info": sandbox_state(),
                        "mcp": mcp_status(),
                        "stream_tools": sorted(STREAM_TOOLS),
                        # num_thread 的上限。只有 Ollama 跟這支服務同機時才算得準 ——
                        # Ollama 的 API 沒有任何一支回報主機的核心數。
                        "cpus": os.cpu_count() or 0,
                        "ollama_local": ollama_is_local(),
                        "client": self.client_address[0], "trust_remote": TRUST_REMOTE})
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
        elif self.path == "/rules":
            self._do_rules()
        elif self.path == "/restart":
            self._do_restart()
        elif self.path == "/journal":
            self._do_journal()
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
            if WORKSPACE is None:
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

        proc = subprocess.Popen(cmd, shell=use_shell, cwd=cwd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, bufsize=1, text=True,
                                encoding="utf-8", errors="replace",
                                start_new_session=True)
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
                line = line.rstrip("\n")
                if len(line) > MAX_LINE_CHARS:
                    line = line[:MAX_LINE_CHARS] + f"…（這一行被截斷，原本 {len(line)} 字元）"
                total += len(line) + 1
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
            if WORKSPACE is None:
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
                scope = "專案" if WORKSPACE is not None else "全域"
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
            if WORKSPACE is None:
                self._json({"entries": []})
                return
            chat = req.get("chat", "") if isinstance(req, dict) else ""
            self._json({"entries": journal_for(chat), "chat": chat,
                        "total": len(journal_read())})
        except Exception as e:
            self._json({"error": f"{type(e).__name__}: {e}"}, 400)

    def _do_rewind(self) -> None:
        if not self._is_local():
            self._json({"error": "只允許本機呼叫"}, 403)
            return
        try:
            req = json.loads(self._read_body(8192) or b"{}")
            if not isinstance(req, dict) or not req.get("id"):
                raise ValueError("要指定還原點 id")
            if WORKSPACE is None:
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
        global ALLOW_WRITE
        if "enabled" in req:
            ALLOW_TOOLS = bool(req.get("enabled"))
        if "write" in req:
            ALLOW_WRITE = bool(req.get("write")) and WORKSPACE is not None
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
        if "plan" in req:
            global REQUIRE_PLAN
            REQUIRE_PLAN = bool(req.get("plan"))
            PLAN["approved"] = not REQUIRE_PLAN
        print(f"  工具     {'已啟用' if ALLOW_TOOLS else '已關閉'}"
              f"{'，可修改檔案' if ALLOW_WRITE else ''}（由網頁切換）")
        self._json({"tools": ALLOW_TOOLS, "write": ALLOW_WRITE, "plan": REQUIRE_PLAN,
                    "browser": ALLOW_BROWSER, "sandbox": ALLOW_SANDBOX,
                    "sandbox_info": sandbox_state(),
                    "tool_defs": tool_defs(), "agent_rules": agent_rules()})

    # -- 工作區 ------------------------------------------------------ #

    def _do_workspace(self) -> None:
        """設定 / 查詢工作區。只有本機能改：工具會在這台機器上動檔案。"""
        global ALLOW_WRITE
        if not self._is_local():
            self._json({"error": "只有本機可以設定工作區。"}, 403)
            return
        try:
            req = json.loads(self._read_body(8192) or b"{}")
            info = set_workspace(req.get("path", ""))
            if WORKSPACE is None:
                ALLOW_WRITE = False
                info = workspace_info()
        except Exception as e:
            self._json({"error": str(e)}, 400)
            return
        print(f"  工作區   {info.get('path') or '（未設定）'}")
        info["tool_defs"] = tool_defs()
        info["agent_rules"] = agent_rules()
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
            diff = preview_tool(name, args)
            risk = command_risk(args.get("command", ""))[0] if name == "run_shell" else "ok"
            hit = rule_match(name, args)
        except Exception as e:
            self._json({"error": f"{type(e).__name__}: {e}"}, 400)
            return
        self._json({"diff": diff, "risk": risk, "rule": hit})

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

    def _do_tool(self) -> None:
        if not ALLOW_TOOLS:
            self._json({"error": "工具未啟用，請在右側面板打開「讓模型呼叫本機工具」。"}, 403)
            return
        if not self._is_local():
            self._json({"error": "工具只允許本機呼叫。"}, 403)
            return
        try:
            req = json.loads(self._read_body(1024 * 1024) or b"{}")
            global CURRENT_CHAT
            CURRENT_CHAT = str(req.get("chat", ""))[:64]
            out = run_tool(req.get("name", ""), req.get("args") or {})
        except subprocess.TimeoutExpired:
            self._json({"error": f"執行超過 {SHELL_TIMEOUT} 秒已中止"}, 400)
            return
        except Exception as e:
            self._json({"error": f"{type(e).__name__}: {e}"}, 400)
            return
        self._json({"result": out, "todos": TODOS, "plan": PLAN,
                    "jobs": jobs_state(), "tool_defs": tool_defs()})

    def do_DELETE(self):
        if self.path.startswith("/api/"):
            self._proxy("DELETE")
        else:
            self._send_bytes(b"Not Found", "text/plain; charset=utf-8", 404)


def build_server(ollama: str, bind: str, port: int) -> ThreadingHTTPServer:
    Handler.ollama = normalize(ollama)
    server = ThreadingHTTPServer((bind, port), Handler)
    server.daemon_threads = True
    return server


def main() -> int:
    parser = argparse.ArgumentParser(
        description="ZackLLMGUI 啟動器（同時代理 Ollama API，避開 CORS）")
    parser.add_argument("--ollama", default=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
                        help="Ollama 位址，例如 http://192.168.1.20:11434")
    parser.add_argument("--port", type=int, default=5678, help="本機服務的 port（預設 8777）")
    parser.add_argument("--host", default="0.0.0.0",
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
    args = parser.parse_args()

    global ALLOW_TOOLS, ALLOW_WRITE, TRUST_REMOTE, ALLOW_SANDBOX, SANDBOX_BACKEND
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
        ALLOW_WRITE = args.allow_write
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
    if WORKSPACE:
        print(f"  工作區   {WORKSPACE}" + ("（可修改檔案）" if ALLOW_WRITE else "（唯讀）"))
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
        mcp_stop_all()
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
