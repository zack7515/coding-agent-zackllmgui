# -*- coding: utf-8 -*-
"""這台機器的沙盒到底擋不擋得住：實際跑一遍，逐項驗證，順便量開銷。

跑得起來不等於擋得住。每一項都是真的執行一次指令去看結果，
不是讀設定檔猜的 —— 沙盒是拿來擋事情的，不實測就只是「應該有效」。
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from pathlib import Path

from . import pick, run

# (名稱, 指令, 判斷, 這一項在驗證什麼)
# 判斷一律看**輸出內容**不看 returncode：指令後面接了 | head 的話，
# 退出碼會變成 head 的 0（踩過這個坑，見 plan-agent.md 4.2）。
CHECKS = [
    ("跑得起來", "echo ok", lambda o: "ok" in o,
     "沙盒本身能不能啟動"),
    ("看得到工作區", "cat marker.txt", lambda o: "HELLO" in o,
     "掛載有生效，不然工具進去等於空的"),
    ("寫得回工作區", "echo WROTE > out.txt && cat out.txt", lambda o: "WROTE" in o,
     "產出要留得下來，不然跑測試沒有意義"),
    ("工作區以外寫不動", "touch /etc/zack-probe 2>&1 | head -1",
     lambda o: ("Read-only" in o or "Permission denied" in o
                or "denied" in o.lower() or "not permitted" in o.lower()),
     "這是 run_shell 最大的洞：不擋的話它跟你在終端機打一樣有力"),
    ("讀不到憑證目錄", "ls -a ~/.ssh 2>&1 | grep -v '^\\.\\{1,2\\}$' | head -1",
     lambda o: ("No such file" in o or "cannot access" in o
                or "denied" in o.lower() or not o.strip()),
     "SSH 金鑰、雲端 token 在那裡。家目錄本身是唯讀讀得到的（工具鏈在裡面）"),
    ("沒有網路",
     "python3 -c \"import socket;socket.gethostbyname('example.com')\" 2>&1 | tail -1",
     lambda o: ("gaierror" in o or "resolution" in o or "unreachable" in o
                or "Errno" in o or "denied" in o.lower()),
     "模型送不出東西"),
]


def probe(backend: str = "", **opts) -> list:
    """回傳 [(名稱, 通過, 輸出, 說明)]。opts 往下傳給 run()（目前只有 image）。"""
    mod = pick(backend)
    out = []
    with tempfile.TemporaryDirectory(prefix="zack-sandbox-") as tmp:
        ws = Path(tmp)
        (ws / "marker.txt").write_text("HELLO\n", encoding="utf-8")
        for name, cmd, ok, why in CHECKS:
            try:
                _, text = run(cmd, ws, net=False, timeout=90, backend=mod.NAME,
                              **opts)
            except subprocess.TimeoutExpired:
                text = "（逾時）"
            except Exception as e:
                text = f"{type(e).__name__}: {e}"
            out.append((name, bool(ok(text)), text[:200], why))

        # 產出的檔案是不是屬於自己 —— 容器忘了 -u 的話這裡會是 root
        made = ws / "out.txt"
        if made.exists() and hasattr(os, "getuid"):
            owned = made.stat().st_uid == os.getuid()
            out.append(("產出檔案屬於自己", owned,
                        f"uid={made.stat().st_uid}（你是 {os.getuid()}）",
                        "不是的話你自己刪不掉它建出來的東西"))

        # 工具鏈也只記錄不判定 —— 需不需要編譯器是專案的事。
        # 但這一項在容器後端上一定要看得到：預設映像檔 python:3.13-slim 裡
        # 沒有 gcc 也沒有 cmake，而 Windows 上容器是唯一的後端。少了這一行，
        # 使用者要跑完一次 cmake 失敗才會知道，而模型會先花三輪想辦法自己裝。
        (ws / "probe.c").write_text("int main(void){ return 0; }\n", encoding="utf-8")
        try:
            _, text = run("got=; for t in cc gcc g++ make cmake ninja; do "
                          "command -v $t >/dev/null 2>&1 && got=\"$got $t\"; done; "
                          "echo \"有：${got:- 一個都沒有}\"; "
                          "cc -o probe_bin probe.c 2>probe_err && echo '編譯 OK' "
                          "|| head -1 probe_err",
                          ws, timeout=90, backend=mod.NAME, **opts)
        except Exception as e:
            text = f"{type(e).__name__}: {e}"
        out.append(("C/C++ 工具鏈", True, text[:160].strip() or "（一個都沒有）",
                    "只記錄：核心層沙盒用的是你機器上的；容器只有映像檔裡的"
                    "（預設那個沒有編譯器，用 --sandbox-image 換）"))

        # GPU 只記錄不判定：需不需要 GPU 是專案的事，不是沙盒的事
        try:
            _, text = run("nvidia-smi --query-gpu=name --format=csv,noheader 2>&1 | head -1",
                          ws, timeout=60, backend=mod.NAME, gpu=True, **opts)
            out.append(("GPU（gpu=True 時）", True, text[:120] or "（沒有 GPU）",
                        "只記錄：核心層沙盒直接看得到，容器要 Container Toolkit"))
        except Exception as e:
            out.append(("GPU（gpu=True 時）", True, f"{type(e).__name__}: {e}", "只記錄"))
    return out


def bench(backend: str = "", rounds: int = 5, **opts) -> dict:
    """量開銷：同一行指令，進沙盒 vs 直接跑。"""
    mod = pick(backend)
    result = {}
    with tempfile.TemporaryDirectory(prefix="zack-bench-") as tmp:
        ws = Path(tmp)
        for label, cmd in (("短指令 echo", "echo hi"),
                           ("列目錄 ls -la", "ls -la"),
                           ("起 python", "python3 -c 'print(1)'")):
            for mode in (False, True):
                times = []
                for _ in range(rounds):
                    t0 = time.perf_counter()
                    try:
                        run(cmd, ws, sandboxed=mode, timeout=90, backend=mod.NAME,
                            **opts)
                    except Exception:
                        times = []
                        break
                    times.append(time.perf_counter() - t0)
                if times:
                    result[f"{label}／{'沙盒' if mode else '直接'}"] = sum(times) / len(times)
    return result
