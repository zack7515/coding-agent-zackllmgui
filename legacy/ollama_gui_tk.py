#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ollama Desktop GUI —— 類 Open WebUI 的本機聊天介面

特色：
  * 自動從 Ollama server 讀取可用模型清單 (/api/tags)
  * 自動偵測模型能力 (/api/show)：thinking / tools / vision
  * Thinking 開關與思考強度 (off / on / low / medium / high)
  * 思考過程獨立顯示，可折疊
  * 完整取樣參數面板 (temperature / top_p / top_k / min_p / repeat_penalty /
    num_ctx / num_predict / seed / stop / keep_alive)
  * 串流輸出、可中途停止、可重新產生
  * 多組對話、自動存檔、可匯出 JSON / Markdown
  * 深色 / 淺色主題
  * 視覺模型可附加圖片

只使用 Python 標準函式庫（tkinter + urllib），不需要安裝任何套件。
用法：  python ollama_gui.py
"""

from __future__ import annotations

import base64
import json
import os
import queue
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# --------------------------------------------------------------------------- #
# 常數與設定檔
# --------------------------------------------------------------------------- #

APP_NAME = "Ollama GUI"
CONFIG_DIR = Path.home() / ".ollama_gui"
CONFIG_FILE = CONFIG_DIR / "config.json"
CHATS_FILE = CONFIG_DIR / "chats.json"

# Ollama 的 OLLAMA_HOST 環境變數常常沒有 scheme（例如 "127.0.0.1:11434"），要正規化
DEFAULT_HOST = os.environ.get("OLLAMA_HOST") or "http://localhost:11434"

# think 下拉選單顯示文字 -> 送給 API 的值
#   False / True 是通用寫法；"low"/"medium"/"high" 目前只有 gpt-oss 這類模型吃得動
THINK_MODES: list[tuple[str, object]] = [
    ("關閉 (off)", False),
    ("開啟 (on)", True),
    ("低強度 (low)", "low"),
    ("中強度 (medium)", "medium"),
    ("高強度 (high)", "high"),
]

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

THEMES = {
    "dark": {
        "bg": "#17181a", "panel": "#202225", "panel2": "#26282c", "border": "#34373c",
        "fg": "#e6e7e9", "muted": "#9aa0a6", "accent": "#4a9eff", "accent_fg": "#ffffff",
        "user": "#6cc4a1", "assistant": "#7aa7ff", "think": "#a98cf0",
        "code_bg": "#0e0f11", "err": "#ff6b6b", "sel": "#2f4d73", "input": "#1b1c1f",
    },
    "light": {
        "bg": "#f6f7f9", "panel": "#ffffff", "panel2": "#eef0f3", "border": "#d5d8dd",
        "fg": "#1b1c1e", "muted": "#666b72", "accent": "#1a73e8", "accent_fg": "#ffffff",
        "user": "#0f7b57", "assistant": "#1a4fa0", "think": "#6b3fc4",
        "code_bg": "#eceff3", "err": "#c62828", "sel": "#cfe0ff", "input": "#ffffff",
    },
}

MONO = ("Consolas", 10)
UI_FONT = ("Microsoft JhengHei UI", 10)


def normalize_host(host: str) -> str:
    """把使用者輸入 / 環境變數整理成 http://host:port 形式。"""
    host = (host or "").strip().rstrip("/")
    if not host:
        return "http://localhost:11434"
    if not host.startswith(("http://", "https://")):
        host = "http://" + host
    return host


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}PB"


# --------------------------------------------------------------------------- #
# Ollama HTTP 客戶端
# --------------------------------------------------------------------------- #

class OllamaError(RuntimeError):
    """Ollama server 回傳的錯誤，或連線失敗。"""


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
            headers={"Content-Type": "application/json",
                     "Accept": "application/json"},
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
            raise OllamaError(
                f"無法連線到 {self.host}\n{e.reason}\n\n"
                "請確認：\n"
                "  1. Ollama 服務已啟動（終端機執行 `ollama serve`）\n"
                "  2. 主機位址與 port 正確（預設 http://localhost:11434）\n"
                "  3. 遠端主機需設定 OLLAMA_HOST=0.0.0.0 才會對外開放"
            ) from None
        except (TimeoutError, OSError) as e:
            raise OllamaError(f"連線失敗：{e}") from None

    def _get_json(self, path: str, payload=None, method="GET", timeout=30) -> dict:
        with self._open(path, payload, method, timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    # -- 公開 API ---------------------------------------------------------- #

    def version(self) -> str:
        try:
            return self._get_json("/api/version", timeout=5).get("version", "?")
        except OllamaError:
            return "?"

    def list_models(self) -> list[dict]:
        """GET /api/tags —— 取得本機已下載、可直接呼叫的模型。"""
        data = self._get_json("/api/tags", timeout=15)
        models = data.get("models") or []
        models.sort(key=lambda m: m.get("name", "").lower())
        return models

    def show(self, model: str) -> dict:
        """POST /api/show —— 取得模型細節，含 capabilities。"""
        return self._get_json("/api/show", {"model": model}, method="POST", timeout=30)

    def running(self) -> list[dict]:
        try:
            return self._get_json("/api/ps", timeout=5).get("models") or []
        except OllamaError:
            return []

    def chat_stream(self, model: str, messages: list[dict], think=None,
                    options: dict | None = None, keep_alive: str | None = None,
                    resp_holder: dict | None = None):
        """POST /api/chat 的串流版本，逐段 yield server 回傳的 JSON 物件。

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


# --------------------------------------------------------------------------- #
# 對話資料結構
# --------------------------------------------------------------------------- #

class Conversation:
    def __init__(self, title: str = "新對話", messages: list[dict] | None = None,
                 cid: str | None = None, model: str = ""):
        self.id = cid or uuid.uuid4().hex[:12]
        self.title = title
        self.messages: list[dict] = messages or []   # {role, content, thinking?, images?}
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
                t = " ".join(m["content"].split())
                self.title = (t[:24] + "…") if len(t) > 24 else (t or "新對話")
                return
        self.title = "新對話"


# --------------------------------------------------------------------------- #
# 主視窗
# --------------------------------------------------------------------------- #

class OllamaGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1360x860")
        self.minsize(1040, 620)

        self.cfg = self._load_config()
        self.theme_name = self.cfg.get("theme", "dark")
        self.C = THEMES[self.theme_name]

        self.client = OllamaClient(self.cfg.get("host", DEFAULT_HOST))
        self.models: list[dict] = []
        self.caps: dict[str, list[str]] = {}      # model -> capabilities
        self.events: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.cancel_flag = threading.Event()
        self.resp_holder: dict = {}
        self.streaming = False
        self.pending_images: list[tuple[str, str]] = []   # (檔名, base64)
        self._think_counter = 0
        self._resp_mark_id = 0

        self.conversations: list[Conversation] = self._load_chats()
        self.current = self.conversations[0]

        self._build_vars()
        self._build_style()
        self._build_ui()
        self._apply_theme()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(40, self._pump)
        self.refresh_models()
        self._render_conversation()

    # ------------------------------------------------------------------ #
    # 設定檔
    # ------------------------------------------------------------------ #

    def _load_config(self) -> dict:
        try:
            return json.loads(CONFIG_FILE.read_text("utf-8"))
        except Exception:
            return {}

    def _save_config(self) -> None:
        cfg = {
            "host": self.host_var.get(),
            "model": self.model_var.get(),
            "theme": self.theme_name,
            "think": self.think_var.get(),
            "show_think": bool(self.show_think_var.get()),
            "params": self._collect_params_raw(),
        }
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), "utf-8")
        except Exception:
            pass

    def _load_chats(self) -> list[Conversation]:
        try:
            data = json.loads(CHATS_FILE.read_text("utf-8"))
            convs = [Conversation.from_dict(d) for d in data]
            if convs:
                return convs
        except Exception:
            pass
        return [Conversation()]

    def _save_chats(self) -> None:
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            data = [c.to_dict() for c in self.conversations[:50]]
            CHATS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # Tk 變數
    # ------------------------------------------------------------------ #

    def _build_vars(self) -> None:
        p = {**DEFAULT_PARAMS, **(self.cfg.get("params") or {})}
        self.host_var = tk.StringVar(value=normalize_host(self.cfg.get("host", DEFAULT_HOST)))
        self.model_var = tk.StringVar(value=self.cfg.get("model", ""))
        self.status_var = tk.StringVar(value="尚未連線")
        self.caps_var = tk.StringVar(value="")
        self.think_var = tk.StringVar(value=self.cfg.get("think", THINK_MODES[1][0]))
        self.show_think_var = tk.BooleanVar(value=self.cfg.get("show_think", True))
        self.attach_var = tk.StringVar(value="")

        self.v_temperature = tk.DoubleVar(value=p["temperature"])
        self.v_top_p = tk.DoubleVar(value=p["top_p"])
        self.v_top_k = tk.DoubleVar(value=p["top_k"])
        self.v_min_p = tk.DoubleVar(value=p["min_p"])
        self.v_repeat_penalty = tk.DoubleVar(value=p["repeat_penalty"])
        self.v_num_ctx = tk.StringVar(value=str(p["num_ctx"]))
        self.v_num_predict = tk.StringVar(value=str(p["num_predict"]))
        self.v_seed = tk.StringVar(value=str(p["seed"]))
        self.v_stop = tk.StringVar(value=p["stop"])
        self.v_keep_alive = tk.StringVar(value=p["keep_alive"])
        self._system_default = p.get("system", "")

    # ------------------------------------------------------------------ #
    # 樣式
    # ------------------------------------------------------------------ #

    def _build_style(self) -> None:
        self.style = ttk.Style(self)
        try:
            self.style.theme_use("clam")
        except tk.TclError:
            pass

    def _apply_theme(self) -> None:
        C, s = self.C, self.style
        self.configure(bg=C["bg"])

        s.configure(".", background=C["panel"], foreground=C["fg"],
                    fieldbackground=C["input"], bordercolor=C["border"], font=UI_FONT)
        s.configure("TFrame", background=C["panel"])
        s.configure("Bg.TFrame", background=C["bg"])
        s.configure("Card.TFrame", background=C["panel"], relief="flat")
        s.configure("TLabel", background=C["panel"], foreground=C["fg"])
        s.configure("Bg.TLabel", background=C["bg"], foreground=C["fg"])
        s.configure("Muted.TLabel", background=C["panel"], foreground=C["muted"],
                    font=(UI_FONT[0], 9))
        s.configure("Head.TLabel", background=C["panel"], foreground=C["fg"],
                    font=(UI_FONT[0], 10, "bold"))
        s.configure("Title.TLabel", background=C["bg"], foreground=C["fg"],
                    font=(UI_FONT[0], 12, "bold"))
        s.configure("TLabelframe", background=C["panel"], foreground=C["muted"],
                    bordercolor=C["border"])
        s.configure("TLabelframe.Label", background=C["panel"], foreground=C["muted"],
                    font=(UI_FONT[0], 9, "bold"))
        s.configure("TButton", background=C["panel2"], foreground=C["fg"],
                    bordercolor=C["border"], focuscolor=C["panel2"], padding=(10, 5))
        s.map("TButton",
              background=[("active", C["border"]), ("disabled", C["panel"])],
              foreground=[("disabled", C["muted"])])
        s.configure("Accent.TButton", background=C["accent"], foreground=C["accent_fg"],
                    font=(UI_FONT[0], 10, "bold"))
        s.map("Accent.TButton", background=[("active", C["accent"]), ("disabled", C["panel2"])],
              foreground=[("disabled", C["muted"])])
        s.configure("Danger.TButton", background=C["panel2"], foreground=C["err"])
        s.configure("TCheckbutton", background=C["panel"], foreground=C["fg"],
                    focuscolor=C["panel"])
        s.map("TCheckbutton", background=[("active", C["panel"])])
        s.configure("TEntry", fieldbackground=C["input"], foreground=C["fg"],
                    bordercolor=C["border"], insertcolor=C["fg"], padding=4)
        s.configure("TCombobox", fieldbackground=C["input"], background=C["panel2"],
                    foreground=C["fg"], arrowcolor=C["fg"], bordercolor=C["border"], padding=4)
        s.map("TCombobox", fieldbackground=[("readonly", C["input"])],
              foreground=[("readonly", C["fg"])])
        self.option_add("*TCombobox*Listbox.background", C["input"])
        self.option_add("*TCombobox*Listbox.foreground", C["fg"])
        self.option_add("*TCombobox*Listbox.selectBackground", C["accent"])
        self.option_add("*TCombobox*Listbox.selectForeground", C["accent_fg"])
        s.configure("TScale", background=C["panel"], troughcolor=C["panel2"])
        s.configure("Vertical.TScrollbar", background=C["panel2"], troughcolor=C["panel"],
                    bordercolor=C["panel"], arrowcolor=C["muted"])
        s.map("Vertical.TScrollbar", background=[("active", C["border"])])
        s.configure("TPanedwindow", background=C["bg"])
        s.configure("Sash", background=C["bg"], gripcount=0)
        s.configure("TSeparator", background=C["border"])

        # 非 ttk 元件手動上色
        for w, kw in (
            (self.chat, dict(bg=C["panel"], fg=C["fg"], insertbackground=C["fg"],
                             selectbackground=C["sel"])),
            (self.input, dict(bg=C["input"], fg=C["fg"], insertbackground=C["fg"],
                              selectbackground=C["sel"])),
            (self.sys_text, dict(bg=C["input"], fg=C["fg"], insertbackground=C["fg"],
                                 selectbackground=C["sel"])),
            (self.chat_list, dict(bg=C["panel"], fg=C["fg"], selectbackground=C["sel"],
                                  selectforeground=C["fg"], highlightbackground=C["border"])),
            (self.param_canvas, dict(bg=C["panel"])),
        ):
            w.configure(**kw)

        self._config_chat_tags()
        self._refresh_chat_list()
        self._render_conversation()

    def _toggle_theme(self) -> None:
        self.theme_name = "light" if self.theme_name == "dark" else "dark"
        self.C = THEMES[self.theme_name]
        self._apply_theme()
        self._save_config()

    # ------------------------------------------------------------------ #
    # 介面組裝
    # ------------------------------------------------------------------ #

    def _build_ui(self) -> None:
        self._build_menu()
        self._build_topbar()

        outer = ttk.Panedwindow(self, orient="horizontal")
        outer.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        self._build_sidebar(outer)
        self._build_chat_area(outer)
        self._build_params(outer)

        self._build_statusbar()
        self.bind("<Control-Return>", lambda e: self.send_message())
        self.bind("<Control-n>", lambda e: self.new_chat())
        self.bind("<F5>", lambda e: self.refresh_models())

    def _build_menu(self) -> None:
        m = tk.Menu(self)
        f = tk.Menu(m, tearoff=0)
        f.add_command(label="新對話\tCtrl+N", command=self.new_chat)
        f.add_separator()
        f.add_command(label="匯出目前對話 (Markdown)…", command=lambda: self.export("md"))
        f.add_command(label="匯出目前對話 (JSON)…", command=lambda: self.export("json"))
        f.add_separator()
        f.add_command(label="離開", command=self._on_close)
        m.add_cascade(label="檔案", menu=f)

        v = tk.Menu(m, tearoff=0)
        v.add_command(label="切換深色 / 淺色主題", command=self._toggle_theme)
        v.add_command(label="重新整理模型清單\tF5", command=self.refresh_models)
        v.add_command(label="顯示已載入模型 (/api/ps)", command=self.show_running)
        m.add_cascade(label="檢視", menu=v)

        h = tk.Menu(m, tearoff=0)
        h.add_command(label="模型資訊 (/api/show)", command=self.show_model_info)
        h.add_command(label="關於", command=self._about)
        m.add_cascade(label="說明", menu=h)
        self.config(menu=m)

    def _build_topbar(self) -> None:
        bar = ttk.Frame(self, style="Bg.TFrame")
        bar.pack(fill="x", padx=8, pady=8)

        ttk.Label(bar, text="🦙 Ollama GUI", style="Title.TLabel").pack(side="left", padx=(2, 14))

        ttk.Label(bar, text="主機", style="Bg.TLabel").pack(side="left")
        e = ttk.Entry(bar, textvariable=self.host_var, width=26)
        e.pack(side="left", padx=(6, 6))
        e.bind("<Return>", lambda ev: self.refresh_models())

        ttk.Button(bar, text="連線 / 重新整理", command=self.refresh_models).pack(side="left")

        ttk.Label(bar, text="模型", style="Bg.TLabel").pack(side="left", padx=(18, 6))
        self.model_box = ttk.Combobox(bar, textvariable=self.model_var,
                                      state="readonly", width=34, values=[])
        self.model_box.pack(side="left")
        self.model_box.bind("<<ComboboxSelected>>", lambda ev: self.on_model_change())

        ttk.Label(bar, textvariable=self.caps_var, style="Bg.TLabel"
                  ).pack(side="left", padx=12)

    def _build_sidebar(self, parent: ttk.Panedwindow) -> None:
        side = ttk.Frame(parent, style="Card.TFrame", padding=8)
        parent.add(side, weight=0)

        row = ttk.Frame(side)
        row.pack(fill="x")
        ttk.Label(row, text="對話", style="Head.TLabel").pack(side="left")
        ttk.Button(row, text="＋ 新對話", command=self.new_chat).pack(side="right")

        self.chat_list = tk.Listbox(side, activestyle="none", borderwidth=0,
                                    highlightthickness=1, font=UI_FONT, width=22)
        self.chat_list.pack(fill="both", expand=True, pady=8)
        self.chat_list.bind("<<ListboxSelect>>", self.on_select_chat)

        ttk.Button(side, text="刪除此對話", style="Danger.TButton",
                   command=self.delete_chat).pack(fill="x")

    def _build_chat_area(self, parent: ttk.Panedwindow) -> None:
        wrap = ttk.Frame(parent, style="Card.TFrame", padding=(2, 2))
        parent.add(wrap, weight=4)

        # 對話顯示區
        cf = ttk.Frame(wrap)
        cf.pack(fill="both", expand=True)
        self.chat = tk.Text(cf, wrap="word", borderwidth=0, highlightthickness=0,
                            font=UI_FONT, padx=18, pady=14, spacing1=2, spacing3=3,
                            state="disabled", cursor="arrow")
        sb = ttk.Scrollbar(cf, orient="vertical", command=self.chat.yview)
        self.chat.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.chat.pack(side="left", fill="both", expand=True)

        # 輸入區
        bottom = ttk.Frame(wrap, padding=(10, 6, 10, 8))
        bottom.pack(fill="x")

        self.attach_bar = ttk.Label(bottom, textvariable=self.attach_var, style="Muted.TLabel")
        self.attach_bar.pack(fill="x", anchor="w")

        ib = ttk.Frame(bottom)
        ib.pack(fill="x", pady=(4, 4))
        self.input = tk.Text(ib, height=4, wrap="word", font=UI_FONT,
                             borderwidth=1, relief="flat", padx=10, pady=8,
                             highlightthickness=1)
        self.input.pack(side="left", fill="both", expand=True)
        self.input.bind("<Return>", self._on_enter)
        self.input.bind("<Shift-Return>", lambda e: None)

        btns = ttk.Frame(ib)
        btns.pack(side="left", fill="y", padx=(8, 0))
        self.send_btn = ttk.Button(btns, text="送出  ▶", style="Accent.TButton",
                                   command=self.send_message)
        self.send_btn.pack(fill="x")
        self.stop_btn = ttk.Button(btns, text="停止  ■", command=self.stop,
                                   state="disabled")
        self.stop_btn.pack(fill="x", pady=4)
        self.img_btn = ttk.Button(btns, text="📎 圖片", command=self.attach_image,
                                  state="disabled")
        self.img_btn.pack(fill="x")

        tools = ttk.Frame(bottom)
        tools.pack(fill="x")
        ttk.Button(tools, text="🔄 重新產生", command=self.regenerate).pack(side="left")
        ttk.Button(tools, text="🧹 清空對話", command=self.clear_chat).pack(side="left", padx=6)
        ttk.Button(tools, text="📋 複製最後回覆", command=self.copy_last).pack(side="left")
        ttk.Label(tools, text="Enter 送出 · Shift+Enter 換行",
                  style="Muted.TLabel").pack(side="right")

    def _build_params(self, parent: ttk.Panedwindow) -> None:
        outer = ttk.Frame(parent, style="Card.TFrame")
        parent.add(outer, weight=0)

        canvas = tk.Canvas(outer, borderwidth=0, highlightthickness=0,
                           bg=self.C["panel"], width=310)
        sb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        p = ttk.Frame(canvas, padding=10)
        win = canvas.create_window((0, 0), window=p, anchor="nw")
        p.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
        canvas.bind_all("<MouseWheel>", lambda e: self._wheel(canvas, e))
        self.param_canvas = canvas

        # --- 思考 (thinking) --- #
        g = ttk.Labelframe(p, text=" 思考模式 THINKING ", padding=10)
        g.pack(fill="x", pady=(0, 10))
        ttk.Label(g, text="Think effort").pack(anchor="w")
        self.think_box = ttk.Combobox(g, textvariable=self.think_var, state="readonly",
                                      values=[m[0] for m in THINK_MODES])
        self.think_box.pack(fill="x", pady=(2, 6))
        ttk.Checkbutton(g, text="在對話中顯示思考過程",
                        variable=self.show_think_var).pack(anchor="w")
        self.think_hint = ttk.Label(
            g, style="Muted.TLabel", wraplength=250, justify="left",
            text="low / medium / high 僅 gpt-oss 等模型支援；\n"
                 "其他推理模型（qwen3、deepseek-r1…）請用 開啟 / 關閉。")
        self.think_hint.pack(anchor="w", pady=(6, 0))

        # --- 系統提示 --- #
        g = ttk.Labelframe(p, text=" 系統提示 SYSTEM ", padding=10)
        g.pack(fill="x", pady=(0, 10))
        self.sys_text = tk.Text(g, height=5, wrap="word", font=UI_FONT,
                                borderwidth=1, relief="flat", padx=6, pady=5,
                                highlightthickness=1)
        self.sys_text.pack(fill="x")
        if self._system_default:
            self.sys_text.insert("1.0", self._system_default)

        # --- 取樣參數 --- #
        g = ttk.Labelframe(p, text=" 取樣參數 SAMPLING ", padding=10)
        g.pack(fill="x", pady=(0, 10))
        self._slider(g, "Temperature", self.v_temperature, 0.0, 2.0, "{:.2f}",
                     "越高越有創意，越低越穩定 (預設 0.8)")
        self._slider(g, "Top P", self.v_top_p, 0.0, 1.0, "{:.2f}",
                     "累積機率截斷 (預設 0.9)")
        self._slider(g, "Top K", self.v_top_k, 0, 100, "{:.0f}",
                     "候選詞數量 (預設 40)")
        self._slider(g, "Min P", self.v_min_p, 0.0, 1.0, "{:.2f}",
                     "最低機率門檻 (預設 0，關閉)")
        self._slider(g, "Repeat penalty", self.v_repeat_penalty, 0.5, 2.0, "{:.2f}",
                     "重複懲罰 (預設 1.1)")

        # --- 進階 --- #
        g = ttk.Labelframe(p, text=" 進階 ADVANCED ", padding=10)
        g.pack(fill="x", pady=(0, 10))
        self._entry(g, "num_ctx (上下文長度)", self.v_num_ctx)
        self._entry(g, "num_predict (最多產生 token，-1 不限)", self.v_num_predict)
        self._entry(g, "seed (-1 表示隨機)", self.v_seed)
        self._entry(g, "stop (多組用 , 分隔)", self.v_stop)
        self._entry(g, "keep_alive (模型駐留時間)", self.v_keep_alive)

        ttk.Button(p, text="↺ 全部還原預設", command=self.reset_params).pack(fill="x")

    def _wheel(self, canvas: tk.Canvas, event) -> None:
        """只有滑鼠在參數面板上時才捲動它。"""
        w = self.winfo_containing(event.x_root, event.y_root)
        while w is not None:
            if w is canvas or w is self.param_canvas:
                canvas.yview_scroll(int(-event.delta / 120), "units")
                return
            w = getattr(w, "master", None)

    def _slider(self, parent, label, var, lo, hi, fmt, hint=""):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(4, 0))
        ttk.Label(row, text=label).pack(side="left")
        val = ttk.Label(row, text=fmt.format(var.get()), style="Muted.TLabel")
        val.pack(side="right")
        ttk.Scale(parent, from_=lo, to=hi, variable=var, orient="horizontal").pack(fill="x")
        if hint:
            ttk.Label(parent, text=hint, style="Muted.TLabel").pack(anchor="w", pady=(0, 4))
        # 只更新顯示、不回寫變數，避免 Scale <-> Variable 互相觸發
        var.trace_add("write", lambda *_: val.configure(text=fmt.format(var.get())))

    def _entry(self, parent, label, var):
        ttk.Label(parent, text=label, style="Muted.TLabel").pack(anchor="w", pady=(4, 0))
        ttk.Entry(parent, textvariable=var).pack(fill="x")

    def _build_statusbar(self) -> None:
        bar = ttk.Frame(self, style="Bg.TFrame")
        bar.pack(fill="x", side="bottom")
        ttk.Label(bar, textvariable=self.status_var, style="Bg.TLabel",
                  anchor="w").pack(fill="x", padx=12, pady=(0, 6))

    # ------------------------------------------------------------------ #
    # 對話文字區 tag 與 Markdown 呈現
    # ------------------------------------------------------------------ #

    def _config_chat_tags(self) -> None:
        C, t = self.C, self.chat
        t.tag_configure("user_hdr", foreground=C["user"], font=(UI_FONT[0], 10, "bold"),
                        spacing1=12, spacing3=4)
        t.tag_configure("asst_hdr", foreground=C["assistant"], font=(UI_FONT[0], 10, "bold"),
                        spacing1=12, spacing3=4)
        t.tag_configure("body", foreground=C["fg"], lmargin1=14, lmargin2=14, spacing3=2)
        t.tag_configure("think_hdr", foreground=C["think"], font=(UI_FONT[0], 9, "bold"),
                        lmargin1=14, spacing1=4)
        t.tag_configure("think_body", foreground=C["muted"], font=(UI_FONT[0], 9, "italic"),
                        lmargin1=26, lmargin2=26, rmargin=20)
        t.tag_configure("code", background=C["code_bg"], font=MONO,
                        lmargin1=26, lmargin2=26, rmargin=16, spacing1=4, spacing3=4)
        t.tag_configure("inline_code", background=C["code_bg"], font=MONO)
        t.tag_configure("bold", font=(UI_FONT[0], 10, "bold"))
        t.tag_configure("h", foreground=C["fg"], font=(UI_FONT[0], 12, "bold"),
                        lmargin1=14, spacing1=8, spacing3=3)
        t.tag_configure("stats", foreground=C["muted"], font=(UI_FONT[0], 8),
                        lmargin1=14, spacing3=6)
        t.tag_configure("err", foreground=C["err"], lmargin1=14, lmargin2=14, spacing1=6)
        t.tag_configure("sys", foreground=C["muted"], font=(UI_FONT[0], 9, "italic"),
                        lmargin1=14, spacing1=6)

    def _at_bottom(self) -> bool:
        try:
            return self.chat.yview()[1] > 0.999
        except Exception:
            return True

    def _append(self, text: str, *tags) -> None:
        stick = self._at_bottom()
        self.chat.configure(state="normal")
        self.chat.insert("end", text, tags)
        self.chat.configure(state="disabled")
        if stick:
            self.chat.see("end")

    _FENCE = re.compile(r"```([^\n`]*)\n?(.*?)(?:```|\Z)", re.S)
    _INLINE = re.compile(r"(\*\*.+?\*\*|`[^`\n]+`)")

    def _insert_markdown(self, content: str) -> None:
        """把回覆用簡易 Markdown 呈現：程式碼區塊、行內程式碼、粗體、標題、清單。"""
        pos = 0
        for m in self._FENCE.finditer(content):
            self._insert_plain(content[pos:m.start()])
            lang, code = m.group(1).strip(), m.group(2)
            if lang:
                self._append(f"  {lang}\n", "think_hdr")
            self._append(code.rstrip("\n") + "\n", "code")
            pos = m.end()
        self._insert_plain(content[pos:])

    def _insert_plain(self, text: str) -> None:
        if not text:
            return
        for line in text.splitlines(keepends=True):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                self._append(stripped.lstrip("# ").rstrip() + "\n", "h")
                continue
            if stripped.startswith(("- ", "* ", "+ ")):
                indent = len(line) - len(stripped)
                self._append(" " * indent + "• ", "body")
                line = stripped[2:]
            for part in self._INLINE.split(line):
                if not part:
                    continue
                if part.startswith("**") and part.endswith("**") and len(part) > 4:
                    self._append(part[2:-2], "body", "bold")
                elif part.startswith("`") and part.endswith("`") and len(part) > 2:
                    self._append(part[1:-1], "body", "inline_code")
                else:
                    self._append(part, "body")

    def _insert_thinking(self, thinking: str, collapsed: bool = True) -> None:
        """插入可折疊的思考區塊。"""
        if not thinking.strip():
            return
        self._think_counter += 1
        body_tag = f"think_body_{self._think_counter}"
        hdr_tag = f"think_hdr_{self._think_counter}"

        self.chat.configure(state="normal")
        hdr_start = self.chat.index("end-1c")
        self.chat.insert("end", ("▸" if collapsed else "▾") + " 思考過程\n",
                         ("think_hdr", hdr_tag))
        body_start = self.chat.index("end-1c")
        self.chat.insert("end", thinking.strip() + "\n", ("think_body", body_tag))
        self.chat.tag_configure(body_tag, elide=collapsed)
        self.chat.tag_configure(hdr_tag, underline=False)
        self.chat.tag_bind(hdr_tag, "<Button-1>",
                           lambda e, b=body_tag, h=hdr_tag, s=hdr_start: self._toggle_think(b, h, s))
        self.chat.tag_bind(hdr_tag, "<Enter>", lambda e: self.chat.configure(cursor="hand2"))
        self.chat.tag_bind(hdr_tag, "<Leave>", lambda e: self.chat.configure(cursor="arrow"))
        self.chat.configure(state="disabled")
        _ = body_start

    def _toggle_think(self, body_tag: str, hdr_tag: str, hdr_start: str) -> None:
        cur = bool(self.chat.tag_cget(body_tag, "elide") in ("1", "true", True))
        self.chat.tag_configure(body_tag, elide=not cur)
        self.chat.configure(state="normal")
        rng = self.chat.tag_ranges(hdr_tag)
        if rng:
            self.chat.replace(rng[0], f"{rng[0]}+1c", "▾" if cur else "▸",
                              ("think_hdr", hdr_tag))
        self.chat.configure(state="disabled")
        _ = hdr_start

    # ------------------------------------------------------------------ #
    # 畫面渲染
    # ------------------------------------------------------------------ #

    def _render_conversation(self) -> None:
        self.chat.configure(state="normal")
        self.chat.delete("1.0", "end")
        self.chat.configure(state="disabled")
        self._think_counter = 0

        if not self.current.messages:
            self._append("開始新的對話 —— 在下方輸入訊息，按 Enter 送出。\n", "sys")
            return

        for m in self.current.messages:
            role = m["role"]
            if role == "user":
                self._append("你\n", "user_hdr")
                if m.get("images"):
                    self._append(f"[附加 {len(m['images'])} 張圖片]\n", "sys")
                self._append(m["content"] + "\n", "body")
            elif role == "assistant":
                self._append(m.get("model") or self.model_var.get() or "assistant",
                             "asst_hdr")
                self._append("\n", "asst_hdr")
                if m.get("thinking") and self.show_think_var.get():
                    self._insert_thinking(m["thinking"])
                self._insert_markdown(m["content"])
                self._append("\n", "body")
                if m.get("stats"):
                    self._append(m["stats"] + "\n", "stats")
        self.chat.see("end")

    def _refresh_chat_list(self) -> None:
        self.chat_list.delete(0, "end")
        for c in self.conversations:
            self.chat_list.insert("end", "  " + c.title)
        try:
            idx = self.conversations.index(self.current)
            self.chat_list.selection_clear(0, "end")
            self.chat_list.selection_set(idx)
        except ValueError:
            pass

    # ------------------------------------------------------------------ #
    # 模型
    # ------------------------------------------------------------------ #

    def refresh_models(self) -> None:
        self.client = OllamaClient(self.host_var.get())
        self.host_var.set(self.client.host)
        self.status_var.set(f"連線中… {self.client.host}")

        def work():
            try:
                models = self.client.list_models()
                ver = self.client.version()
                self.events.put(("models", models, ver))
            except OllamaError as e:
                self.events.put(("error_box", "無法取得模型清單", str(e)))
                self.events.put(("status", "連線失敗"))

        threading.Thread(target=work, daemon=True).start()

    def _on_models(self, models: list[dict], ver: str) -> None:
        self.models = models
        names = [m.get("name", "") for m in models]
        self.model_box["values"] = names
        if names:
            if self.model_var.get() not in names:
                self.model_var.set(self.current.model if self.current.model in names
                                   else names[0])
            self.status_var.set(
                f"已連線 {self.client.host}  ·  Ollama v{ver}  ·  {len(names)} 個模型可用")
            self.on_model_change()
        else:
            self.status_var.set(
                f"已連線 {self.client.host}，但沒有任何模型。請先執行： ollama pull llama3.2")
            self.caps_var.set("")

    def on_model_change(self) -> None:
        model = self.model_var.get()
        if not model:
            return
        self.current.model = model
        if model in self.caps:
            self._apply_caps(model)
            return
        self.caps_var.set("讀取模型能力中…")

        def work():
            try:
                info = self.client.show(model)
                caps = info.get("capabilities") or []
                self.events.put(("caps", model, caps))
            except OllamaError:
                self.events.put(("caps", model, []))

        threading.Thread(target=work, daemon=True).start()

    def _apply_caps(self, model: str) -> None:
        caps = self.caps.get(model, [])
        icons = []
        if "thinking" in caps:
            icons.append("🧠 thinking")
        if "tools" in caps:
            icons.append("🔧 tools")
        if "vision" in caps:
            icons.append("👁 vision")
        info = next((m for m in self.models if m.get("name") == model), {})
        size = human_size(info.get("size", 0)) if info.get("size") else ""
        params = (info.get("details") or {}).get("parameter_size", "")
        meta = "  ·  ".join(x for x in [params, size] + icons if x)
        self.caps_var.set(meta)

        # 沒有 thinking 能力就把選單鎖成關閉
        if "thinking" in caps:
            self.think_box.configure(state="readonly")
            self.think_hint.configure(
                text="low / medium / high 僅 gpt-oss 等模型支援；\n"
                     "其他推理模型（qwen3、deepseek-r1…）請用 開啟 / 關閉。")
        else:
            self.think_box.configure(state="disabled")
            self.think_var.set(THINK_MODES[0][0])
            self.think_hint.configure(text=f"⚠ {model} 不支援 thinking，此設定已停用。")

        self.img_btn.configure(state="normal" if "vision" in caps else "disabled")
        if "vision" not in caps:
            self.pending_images.clear()
            self.attach_var.set("")

    def show_model_info(self) -> None:
        model = self.model_var.get()
        if not model:
            return

        def work():
            try:
                info = self.client.show(model)
                info.pop("license", None)
                info.pop("modelfile", None)
                self.events.put(("info_box", f"模型資訊：{model}",
                                 json.dumps(info, ensure_ascii=False, indent=2)[:6000]))
            except OllamaError as e:
                self.events.put(("error_box", "讀取失敗", str(e)))

        threading.Thread(target=work, daemon=True).start()

    def show_running(self) -> None:
        def work():
            ms = self.client.running()
            if not ms:
                txt = "目前沒有模型駐留在記憶體中。"
            else:
                txt = "\n".join(
                    f"{m.get('name')}  ·  {human_size(m.get('size', 0))}  ·  "
                    f"到期 {m.get('expires_at', '?')[:19]}" for m in ms)
            self.events.put(("info_box", "已載入的模型 (/api/ps)", txt))

        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------------ #
    # 參數收集
    # ------------------------------------------------------------------ #

    def _collect_params_raw(self) -> dict:
        return {
            "temperature": round(self.v_temperature.get(), 3),
            "top_p": round(self.v_top_p.get(), 3),
            "top_k": int(round(self.v_top_k.get())),
            "min_p": round(self.v_min_p.get(), 3),
            "repeat_penalty": round(self.v_repeat_penalty.get(), 3),
            "num_ctx": self.v_num_ctx.get(),
            "num_predict": self.v_num_predict.get(),
            "seed": self.v_seed.get(),
            "stop": self.v_stop.get(),
            "keep_alive": self.v_keep_alive.get(),
            "system": self.sys_text.get("1.0", "end-1c"),
        }

    def _sampling_options(self) -> dict:
        """組出 /api/chat 的 options，-1 / 空白代表不送出。

        （注意：方法名不能叫 _options，那是 tkinter.Misc 的內部方法。）
        """
        def as_int(s, default=None):
            try:
                return int(str(s).strip())
            except (TypeError, ValueError):
                return default

        o: dict = {
            "temperature": round(self.v_temperature.get(), 3),
            "top_p": round(self.v_top_p.get(), 3),
            "top_k": int(round(self.v_top_k.get())),
            "repeat_penalty": round(self.v_repeat_penalty.get(), 3),
        }
        min_p = round(self.v_min_p.get(), 3)
        if min_p > 0:
            o["min_p"] = min_p
        n = as_int(self.v_num_ctx.get())
        if n and n > 0:
            o["num_ctx"] = n
        n = as_int(self.v_num_predict.get(), -1)
        if n is not None and n != -1:
            o["num_predict"] = n
        n = as_int(self.v_seed.get(), -1)
        if n is not None and n >= 0:
            o["seed"] = n
        stops = [s.strip() for s in self.v_stop.get().split(",") if s.strip()]
        if stops:
            o["stop"] = stops
        return o

    def _think_value(self):
        """回傳要送給 API 的 think 值；模型不支援 thinking 時回傳 None（完全不送）。"""
        model = self.model_var.get()
        if "thinking" not in self.caps.get(model, []):
            return None
        label = self.think_var.get()
        for name, value in THINK_MODES:
            if name == label:
                return value
        return None

    def reset_params(self) -> None:
        self.v_temperature.set(DEFAULT_PARAMS["temperature"])
        self.v_top_p.set(DEFAULT_PARAMS["top_p"])
        self.v_top_k.set(DEFAULT_PARAMS["top_k"])
        self.v_min_p.set(DEFAULT_PARAMS["min_p"])
        self.v_repeat_penalty.set(DEFAULT_PARAMS["repeat_penalty"])
        self.v_num_ctx.set(str(DEFAULT_PARAMS["num_ctx"]))
        self.v_num_predict.set(str(DEFAULT_PARAMS["num_predict"]))
        self.v_seed.set(str(DEFAULT_PARAMS["seed"]))
        self.v_stop.set("")
        self.v_keep_alive.set(DEFAULT_PARAMS["keep_alive"])

    # ------------------------------------------------------------------ #
    # 對話管理
    # ------------------------------------------------------------------ #

    def new_chat(self) -> None:
        if self.streaming:
            return
        c = Conversation(model=self.model_var.get())
        self.conversations.insert(0, c)
        self.current = c
        self._refresh_chat_list()
        self._render_conversation()
        self.input.focus_set()

    def on_select_chat(self, _event=None) -> None:
        sel = self.chat_list.curselection()
        if not sel or self.streaming:
            return
        c = self.conversations[sel[0]]
        if c is self.current:
            return
        self.current = c
        if c.model and c.model in (self.model_box["values"] or []):
            self.model_var.set(c.model)
            self.on_model_change()
        self._render_conversation()

    def delete_chat(self) -> None:
        if self.streaming:
            return
        if not messagebox.askyesno("刪除對話", f"確定刪除「{self.current.title}」？"):
            return
        self.conversations.remove(self.current)
        if not self.conversations:
            self.conversations.append(Conversation(model=self.model_var.get()))
        self.current = self.conversations[0]
        self._refresh_chat_list()
        self._render_conversation()
        self._save_chats()

    def clear_chat(self) -> None:
        if self.streaming:
            return
        self.current.messages.clear()
        self.current.auto_title()
        self._refresh_chat_list()
        self._render_conversation()
        self._save_chats()

    def copy_last(self) -> None:
        for m in reversed(self.current.messages):
            if m["role"] == "assistant":
                self.clipboard_clear()
                self.clipboard_append(m["content"])
                self.status_var.set("已複製最後一則回覆到剪貼簿")
                return

    def export(self, fmt: str) -> None:
        if not self.current.messages:
            return
        ext = ".md" if fmt == "md" else ".json"
        path = filedialog.asksaveasfilename(
            defaultextension=ext, initialfile=f"{self.current.title}{ext}",
            filetypes=[("Markdown", "*.md")] if fmt == "md" else [("JSON", "*.json")])
        if not path:
            return
        if fmt == "json":
            Path(path).write_text(
                json.dumps(self.current.to_dict(), ensure_ascii=False, indent=2), "utf-8")
        else:
            lines = [f"# {self.current.title}", f"\n> 模型：{self.current.model}\n"]
            for m in self.current.messages:
                who = "你" if m["role"] == "user" else m.get("model", "assistant")
                lines.append(f"\n## {who}\n")
                if m.get("thinking"):
                    lines.append("<details><summary>思考過程</summary>\n\n"
                                 f"{m['thinking']}\n\n</details>\n")
                lines.append(m["content"])
            Path(path).write_text("\n".join(lines), "utf-8")
        self.status_var.set(f"已匯出：{path}")

    def attach_image(self) -> None:
        paths = filedialog.askopenfilenames(
            title="選擇圖片",
            filetypes=[("圖片", "*.png *.jpg *.jpeg *.gif *.webp *.bmp")])
        for p in paths:
            try:
                b64 = base64.b64encode(Path(p).read_bytes()).decode("ascii")
                self.pending_images.append((Path(p).name, b64))
            except OSError as e:
                messagebox.showerror("讀取圖片失敗", str(e))
        if self.pending_images:
            names = ", ".join(n for n, _ in self.pending_images)
            self.attach_var.set(f"📎 {names}   （送出後清除）")

    # ------------------------------------------------------------------ #
    # 送出 / 串流
    # ------------------------------------------------------------------ #

    def _on_enter(self, event):
        if event.state & 0x0001:        # Shift
            return
        self.send_message()
        return "break"

    def send_message(self) -> None:   # 不能叫 send，會遮蔽 tkinter.Misc.send
        if self.streaming:
            return
        text = self.input.get("1.0", "end-1c").strip()
        if not text:
            return
        if not self.model_var.get():
            messagebox.showwarning("尚未選擇模型", "請先連線並選擇一個模型。")
            return

        msg: dict = {"role": "user", "content": text}
        if self.pending_images:
            msg["images"] = [b64 for _, b64 in self.pending_images]
        self.current.messages.append(msg)

        self.input.delete("1.0", "end")
        self._append("你\n", "user_hdr")
        if self.pending_images:
            self._append(f"[附加 {len(self.pending_images)} 張圖片]\n", "sys")
        self._append(text + "\n", "body")
        self.pending_images.clear()
        self.attach_var.set("")

        if len(self.current.messages) == 1:
            self.current.auto_title()
            self._refresh_chat_list()
        self._start_stream()

    def regenerate(self) -> None:
        if self.streaming or not self.current.messages:
            return
        while self.current.messages and self.current.messages[-1]["role"] == "assistant":
            self.current.messages.pop()
        if not self.current.messages:
            return
        self._render_conversation()
        self._start_stream()

    def _start_stream(self) -> None:
        model = self.model_var.get()
        think = self._think_value()
        options = self._sampling_options()
        keep_alive = self.v_keep_alive.get().strip() or None
        system = self.sys_text.get("1.0", "end-1c").strip()

        payload_msgs: list[dict] = []
        if system:
            payload_msgs.append({"role": "system", "content": system})
        for m in self.current.messages:
            item = {"role": m["role"], "content": m["content"]}
            if m.get("images"):
                item["images"] = m["images"]
            payload_msgs.append(item)

        # 準備 UI
        self.streaming = True
        self.cancel_flag.clear()
        self.resp_holder = {}
        self.send_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.status_var.set(f"{model} 產生中…")

        self._append(model, "asst_hdr")
        self._append("\n", "asst_hdr")
        self._resp_mark_id += 1
        self._resp_mark = f"resp_{self._resp_mark_id}"
        self.chat.mark_set(self._resp_mark, "end-1c")
        self.chat.mark_gravity(self._resp_mark, "left")
        self._think_open = False
        self._buf_content: list[str] = []
        self._buf_think: list[str] = []

        def work():
            t0 = time.time()
            first = None
            try:
                for obj in self.client.chat_stream(
                        model, payload_msgs, think=think, options=options,
                        keep_alive=keep_alive, resp_holder=self.resp_holder):
                    if self.cancel_flag.is_set():
                        break
                    m = obj.get("message") or {}
                    if m.get("thinking"):
                        if first is None:
                            first = time.time() - t0
                        self.events.put(("chunk", "thinking", m["thinking"]))
                    if m.get("content"):
                        if first is None:
                            first = time.time() - t0
                        self.events.put(("chunk", "content", m["content"]))
                    if obj.get("done"):
                        total = time.time() - t0
                        ec = obj.get("eval_count") or 0
                        ed = (obj.get("eval_duration") or 0) / 1e9
                        pc = obj.get("prompt_eval_count") or 0
                        bits = [f"{pc} prompt tokens", f"{ec} tokens"]
                        if ed > 0:
                            bits.append(f"{ec / ed:.1f} tok/s")
                        if first:
                            bits.append(f"首字 {first:.2f}s")
                        bits.append(f"總計 {total:.1f}s")
                        self.events.put(("done", "  ·  ".join(bits), model))
                        return
                self.events.put(("done", "（已停止）", model))
            except OllamaError as e:
                if self.cancel_flag.is_set():
                    self.events.put(("done", "（已停止）", model))
                else:
                    self.events.put(("stream_error", str(e), model))
            except Exception as e:                       # noqa: BLE001
                if self.cancel_flag.is_set():
                    self.events.put(("done", "（已停止）", model))
                else:
                    self.events.put(("stream_error", f"{type(e).__name__}: {e}", model))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def stop(self) -> None:
        self.cancel_flag.set()
        resp = self.resp_holder.get("resp")
        if resp is not None:
            try:
                resp.close()                  # 讓阻塞中的 read() 立刻拋出
            except Exception:
                pass
        self.status_var.set("停止中…")

    # -- 串流事件處理 ------------------------------------------------------ #

    def _on_chunk(self, kind: str, text: str) -> None:
        if kind == "thinking":
            self._buf_think.append(text)
            if not self.show_think_var.get():
                return
            if not self._think_open:
                self._append("▾ 思考中…\n", "think_hdr")
                self._think_open = True
            self._append(text, "think_body")
        else:
            if self._think_open:
                self._append("\n", "think_body")
                self._think_open = False
            self._buf_content.append(text)
            self._append(text, "body")

    def _on_done(self, stats: str, model: str) -> None:
        content = "".join(self._buf_content)
        thinking = "".join(self._buf_think)

        # 串流時是逐字純文字塞進去的，結束後整段重繪一次以套用 Markdown 格式
        self.chat.configure(state="normal")
        self.chat.delete(self._resp_mark, "end-1c")
        self.chat.configure(state="disabled")
        if thinking and self.show_think_var.get():
            self._insert_thinking(thinking, collapsed=True)
        self._insert_markdown(content)
        self._append("\n", "body")
        self._append(stats + "\n", "stats")
        self.chat.see("end")

        if content or thinking:
            self.current.messages.append({
                "role": "assistant", "content": content,
                "thinking": thinking, "model": model, "stats": stats,
            })
        self._finish(stats)

    def _on_stream_error(self, msg: str, model: str) -> None:
        self.chat.configure(state="normal")
        self.chat.delete(self._resp_mark, "end-1c")
        self.chat.configure(state="disabled")
        hint = ""
        if "does not support thinking" in msg.lower():
            hint = "\n👉 這個模型不支援 thinking，請把「思考模式」設成 關閉 (off)。"
        elif "not found" in msg.lower():
            hint = f"\n👉 請先下載模型： ollama pull {model}"
        elif "memory" in msg.lower():
            hint = "\n👉 記憶體不足，試著調小 num_ctx 或改用較小的模型。"
        self._append(f"⚠ {msg}{hint}\n", "err")
        self._finish("發生錯誤")

    def _finish(self, status: str) -> None:
        self.streaming = False
        self.send_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.status_var.set(status)
        self._save_chats()
        self.input.focus_set()

    # ------------------------------------------------------------------ #
    # 事件幫浦（把背景執行緒的結果搬到主執行緒處理，tkinter 不是 thread-safe）
    # ------------------------------------------------------------------ #

    def _pump(self) -> None:
        try:
            while True:
                ev = self.events.get_nowait()
                kind = ev[0]
                if kind == "models":
                    self._on_models(ev[1], ev[2])
                elif kind == "caps":
                    self.caps[ev[1]] = ev[2]
                    if ev[1] == self.model_var.get():
                        self._apply_caps(ev[1])
                elif kind == "chunk":
                    self._on_chunk(ev[1], ev[2])
                elif kind == "done":
                    self._on_done(ev[1], ev[2])
                elif kind == "stream_error":
                    self._on_stream_error(ev[1], ev[2])
                elif kind == "status":
                    self.status_var.set(ev[1])
                elif kind == "error_box":
                    self.status_var.set(ev[1])
                    messagebox.showerror(ev[1], ev[2])
                elif kind == "info_box":
                    self._text_dialog(ev[1], ev[2])
        except queue.Empty:
            pass
        self.after(40, self._pump)

    # ------------------------------------------------------------------ #
    # 雜項
    # ------------------------------------------------------------------ #

    def _text_dialog(self, title: str, body: str) -> None:
        C = self.C
        win = tk.Toplevel(self)
        win.title(title)
        win.geometry("720x520")
        win.configure(bg=C["panel"])
        t = tk.Text(win, wrap="none", font=MONO, bg=C["input"], fg=C["fg"],
                    borderwidth=0, padx=10, pady=10, insertbackground=C["fg"])
        sb = ttk.Scrollbar(win, orient="vertical", command=t.yview)
        t.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        t.pack(fill="both", expand=True)
        t.insert("1.0", body)
        t.configure(state="disabled")

    def _about(self) -> None:
        messagebox.showinfo(
            "關於",
            f"{APP_NAME}\n\n"
            "純 Python 標準函式庫寫成的 Ollama 桌面前端。\n"
            "使用 /api/tags 取得模型、/api/show 偵測能力、\n"
            "/api/chat 串流對話並支援 think 參數。\n\n"
            f"設定檔位置：{CONFIG_DIR}")

    def _on_close(self) -> None:
        if self.streaming:
            self.stop()
        self._save_config()
        self._save_chats()
        self.destroy()


def main() -> None:
    app = OllamaGUI()
    app.mainloop()


if __name__ == "__main__":
    main()
