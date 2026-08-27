# -*- coding: utf-8 -*-
"""訊息呈現：Markdown 分段、程式碼區塊、可折疊思考區塊、單則訊息。"""

from __future__ import annotations

import html
import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QScrollArea,
                               QSizePolicy, QVBoxLayout, QWidget)

from . import theme
from .ui_common import IconButton, IconLabel, palette

SELECTABLE = Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard


def enable_height_for_width(widget: QWidget,
                            horizontal=QSizePolicy.Preferred) -> None:
    """讓版面用 heightForWidth() 來排這個 widget。

    QLabel 開了 wordWrap 之後高度取決於寬度，但預設的 size policy 沒有標記
    heightForWidth，版面就會拿「單行時的 sizeHint」去排，導致文字被裁掉——
    連續送第二則訊息後版面跑掉就是這個原因。
    """
    policy = widget.sizePolicy()
    policy.setHorizontalPolicy(horizontal)
    # 用 Preferred 而不是 Minimum：Minimum 會把「單行時的 sizeHint 高度」
    # 當成最小高度，但實際換行後需要的高度更小，多出來的差額會被訊息串
    # 尾端的 stretch 吃掉，在畫面底部留下一塊空白。
    policy.setVerticalPolicy(QSizePolicy.Preferred)
    policy.setHeightForWidth(True)
    widget.setSizePolicy(policy)


def wrap_label(object_name: str = "", rich: bool = True) -> QLabel:
    """建立一個會正確換行、且高度會跟著寬度走的 QLabel。"""
    lb = QLabel()
    if object_name:
        lb.setObjectName(object_name)
    if rich:
        lb.setTextFormat(Qt.RichText)
    lb.setWordWrap(True)
    lb.setTextInteractionFlags(SELECTABLE)
    lb.setAlignment(Qt.AlignTop | Qt.AlignLeft)
    enable_height_for_width(lb)
    return lb

# --------------------------------------------------------------------------- #
# Markdown → 分段
# --------------------------------------------------------------------------- #

_FENCE = re.compile(r"```([^\n`]*)\n?(.*?)(?:```|\Z)", re.S)
_INLINE = re.compile(r"(\*\*.+?\*\*|`[^`\n]+`|\*[^*\n]+\*)")


def split_segments(text: str) -> list[tuple[str, str, str]]:
    """切成 ("text", "", 內容) 與 ("code", 語言, 內容) 的序列。"""
    out: list[tuple[str, str, str]] = []
    pos = 0
    for match in _FENCE.finditer(text):
        before = text[pos:match.start()]
        if before:
            out.append(("text", "", before))
        out.append(("code", match.group(1).strip(), match.group(2)))
        pos = match.end()
    tail = text[pos:]
    if tail or not out:
        out.append(("text", "", tail))
    return out


def _inline_html(line: str, mono: str, code_bg: str) -> str:
    """處理行內語法：粗體、斜體、行內程式碼。"""
    parts = []
    for chunk in _INLINE.split(line):
        if not chunk:
            continue
        if chunk.startswith("**") and chunk.endswith("**") and len(chunk) > 4:
            parts.append(f"<b>{html.escape(chunk[2:-2])}</b>")
        elif chunk.startswith("`") and chunk.endswith("`") and len(chunk) > 2:
            parts.append(
                f'<span style="font-family:{mono}; background-color:{code_bg};">'
                f"&nbsp;{html.escape(chunk[1:-1])}&nbsp;</span>")
        elif chunk.startswith("*") and chunk.endswith("*") and len(chunk) > 2:
            parts.append(f"<i>{html.escape(chunk[1:-1])}</i>")
        else:
            parts.append(html.escape(chunk))
    return "".join(parts)


def text_to_html(text: str) -> str:
    """把非程式碼片段轉成 QLabel 吃得下的簡易 HTML。"""
    colors = palette()
    mono = theme.MONO_FAMILIES[0]
    lines_html = []
    for raw in text.split("\n"):
        stripped = raw.lstrip()
        indent = len(raw) - len(stripped)
        if stripped.startswith("#"):
            body = _inline_html(stripped.lstrip("# ").rstrip(), mono, colors["inline_bg"])
            lines_html.append(f'<span style="font-size:15px;"><b>{body}</b></span>')
        elif stripped.startswith(("- ", "* ", "+ ")):
            body = _inline_html(stripped[2:], mono, colors["inline_bg"])
            pad = "&nbsp;" * (indent + 2)
            lines_html.append(f"{pad}•&nbsp;{body}")
        elif re.match(r"^\d+\.\s", stripped):
            body = _inline_html(stripped, mono, colors["inline_bg"])
            pad = "&nbsp;" * indent
            lines_html.append(pad + body)
        else:
            lines_html.append(_inline_html(raw, mono, colors["inline_bg"]))
    return "<br>".join(lines_html)


# --------------------------------------------------------------------------- #
# 程式碼區塊
# --------------------------------------------------------------------------- #

_HIGHLIGHTABLE = {"", "python", "py", "javascript", "js", "typescript", "ts",
                  "json", "bash", "sh", "shell", "powershell", "ps1", "sql",
                  "c", "cpp", "java", "go", "rust", "rs", "yaml", "toml"}

_KEYWORDS = {
    "def", "class", "return", "import", "from", "as", "if", "elif", "else", "for",
    "while", "with", "try", "except", "finally", "raise", "lambda", "yield", "pass",
    "break", "continue", "in", "not", "and", "or", "is", "None", "True", "False",
    "async", "await", "global", "nonlocal", "assert", "del",
    "function", "const", "let", "var", "new", "this", "typeof", "export", "default",
    "public", "private", "static", "void", "int", "float", "char", "bool", "struct",
    "func", "package", "type", "interface", "fn", "let", "mut", "impl", "use",
    "select", "insert", "update", "delete", "where", "join", "group", "order",
    "echo", "exit", "then", "fi", "do", "done", "esac",
}

_TOKEN = re.compile(
    r'(?P<str>"[^"\n]*"|\'[^\'\n]*\')'
    r"|(?P<com>#[^\n]*|//[^\n]*)"
    r"|(?P<num>\b\d+\.?\d*\b)"
    r"|(?P<word>\b[A-Za-z_]\w*\b)"
)


def _code_escape(text: str) -> str:
    """跳脫並保留空白與換行。

    注意：只能對「文字片段」做，不能對組好的 HTML 做——否則會把
    <span style="…"> 標籤裡的空格也換成 &nbsp;，整個標籤就壞了。
    """
    return (html.escape(text)
            .replace("\n", "<br>")
            .replace("  ", "&nbsp;&nbsp;")
            .replace(" ", "&nbsp;"))


def highlight_code(code: str, lang: str) -> str:
    """很輕量的語法上色；語言不認得就只做跳脫。"""
    colors = palette()
    if lang.lower() not in _HIGHLIGHTABLE:
        return _code_escape(code)

    def tint(role: str, text: str) -> str:
        return f'<span style="color:{colors[role]};">{_code_escape(text)}</span>'

    out: list[str] = []
    pos = 0
    for match in _TOKEN.finditer(code):
        out.append(_code_escape(code[pos:match.start()]))
        text = match.group(0)
        kind = match.lastgroup
        if kind == "word":
            if text in _KEYWORDS:
                out.append(tint("syn_kw", text))
            elif match.end() < len(code) and code[match.end()] == "(":
                out.append(tint("syn_fn", text))
            else:
                out.append(_code_escape(text))
        elif kind == "str":
            out.append(tint("syn_str", text))
        elif kind == "com":
            out.append(tint("syn_com", text))
        else:
            out.append(tint("syn_num", text))
        pos = match.end()
    out.append(_code_escape(code[pos:]))
    return "".join(out).replace("\n", "<br>").replace(" ", "&nbsp;")


class CodeBlock(QFrame):
    """帶語言標題列與複製鍵的程式碼區塊；過長的行可橫向捲動。"""

    copy_requested = Signal(str)

    def __init__(self, lang: str, code: str, parent=None):
        super().__init__(parent)
        self.setObjectName("CodeBlock")
        self._lang = lang
        self._code = ""

        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)

        head = QFrame()
        head.setObjectName("CodeHeader")
        head.setFixedHeight(30)
        head_row = QHBoxLayout(head)
        head_row.setContentsMargins(12, 0, 8, 0)
        head_row.setSpacing(8)
        self.lang_label = QLabel(lang or "text")
        self.lang_label.setObjectName("CodeLang")
        self.copy_btn = IconButton("copy", 13, 24, "複製程式碼", "ink3", "FlatIconButton")
        self.copy_btn.clicked.connect(lambda: self.copy_requested.emit(self._code))
        head_row.addWidget(self.lang_label)
        head_row.addStretch(1)
        head_row.addWidget(self.copy_btn)

        self.body = QLabel()
        self.body.setObjectName("CodeText")
        self.body.setTextFormat(Qt.RichText)
        self.body.setWordWrap(False)
        self.body.setTextInteractionFlags(SELECTABLE)
        self.body.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.body.setFont(QFont(theme.MONO_FAMILIES[0], 10))

        self.scroll = QScrollArea()
        self.scroll.setWidget(self.body)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll.setStyleSheet("background: transparent;")

        wrap = QWidget()
        wrap_box = QVBoxLayout(wrap)
        wrap_box.setContentsMargins(14, 11, 14, 11)
        wrap_box.setSpacing(0)
        wrap_box.addWidget(self.scroll)

        box.addWidget(head)
        box.addWidget(wrap)

        self.set_code(code)

    def set_code(self, code: str) -> None:
        code = code.rstrip("\n")
        if code == self._code:
            return
        self._code = code
        self.body.setText(highlight_code(code, self._lang))
        self.body.adjustSize()
        self._apply_height()

    def _apply_height(self) -> None:
        """只有長行需要橫向捲動時才多留捲軸空間，否則底部會空一大塊。"""
        content = self.body.sizeHint().height()
        available = self.scroll.viewport().width()
        needs_bar = available > 0 and self.body.sizeHint().width() > available
        extra = 14 if needs_bar else 2
        self.scroll.setFixedHeight(content + extra)
        self.setFixedHeight(30 + 22 + content + extra)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._apply_height()

    def code(self) -> str:
        return self._code


# --------------------------------------------------------------------------- #
# 思考區塊
# --------------------------------------------------------------------------- #

class ThinkBlock(QFrame):
    """可折疊的思考過程；串流中自動展開，結束後收合。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ThinkBlock")
        self._collapsed = False
        self._text = ""
        enable_height_for_width(self)

        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)

        self.header = QWidget()
        self.header.setObjectName("ThinkHeader")
        self.header.setFixedHeight(32)
        self.header.setCursor(Qt.PointingHandCursor)
        self.header.mousePressEvent = self._on_header_click  # type: ignore[method-assign]

        row = QHBoxLayout(self.header)
        row.setContentsMargins(12, 0, 12, 0)
        row.setSpacing(7)
        self.spark = IconLabel("sparkle", 12, "think")
        self.title = QLabel("思考中")
        self.title.setObjectName("ThinkTitle")
        self.time_label = QLabel("")
        self.time_label.setObjectName("ThinkTime")
        self.chevron = IconLabel("chevron-down", 12, "think_dim", 2.2)
        row.addWidget(self.spark)
        row.addWidget(self.title)
        row.addStretch(1)
        row.addWidget(self.time_label)
        row.addWidget(self.chevron)

        self.body = wrap_label("ThinkBody", rich=False)
        self.body.setContentsMargins(31, 0, 14, 11)
        font = self.body.font()
        font.setItalic(True)
        self.body.setFont(font)

        box.addWidget(self.header)
        box.addWidget(self.body)

    def _on_header_click(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.set_collapsed(not self._collapsed)

    def set_collapsed(self, collapsed: bool) -> None:
        self._collapsed = collapsed
        self.body.setVisible(not collapsed)
        self.chevron._icon_name = "chevron-right" if collapsed else "chevron-down"
        self.chevron.refresh_icons()

    def is_collapsed(self) -> bool:
        return self._collapsed

    def append(self, text: str) -> None:
        self._text += text
        self.body.setText(self._text.strip())

    def set_text(self, text: str) -> None:
        self._text = text
        self.body.setText(text.strip())

    def text(self) -> str:
        return self._text

    def set_streaming(self, streaming: bool, seconds: float = 0.0) -> None:
        if streaming:
            self.title.setText("思考中")
            self.time_label.setText("")
            self.set_collapsed(False)
        else:
            self.title.setText(f"已思考 {seconds:.1f} 秒" if seconds else "思考過程")
            self.time_label.setText("")

    def refresh_icons(self) -> None:
        self.spark.refresh_icons()
        self.chevron.refresh_icons()


# --------------------------------------------------------------------------- #
# 單則訊息
# --------------------------------------------------------------------------- #

class MessageWidget(QFrame):
    """一則訊息。user 靠右氣泡，assistant 左側頭像 + 全寬內容。"""

    copy_requested = Signal(str)
    regenerate_requested = Signal()

    BUBBLE_RATIO = 0.76

    def __init__(self, role: str, model: str = "", parent=None):
        super().__init__(parent)
        self.role = role
        self.model = model
        self._content = ""
        self._segment_widgets: list[QWidget] = []
        self._segment_kinds: list[str] = []
        self.think: ThinkBlock | None = None
        enable_height_for_width(self)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        if role == "user":
            self._build_user(outer)
        else:
            self._build_assistant(outer)

    # -- 使用者 -------------------------------------------------------- #

    def _build_user(self, outer: QVBoxLayout) -> None:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addStretch(1)

        self.bubble = wrap_label("UserBubble", rich=False)
        enable_height_for_width(self.bubble, QSizePolicy.Maximum)
        # 字級明確設在 QFont 上，否則 fontMetrics() 讀到的是預設 13px，
        # 而實際渲染用 QSS 的 14px，量出來的寬度會偏小、氣泡被擠成兩行。
        bubble_font = self.bubble.font()
        bubble_font.setPixelSize(14)
        self.bubble.setFont(bubble_font)
        row.addWidget(self.bubble)
        outer.addLayout(row)

        self.attachment = QLabel()
        self.attachment.setObjectName("Muted")
        self.attachment.setAlignment(Qt.AlignRight)
        self.attachment.hide()
        outer.addWidget(self.attachment)

    def set_user_text(self, text: str, images: int = 0) -> None:
        self._user_text = text
        self.bubble.setText(text)
        self._fit_bubble()
        if images:
            self.attachment.setText(f"附加 {images} 張圖片")
            self.attachment.show()

    def _fit_bubble(self) -> None:
        """氣泡寬度貼合內容，只有真的太長才在 76% 處換行。

        不能只設 maximumWidth：QLabel 一旦開了 wordWrap，sizeHint() 回的是
        「排成方塊」的啟發式寬度（量到的比整行短很多），而 Maximum 政策會
        拿 sizeHint 當實際寬度，短句因此被擠成兩行。所以把上下限一起釘死。
        """
        text = getattr(self, "_user_text", "")
        if not text:
            return
        self.bubble.ensurePolished()
        metrics = self.bubble.fontMetrics()
        longest = max((metrics.horizontalAdvance(line) for line in text.split("\n")),
                      default=0)
        natural = longest + 36          # QSS padding 10px 15px 加上餘裕
        if self.width() > 0:
            natural = min(natural, max(200, int(self.width() * self.BUBBLE_RATIO)))
        self.bubble.setMinimumWidth(natural)
        self.bubble.setMaximumWidth(natural)

    def resizeEvent(self, event) -> None:  # noqa: N802
        if self.role == "user":
            self._fit_bubble()
        super().resizeEvent(event)

    # -- 助理 ---------------------------------------------------------- #

    def _build_assistant(self, outer: QVBoxLayout) -> None:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        row.setAlignment(Qt.AlignTop)

        self.avatar = IconLabel("node", 14, "accent")
        avatar_box = QLabel()
        avatar_box.setObjectName("AssistantAvatar")
        avatar_box.setFixedSize(28, 28)
        avatar_layout = QVBoxLayout(avatar_box)
        avatar_layout.setContentsMargins(0, 0, 0, 0)
        avatar_layout.addWidget(self.avatar, 0, Qt.AlignCenter)
        self.avatar_box = avatar_box

        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(9)

        name_row = QHBoxLayout()
        name_row.setContentsMargins(0, 0, 0, 0)
        name_row.setSpacing(8)
        self.name = QLabel(self.model or "assistant")
        self.name.setObjectName("MsgName")
        name_row.addWidget(self.name)
        name_row.addStretch(1)
        column.addLayout(name_row)

        self.body_box = QVBoxLayout()
        self.body_box.setContentsMargins(0, 0, 0, 0)
        self.body_box.setSpacing(9)
        column.addLayout(self.body_box)

        foot = QHBoxLayout()
        foot.setContentsMargins(0, 0, 0, 0)
        foot.setSpacing(10)
        self.stats = QLabel("")
        self.stats.setObjectName("MsgMeta")
        self.copy_btn = IconButton("copy", 15, 24, "複製回覆", "ink3", "FlatIconButton")
        self.copy_btn.clicked.connect(lambda: self.copy_requested.emit(self._content))
        self.regen_btn = IconButton("refresh", 15, 24, "重新產生", "ink3", "FlatIconButton")
        self.regen_btn.clicked.connect(self.regenerate_requested.emit)
        foot.addWidget(self.stats)
        foot.addStretch(1)
        foot.addWidget(self.copy_btn)
        foot.addWidget(self.regen_btn)
        self.foot_widget = QWidget()
        self.foot_widget.setLayout(foot)
        self.foot_widget.hide()
        column.addWidget(self.foot_widget)

        row.addWidget(avatar_box, 0, Qt.AlignTop)
        row.addLayout(column, 1)
        outer.addLayout(row)

    # -- 串流 ---------------------------------------------------------- #

    def ensure_think(self) -> ThinkBlock:
        if self.think is None:
            self.think = ThinkBlock()
            self.think.set_streaming(True)
            self.body_box.insertWidget(0, self.think)
        return self.think

    def append_thinking(self, text: str) -> None:
        self.ensure_think().append(text)

    def set_thinking(self, text: str, collapsed: bool = True,
                     seconds: float = 0.0) -> None:
        if not text.strip():
            return
        block = self.ensure_think()
        block.set_text(text)
        block.set_streaming(False, seconds)
        block.set_collapsed(collapsed)

    def append_content(self, text: str) -> None:
        self.set_content(self._content + text)

    def set_content(self, text: str) -> None:
        self._content = text
        self._render_segments(split_segments(text))

    def content(self) -> str:
        return self._content

    def _render_segments(self, segments: list[tuple[str, str, str]]) -> None:
        kinds = [s[0] for s in segments]
        if kinds != self._segment_kinds:
            self._rebuild_segments(segments)
            return
        # 結構沒變就只更新內容，避免每個 token 都重建 widget
        for widget, (kind, _lang, body) in zip(self._segment_widgets, segments):
            if kind == "code":
                widget.set_code(body)
            else:
                widget.setText(text_to_html(body))

    def _rebuild_segments(self, segments: list[tuple[str, str, str]]) -> None:
        insert_at = 1 if self.think is not None else 0
        for widget in self._segment_widgets:
            self.body_box.removeWidget(widget)
            widget.setParent(None)
            widget.deleteLater()
        self._segment_widgets.clear()
        self._segment_kinds.clear()

        for kind, lang, body in segments:
            if kind == "code":
                widget = CodeBlock(lang, body)
                widget.copy_requested.connect(self.copy_requested.emit)
            else:
                widget = wrap_label("MsgBody")
                widget.setText(text_to_html(body))
            self.body_box.insertWidget(insert_at, widget)
            insert_at += 1
            self._segment_widgets.append(widget)
            self._segment_kinds.append(kind)

    def finish(self, stats: str) -> None:
        if self.role != "user":
            self.stats.setText(stats)
            self.foot_widget.show()

    def set_error(self, message: str, hint: str = "") -> None:
        """把這則訊息換成錯誤卡片。"""
        for widget in self._segment_widgets:
            self.body_box.removeWidget(widget)
            widget.setParent(None)
            widget.deleteLater()
        self._segment_widgets.clear()
        self._segment_kinds.clear()

        card = QFrame()
        card.setObjectName("ErrorCard")
        box = QHBoxLayout(card)
        box.setContentsMargins(15, 13, 15, 13)
        box.setSpacing(13)
        alert = IconLabel("alert", 18, "err")
        column = QVBoxLayout()
        column.setSpacing(3)
        title = wrap_label("ErrorTitle", rich=False)
        title.setText(message)
        column.addWidget(title)
        if hint:
            detail = wrap_label("ErrorBody", rich=False)
            detail.setText(hint)
            column.addWidget(detail)
        box.addWidget(alert, 0, Qt.AlignTop)
        box.addLayout(column, 1)

        self.body_box.addWidget(card)
        self._segment_widgets.append(card)
        self._segment_kinds.append("error")

    def refresh_icons(self) -> None:
        if hasattr(self, "avatar"):
            self.avatar.refresh_icons()
