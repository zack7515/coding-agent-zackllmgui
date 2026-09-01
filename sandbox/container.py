# -*- coding: utf-8 -*-
"""docker / podman：三個作業系統上都可能有的後端。

跟 bwrap 相反，容器**換掉整個檔案系統**：裡面是映像檔的內容，不是你的機器。
好處是隔離乾淨、有記憶體與 CPU 上限；壞處是「映像檔裡沒裝的東西就是沒有」——
專案要 node、gcc、CUDA 的話得自己換映像檔。

Windows 上這是唯一可用的後端（Docker Desktop）。macOS 上通常也是走這裡，
除非用內建的 sandbox-exec（見 seatbelt.py）。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

NAME = "container"
OS = ("linux", "darwin", "win32")
KIND = "容器"
# 容器有自己的 rootfs，宿主機的絕對路徑進去就不存在了 —— 呼叫端要換成相對路徑。
SAME_FS = False

IMAGE = "python:3.13-slim"
LIMITS = ["--memory", "4g", "--pids-limit", "512", "--cpus", "4"]
RUNTIMES = ("podman", "docker")     # podman 在前：不需要 daemon、預設 rootless
WORKDIR = "/work"                   # 容器裡工作區的位置
_HEALTH = {"at": 0.0, "runtime": "", "ok": False}


def runtime() -> str:
    for name in RUNTIMES:
        if shutil.which(name):
            return name
    return ""


def available() -> str:
    rt = runtime()
    if not rt:
        return ""
    # 好的時候問久一點（daemon 不會每兩秒關一次），壞的時候問密一點 ——
    # 那通常是使用者正在開 Docker Desktop，等太久按鈕不會亮。
    now = time.monotonic()
    if _HEALTH["runtime"] != rt or now - _HEALTH["at"] > (30 if _HEALTH["ok"] else 3):
        try:
            proc = subprocess.run([rt, "info", "--format", "{{.ServerVersion}}"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  timeout=4)
            ok = proc.returncode == 0
        except Exception:
            ok = False
        _HEALTH.update(at=now, runtime=rt, ok=ok)
    return shutil.which(rt) if _HEALTH["ok"] else ""


def why() -> str:
    if available():
        return ""
    hint = ("Docker Desktop" if sys.platform == "win32" else "／".join(RUNTIMES))
    return (f"{hint} 已安裝，但容器服務沒有啟動或無法連線"
            if runtime() else f"沒有裝 {hint}")


def wrap(command: str, workspace, net: bool = False, gpu: bool = False,
         image: str = "", **_) -> list:
    """把一行指令包成「在容器裡跑」的 argv。"""
    rt = runtime()
    if not rt or not available():
        raise RuntimeError(why())
    ws = str(Path(workspace).resolve())
    argv = [rt, "run", "--rm", "-i",
            "-v", f"{ws}:{WORKDIR}",
            "-w", WORKDIR,
            "--network", "bridge" if net else "none"]
    # Windows 沒有 uid/gid 這種東西，getuid 根本不存在。Linux／macOS 上不加這個
    # 的話容器裡是 root，它建出來的 .venv 與測試產物在你的機器上會變成 root 所有。
    if hasattr(os, "getuid"):
        argv += ["-u", f"{os.getuid()}:{os.getgid()}"]
    if gpu:
        # 需要 NVIDIA Container Toolkit，而且映像檔裡要有對應的 CUDA userspace。
        # 沒裝的話 docker 會直接報錯 —— 那是對的，總比安靜地跑在 CPU 上好。
        argv += ["--gpus", "all"] if rt == "docker" else ["--device", "nvidia.com/gpu=all"]
    argv += LIMITS
    return argv + [image or IMAGE, "sh", "-lc", command]


def describe() -> dict:
    return {
        "name": NAME, "kind": KIND, "path": available(), "why": why(),
        "runtime": runtime(), "image": IMAGE, "workdir": WORKDIR,
        "isolation": ["只掛得到工作區", "無網路（--network none）",
                      "記憶體 4g／程序數 512／CPU 4", "以你的 uid 執行（非 Windows）"],
        "notes": ["映像檔裡沒有的東西就是沒有（pytest、node、gcc 都要自己裝）",
                  "GPU 要 NVIDIA Container Toolkit 加上 CUDA 映像檔",
                  "冷啟動依平台而異（實測 Linux 約 176 ms、Windows 約 600 ms）"],
    }
