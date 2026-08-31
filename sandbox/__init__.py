# -*- coding: utf-8 -*-
"""沙盒：把 run_shell / run_tests 關進一個跑不出工作區的地方。

**為什麼需要**：`run_shell` 是唯一跑得出工作區的工具。檔案工具有 `ws_path()`
擋著，它沒有 —— `cat ~/.ssh/id_rsa`、`curl` 把東西送出去，不關起來就都做得到。

**一個作業系統一種做法**，因為能用的東西本來就不一樣：

| 平台 | 後端 | 要裝什麼 |
|---|---|---|
| Linux | `bwrap`（bubblewrap） | `apt install bubblewrap` |
| macOS | `seatbelt`（內建 sandbox-exec） | 不用裝 |
| Windows | `container`（Docker Desktop） | Docker Desktop |
| 都可以 | `container`（docker / podman） | docker 或 podman |

**只有 Linux 實測過**（bwrap 與 container 兩個後端都是在 Linux 上驗的）。
macOS 的 seatbelt 與 Windows 上的 Docker Desktop 沒有機器可以跑 ——
在那兩個平台上先跑 `python -m sandbox` 逐項驗過再開。

挑選順序是「核心層優先、容器墊底」：核心層的沙盒不換掉檔案系統，
所以 pytest、node、gcc、CUDA 都還在原地，而且快一個數量級
（這台實測 bwrap 7 ms、docker 176 ms）。容器要嘛映像檔裡有、要嘛就是沒有。

對外只有四個東西：`detect()` 看這台有什麼、`wrap()` 包一行指令、
`run()` 直接跑、`pick()` 指定後端。全部只用標準函式庫。

    python -m sandbox              # 這台機器能用哪一種，逐項驗證 + 量開銷
    python -m sandbox --json
"""

from __future__ import annotations

import platform
import subprocess
import sys
from pathlib import Path

from . import bwrap, container, seatbelt

# 順序＝偏好。核心層在前：不換檔案系統，工具鏈與 GPU 都還在，而且快得多。
BACKENDS = (bwrap, seatbelt, container)


def host() -> dict:
    """這台機器是什麼。開啟功能之前介面要先問這個。"""
    return {
        "platform": sys.platform,
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
    }


def detect() -> dict:
    """這台機器上哪些後端可用，會挑哪一個，不能用的話為什麼。"""
    found = []
    for mod in BACKENDS:
        found.append(dict(mod.describe(), ok=bool(mod.available())))
    usable = [b for b in found if b["ok"]]
    return {
        "host": host(),
        "backends": found,
        "backend": usable[0]["name"] if usable else "",
        "ok": bool(usable),
        # 挑不出來的時候，講「這個平台上該裝什麼」比列出三個都沒有有用
        "why": "" if usable else _advice(),
    }


def _advice() -> str:
    if sys.platform.startswith("linux"):
        return "這台沒有沙盒可用。裝一個：sudo apt install bubblewrap（最輕），或 podman／docker"
    if sys.platform == "darwin":
        return "這台 macOS 找不到 sandbox-exec，也沒有 docker／podman"
    if sys.platform == "win32":
        return "Windows 上需要 Docker Desktop —— 系統內建的 Windows Sandbox 是完整桌面環境，接不上這裡"
    return f"{sys.platform} 上沒有已知的沙盒做法"


def pick(prefer: str = ""):
    """挑一個後端。指定名字就用那一個（不可用會講原因），沒指定就照偏好順序。"""
    if prefer:
        for mod in BACKENDS:
            if mod.NAME == prefer:
                if not mod.available():
                    raise RuntimeError(f"{prefer}：{mod.why()}")
                return mod
        raise RuntimeError(f"沒有這個後端：{prefer}（有 " +
                           "、".join(m.NAME for m in BACKENDS) + "）")
    for mod in BACKENDS:
        if mod.available():
            return mod
    raise RuntimeError(_advice())


def wrap(command: str, workspace, net: bool = False, gpu: bool = False,
         backend: str = "", **opts) -> list:
    """把一行 shell 包成「在沙盒裡跑」的 argv。

    介面刻意跟 serve.py 的 build_command() 對齊：進去一行 shell，
    出來可以直接丟給 Popen 的東西，其他部分完全不用改。
    """
    return pick(backend).wrap(command, workspace, net=net, gpu=gpu, **opts)


def run(command: str, workspace, net: bool = False, timeout: int = 120,
        sandboxed: bool = True, backend: str = "", gpu: bool = False, **opts):
    """跑一行指令，回傳 (returncode, 輸出)。sandboxed=False 就是原本的行為。"""
    if sandboxed:
        argv, shell = wrap(command, workspace, net=net, gpu=gpu, backend=backend,
                           **opts), False
    else:
        argv, shell = command, True
    proc = subprocess.run(argv, shell=shell, cwd=str(workspace),
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          timeout=timeout)
    return proc.returncode, proc.stdout.decode("utf-8", "replace").strip()


# ── 相容用：serve.py 原本呼叫的名字 ─────────────────────────
def runtime() -> str:
    """現在會用哪一個後端的名字，沒有就是空字串。"""
    try:
        return pick().NAME
    except RuntimeError:
        return ""


RUNTIMES = tuple(m.NAME for m in BACKENDS)
IMAGE = container.IMAGE
