# -*- coding: utf-8 -*-
"""macOS：內建的 sandbox-exec（Seatbelt）。

跟 bwrap 同一個路數 —— 核心層的限制，不換檔案系統，所以工具鏈與 GPU 都還在。
macOS 每一台都有，不用另外安裝。

Apple 把 `sandbox-exec` 標成 deprecated 很多年了，但它一直都在，
而且是 macOS 上唯一不必安裝任何東西就能用的做法。真的哪天被拿掉，
`available()` 會回空字串，介面就會顯示「這台沒有可用的沙盒」。

**沒有在 macOS 上實測過**（手邊沒有機器）。profile 的寫法照 Apple 的
sandbox profile 語法依公開的系統與瀏覽器實作撰寫。
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

NAME = "seatbelt"
OS = ("darwin",)
KIND = "核心層（Seatbelt）"
# 檔案系統就是宿主機的（唯讀掛進去），所以宿主機的絕對路徑在裡面一樣有效。
SAME_FS = True


def available() -> str:
    return shutil.which("sandbox-exec") or "" if sys.platform == "darwin" else ""


def why() -> str:
    if sys.platform != "darwin":
        return "sandbox-exec 只有 macOS 有"
    return "" if available() else "這台 macOS 找不到 sandbox-exec"


def profile(workspace: str, net: bool) -> str:
    """允許讀、限制寫、可選擇斷網。

    寫入只開工作區與暫存目錄 —— 跟 bwrap 的「工作區以外唯讀」是同一個意思。
    """
    lines = [
        "(version 1)",
        "(allow default)",
        "(deny file-write*)",
        f'(allow file-write* (subpath "{workspace}"))',
        '(allow file-write* (subpath "/private/var/folders"))',
        '(allow file-write* (subpath "/private/tmp"))',
        '(allow file-write* (literal "/dev/null") (literal "/dev/stdout")'
        ' (literal "/dev/stderr"))',
    ]
    if not net:
        lines.append("(deny network*)")
    return "\n".join(lines)


def wrap(command: str, workspace, net: bool = False, gpu: bool = False, **_) -> list:
    exe = available()
    if not exe:
        raise RuntimeError(why())
    ws = str(Path(workspace).resolve())
    return [exe, "-p", profile(ws, net), "sh", "-lc", command]


def describe() -> dict:
    return {
        "name": NAME, "kind": KIND, "path": available(), "why": why(),
        "isolation": ["工作區以外不能寫", "可選擇斷網"],
        "notes": ["讀取沒有限制 —— Seatbelt 擋的是寫入與網路，不是讀取",
                  "宿主機的工具鏈直接可用",
                  "Apple 標為 deprecated，但目前每一台 macOS 都有",
                  "還沒有在真的 macOS 上實測過"],
    }
