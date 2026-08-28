#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""端到端試跑工具呼叫：讓真的模型改一個壞掉的專案，直到測試通過。

跟 test_serve.py 不同，這支需要 Ollama 正在跑，而且會真的花時間推論，
所以不放進一般測試流程。

    python test_agent.py                          # 自動挑一個支援 tools 的模型
    python test_agent.py qwen3.8:27b-mtp-q4_K_M   # 指定模型
    python test_agent.py --keep                   # 結束後保留暫存專案
    python test_agent.py --no-rules               # 拿掉 agent_rules，比較提示詞值不值得
    python test_agent.py --tools=read_file,edit_file,run_tests   # 只送這幾支，量工具定義的成本

它做的事跟網頁按下送出之後完全一樣：同一份 tool_defs、同一段 agent_rules、
同一支 /tool 端點。差別只有「確認」在這裡是自動答應的（人不在場）。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import serve

PORT = 8899
MAX_ROUNDS = 12
ROUND_TIMEOUT = 600

TASK = (
    "這個專案的測試沒有通過。請先用 run_tests 看錯誤，"
    "再用 read_file 看清楚程式碼，然後用 edit_file 修好 pkg/calc.py，"
    "最後再跑一次 run_tests 確認通過。不要修改測試檔。"
)

DEMO = {
    "pkg/__init__.py": "",
    "pkg/calc.py": (
        "def add(a, b):\n"
        "    \"\"\"兩數相加。\"\"\"\n"
        "    return a - b\n"
        "\n"
        "\n"
        "def mul(a, b):\n"
        "    return a * b\n"
    ),
    "test_calc.py": (
        "from pkg.calc import add, mul\n"
        "\n"
        "\n"
        "def test_add():\n"
        "    assert add(2, 3) == 5\n"
        "\n"
        "\n"
        "def test_mul():\n"
        "    assert mul(2, 3) == 6\n"
    ),
}


def post(path: str, payload: dict, timeout: int = ROUND_TIMEOUT) -> dict:
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}",
                                 data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return json.loads(res.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read())


def stream_run(name: str, args: dict) -> dict:
    """走 /run 的串流路徑，跟網頁按下確認之後做的事一模一樣。"""
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/run",
                                 data=json.dumps({"name": name, "args": args}).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=ROUND_TIMEOUT) as res:
            for raw in res:
                obj = json.loads(raw)
                if obj.get("done"):
                    return {"result": obj.get("result", "")}
    except urllib.error.HTTPError as e:
        return json.loads(e.read())
    return {"error": "串流沒有回傳結果"}


def get(path: str) -> dict:
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}", timeout=30) as res:
        return json.loads(res.read())


def pick_model(want: str) -> str:
    tags = json.loads(urllib.request.urlopen(
        serve.Handler.ollama + "/api/tags", timeout=10).read())
    names = [m["name"] for m in tags.get("models", [])]
    if want:
        if want not in names:
            sys.exit(f"找不到模型 {want}，這台有：{', '.join(names)}")
        return want
    for name in names:
        info = post("/api/show", {"model": name}, timeout=30)
        if "tools" in (info.get("capabilities") or []):
            return name
    sys.exit("這台 Ollama 沒有支援 tools 的模型")


def make_demo() -> Path:
    root = Path(tempfile.mkdtemp(prefix="zack-agent-"))
    for rel, body in DEMO.items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body, encoding="utf-8")
    return root


def pytest_passes(root: Path) -> tuple[bool, str]:
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "--color=no"],
                          cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          timeout=300)
    return proc.returncode == 0, proc.stdout.decode("utf-8", "replace").strip()


def short(text: str, n: int = 220) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[:n] + "…"


def main() -> int:
    keep = "--keep" in sys.argv
    want = next((a for a in sys.argv[1:] if not a.startswith("-")), "")

    root = make_demo()
    server = serve.build_server("http://localhost:11434", "127.0.0.1", PORT)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    time.sleep(0.3)

    serve.ALLOW_TOOLS = True
    info = post("/workspace", {"path": str(root)})
    if "error" in info:
        sys.exit(info["error"])
    post("/tools", {"enabled": True, "write": True})
    state = get("/upstream")
    tools = state["tool_defs"]
    rules = state["agent_rules"]

    # 只送指定的幾支：用來回答「工具定義每一輪都全部送，值不值得省」。
    # 省的是每輪的固定 tokens，賠的可能是模型找不到該用的工具 —— 兩邊都要量。
    only = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--tools=")), "")
    if only:
        keep_names = [n.strip() for n in only.split(",") if n.strip()]
        tools = [t for t in tools if t["function"]["name"] in keep_names]
        missing = [n for n in keep_names
                   if n not in [t["function"]["name"] for t in tools]]
        if missing:
            sys.exit(f"沒有這幾支工具：{', '.join(missing)}")

    model = pick_model(want)
    ok0, out0 = pytest_passes(root)
    print(f"專案   {root}")
    print(f"模型   {model}")
    print(f"工具   {', '.join(t['function']['name'] for t in tools)}")
    print(f"起始   pytest {'通過' if ok0 else '失敗'}：{out0.splitlines()[-1] if out0 else ''}")
    if state.get("todos"):
        print(f"待辦   {len(state['todos'])} 項")
    print("-" * 72)

    if "--no-rules" in sys.argv:
        # 對照組：只給工具、不給規則。用來回答「這種 harness 需不需要特定提示詞」
        rules = ""
        print("提示   （已拿掉 agent_rules，對照組）")
    messages = ([{"role": "system", "content": rules}] if rules else []) + \
               [{"role": "user", "content": TASK}]
    calls = 0
    prompt_tokens = 0        # 每一輪都要重送的那一份，累加起來才看得出固定成本
    t0 = time.time()

    for rnd in range(1, MAX_ROUNDS + 1):
        reply = post("/api/chat", {"model": model, "messages": messages,
                                   "tools": tools, "stream": False, "think": False,
                                   "options": {"num_ctx": 32768, "temperature": 0.3}})
        if "error" in reply:
            print(f"[{rnd}] 模型回報錯誤：{reply['error']}")
            break
        prompt_tokens += reply.get("prompt_eval_count") or 0
        msg = reply.get("message") or {}
        messages.append(msg)

        text = (msg.get("content") or "").strip()
        if text:
            print(f"[{rnd}] 說：{short(text)}")

        tcs = msg.get("tool_calls") or []
        if not tcs:
            print(f"[{rnd}] 沒有再呼叫工具，結束")
            break

        for tc in tcs:
            fn = tc.get("function") or {}
            name = fn.get("name", "")
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {}
            calls += 1
            # ask_user_question 在網頁上是問人，harness 沒有人，給一句固定回答
            if name == "ask_user_question":
                out = "你自己判斷，照最直接的做法做，不要再問。"
                print(f"[{rnd}] ask_user_question({short(args.get('question', ''), 90)})"
                      f"\n      → {out}")
                messages.append({"role": "tool", "tool_name": name, "content": out})
                continue
            # 網頁上這裡會跳確認卡；harness 沒有人可以問，一律答應
            # 長指令走 /run（跟網頁一樣），順便驗串流那條路也能用
            res = (stream_run(name, args) if name in serve.STREAM_TOOLS
                   else post("/tool", {"name": name, "args": args}))
            out = res.get("result", "錯誤：" + str(res.get("error")))
            flag = "！" if "error" in res else " "
            print(f"[{rnd}]{flag}{name}({short(json.dumps(args, ensure_ascii=False), 90)})"
                  f"\n      → {short(out, 160)}")
            messages.append({"role": "tool", "tool_name": name, "content": out})

    ok, out = pytest_passes(root)
    print("-" * 72)
    print(f"結果   pytest {'通過' if ok else '失敗'}：{out.splitlines()[-1] if out else ''}")
    print(f"統計   {rnd} 輪 · {calls} 次工具呼叫 · {time.time() - t0:.0f} 秒"
          f" · {len(tools)} 支工具 · prompt {prompt_tokens} tokens")
    print(f"檔案   {(root / 'pkg' / 'calc.py').read_text().strip()}")

    server.shutdown()
    server.server_close()
    if keep:
        print(f"保留   {root}")
    else:
        shutil.rmtree(root, ignore_errors=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
