# -*- coding: utf-8 -*-
"""三大面板：左側對話欄、底部輸入區、右側參數面板，以及主機設定對話框。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMenu,
                               QPushButton, QScrollArea, QSizePolicy, QTextEdit,
                               QVBoxLayout, QWidget)

from . import store
from .ui_common import (Field, IconButton, IconLabel, ParamSlider, SegmentedControl,
                        Toggle, divider, palette)


def section_label(text: str) -> QLabel:
    lb = QLabel(text)
    lb.setObjectName("SectionLabel")
    return lb


# --------------------------------------------------------------------------- #
# 左側對話欄
# --------------------------------------------------------------------------- #

class Sidebar(QFrame):
    new_chat = Signal()
    chat_selected = Signal(str)          # conversation id
    chat_deleted = Signal(str)
    settings_clicked = Signal()
    theme_toggled = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setMinimumWidth(200)
        self.setMaximumWidth(400)

        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)

        # 標題列
        header = QFrame()
        header.setObjectName("SidebarHeader")
        header.setFixedHeight(56)
        head_row = QHBoxLayout(header)
        head_row.setContentsMargins(14, 0, 10, 0)
        head_row.setSpacing(10)

        mark = QLabel()
        mark.setObjectName("AssistantAvatar")
        mark.setFixedSize(26, 26)
        mark_box = QVBoxLayout(mark)
        mark_box.setContentsMargins(0, 0, 0, 0)
        self.mark_icon = IconLabel("node", 14, "accent")
        mark_box.addWidget(self.mark_icon, 0, Qt.AlignCenter)

        name = QLabel("Ollama GUI")
        name.setObjectName("AppName")
        self.theme_btn = IconButton("moon", 16, 30, "切換深色 / 淺色", "ink3",
                                    "FlatIconButton")
        self.theme_btn.clicked.connect(self.theme_toggled.emit)

        head_row.addWidget(mark)
        head_row.addWidget(name)
        head_row.addStretch(1)
        head_row.addWidget(self.theme_btn)
        box.addWidget(header)

        # 新對話 + 搜尋
        top = QWidget()
        top_box = QVBoxLayout(top)
        top_box.setContentsMargins(12, 12, 12, 8)
        top_box.setSpacing(8)

        self.new_btn = QPushButton("  新對話")
        self.new_btn.setFixedHeight(36)
        self.new_btn.setCursor(Qt.PointingHandCursor)
        self.new_btn.clicked.connect(self.new_chat.emit)
        self._new_icon_role = "ink"
        top_box.addWidget(self.new_btn)

        self.search = QLineEdit()
        self.search.setPlaceholderText("搜尋對話")
        self.search.setFixedHeight(32)
        self.search.textChanged.connect(self._apply_filter)
        top_box.addWidget(self.search)
        box.addWidget(top)

        # 對話清單
        self.label_today = section_label("對話")
        label_wrap = QWidget()
        label_box = QHBoxLayout(label_wrap)
        label_box.setContentsMargins(20, 6, 12, 6)
        label_box.addWidget(self.label_today)
        box.addWidget(label_wrap)

        self.list = QListWidget()
        self.list.setObjectName("ChatList")
        self.list.setFrameShape(QFrame.NoFrame)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._show_menu)
        self.list.currentItemChanged.connect(self._on_current_changed)
        list_wrap = QWidget()
        list_box = QVBoxLayout(list_wrap)
        list_box.setContentsMargins(8, 0, 8, 8)
        list_box.addWidget(self.list)
        box.addWidget(list_wrap, 1)

        # 底部
        footer = QFrame()
        footer.setObjectName("SidebarFooter")
        foot_row = QHBoxLayout(footer)
        foot_row.setContentsMargins(12, 10, 10, 10)
        foot_row.setSpacing(10)
        avatar = QLabel("本")
        avatar.setObjectName("Avatar")
        avatar.setFixedSize(26, 26)
        avatar.setAlignment(Qt.AlignCenter)
        text_box = QVBoxLayout()
        text_box.setSpacing(0)
        self.host_name = QLabel("本機")
        self.model_count = QLabel("尚未連線")
        self.model_count.setObjectName("Muted")
        text_box.addWidget(self.host_name)
        text_box.addWidget(self.model_count)
        self.settings_btn = IconButton("sliders", 17, 30, "主機設定", "ink3",
                                       "FlatIconButton")
        self.settings_btn.clicked.connect(self.settings_clicked.emit)
        foot_row.addWidget(avatar)
        foot_row.addLayout(text_box, 1)
        foot_row.addWidget(self.settings_btn)
        box.addWidget(footer)

        self._suppress = False
        self.refresh_icons()

    # -- 清單 ---------------------------------------------------------- #

    def set_conversations(self, conversations, current_id: str) -> None:
        self._suppress = True
        self.list.clear()
        for conv in conversations:
            item = QListWidgetItem(conv.title)
            item.setData(Qt.UserRole, conv.id)
            item.setToolTip(conv.title)
            self.list.addItem(item)
            if conv.id == current_id:
                self.list.setCurrentItem(item)
        self._suppress = False
        self._apply_filter(self.search.text())

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        for i in range(self.list.count()):
            item = self.list.item(i)
            item.setHidden(bool(needle) and needle not in item.text().lower())

    def _on_current_changed(self, current, _previous) -> None:
        if self._suppress or current is None:
            return
        self.chat_selected.emit(current.data(Qt.UserRole))

    def _show_menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        if item is None:
            return
        menu = QMenu(self)
        delete = menu.addAction("刪除此對話")
        if menu.exec(self.list.mapToGlobal(pos)) == delete:
            self.chat_deleted.emit(item.data(Qt.UserRole))

    def set_connection_summary(self, host: str, summary: str) -> None:
        self.host_name.setText(host)
        self.model_count.setText(summary)

    def set_theme_icon(self, theme_name: str) -> None:
        self.theme_btn.set_icon_name("sun" if theme_name == "dark" else "moon")

    def refresh_icons(self) -> None:
        from . import icons
        dpr = self.devicePixelRatioF() or 1.0
        self.new_btn.setIcon(icons.icon("plus", palette()["ink"], 15, 2.0, dpr))


# --------------------------------------------------------------------------- #
# 輸入區
# --------------------------------------------------------------------------- #

class ComposerInput(QTextEdit):
    """Enter 送出、Shift+Enter 換行，並隨內容長高。"""

    submitted = Signal()

    MIN_HEIGHT = 26
    MAX_HEIGHT = 170

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ComposerInput")
        self.setFrameShape(QFrame.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setPlaceholderText("輸入訊息…")
        self.setFixedHeight(self.MIN_HEIGHT)
        self.textChanged.connect(self._adjust_height)

    def _adjust_height(self) -> None:
        doc = self.document()
        doc.setTextWidth(self.viewport().width())
        wanted = int(doc.size().height()) + 6
        height = max(self.MIN_HEIGHT, min(self.MAX_HEIGHT, wanted))
        self.setFixedHeight(height)
        # 只有真的塞不下才顯示捲軸，否則會有一條殘影卡在輸入框右側
        self.setVerticalScrollBarPolicy(
            Qt.ScrollBarAsNeeded if wanted > self.MAX_HEIGHT else Qt.ScrollBarAlwaysOff)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._adjust_height()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if event.modifiers() & Qt.ShiftModifier:
                super().keyPressEvent(event)
            else:
                self.submitted.emit()
            return
        super().keyPressEvent(event)


class Composer(QFrame):
    submitted = Signal(str)
    stop_requested = Signal()
    attach_requested = Signal()
    think_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Composer")
        self._streaming = False
        self._blocked_reason = ""

        box = QVBoxLayout(self)
        box.setContentsMargins(16, 12, 12, 10)
        box.setSpacing(10)

        self.input = ComposerInput()
        self.input.submitted.connect(self._on_submit)
        box.addWidget(self.input)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        self.attach_btn = IconButton("paperclip", 15, 30, "附加圖片（需視覺模型）",
                                     "ink2")
        self.attach_btn.clicked.connect(self.attach_requested.emit)
        self.attach_btn.setEnabled(False)

        self.think_chip = QPushButton("思考 · 關閉")
        self.think_chip.setObjectName("ThinkChip")
        self.think_chip.setFixedHeight(30)
        self.think_chip.setCursor(Qt.PointingHandCursor)
        self.think_chip.clicked.connect(self.think_clicked.emit)

        self.attachment_label = QLabel("")
        self.attachment_label.setObjectName("Muted")
        self.attachment_label.hide()

        self.hint = QLabel("Enter 送出 · Shift+Enter 換行")
        self.hint.setObjectName("Hint")

        self.send_btn = IconButton("send", 17, 34, "送出", "accent_ink", "SendButton",
                                   2.4)
        self.send_btn.clicked.connect(self._on_submit)

        row.addWidget(self.attach_btn)
        row.addWidget(self.think_chip)
        row.addWidget(self.attachment_label)
        row.addStretch(1)
        row.addWidget(self.hint)
        row.addWidget(self.send_btn)
        box.addLayout(row)

    def _on_submit(self) -> None:
        if self._streaming:
            self.stop_requested.emit()
            return
        text = self.input.toPlainText().strip()
        if text:
            self.submitted.emit(text)

    def clear(self) -> None:
        self.input.clear()

    def set_streaming(self, streaming: bool) -> None:
        self._streaming = streaming
        if streaming:
            self.send_btn.setObjectName("StopButton")
            self.send_btn.set_icon_name("stop")
            self.send_btn.setToolTip("停止產生")
            self.hint.setText("產生中…")
        else:
            self.send_btn.setObjectName("SendButton")
            self.send_btn.set_icon_name("send")
            self.send_btn.setToolTip("送出")
            self.hint.setText(self._blocked_reason or "Enter 送出 · Shift+Enter 換行")
        self.send_btn.style().unpolish(self.send_btn)
        self.send_btn.style().polish(self.send_btn)

    def set_blocked(self, reason: str) -> None:
        """斷線或沒有模型時鎖住輸入，並說明原因。"""
        self._blocked_reason = reason
        blocked = bool(reason)
        self.input.setEnabled(not blocked)
        self.send_btn.setEnabled(not blocked)
        self.think_chip.setEnabled(not blocked)
        self.input.setPlaceholderText(reason or "輸入訊息…")
        if not self._streaming:
            self.hint.setText(reason or "Enter 送出 · Shift+Enter 換行")

    def set_think_label(self, text: str, enabled: bool = True) -> None:
        self.think_chip.setText(f"思考 · {text}")
        self.think_chip.setEnabled(enabled and not self._blocked_reason)

    def set_attachments(self, names: list[str]) -> None:
        if names:
            self.attachment_label.setText("📎 " + ", ".join(names))
            self.attachment_label.show()
        else:
            self.attachment_label.hide()


# --------------------------------------------------------------------------- #
# 右側參數面板
# --------------------------------------------------------------------------- #

class ParamPanel(QFrame):
    changed = Signal()
    think_changed = Signal(object)
    show_think_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ParamPanel")
        self.setMinimumWidth(280)
        self.setMaximumWidth(460)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QFrame()
        header.setObjectName("ParamHeader")
        header.setFixedHeight(56)
        head_row = QHBoxLayout(header)
        head_row.setContentsMargins(16, 0, 12, 0)
        title = QLabel("參數")
        title.setObjectName("PanelTitle")
        self.reset_btn = QPushButton("還原")
        self.reset_btn.setObjectName("GhostButton")
        self.reset_btn.setCursor(Qt.PointingHandCursor)
        self.reset_btn.setFixedHeight(26)
        self.reset_btn.clicked.connect(self.reset)
        head_row.addWidget(title)
        head_row.addStretch(1)
        head_row.addWidget(self.reset_btn)
        outer.addWidget(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        # viewport 要自己上色，否則右緣會露出系統 palette 的深色底
        scroll.viewport().setObjectName("ParamViewport")
        content = QWidget()
        content.setObjectName("ParamContent")
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        box = QVBoxLayout(content)
        box.setContentsMargins(16, 16, 16, 16)
        box.setSpacing(18)

        # ── 思考模式 ──
        think_box = QVBoxLayout()
        think_box.setSpacing(9)
        head = QHBoxLayout()
        head.setSpacing(7)
        self.think_icon = IconLabel("sparkle", 13, "think")
        head.addWidget(self.think_icon)
        head.addWidget(section_label("思考模式"))
        head.addStretch(1)
        think_box.addLayout(head)

        self.think_seg = SegmentedControl()
        self.think_seg.changed.connect(self.think_changed.emit)
        think_box.addWidget(self.think_seg)

        self.show_think = Toggle("在對話中顯示思考過程", True)
        self.show_think.toggled.connect(self.show_think_changed.emit)
        think_box.addWidget(self.show_think)

        self.think_hint = QLabel("")
        self.think_hint.setObjectName("Muted")
        self.think_hint.setWordWrap(True)
        think_box.addWidget(self.think_hint)
        box.addLayout(think_box)
        box.addWidget(divider())

        # ── 系統提示 ──
        sys_box = QVBoxLayout()
        sys_box.setSpacing(9)
        sys_box.addWidget(section_label("系統提示"))
        self.system = QTextEdit()
        self.system.setFixedHeight(72)
        self.system.setPlaceholderText("例如：你是一位精確、簡潔的技術助理。")
        self.system.textChanged.connect(self.changed.emit)
        sys_box.addWidget(self.system)
        box.addLayout(sys_box)
        box.addWidget(divider())

        # ── 取樣參數 ──
        sample_box = QVBoxLayout()
        sample_box.setSpacing(14)
        sample_box.addWidget(section_label("取樣參數"))
        defaults = store.DEFAULT_PARAMS
        self.temperature = ParamSlider("Temperature", 0.0, 2.0, defaults["temperature"], 2)
        self.top_p = ParamSlider("Top P", 0.0, 1.0, defaults["top_p"], 2)
        self.top_k = ParamSlider("Top K", 0, 100, defaults["top_k"], 0)
        self.min_p = ParamSlider("Min P", 0.0, 1.0, defaults["min_p"], 2)
        self.repeat_penalty = ParamSlider("Repeat penalty", 0.5, 2.0,
                                          defaults["repeat_penalty"], 2)
        for slider in (self.temperature, self.top_p, self.top_k, self.min_p,
                       self.repeat_penalty):
            slider.changed.connect(lambda _v: self.changed.emit())
            sample_box.addWidget(slider)
        box.addLayout(sample_box)
        box.addWidget(divider())

        # ── 進階 ──
        adv_box = QVBoxLayout()
        adv_box.setSpacing(9)
        adv_box.addWidget(section_label("進階"))
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(9)
        grid.setVerticalSpacing(9)
        self.num_ctx = Field("num_ctx", str(defaults["num_ctx"]))
        self.num_predict = Field("num_predict", str(defaults["num_predict"]))
        self.seed = Field("seed", str(defaults["seed"]))
        self.keep_alive = Field("keep_alive", defaults["keep_alive"])
        grid.addWidget(self.num_ctx, 0, 0)
        grid.addWidget(self.num_predict, 0, 1)
        grid.addWidget(self.seed, 1, 0)
        grid.addWidget(self.keep_alive, 1, 1)
        adv_box.addLayout(grid)
        self.stop = Field("stop（多組用逗號分隔）", defaults["stop"])
        adv_box.addWidget(self.stop)
        for field in (self.num_ctx, self.num_predict, self.seed, self.keep_alive,
                      self.stop):
            field.edit.editingFinished.connect(self.changed.emit)
        adv_box.addWidget(QLabel(""))
        box.addLayout(adv_box)
        box.addStretch(1)

    # -- 思考 ---------------------------------------------------------- #

    def set_think_options(self, options: list[tuple[str, object]], model: str) -> None:
        if options:
            self.think_seg.set_options(options)
            self.think_seg.setEnabled(True)
            if len(options) == 4:
                self.think_hint.setText(
                    "gpt-oss 系列支援強度分級，四段皆可用。")
            else:
                self.think_hint.setText(
                    "此模型只認得開啟／關閉，不吃 low／medium／high。")
        else:
            self.think_seg.set_options([("關閉", False), ("開啟", True)])
            self.think_seg.setEnabled(False)
            self.think_hint.setText(f"{model or '此模型'} 不支援 thinking，控制項已停用。")

    def think_value(self):
        if not self.think_seg.isEnabled():
            return None
        return self.think_seg.value()

    def think_label(self) -> str:
        if not self.think_seg.isEnabled():
            return "不支援"
        for btn, value in zip(self.think_seg._buttons, self.think_seg._values):
            if btn.isChecked():
                return btn.text()
        return "關閉"

    def cycle_think(self) -> None:
        """輸入區的思考膠囊被點時，往下一個選項輪替。"""
        seg = self.think_seg
        if not seg.isEnabled() or not seg._values:
            return
        current = seg.value()
        try:
            index = seg._values.index(current)
        except ValueError:
            index = -1
        seg.set_value(seg._values[(index + 1) % len(seg._values)])

    # -- 參數 ---------------------------------------------------------- #

    def params(self) -> dict:
        return {
            "temperature": self.temperature.value(),
            "top_p": self.top_p.value(),
            "top_k": int(self.top_k.value()),
            "min_p": self.min_p.value(),
            "repeat_penalty": self.repeat_penalty.value(),
            "num_ctx": self.num_ctx.text(),
            "num_predict": self.num_predict.text(),
            "seed": self.seed.text(),
            "stop": self.stop.text(),
            "keep_alive": self.keep_alive.text(),
            "system": self.system.toPlainText().strip(),
        }

    def set_params(self, values: dict) -> None:
        merged = {**store.DEFAULT_PARAMS, **(values or {})}
        self.temperature.set_value(float(merged["temperature"]))
        self.top_p.set_value(float(merged["top_p"]))
        self.top_k.set_value(float(merged["top_k"]))
        self.min_p.set_value(float(merged["min_p"]))
        self.repeat_penalty.set_value(float(merged["repeat_penalty"]))
        self.num_ctx.set_text(str(merged["num_ctx"]))
        self.num_predict.set_text(str(merged["num_predict"]))
        self.seed.set_text(str(merged["seed"]))
        self.keep_alive.set_text(str(merged["keep_alive"]))
        self.stop.set_text(str(merged["stop"]))
        self.system.setPlainText(str(merged.get("system", "")))

    def reset(self) -> None:
        self.set_params(store.DEFAULT_PARAMS)
        self.changed.emit()

    def refresh_icons(self) -> None:
        self.think_icon.refresh_icons()


# --------------------------------------------------------------------------- #
# 主機設定
# --------------------------------------------------------------------------- #

class HostDialog(QDialog):
    test_requested = Signal(str)

    def __init__(self, host: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Ollama 主機")
        self.setMinimumWidth(430)

        box = QVBoxLayout(self)
        box.setContentsMargins(18, 18, 18, 16)
        box.setSpacing(11)

        title = QLabel("Ollama 主機")
        title.setObjectName("PanelTitle")
        box.addWidget(title)

        self.edit = QLineEdit(host)
        self.edit.setObjectName("MonoEdit")
        self.edit.setFixedHeight(34)
        self.edit.setPlaceholderText("http://localhost:11434")
        box.addWidget(self.edit)

        row = QHBoxLayout()
        row.setSpacing(9)
        self.test_btn = QPushButton("測試連線")
        self.test_btn.setObjectName("DangerButton")
        self.test_btn.setStyleSheet(
            f"background: {palette()['accent']}; color: {palette()['accent_ink']};")
        self.test_btn.setCursor(Qt.PointingHandCursor)
        self.test_btn.setFixedHeight(30)
        self.test_btn.clicked.connect(
            lambda: self.test_requested.emit(self.edit.text()))
        self.result = QLabel("")
        self.result.setWordWrap(True)
        row.addWidget(self.test_btn)
        row.addWidget(self.result, 1)
        box.addLayout(row)

        box.addWidget(divider())
        note = QLabel("留空會自動套用環境變數 OLLAMA_HOST；未填通訊協定時自動補上 http://。\n"
                      "遠端主機需以 OLLAMA_HOST=0.0.0.0 啟動才會對外開放。")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        box.addWidget(note)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("取消")
        cancel.setObjectName("GhostButton")
        cancel.setFixedHeight(30)
        cancel.clicked.connect(self.reject)
        save = QPushButton("儲存並連線")
        save.setObjectName("DangerButton")
        save.setStyleSheet(
            f"background: {palette()['accent']}; color: {palette()['accent_ink']};")
        save.setFixedHeight(30)
        save.setCursor(Qt.PointingHandCursor)
        save.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        box.addLayout(buttons)

    def host(self) -> str:
        return self.edit.text()

    def set_result(self, ok: bool, message: str) -> None:
        color = palette()["ok"] if ok else palette()["err"]
        self.result.setStyleSheet(f"color: {color}; font-size: 12px;")
        self.result.setText(message)

    def set_testing(self) -> None:
        self.result.setStyleSheet(f"color: {palette()['ink3']}; font-size: 12px;")
        self.result.setText("測試中…")
