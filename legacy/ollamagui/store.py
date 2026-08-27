# -*- coding: utf-8 -*-
"""設定、對話資料與磁碟持久化。"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

CONFIG_DIR = Path.home() / ".ollama_gui"
CONFIG_FILE = CONFIG_DIR / "config.json"
CHATS_FILE = CONFIG_DIR / "chats.json"

MAX_CHATS = 100

# 取樣參數預設值（與 Ollama 預設一致；-1 代表「不送出這個參數」）
DEFAULT_PARAMS = {
    "temperature": 0.8,
    "top_p": 0.9,
    "top_k": 40,
    "min_p": 0.0,
    "repeat_penalty": 1.1,
    "num_ctx": 4096,
    "num_predict": -1,
    "seed": -1,
    "stop": "",
    "keep_alive": "5m",
    "system": "",
}

# 思考模式：(顯示文字, 送給 API 的 think 值)
THINK_LEVELS = [("關", False), ("低", "low"), ("中", "medium"), ("高", "high")]
THINK_TOGGLE = [("關閉", False), ("開啟", True)]


def supports_think_levels(model: str) -> bool:
    """是否吃 low/medium/high 分級。

    Ollama 沒有在 capabilities 裡揭露這件事，目前只有 gpt-oss 系列支援分級，
    其他推理模型（qwen3、deepseek-r1…）只認得 true/false。
    """
    return "gpt-oss" in (model or "").lower()


def think_options(model: str, caps: list[str]) -> list[tuple[str, object]]:
    """依模型能力回傳分段控制項要顯示的選項；不支援 thinking 時回傳空列表。"""
    if "thinking" not in (caps or []):
        return []
    return THINK_LEVELS if supports_think_levels(model) else THINK_TOGGLE


# --------------------------------------------------------------------------- #


class Conversation:
    def __init__(self, title: str = "新對話", messages: list[dict] | None = None,
                 cid: str | None = None, model: str = ""):
        self.id = cid or uuid.uuid4().hex[:12]
        self.title = title
        self.messages: list[dict] = messages or []
        self.model = model

    def to_dict(self) -> dict:
        return {"id": self.id, "title": self.title,
                "messages": self.messages, "model": self.model}

    @staticmethod
    def from_dict(d: dict) -> "Conversation":
        return Conversation(d.get("title", "新對話"), d.get("messages", []),
                            d.get("id"), d.get("model", ""))

    def auto_title(self) -> None:
        for m in self.messages:
            if m["role"] == "user":
                text = " ".join(m["content"].split())
                self.title = (text[:26] + "…") if len(text) > 26 else (text or "新對話")
                return
        self.title = "新對話"

    def api_messages(self, system: str = "") -> list[dict]:
        """轉成 /api/chat 要的格式：只留 role / content / images。"""
        out: list[dict] = []
        if system:
            out.append({"role": "system", "content": system})
        for m in self.messages:
            item = {"role": m["role"], "content": m["content"]}
            if m.get("images"):
                item["images"] = m["images"]
            out.append(item)
        return out


# --------------------------------------------------------------------------- #


def load_config() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text("utf-8"))
    except Exception:
        return {}


def save_config(cfg: dict) -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), "utf-8")
    except OSError:
        pass


def load_chats() -> list[Conversation]:
    try:
        data = json.loads(CHATS_FILE.read_text("utf-8"))
        convs = [Conversation.from_dict(d) for d in data]
        if convs:
            return convs
    except Exception:
        pass
    return [Conversation()]


def save_chats(chats: list[Conversation]) -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        data = [c.to_dict() for c in chats[:MAX_CHATS]]
        CHATS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    except OSError:
        pass


# --------------------------------------------------------------------------- #


def build_options(params: dict) -> dict:
    """把參數面板的值組成 /api/chat 的 options；-1 或空白代表不送出。"""
    def as_int(value, default=None):
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return default

    opts: dict = {
        "temperature": round(float(params["temperature"]), 3),
        "top_p": round(float(params["top_p"]), 3),
        "top_k": int(params["top_k"]),
        "repeat_penalty": round(float(params["repeat_penalty"]), 3),
    }

    min_p = round(float(params.get("min_p", 0)), 3)
    if min_p > 0:
        opts["min_p"] = min_p

    n = as_int(params.get("num_ctx"))
    if n and n > 0:
        opts["num_ctx"] = n

    n = as_int(params.get("num_predict"), -1)
    if n is not None and n != -1:
        opts["num_predict"] = n

    n = as_int(params.get("seed"), -1)
    if n is not None and n >= 0:
        opts["seed"] = n

    stops = [s.strip() for s in str(params.get("stop", "")).split(",") if s.strip()]
    if stops:
        opts["stop"] = stops

    return opts
