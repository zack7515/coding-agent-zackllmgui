# -*- coding: utf-8 -*-
"""這台機器的 CPU／RAM／GPU 用量。只讀 /proc 與 nvidia-smi。"""

import os
import shutil
import subprocess
import time


CPU_LAST = {}
SYS_CACHE = {"at": 0.0, "data": {}}
SYS_TTL = 1.5                      # 開兩個分頁時不要變成一秒兩次 nvidia-smi


def cpu_percent() -> float:
    """/proc/stat 兩次取樣之間的忙碌比例。第一次沒有基準，回 -1。"""
    try:
        with open("/proc/stat", encoding="utf-8") as fh:
            v = [float(x) for x in fh.readline().split()[1:]]
    except Exception:
        return -1.0
    idle, total = v[3] + (v[4] if len(v) > 4 else 0), sum(v)
    prev = CPU_LAST.get("v")
    CPU_LAST["v"] = (total, idle)
    if not prev or total <= prev[0]:
        return -1.0
    return round(100.0 * (1 - (idle - prev[1]) / (total - prev[0])), 1)


def ram_info() -> dict:
    """RAM 用量（GB）。用 MemAvailable 不是 MemFree —— cache 拿得回來，
    算成已用會看起來永遠快滿。"""
    try:
        got = {}
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                k, _, val = line.partition(":")
                if k in ("MemTotal", "MemAvailable"):
                    got[k] = int(val.split()[0]) / 1048576.0
        if len(got) == 2:
            return {"used": round(got["MemTotal"] - got["MemAvailable"], 1),
                    "total": round(got["MemTotal"], 1)}
    except Exception:
        pass
    return {}


def gpu_info() -> list:
    """問一次 nvidia-smi。沒有卡、沒裝驅動、指令不在，都回空清單。"""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return []
    try:
        out = subprocess.run(
            [exe, "--query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu",
             "--format=csv,noheader,nounits"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=3).stdout.decode(
                "utf-8", "replace")
    except Exception:
        return []
    cards = []
    for line in out.strip().splitlines():
        f = [x.strip() for x in line.split(",")]
        if len(f) < 5:
            continue
        try:
            cards.append({"name": f[0], "util": int(f[3]), "temp": int(f[4]),
                          "vram": {"used": round(int(f[1]) / 1024.0, 1),
                                   "total": round(int(f[2]) / 1024.0, 1)}})
        except ValueError:
            continue
    return cards


def sys_usage() -> dict:
    """給 topbar 用的一包數字。是**跑 serve.py 這一台**的，
    Ollama 在別台的話 GPU 那幾格講的不是跑模型的那張卡。"""
    now = time.time()
    if now - SYS_CACHE["at"] < SYS_TTL and SYS_CACHE["data"]:
        return SYS_CACHE["data"]
    data = {"cpu": cpu_percent(), "ram": ram_info(), "gpu": gpu_info(),
            "cores": os.cpu_count() or 0}
    SYS_CACHE.update(at=now, data=data)
    return data
