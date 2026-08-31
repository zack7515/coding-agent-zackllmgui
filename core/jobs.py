# -*- coding: utf-8 -*-
"""背景指令：跑得久的丟到背景，工具立刻回一個 id，之後用 check_job 收。

run_shell 只有 30 秒，npm install、cargo build 都跑不完，而且同步的時候
整個 agent 迴圈卡在那裡。
"""

import collections
import os
import re
import signal
import subprocess
import threading
import time

from core import workspace
from core.workspace import _CUR

RING_LINES = 2000                  # 串流時最多留這麼多行回灌給模型
MAX_LINE_CHARS = 4000              # 單行上限。minified JS 或 base64 一行就好幾 MB

# ── 背景指令 ──────────────────────────────────────────────────── #
# run_shell 只有 30 秒，npm install、cargo build 都跑不完，而且同步的時候
# 整個 agent 迴圈卡在那裡。背景版交給一條讀取執行緒，立刻回一個 id。
# 這推翻了 tech.md〈長指令為什麼沒做 job API〉—— 那個結論的前提是
# 「人一定在旁邊看著」，自動模式讓前提不成立了。
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


def kill_tree(proc) -> None:
    """殺掉整棵程序樹，不是只殺最上面那一個。

    `shell=True` 跑的是 `sh -c`，指令一複雜 sh 會 fork，真正在跑的是它的孫子。
    `proc.kill()` 只殺得到 sh，孫子繼續握著 stdout，讀取執行緒永遠等不到 EOF。
    Popen 開 start_new_session 自成一個 process group，這裡整組送 SIGKILL。
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
        rec = getattr(_CUR, "agent", None)
        job = {"id": jid, "cmd": " ".join(str(command).split())[:200], "code": None,
               "lines": collections.deque(maxlen=RING_LINES),
               "started": time.time(), "ended": 0.0, "proc": None,
               # 誰丟的。中斷一個子代理時要連它的背景指令一起殺，
               # 不然「已中斷」只中斷了一半 —— 指令還在這台機器上跑。
               "agent": (rec or {}).get("id", ""), "chat": workspace.cur_chat()}
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


def jobs_state() -> list:
    """給網頁看的背景指令狀態。關掉分頁再打開，這些還在。"""
    with JOBS_LOCK:
        every = list(JOBS.values())
    return [{"id": j["id"], "cmd": j["cmd"], "code": j["code"],
             "secs": int((j["ended"] or time.time()) - j["started"]),
             "agent": j.get("agent", ""), "chat": j.get("chat", "")}
            for j in every]
