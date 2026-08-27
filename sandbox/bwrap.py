# -*- coding: utf-8 -*-
"""Linux：bubblewrap。

跟容器最大的差別是**不換掉檔案系統**：把宿主機的 `/` 唯讀掛進去，
所以 pytest、node、gcc、CUDA 驅動本來就在那裡 —— 沒有「映像檔裡沒裝」這回事。
代價是它只在 Linux 上有。

擋的東西一樣：工作區以外唯讀、家目錄用 tmpfs 蓋掉、`--unshare-net` 斷網。

量過（這台，見 ../plan-agent.md 4.2）：冷啟動約 42 ms，容器是 176 ms。
"""

from __future__ import annotations

import glob
import os
import shutil
import stat
import sys
from pathlib import Path

NAME = "bwrap"
OS = ("linux",)
KIND = "核心層（namespace）"
# 檔案系統就是宿主機的（唯讀掛進去），所以宿主機的絕對路徑在裡面一樣有效。
SAME_FS = True

# 蓋掉的是**憑證目錄**，不是整個家目錄。
#
# 一開始是把整個 $HOME 用 tmpfs 蓋掉的，結果 run_tests 直接爆掉：
# 這台的 python 是 ~/miniconda3/bin/python3 —— 跟著家目錄一起消失了。
# conda、pyenv、nvm、cargo、~/.local/bin 都在家目錄底下，蓋掉家目錄
# 等於蓋掉開發機器的整套工具鏈，那個沙盒沒有人用得下去。
#
# 所以邊界改成：家目錄**讀得到但寫不動**（整個 / 都是唯讀的），
# 只有裝憑證的那幾個目錄看不到。配上「沒有網路」，讀得到的東西也送不出去。
HIDE = ("~/.ssh", "~/.aws", "~/.gnupg", "~/.kube", "~/.docker",
        "~/.config/gcloud", "~/.config/gh", "~/.azure")


def available() -> str:
    return shutil.which("bwrap") or "" if sys.platform.startswith("linux") else ""


def why() -> str:
    if not sys.platform.startswith("linux"):
        return "bubblewrap 只有 Linux 有"
    return "" if available() else "沒有裝 bubblewrap（sudo apt install bubblewrap）"


# 各家 GPU 的裝置節點。只有 nvidia 那組實測過（RTX 4070 SUPER、
# torch 2.13+cu130，沙盒內 is_available() 回 True、矩陣乘法跑得動）。
# 其他幾條是照各家文件寫的，**沒有硬體可以驗** —— 換到別台不能用的話先看這裡。
# ponytail: 一份寫死的 glob 清單，不做偵測。只有 NVIDIA 那組驗過，其餘照文件寫。
#           換到別台不能用的時候，在沙盒裡 `ls /dev` 看少了什麼再往這裡補一條。
GPU_NODES = (
    "/dev/nvidia*",         # NVIDIA：nvidia0、nvidiactl、nvidia-uvm…（實測過）
    "/dev/nvidia-caps/*",   # NVIDIA MIG 切分出來的 capability 節點（上面那條只到目錄）
    "/dev/dri/*",           # AMD／Intel 的 card* 與 renderD*，也是 ROCm 的一半
    "/dev/kfd",             # AMD ROCm 的核心介面，少了它 ROCm 一定不能用
    "/dev/dxg",             # WSL2 把 Windows 的顯示卡接進來的節點
)


def gpu_binds() -> list:
    """顯示卡的裝置節點，接進乾淨的 /dev 裡。沒有卡就回空的。"""
    out = []
    for pattern in GPU_NODES:
        for node in sorted(glob.glob(pattern)):
            # 只收字元裝置。/dev/dri/by-path 是一個裝符號連結的目錄，
            # 指到的 card* 與 renderD* 這裡本來就會收，接目錄沒有意義。
            try:
                if not stat.S_ISCHR(os.stat(node).st_mode):
                    continue
            except OSError:
                continue
            out += ["--dev-bind", node, node]
    return out


def wrap(command: str, workspace, net: bool = False, gpu: bool = False, **_) -> list:
    """把一行指令包成「在 bwrap 裡跑」的 argv。

    工作區掛在**原本的路徑**上，不是 /work —— 這樣錯誤訊息裡的路徑
    跟宿主機一致，模型與人看到的是同一個檔案。

    **順序有意義**：bwrap 是照參數順序一層一層疊的，所以遮蔽用的 tmpfs
    要先放，工作區的 bind 最後放。反過來的話，工作區只要在 /tmp 或家目錄
    底下（這是常態），就會被後面的 tmpfs 蓋成空的。
    """
    exe = available()
    if not exe:
        raise RuntimeError(why())
    ws = str(Path(workspace).resolve())
    argv = [exe, "--ro-bind", "/", "/",          # 整台機器唯讀
            "--proc", "/proc"]
    # /dev 一律是乾淨的假的，**只把顯示卡的節點單獨接回來**。
    #
    # 原本是 gpu=True 就 --dev-bind 整個 /dev —— 那會連其他裝置節點一起露出去，
    # 而且 serve.py 從來沒傳過 gpu=True，所以實際效果是「沙盒裡永遠沒有 GPU」，
    # 跟 README 上寫的相反。torch 的症狀是 Can't initialize NVML、
    # is_available() 回 False。
    #
    # 不做成開關：沒有卡的時候 glob 是空的，什麼都不會發生；有卡的時候
    # 使用者要的就是它能用。多一個開關只會多一個「為什麼不能動」的問題。
    argv += ["--dev", "/dev"]
    argv += gpu_binds()

    # 先遮蔽憑證目錄（順序：遮蔽在前、工作區的 bind 在後）
    argv += ["--tmpfs", "/tmp"]
    for path in HIDE:
        real = os.path.expanduser(path)
        if real and real != "/" and os.path.isdir(real):
            argv += ["--tmpfs", real]

    # 再把工作區接回來 —— 一定要在所有 tmpfs 之後
    argv += ["--bind", ws, ws,
             "--die-with-parent",                # serve.py 掛掉時一起收
             "--new-session",                    # 不繼承 tty，擋掉 TIOCSTI 那類把戲
             "--chdir", ws]
    if not net:
        argv += ["--unshare-net"]
    return argv + ["sh", "-lc", command]


def describe() -> dict:
    return {
        "name": NAME, "kind": KIND, "path": available(), "why": why(),
        "isolation": ["工作區以外唯讀", "憑證目錄蓋掉（.ssh/.aws/.gnupg…）",
                      "無網路（--unshare-net）", "獨立 /tmp 與 /proc"],
        "notes": ["宿主機的工具鏈直接可用（pytest / node / gcc / CUDA）",
                  "顯示卡會自動接進去（/dev/nvidia*、/dev/dri），其他裝置節點看不到",
                  "家目錄讀得到但寫不動 —— 蓋掉的是憑證目錄。配上斷網，讀到也送不出去",
                  "沒有記憶體與 CPU 上限 —— 那要 cgroup，bwrap 自己不做",
                  "只有 Linux"],
    }
