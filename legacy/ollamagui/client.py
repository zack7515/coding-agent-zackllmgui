# -*- coding: utf-8 -*-
"""Ollama HTTP 客戶端 —— 只用標準函式庫，沒有 requests 依賴。

所有方法都是同步阻塞的，必須在背景執行緒呼叫，不可在 Qt 主執行緒使用。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

DEFAULT_HOST = os.environ.get("OLLAMA_HOST") or "http://localhost:11434"

CONNECT_HELP = (
    "請確認：\n"
    "  1. Ollama 服務已啟動（終端機執行 ollama serve）\n"
    "  2. 主機位址與 port 正確（預設 http://localhost:11434）\n"
    "  3. 遠端主機需設定 OLLAMA_HOST=0.0.0.0 才會對外開放"
)


class OllamaError(RuntimeError):
    """Ollama 回傳的錯誤，或連線失敗。"""


def normalize_host(host: str) -> str:
    """把使用者輸入 / 環境變數整理成 http://host:port 形式。"""
    host = (host or "").strip().rstrip("/")
    if not host:
        return "http://localhost:11434"
    if not host.startswith(("http://", "https://")):
        host = "http://" + host
    return host


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}PB"


class OllamaClient:
    def __init__(self, host: str = DEFAULT_HOST):
        self.host = normalize_host(host)

    def _url(self, path: str) -> str:
        return f"{self.host}{path}"

    def _open(self, path: str, payload: dict | None = None,
              method: str = "GET", timeout: float = 30):
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            self._url(path), data=data, method=method,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "replace")
                body = json.loads(body).get("error", body)
            except Exception:
                pass
            raise OllamaError(f"HTTP {e.code}: {body or e.reason}") from None
        except urllib.error.URLError as e:
            raise OllamaError(f"無法連線到 {self.host}\n{e.reason}\n\n{CONNECT_HELP}") from None
        except (TimeoutError, OSError) as e:
            raise OllamaError(f"連線失敗：{e}") from None

    def _get_json(self, path: str, payload=None, method="GET", timeout=30) -> dict:
        with self._open(path, payload, method, timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    # ------------------------------------------------------------------ #

    def version(self) -> str:
        try:
            return self._get_json("/api/version", timeout=5).get("version", "?")
        except OllamaError:
            return "?"

    def list_models(self, timeout: float = 8) -> list[dict]:
        """GET /api/tags —— 本機已下載、可直接呼叫的模型。

        逾時刻意設短：這支同時也是連線偵測，主機不在時要盡快亮紅燈，
        不能讓使用者對著黃燈等十幾秒。
        """
        data = self._get_json("/api/tags", timeout=timeout)
        models = data.get("models") or []
        models.sort(key=lambda m: m.get("name", "").lower())
        return models

    def show(self, model: str) -> dict:
        """POST /api/show —— 模型細節，含 capabilities。"""
        return self._get_json("/api/show", {"model": model}, method="POST", timeout=30)

    def running(self) -> list[dict]:
        try:
            return self._get_json("/api/ps", timeout=5).get("models") or []
        except OllamaError:
            return []

    def chat_stream(self, model: str, messages: list[dict], think=None,
                    options: dict | None = None, keep_alive: str | None = None,
                    resp_holder: dict | None = None):
        """POST /api/chat 串流版，逐段 yield server 回傳的 JSON 物件。

        resp_holder 讓呼叫端拿得到 response 物件，才能從別的執行緒中止串流。
        """
        payload: dict = {"model": model, "messages": messages, "stream": True}
        if think is not None:
            payload["think"] = think
        if options:
            payload["options"] = options
        if keep_alive:
            payload["keep_alive"] = keep_alive

        resp = self._open("/api/chat", payload, method="POST", timeout=600)
        if resp_holder is not None:
            resp_holder["resp"] = resp
        try:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if "error" in obj:
                    raise OllamaError(obj["error"])
                yield obj
                if obj.get("done"):
                    break
        finally:
            try:
                resp.close()
            except Exception:
                pass
