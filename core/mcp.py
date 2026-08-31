# -*- coding: utf-8 -*-
"""MCP（Model Context Protocol）客戶端：把外部 server 的工具接進來。

只用標準函式庫講 JSON-RPC over stdio，不裝 SDK。
"""

import json
import os
import subprocess
import threading
import time
from pathlib import Path

from core.workspace import HERE, _CUR, cur


# ── MCP（Model Context Protocol）客戶端 ──────────────────────────── #
MCP_CONFIG = ".zackllmgui-mcp.json"
MCP_TOOL_CAP = 12                  # 一台 server 最多收這麼多支工具，否則每輪光工具定義就燒掉幾千 token
MCP = {}                           # 工作區 -> {server 名 -> {"proc", "tools", "lock", "id", "error"}}
# 為什麼多包一層：工作區、修改權限、自動模式、待辦、計畫都跟著分頁走了，MCP 不跟的話
# 兩個分頁開兩個專案時，拿到的是「先啟動的那個專案」的 server（連 cwd 都是它的），
# 另一個分頁的工具會安靜地指向錯的目錄。


# grok-build 有一整個 xai-grok-mcp crate（stdio + HTTP、OAuth、elicitation、liveness）。
# 這裡只做最小可用的那一塊：stdio 上的 JSON-RPC，把對方的 tools/list 併進 tool_defs()。
# 守住三點：一樣要過確認卡、一樣只接受本機請求、工具數量要有上限。

def mcp_config_path() -> Path:
    """設定檔位置：工作區優先，其次是 serve.py 旁邊。"""
    if cur().ws is not None and (cur().ws / MCP_CONFIG).is_file():
        return cur().ws / MCP_CONFIG
    return HERE / MCP_CONFIG


def mcp_key() -> str:
    rec = getattr(_CUR, "agent", None)
    return str((rec or {}).get("root") or cur().ws or HERE)


def mcps() -> dict:
    """這個分頁（或這個子代理）的 MCP 連線。

    子代理走 root：worktree 是同一個專案的另一份 checkout，為它再開一整套 server
    是白花行程。沒有 worktree 的子代理 root 是 None，自然落回上層的工作區。
    """
    return MCP.setdefault(mcp_key(), {})


def _mcp_rpc(server: str, method: str, params: dict, timeout: float = 30):
    """送一次 JSON-RPC 並等對應 id 的回覆。通知（沒有 id）直接跳過。"""
    st = mcps()[server]
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
    st = mcps()[server]
    with st["lock"]:
        st["proc"].stdin.write(json.dumps({"jsonrpc": "2.0", "method": method,
                                           "params": params}) + "\n")
        st["proc"].stdin.flush()


def mcp_start(name: str, spec: dict) -> None:
    proc = subprocess.Popen(
        [spec["command"]] + list(spec.get("args") or []),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        cwd=str(cur().ws) if cur().ws else None,
        env={**os.environ, **(spec.get("env") or {})},
        text=True, encoding="utf-8", bufsize=1)
    mcps()[name] = {"proc": proc, "tools": [], "lock": threading.Lock(), "id": 0, "error": ""}
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
        mcps()[name]["error"] = f"工具超過 {MCP_TOOL_CAP} 支，只取前面幾支（用 tools 欄位自己挑）"
    mcps()[name]["tools"] = tools


def mcp_stop(key: str = "") -> None:
    """關掉 server。不給 key 就是全部 —— 收攤的時候用。"""
    for k in ([key] if key else list(MCP)):
        for st in MCP.pop(k, {}).values():
            try:
                st["proc"].terminate()
            except Exception:
                pass


def mcp_load() -> dict:
    """重讀設定檔並重開這個工作區的 server。回傳每一台的狀態。"""
    mcp_stop(mcp_key())
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
            mcps()[name] = {"proc": None, "tools": [], "lock": threading.Lock(), "id": 0,
                         "error": f"{type(e).__name__}: {e}"}
    return mcp_status()


def mcp_status() -> dict:
    return {"config": str(mcp_config_path()),
            "servers": [{"name": n, "tools": [t.get("name") for t in st["tools"]],
                         "error": st["error"]} for n, st in mcps().items()]}


def mcp_tool_defs() -> list:
    """MCP 工具併進來時加前綴，免得跟本地工具或彼此撞名。"""
    out = []
    for server, st in mcps().items():
        for t in st["tools"]:
            schema = t.get("inputSchema") or {"type": "object", "properties": {}}
            out.append({"type": "function", "function": {
                "name": f"mcp__{server}__{t.get('name')}",
                "description": (t.get("description") or "")[:600],
                "parameters": schema}})
    return out


def mcp_call(full_name: str, args: dict) -> str:
    _, server, tool = full_name.split("__", 2)
    if server not in mcps() or mcps()[server]["proc"] is None:
        raise ValueError(f"MCP server {server} 沒有在跑")
    res = _mcp_rpc(server, "tools/call", {"name": tool, "arguments": args}, timeout=120)
    parts = []
    for item in (res.get("content") or []):
        if item.get("type") == "text":
            parts.append(item.get("text", ""))
        else:
            parts.append(f"（{item.get('type')} 內容，這裡不顯示）")
    return "\n".join(parts) or "（沒有回傳內容）"
