# -*- coding: utf-8 -*-
"""工作區與分頁狀態 —— 其他模組要動檔案就從這裡拿邊界。

`ws_path()` 是所有檔案工具的安全邊界，只有這一份，不要為了方便繞過它。
"""

import os
import re
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent   # serve.py 那一層

# ── 工作區（改檔案／跑測試用，見 plan-agent.md） ──────────────────── #
BACKUP_DIR = ".zackllmgui-backup"
WORKTREE_DIR = ".zackllmgui-worktrees"   # 隔離型子代理各自的 git worktree
WORKTREE_MAX = 8                   # 同時最多幾份，忘了收的不會無限長
WORKTREE_LINK = ("node_modules",)  # 開 worktree 時從主 repo 連過去的資料夾
# ponytail: 一個名字就夠了。第二個出現時這裡是加一個字串，不是開一份設定檔 ——
# 條件很嚴：純相依、名字全世界一樣、重建很貴。vendor/target 都還沒真的遇到。
# .venv 刻意不連：那一份是 detect_python() 用讀的借過去的，連過去的話子代理的
# setup_env 會裝進主專案。
WORKTREE_SKIP = (BACKUP_DIR, WORKTREE_DIR) + WORKTREE_LINK
# 這些資料夾不讓模型碰：版控內部、虛擬環境、相依套件、備份自己
DENY_DIRS = {".git", ".hg", ".svn", ".venv", "venv", "env", "node_modules",
             "__pycache__", ".mypy_cache", ".pytest_cache", ".tox", "dist",
             "build", ".next", ".idea", ".vscode", BACKUP_DIR, WORKTREE_DIR}
# 這些檔案不讓模型讀，免得金鑰跟著進 context
# 這裡也擋 .zackllmgui-*.json：那幾個檔案決定「什麼可以自動放行」，
# 讓模型讀得到等於讓它知道怎麼繞，寫得到就等於自己給自己開權限。
DENY_FILES = re.compile(r"^(\.env(\..*)?|.*\.env|.*\.pem|.*\.key|.*\.pfx|id_rsa.*|"
                        r".*\.p12|.*credentials.*\.json|\.npmrc|\.netrc|"
                        r"\.zackllmgui-.*\.json)$", re.I)
# 編譯產物。DENY_DIRS 擋得掉 build/，Makefile 專案的產出卻落在原地，
# 而專案地圖是每一輪都要重送的固定成本。不擋 read_file，只是不主動列出來。
DENY_EXT = {".o", ".obj", ".a", ".so", ".dylib", ".dll", ".lib", ".exe",
            ".d", ".gch", ".pch", ".pdb", ".ilk", ".pyd", ".pyc", ".class"}
MAX_FILE_BYTES = 400_000           # 單檔上限，再大就不是給模型看的

# 工作區是哪種語言。決定要送哪幾支工具、提示詞怎麼寫 ——
# 在 C++ 專案裡送 setup_env 與「不要自己 pip install」只會誤導模型。
# C 與 C++ 併成一個 "c"：工具鏈是同一套（gcc／cmake／ctest），分開沒有用處。
LANG_EXT = {".py": "python",
            ".c": "c", ".h": "c", ".cpp": "c", ".cc": "c", ".cxx": "c",
            ".hpp": "c", ".hh": "c", ".hxx": "c"}
LANG_SCAN = 400                    # 掃到這麼多檔就夠判斷了，跟專案地圖同一個上限


class Session:
    """一個瀏覽器分頁的狀態（工作區、寫入權、自動模式、待辦、計畫）。

    這五樣不能是全域的：兩個分頁各開一個專案時，A 的 write_file 會靜靜寫進
    B 的資料夾。分頁每個請求都帶 X-Tab，照它找回自己那一份。
    工具開關、沙盒、連網、MCP、背景指令仍是行程一份 —— 那是對這台服務授的權。
    """
    __slots__ = ("ws", "write", "auto", "todos", "todo_mtime", "plan", "agents", "seen")

    def __init__(self, base=None):
        self.ws = base.ws if base else None            # Path；沒設定就沒有任何檔案工具
        self.write = base.write if base else False     # 寫入類工具要再多一道開關
        self.auto = "off"                              # 只影響系統提示怎麼寫，不決定放不放行
        self.todos = []                                # [{"text": ..., "done": bool}]
        self.todo_mtime = 0.0                          # 上次自己寫進去的時間戳，用來分辨「誰改的」
        self.plan = {"text": "", "approved": False, "on": False}
        self.agents = {}                               # 子代理 id -> {"ws", "branch"}
        self.seen = time.time()


SESSIONS = {"": Session()}         # "" 是預設分頁：命令列 --workspace 設的就是它
SESSIONS_MAX = 32                  # 分頁關掉不會通知伺服器，滿了就丟最久沒動的
SESSIONS_LOCK = threading.Lock()
_CUR = threading.local()


def cur() -> "Session":
    """這個請求屬於哪個分頁。沒帶 X-Tab（測試、curl）就是預設那一份。"""
    return getattr(_CUR, "s", None) or SESSIONS[""]


# 現在是哪則對話在呼叫工具。網頁每次呼叫都帶上來，記進 journal ——
# 「紀錄」分頁才能只顯示這一則對話改過的東西。
# 跟 cur() 一樣掛在 _CUR 上：兩個分頁同時跑的時候，全域版會讓 A 的紀錄
# 記成 B 的對話。分頁狀態都是每個請求一份了，這個沒有理由是例外。
def cur_chat() -> str:
    return getattr(_CUR, "chat", "")


def set_cur_chat(chat: str) -> None:
    _CUR.chat = str(chat or "")[:64]


def session_for(tab: str) -> "Session":
    tab = str(tab or "")[:64]
    if not tab:
        return SESSIONS[""]
    with SESSIONS_LOCK:
        s = SESSIONS.get(tab)
        if s is None:
            # 新分頁承接命令列給的工作區，否則 --workspace 開起來的分頁會是空的
            s = SESSIONS[tab] = Session(SESSIONS[""])
            if len(SESSIONS) > SESSIONS_MAX:
                del SESSIONS[min(((v.seen, k) for k, v in SESSIONS.items() if k))[1]]
        s.seen = time.time()
        return s


def ws_root() -> Path:
    if cur().ws is None:
        raise RuntimeError("還沒設定工作區資料夾（介面：設定 → 工作區）")
    return cur().ws


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
    return "." if p == root else p.relative_to(root).as_posix()


def ws_langs() -> set:
    """工作區裡有哪些語言。掃不完整不要緊 —— 這只決定提示詞怎麼寫，不決定放不放行。"""
    if cur().ws is None:
        return set()
    got = set()
    for n, f in enumerate(ws_walk()):
        if n >= LANG_SCAN:
            break
        lang = LANG_EXT.get(f.suffix.lower())
        if lang:
            got.add(lang)
    return got


def ws_walk():
    """走訪工作區，跳過封鎖目錄。"""
    root = ws_root().resolve()
    for base, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in DENY_DIRS and not d.startswith("."))
        for name in sorted(files):
            if DENY_FILES.match(name):
                continue
            if Path(name).suffix.lower() in DENY_EXT:
                continue
            yield Path(base) / name
