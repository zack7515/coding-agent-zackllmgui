# -*- coding: utf-8 -*-
"""主視窗：組裝三欄版面、串接背景執行緒與 Ollama。

執行緒規則：worker 一律只發 Signal，絕不直接碰任何 widget。
"""

from __future__ import annotations

import base64
import time
from pathlib import Path

from PySide6.QtCore import QThread, QTimer, Qt, Signal
from PySide6.QtGui import QFont, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QFileDialog, QFrame, QHBoxLayout,
                               QLabel, QMainWindow, QMenu, QMessageBox,
                               QPushButton, QScrollArea, QSplitter, QVBoxLayout,
                               QWidget)

from . import store, theme
from .client import DEFAULT_HOST, OllamaClient, OllamaError, human_size, normalize_host
from .ui_common import (IconButton, IconLabel, StatusPill, palette,
                        refresh_icons_recursive, set_palette)
from .ui_message import MessageWidget
from .ui_panels import Composer, HostDialog, ParamPanel, Sidebar

CENTER_WIDTH = 720
POLL_INTERVAL_MS = 30_000
FLUSH_INTERVAL_MS = 50


# --------------------------------------------------------------------------- #
# 背景執行緒
# --------------------------------------------------------------------------- #

class ProbeWorker(QThread):
    """讀取模型清單與版本；連線狀態燈就靠它。"""

    done = Signal(list, str)
    failed = Signal(str)

    def __init__(self, host: str, parent=None):
        super().__init__(parent)
        self._host = host

    def run(self) -> None:
        client = OllamaClient(self._host)
        try:
            models = client.list_models()
            self.done.emit(models, client.version())
        except OllamaError as exc:
            self.failed.emit(str(exc))


class CapsWorker(QThread):
    """讀取單一模型的 capabilities。"""

    done = Signal(str, list)

    def __init__(self, host: str, model: str, parent=None):
        super().__init__(parent)
        self._host = host
        self._model = model

    def run(self) -> None:
        try:
            info = OllamaClient(self._host).show(self._model)
            self.done.emit(self._model, info.get("capabilities") or [])
        except OllamaError:
            self.done.emit(self._model, [])


class ChatWorker(QThread):
    """串流對話。cancel() 由主執行緒呼叫，直接關掉 response 讓阻塞的讀取跳出。"""

    chunk = Signal(str, str)        # kind("thinking"|"content"), text
    finished_ok = Signal(str)       # 統計字串
    failed = Signal(str)
    stopped = Signal()

    def __init__(self, host: str, model: str, messages: list[dict], think,
                 options: dict, keep_alive: str | None, parent=None):
        super().__init__(parent)
        self._host = host
        self._model = model
        self._messages = messages
        self._think = think
        self._options = options
        self._keep_alive = keep_alive
        self._cancel = False
        self._holder: dict = {}

    def cancel(self) -> None:
        self._cancel = True
        resp = self._holder.get("resp")
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass

    def run(self) -> None:
        client = OllamaClient(self._host)
        started = time.time()
        first_token = None
        try:
            for obj in client.chat_stream(
                    self._model, self._messages, think=self._think,
                    options=self._options, keep_alive=self._keep_alive,
                    resp_holder=self._holder):
                if self._cancel:
                    break
                message = obj.get("message") or {}
                if message.get("thinking"):
                    first_token = first_token or (time.time() - started)
                    self.chunk.emit("thinking", message["thinking"])
                if message.get("content"):
                    first_token = first_token or (time.time() - started)
                    self.chunk.emit("content", message["content"])
                if obj.get("done"):
                    self.finished_ok.emit(self._stats(obj, started, first_token))
                    return
            self.stopped.emit()
        except OllamaError as exc:
            if self._cancel:
                self.stopped.emit()
            else:
                self.failed.emit(str(exc))
        except Exception as exc:                       # noqa: BLE001
            if self._cancel:
                self.stopped.emit()
            else:
                self.failed.emit(f"{type(exc).__name__}: {exc}")

    @staticmethod
    def _stats(obj: dict, started: float, first_token: float | None) -> str:
        total = time.time() - started
        eval_count = obj.get("eval_count") or 0
        eval_seconds = (obj.get("eval_duration") or 0) / 1e9
        parts = [f"{obj.get('prompt_eval_count') or 0} prompt tokens",
                 f"{eval_count} tokens"]
        if eval_seconds > 0:
            parts.append(f"{eval_count / eval_seconds:.1f} tok/s")
        if first_token:
            parts.append(f"首字 {first_token:.2f}s")
        parts.append(f"總計 {total:.1f}s")
        return "  ·  ".join(parts)


# --------------------------------------------------------------------------- #
# 主視窗
# --------------------------------------------------------------------------- #

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Ollama GUI")
        self.resize(1440, 900)
        self.setMinimumSize(980, 620)

        self.cfg = store.load_config()
        self.theme_name = self.cfg.get("theme", "dark")
        set_palette(theme.THEMES[self.theme_name])

        self.host = normalize_host(self.cfg.get("host", DEFAULT_HOST))
        self.models: list[dict] = []
        self.caps: dict[str, list[str]] = {}
        self.model = self.cfg.get("model", "")
        self.conn_state = "idle"
        self.conn_message = ""
        self.version = "?"
        # 使用者選的思考強度；換模型時盡量沿用
        self._think_pref = self.cfg.get("think")

        self.conversations = store.load_chats()
        self.current = self.conversations[0]

        self._probes: list[ProbeWorker] = []
        self._probe_seq = 0
        self.caps_worker: CapsWorker | None = None
        self.chat_worker: ChatWorker | None = None
        self.host_test: ProbeWorker | None = None
        self.host_dialog: HostDialog | None = None

        self.stream_msg: MessageWidget | None = None
        self.stream_started = 0.0
        self._pending = {"thinking": "", "content": ""}
        self._buffer = {"thinking": "", "content": ""}
        self.pending_images: list[tuple[str, str]] = []

        self._build_ui()
        self._apply_theme()
        self._restore_settings()
        self._render_conversation()
        self._refresh_sidebar()

        self.flush_timer = QTimer(self)
        self.flush_timer.setInterval(FLUSH_INTERVAL_MS)
        self.flush_timer.timeout.connect(self._flush_stream)

        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(POLL_INTERVAL_MS)
        self.poll_timer.timeout.connect(lambda: self.refresh_models(quiet=True))
        self.poll_timer.start()

        QShortcut(QKeySequence("Ctrl+N"), self, self.new_chat)
        QShortcut(QKeySequence("F5"), self, self.refresh_models)

        self._update_center_margins()
        self.refresh_models()

    # ------------------------------------------------------------------ #
    # 版面
    # ------------------------------------------------------------------ #

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(1)
        layout.addWidget(self.splitter)

        self.sidebar = Sidebar()
        self.sidebar.new_chat.connect(self.new_chat)
        self.sidebar.chat_selected.connect(self.select_chat)
        self.sidebar.chat_deleted.connect(self.delete_chat)
        self.sidebar.settings_clicked.connect(self.open_host_dialog)
        self.sidebar.theme_toggled.connect(self.toggle_theme)
        self.splitter.addWidget(self.sidebar)

        self.splitter.addWidget(self._build_center())

        self.params = ParamPanel()
        self.params.changed.connect(self._save_settings)
        self.params.think_changed.connect(self._on_think_changed)
        self.params.show_think_changed.connect(self._on_show_think_changed)
        self.splitter.addWidget(self.params)
        # 輸入區的思考膠囊要等 params 建好才能接
        self.composer.think_clicked.connect(self.params.cycle_think)

        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 0)
        self.splitter.setSizes([260, 860, 320])

    def _build_center(self) -> QWidget:
        center = QWidget()
        box = QVBoxLayout(center)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)

        # ── 頂列 ──
        header = QFrame()
        header.setObjectName("SidebarHeader")
        header.setFixedHeight(56)
        row = QHBoxLayout(header)
        row.setContentsMargins(20, 0, 16, 0)
        row.setSpacing(12)

        self.model_btn = QPushButton("尚未選擇模型")
        self.model_btn.setObjectName("ModelButton")
        self.model_btn.setFixedHeight(32)
        self.model_btn.setCursor(Qt.PointingHandCursor)
        self.model_btn.clicked.connect(self._show_model_menu)

        self.chip_think = QLabel("thinking")
        self.chip_think.setObjectName("CapChip")
        self.chip_tools = QLabel("tools")
        self.chip_tools.setObjectName("CapChip")
        self.chip_vision = QLabel("vision")
        self.chip_vision.setObjectName("CapChip")
        for chip in (self.chip_think, self.chip_tools, self.chip_vision):
            chip.setFixedHeight(22)
            chip.setAlignment(Qt.AlignCenter)
            chip.hide()

        self.status_pill = StatusPill()
        self.status_pill.clicked.connect(self.open_host_dialog)
        self.status_pill.retry_requested.connect(self.refresh_models)

        self.more_btn = IconButton("more", 16, 32, "更多", "ink2")
        self.more_btn.clicked.connect(self._show_more_menu)

        row.addWidget(self.model_btn)
        row.addWidget(self.chip_think, 0, Qt.AlignVCenter)
        row.addWidget(self.chip_tools, 0, Qt.AlignVCenter)
        row.addWidget(self.chip_vision, 0, Qt.AlignVCenter)
        row.addStretch(1)
        row.addWidget(self.status_pill)
        row.addWidget(self.more_btn)
        box.addWidget(header)

        # ── 訊息串 ──
        self.chat_scroll = QScrollArea()
        self.chat_scroll.setWidgetResizable(True)
        self.chat_scroll.setFrameShape(QFrame.NoFrame)
        self.chat_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.chat_scroll.viewport().setObjectName("ChatViewport")
        canvas = QWidget()
        canvas.setObjectName("Root")
        self.canvas_layout = QVBoxLayout(canvas)
        self.canvas_layout.setContentsMargins(24, 24, 24, 8)
        self.canvas_layout.setSpacing(22)
        self.canvas_layout.addStretch(1)
        self.chat_scroll.setWidget(canvas)
        box.addWidget(self.chat_scroll, 1)

        # 黏底：內容長高時（rangeChanged）才重新對齊底部，而不是猜時機用
        # singleShot——那樣會在版面還沒重算完就捲，永遠差一截。
        self._stick_bottom = True
        scrollbar = self.chat_scroll.verticalScrollBar()
        scrollbar.rangeChanged.connect(lambda *_: self._pin_bottom())
        scrollbar.valueChanged.connect(self._track_stick)

        # ── 輸入區 ──
        composer_wrap = QWidget()
        self.composer_layout = QHBoxLayout(composer_wrap)
        self.composer_layout.setContentsMargins(24, 8, 24, 18)
        self.composer = Composer()
        self.composer.submitted.connect(self.send_message)
        self.composer.stop_requested.connect(self.stop_stream)
        self.composer.attach_requested.connect(self.attach_images)
        self.composer_layout.addWidget(self.composer)
        box.addWidget(composer_wrap)

        self.chat_scroll.viewport().installEventFilter(self)
        return center

    def eventFilter(self, obj, event):  # noqa: N802
        if obj is self.chat_scroll.viewport() and event.type() == event.Type.Resize:
            self._update_center_margins()
        return super().eventFilter(obj, event)

    def _update_center_margins(self) -> None:
        """把訊息與輸入區維持在 720px 置中欄內。"""
        width = self.chat_scroll.viewport().width()
        side = max(24, (width - CENTER_WIDTH) // 2)
        self.canvas_layout.setContentsMargins(side, 24, side, 8)
        self.composer_layout.setContentsMargins(side, 8, side, 18)

    # ------------------------------------------------------------------ #
    # 主題
    # ------------------------------------------------------------------ #

    def _apply_theme(self) -> None:
        colors = theme.THEMES[self.theme_name]
        set_palette(colors)
        app = QApplication.instance()
        font = QFont()
        font.setFamilies(theme.UI_FAMILIES)
        font.setPointSize(10)
        app.setFont(font)
        app.setStyleSheet(theme.build_qss(colors))

        self.chip_think.setStyleSheet(
            f"background: {colors['think_bg']}; color: {colors['think']};")
        self.chip_tools.setStyleSheet(
            f"background: {colors['surface']}; color: {colors['ok']};")
        self.chip_vision.setStyleSheet(
            f"background: {colors['surface']}; color: {colors['accent']};")

        refresh_icons_recursive(self)
        self._repolish(self)
        self.sidebar.set_theme_icon(self.theme_name)

    @staticmethod
    def _repolish(root: QWidget) -> None:
        """換樣式表後強制整棵樹重新套用樣式。

        只呼叫 setStyleSheet 有時候不會讓已經建好的子元件重畫背景，
        換到淺色主題時面板會殘留深色底。
        """
        for widget in [root, *root.findChildren(QWidget)]:
            widget.style().unpolish(widget)
            widget.style().polish(widget)
            widget.update()

    def toggle_theme(self) -> None:
        self.theme_name = "light" if self.theme_name == "dark" else "dark"
        self._apply_theme()
        self._render_conversation()          # 內文顏色是烘進 HTML 的，要重繪
        self._update_status_display()
        self._save_settings()

    # ------------------------------------------------------------------ #
    # 設定
    # ------------------------------------------------------------------ #

    def _restore_settings(self) -> None:
        self.params.set_params(self.cfg.get("params") or {})
        self.params.show_think.setChecked(bool(self.cfg.get("show_think", True)))

    def _save_settings(self) -> None:
        store.save_config({
            "host": self.host,
            "model": self.model,
            "theme": self.theme_name,
            "show_think": self.params.show_think.isChecked(),
            "think": self.params.think_value(),
            "params": self.params.params(),
        })

    # ------------------------------------------------------------------ #
    # 連線與模型
    # ------------------------------------------------------------------ #

    def refresh_models(self, quiet: bool = False) -> None:
        """探測 server。使用者主動觸發時一定重新發起，舊探測的結果會被丟棄，
        否則主機卡住時按「重試」或換主機都會沒反應。"""
        if quiet and self._probes:
            return                      # 定時輪詢就不要疊上去
        self._probe_seq += 1
        seq = self._probe_seq
        if not quiet:
            self._set_conn_state("connecting")

        worker = ProbeWorker(self.host, self)
        worker.done.connect(
            lambda models, version, s=seq: self._on_models(models, version, s))
        worker.failed.connect(lambda message, s=seq: self._on_probe_failed(message, s))
        worker.finished.connect(lambda w=worker: self._probe_finished(w))
        self._probes.append(worker)
        worker.start()

    def _probe_finished(self, worker: ProbeWorker) -> None:
        if worker in self._probes:
            self._probes.remove(worker)
        worker.deleteLater()

    def _on_models(self, models: list, version: str, seq: int = 0) -> None:
        if seq and seq != self._probe_seq:
            return                      # 已被更新的探測取代
        self.models = models
        names = [m.get("name", "") for m in models]
        self.version = version

        if not names:
            self._set_conn_state("empty")
        else:
            self._set_conn_state("ok")
            if self.model not in names:
                self.model = self.current.model if self.current.model in names else names[0]
            self._update_model_button()
            self._ensure_caps(self.model)

    def _on_probe_failed(self, message: str, seq: int = 0) -> None:
        if seq and seq != self._probe_seq:
            return
        self._set_conn_state("error", message)

    def _set_conn_state(self, state: str, message: str = "") -> None:
        self.conn_state = state
        self.conn_message = message
        self._update_status_display()

        if state == "ok":
            self.composer.set_blocked("")
        elif state == "empty":
            self.composer.set_blocked("沒有可用模型，請先執行 ollama pull")
        elif state == "error":
            self.composer.set_blocked("尚未連線，無法送出")
        # connecting 時不鎖，避免使用者誤以為壞掉

    def _update_status_display(self) -> None:
        short_host = self.host.replace("http://", "").replace("https://", "")
        state = self.conn_state
        if state == "ok":
            self.status_pill.set_status("ok", "已連線", short_host)
            self.sidebar.set_connection_summary(
                short_host, f"{len(self.models)} 個模型 · v{getattr(self, 'version', '?')}")
        elif state == "empty":
            self.status_pill.set_status("ok", "已連線", "無可用模型", "warn")
            self.sidebar.set_connection_summary(short_host, "沒有已下載的模型")
        elif state == "connecting":
            self.status_pill.set_status("connecting", "連線中…", short_host)
            self.sidebar.set_connection_summary(short_host, "連線中…")
        elif state == "error":
            self.status_pill.set_status("error", "無法連線", "")
            self.sidebar.set_connection_summary(short_host, "連線失敗")
        else:
            self.status_pill.set_status("idle", "尚未連線", short_host)

    def _ensure_caps(self, model: str) -> None:
        if not model:
            return
        if model in self.caps:
            self._apply_caps(model)
            return
        self.caps_worker = CapsWorker(self.host, model, self)
        self.caps_worker.done.connect(self._on_caps)
        self.caps_worker.start()

    def _on_caps(self, model: str, caps: list) -> None:
        self.caps[model] = caps
        if model == self.model:
            self._apply_caps(model)

    def _apply_caps(self, model: str) -> None:
        caps = self.caps.get(model, [])
        self.chip_think.setVisible("thinking" in caps)
        self.chip_tools.setVisible("tools" in caps)
        self.chip_vision.setVisible("vision" in caps)

        self.params.set_think_options(store.think_options(model, caps), model)
        if self._think_pref is not None and self.params.think_seg.isEnabled():
            self.params.think_seg.set_value(self._think_pref, notify=False)
        self._sync_think_chip()

        self.composer.attach_btn.setEnabled("vision" in caps)
        if "vision" not in caps and self.pending_images:
            self.pending_images.clear()
            self.composer.set_attachments([])

    def _sync_think_chip(self) -> None:
        self.composer.set_think_label(self.params.think_label(),
                                      self.params.think_seg.isEnabled())

    def _on_think_changed(self, value) -> None:
        self._think_pref = value
        self._sync_think_chip()
        self._save_settings()

    def _on_show_think_changed(self, _value: bool) -> None:
        self._render_conversation()
        self._save_settings()

    def _update_model_button(self) -> None:
        info = next((m for m in self.models if m.get("name") == self.model), {})
        params = (info.get("details") or {}).get("parameter_size", "")
        size = human_size(info["size"]) if info.get("size") else ""
        suffix = "  ·  ".join(x for x in (params, size) if x)
        self.model_btn.setText(f"{self.model}    {suffix}" if suffix else self.model)

    def _show_model_menu(self) -> None:
        menu = QMenu(self)
        if not self.models:
            menu.addAction("（沒有可用模型）").setEnabled(False)
        for info in self.models:
            name = info.get("name", "")
            params = (info.get("details") or {}).get("parameter_size", "")
            size = human_size(info["size"]) if info.get("size") else ""
            label = "  ·  ".join(x for x in (name, params, size) if x)
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(name == self.model)
            action.triggered.connect(lambda _c=False, n=name: self.select_model(n))
        menu.addSeparator()
        menu.addAction("重新整理模型清單", lambda: self.refresh_models())
        menu.exec(self.model_btn.mapToGlobal(self.model_btn.rect().bottomLeft()))

    def select_model(self, name: str) -> None:
        self.model = name
        self.current.model = name
        self._update_model_button()
        self._ensure_caps(name)
        self._save_settings()

    # ------------------------------------------------------------------ #
    # 主機設定
    # ------------------------------------------------------------------ #

    def open_host_dialog(self) -> None:
        dialog = HostDialog(self.host, self)
        self.host_dialog = dialog
        dialog.test_requested.connect(self._test_host)
        if dialog.exec():
            self.host = normalize_host(dialog.host())
            self.caps.clear()
            self._save_settings()
            self.refresh_models()
        self.host_dialog = None

    def _test_host(self, host: str) -> None:
        if self.host_test is not None and self.host_test.isRunning():
            return
        if self.host_dialog:
            self.host_dialog.set_testing()
        self.host_test = ProbeWorker(normalize_host(host), self)
        self.host_test.done.connect(
            lambda models, version: self.host_dialog and self.host_dialog.set_result(
                True, f"成功 · v{version} · {len(models)} 個模型"))
        self.host_test.failed.connect(
            lambda msg: self.host_dialog and self.host_dialog.set_result(
                False, msg.split("\n")[0]))
        self.host_test.start()

    # ------------------------------------------------------------------ #
    # 對話管理
    # ------------------------------------------------------------------ #

    def _refresh_sidebar(self) -> None:
        self.sidebar.set_conversations(self.conversations, self.current.id)

    def new_chat(self) -> None:
        if self.chat_worker is not None and self.chat_worker.isRunning():
            return
        conv = store.Conversation(model=self.model)
        self.conversations.insert(0, conv)
        self.current = conv
        self._refresh_sidebar()
        self._render_conversation()
        self.composer.input.setFocus()

    def select_chat(self, conv_id: str) -> None:
        if self.chat_worker is not None and self.chat_worker.isRunning():
            return
        for conv in self.conversations:
            if conv.id == conv_id and conv is not self.current:
                self.current = conv
                if conv.model and conv.model in [m.get("name") for m in self.models]:
                    self.select_model(conv.model)
                self._render_conversation()
                return

    def delete_chat(self, conv_id: str) -> None:
        target = next((c for c in self.conversations if c.id == conv_id), None)
        if target is None:
            return
        confirm = QMessageBox.question(self, "刪除對話", f"確定刪除「{target.title}」？")
        if confirm != QMessageBox.Yes:
            return
        self.conversations.remove(target)
        if not self.conversations:
            self.conversations.append(store.Conversation(model=self.model))
        if target is self.current:
            self.current = self.conversations[0]
            self._render_conversation()
        self._refresh_sidebar()
        store.save_chats(self.conversations)

    # ------------------------------------------------------------------ #
    # 訊息渲染
    # ------------------------------------------------------------------ #

    def _clear_messages(self) -> None:
        while self.canvas_layout.count() > 1:
            item = self.canvas_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # takeAt 只把它移出版面，widget 仍是 canvas 的子物件而且照畫，
                # deleteLater 又要等事件迴圈——中間會出現新舊訊息疊在一起。
                widget.setParent(None)
                widget.deleteLater()

    def _add_message_widget(self, widget: MessageWidget) -> None:
        self.canvas_layout.insertWidget(self.canvas_layout.count() - 1, widget)

    def _render_conversation(self) -> None:
        self._clear_messages()
        self.stream_msg = None
        show_think = self.params.show_think.isChecked()

        for message in self.current.messages:
            if message["role"] == "user":
                widget = MessageWidget("user")
                widget.set_user_text(message["content"],
                                     len(message.get("images") or []))
            else:
                widget = MessageWidget("assistant", message.get("model", self.model))
                if message.get("thinking") and show_think:
                    widget.set_thinking(message["thinking"], collapsed=True)
                widget.set_content(message["content"])
                widget.finish(message.get("stats", ""))
                widget.copy_requested.connect(self._copy_text)
                widget.regenerate_requested.connect(self.regenerate)
            self._add_message_widget(widget)

        QTimer.singleShot(0, self._scroll_to_bottom)

    def _track_stick(self, value: int) -> None:
        """使用者自己往上捲就放開黏底，別把畫面硬拉回去。"""
        bar = self.chat_scroll.verticalScrollBar()
        self._stick_bottom = value >= bar.maximum() - 8

    def _pin_bottom(self) -> None:
        if self._stick_bottom:
            bar = self.chat_scroll.verticalScrollBar()
            bar.setValue(bar.maximum())

    def _scroll_to_bottom(self) -> None:
        self._stick_bottom = True
        self._pin_bottom()

    def _copy_text(self, text: str) -> None:
        QGuiApplication.clipboard().setText(text)
        self.statusBar().showMessage("已複製到剪貼簿", 2000)

    # ------------------------------------------------------------------ #
    # 送出與串流
    # ------------------------------------------------------------------ #

    def send_message(self, text: str) -> None:
        if self.chat_worker is not None and self.chat_worker.isRunning():
            return
        if not self.model:
            QMessageBox.warning(self, "尚未選擇模型", "請先連線並選擇一個模型。")
            return

        message: dict = {"role": "user", "content": text}
        if self.pending_images:
            message["images"] = [data for _name, data in self.pending_images]
        self.current.messages.append(message)

        widget = MessageWidget("user")
        widget.set_user_text(text, len(self.pending_images))
        self._add_message_widget(widget)

        self.composer.clear()
        self.pending_images.clear()
        self.composer.set_attachments([])

        if len(self.current.messages) == 1:
            self.current.auto_title()
            self._refresh_sidebar()

        self._start_stream()

    def regenerate(self) -> None:
        if self.chat_worker is not None and self.chat_worker.isRunning():
            return
        while self.current.messages and self.current.messages[-1]["role"] == "assistant":
            self.current.messages.pop()
        if not self.current.messages:
            return
        self._render_conversation()
        self._start_stream()

    def _start_stream(self) -> None:
        params = self.params.params()
        think = self.params.think_value()
        options = store.build_options(params)
        keep_alive = params["keep_alive"].strip() or None
        messages = self.current.api_messages(params["system"])

        self.stream_msg = MessageWidget("assistant", self.model)
        self.stream_msg.copy_requested.connect(self._copy_text)
        self.stream_msg.regenerate_requested.connect(self.regenerate)
        self._add_message_widget(self.stream_msg)

        self._pending = {"thinking": "", "content": ""}
        self._buffer = {"thinking": "", "content": ""}
        self.stream_started = time.time()
        self.composer.set_streaming(True)
        self.flush_timer.start()
        QTimer.singleShot(0, self._scroll_to_bottom)

        self.chat_worker = ChatWorker(self.host, self.model, messages, think,
                                      options, keep_alive, self)
        self.chat_worker.chunk.connect(self._on_chunk)
        self.chat_worker.finished_ok.connect(self._on_stream_done)
        self.chat_worker.failed.connect(self._on_stream_failed)
        self.chat_worker.stopped.connect(self._on_stream_stopped)
        self.chat_worker.start()

    def stop_stream(self) -> None:
        if self.chat_worker is not None and self.chat_worker.isRunning():
            self.chat_worker.cancel()

    def _on_chunk(self, kind: str, text: str) -> None:
        self._pending[kind] += text

    def _flush_stream(self) -> None:
        """每 50ms 把累積的片段一次寫進畫面，避免每個 token 都重排版面。"""
        if self.stream_msg is None:
            return

        if self._pending["thinking"]:
            self._buffer["thinking"] += self._pending["thinking"]
            self._pending["thinking"] = ""
            if self.params.show_think.isChecked():
                self.stream_msg.ensure_think().set_text(self._buffer["thinking"])

        if self._pending["content"]:
            self._buffer["content"] += self._pending["content"]
            self._pending["content"] = ""
            if (self.stream_msg.think is not None
                    and not self.stream_msg.think.is_collapsed()
                    and self._buffer["content"].strip()):
                elapsed = time.time() - self.stream_started
                self.stream_msg.think.set_streaming(False, elapsed)
                self.stream_msg.think.set_collapsed(True)
            self.stream_msg.set_content(self._buffer["content"])
        # 捲動交給 rangeChanged 的黏底處理，這裡不用自己算時機

    def _finish_stream(self) -> None:
        self.flush_timer.stop()
        self._flush_stream()
        self.composer.set_streaming(False)
        self.composer.input.setFocus()

    def _on_stream_done(self, stats: str) -> None:
        self._finish_stream()
        if self.stream_msg is None:
            return
        thinking = self._buffer["thinking"]
        content = self._buffer["content"]
        if thinking and self.stream_msg.think is not None:
            self.stream_msg.think.set_streaming(
                False, time.time() - self.stream_started)
            self.stream_msg.think.set_collapsed(True)
        self.stream_msg.finish(stats)

        self.current.messages.append({
            "role": "assistant", "content": content, "thinking": thinking,
            "model": self.model, "stats": stats,
        })
        store.save_chats(self.conversations)
        self.stream_msg = None

    def _on_stream_stopped(self) -> None:
        self._finish_stream()
        if self.stream_msg is None:
            return
        content = self._buffer["content"]
        thinking = self._buffer["thinking"]
        self.stream_msg.finish("（已停止）")
        if content or thinking:
            self.current.messages.append({
                "role": "assistant", "content": content, "thinking": thinking,
                "model": self.model, "stats": "（已停止）",
            })
            store.save_chats(self.conversations)
        self.stream_msg = None

    def _on_stream_failed(self, message: str) -> None:
        self._finish_stream()
        if self.stream_msg is None:
            return
        self.stream_msg.set_error(message.split("\n")[0], self._error_hint(message))
        self.stream_msg = None

    def _error_hint(self, message: str) -> str:
        lowered = message.lower()
        if "does not support thinking" in lowered:
            return "這個模型不支援 thinking，請把右側的思考模式切成關閉。"
        if "not found" in lowered:
            return f"請先下載模型：ollama pull {self.model}"
        if "memory" in lowered:
            return "記憶體不足，試著調小 num_ctx 或改用較小的模型。"
        rest = "\n".join(message.split("\n")[1:]).strip()
        return rest

    # ------------------------------------------------------------------ #
    # 附件與選單
    # ------------------------------------------------------------------ #

    def attach_images(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "選擇圖片", "", "圖片 (*.png *.jpg *.jpeg *.gif *.webp *.bmp)")
        for path in paths:
            try:
                data = base64.b64encode(Path(path).read_bytes()).decode("ascii")
                self.pending_images.append((Path(path).name, data))
            except OSError as exc:
                QMessageBox.critical(self, "讀取圖片失敗", str(exc))
        self.composer.set_attachments([name for name, _ in self.pending_images])

    def _show_more_menu(self) -> None:
        menu = QMenu(self)
        menu.addAction("匯出對話為 Markdown", lambda: self.export("md"))
        menu.addAction("匯出對話為 JSON", lambda: self.export("json"))
        menu.addSeparator()
        menu.addAction("已載入的模型…", self.show_running)
        menu.addAction("主機設定…", self.open_host_dialog)
        menu.addSeparator()
        menu.addAction("關於", self.show_about)
        menu.exec(self.more_btn.mapToGlobal(self.more_btn.rect().bottomLeft()))

    def export(self, fmt: str) -> None:
        if not self.current.messages:
            return
        suffix = ".md" if fmt == "md" else ".json"
        path, _ = QFileDialog.getSaveFileName(
            self, "匯出對話", f"{self.current.title}{suffix}",
            "Markdown (*.md)" if fmt == "md" else "JSON (*.json)")
        if not path:
            return
        if fmt == "json":
            import json
            Path(path).write_text(
                json.dumps(self.current.to_dict(), ensure_ascii=False, indent=2),
                "utf-8")
        else:
            lines = [f"# {self.current.title}", "", f"> 模型：{self.current.model}", ""]
            for message in self.current.messages:
                who = "你" if message["role"] == "user" else message.get("model", "assistant")
                lines.append(f"## {who}")
                lines.append("")
                if message.get("thinking"):
                    lines.append("<details><summary>思考過程</summary>")
                    lines.append("")
                    lines.append(message["thinking"])
                    lines.append("")
                    lines.append("</details>")
                    lines.append("")
                lines.append(message["content"])
                lines.append("")
            Path(path).write_text("\n".join(lines), "utf-8")
        self.statusBar().showMessage(f"已匯出：{path}", 4000)

    def show_running(self) -> None:
        models = OllamaClient(self.host).running()
        if not models:
            text = "目前沒有模型駐留在記憶體中。"
        else:
            text = "\n".join(
                f"{m.get('name')}  ·  {human_size(m.get('size', 0))}  ·  "
                f"到期 {str(m.get('expires_at', '?'))[:19]}" for m in models)
        QMessageBox.information(self, "已載入的模型", text)

    def show_about(self) -> None:
        QMessageBox.information(
            self, "關於",
            "Ollama GUI\n\n"
            "PySide6 桌面前端，透過 /api/tags 取得模型、/api/show 偵測能力、\n"
            "/api/chat 串流對話並支援 think 參數。\n\n"
            f"設定檔：{store.CONFIG_DIR}")

    # ------------------------------------------------------------------ #

    def closeEvent(self, event) -> None:  # noqa: N802
        self.stop_stream()
        self.poll_timer.stop()
        for worker in [self.chat_worker, self.caps_worker, self.host_test, *self._probes]:
            if worker is not None and worker.isRunning():
                worker.wait(1500)
        self._save_settings()
        store.save_chats(self.conversations)
        super().closeEvent(event)


def main() -> int:
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("Ollama GUI")
    window = MainWindow()
    window.show()
    return app.exec()
