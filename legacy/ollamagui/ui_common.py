# -*- coding: utf-8 -*-
"""可重複使用的小元件：狀態燈、分段控制項、參數滑桿、圖示按鈕。"""

from __future__ import annotations

from PySide6.QtCore import (Property, QEasingCurve, QPropertyAnimation, QSize, Qt,
                            Signal)
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QCheckBox, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QSizePolicy, QSlider, QVBoxLayout,
                               QWidget)

from . import icons, theme

# 目前套用的色票，由 set_palette() 更新
_palette: dict = theme.DARK


def palette() -> dict:
    return _palette


def set_palette(colors: dict) -> None:
    global _palette
    _palette = colors
    icons.clear_cache()


def refresh_icons_recursive(root: QWidget) -> None:
    """換主題後，讓所有自訂元件重畫圖示。"""
    for child in root.findChildren(QWidget):
        hook = getattr(child, "refresh_icons", None)
        if callable(hook):
            hook()
    hook = getattr(root, "refresh_icons", None)
    if callable(hook):
        hook()


def divider(horizontal: bool = True) -> QFrame:
    line = QFrame()
    line.setObjectName("Divider")
    if horizontal:
        line.setFixedHeight(1)
        line.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    else:
        line.setFixedWidth(1)
        line.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
    return line


def label(text: str, object_name: str = "") -> QLabel:
    lb = QLabel(text)
    if object_name:
        lb.setObjectName(object_name)
    return lb


# --------------------------------------------------------------------------- #


class IconButton(QPushButton):
    """只有圖示的按鈕；換主題時自己重新產生圖示。"""

    def __init__(self, name: str, size: int = 16, box: int = 32,
                 tooltip: str = "", role: str = "ink2",
                 object_name: str = "IconButton", stroke: float = 1.8,
                 parent=None):
        super().__init__(parent)
        self.setObjectName(object_name)
        self._icon_name = name
        self._icon_size = size
        self._role = role
        self._stroke = stroke
        self.setFixedSize(box, box)
        self.setIconSize(QSize(size, size))
        self.setCursor(Qt.PointingHandCursor)
        if tooltip:
            self.setToolTip(tooltip)
        self.refresh_icons()

    def set_icon_name(self, name: str) -> None:
        self._icon_name = name
        self.refresh_icons()

    def set_role(self, role: str) -> None:
        self._role = role
        self.refresh_icons()

    def refresh_icons(self) -> None:
        dpr = self.devicePixelRatioF() or 1.0
        self.setIcon(icons.icon(self._icon_name, palette()[self._role],
                                self._icon_size, self._stroke, dpr))


class IconLabel(QLabel):
    """純顯示用的圖示。"""

    def __init__(self, name: str, size: int = 14, role: str = "ink2",
                 stroke: float = 2.0, parent=None):
        super().__init__(parent)
        self._icon_name = name
        self._icon_size = size
        self._role = role
        self._stroke = stroke
        self.setFixedSize(size, size)
        self.refresh_icons()

    def set_role(self, role: str) -> None:
        self._role = role
        self.refresh_icons()

    def refresh_icons(self) -> None:
        dpr = self.devicePixelRatioF() or 1.0
        self.setPixmap(icons.pixmap(self._icon_name, palette()[self._role],
                                    self._icon_size, self._stroke, dpr))


# --------------------------------------------------------------------------- #


class StatusDot(QWidget):
    """連線狀態燈：實心圓點 + 外圈光暈；連線中會呼吸。"""

    STATE_COLORS = {"ok": "ok", "connecting": "warn", "error": "err", "idle": "ink3"}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(16, 16)
        self._state = "idle"
        self._glow = 1.0
        self._anim = QPropertyAnimation(self, b"glow", self)
        self._anim.setDuration(1400)
        self._anim.setStartValue(0.35)
        self._anim.setKeyValueAt(0.5, 1.0)
        self._anim.setEndValue(0.35)
        self._anim.setEasingCurve(QEasingCurve.InOutSine)
        self._anim.setLoopCount(-1)

    def get_glow(self) -> float:
        return self._glow

    def set_glow(self, value: float) -> None:
        self._glow = value
        self.update()

    glow = Property(float, get_glow, set_glow)

    def set_state(self, state: str) -> None:
        if state == self._state:
            return
        self._state = state
        if state == "connecting":
            self._anim.start()
        else:
            self._anim.stop()
            self._glow = 1.0
        self.update()

    def state(self) -> str:
        return self._state

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        base = QColor(palette()[self.STATE_COLORS.get(self._state, "ink3")])

        halo = QColor(base)
        halo.setAlphaF(0.18 * self._glow)
        painter.setPen(Qt.NoPen)
        painter.setBrush(halo)
        painter.drawEllipse(self.rect().center(), 6, 6)

        painter.setBrush(base)
        painter.drawEllipse(self.rect().center(), 3, 3)
        painter.end()


class StatusPill(QFrame):
    """頂列右側的連線狀態膠囊；點擊開啟主機設定，錯誤時多一顆重試鍵。"""

    clicked = Signal()
    retry_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("StatusPill")
        self.setFixedHeight(30)
        self.setCursor(Qt.PointingHandCursor)

        row = QHBoxLayout(self)
        row.setContentsMargins(11, 0, 12, 0)
        row.setSpacing(8)

        self.dot = StatusDot()
        self.text = label("尚未連線", "StatusText")
        self.sep = divider(horizontal=False)
        self.sep.setFixedHeight(13)
        self.host = label("", "StatusHost")

        self.retry = QPushButton("重試")
        self.retry.setObjectName("GhostButton")
        self.retry.setCursor(Qt.PointingHandCursor)
        self.retry.setFixedHeight(22)
        self.retry.clicked.connect(self.retry_requested.emit)
        self.retry.hide()

        row.addWidget(self.dot)
        row.addWidget(self.text)
        row.addWidget(self.sep)
        row.addWidget(self.host)
        row.addWidget(self.retry)

    def set_status(self, state: str, text: str, host: str = "",
                   host_role: str = "ink3") -> None:
        self.dot.set_state(state)
        self.text.setText(text)
        self.host.setText(host)
        self.host.setVisible(bool(host))
        self.sep.setVisible(bool(host))
        self.host.setStyleSheet(f"color: {palette()[host_role]}; font-size: 11px;")
        self.retry.setVisible(state == "error")
        self.setObjectName("StatusPillError" if state == "error" else "StatusPill")
        # 換 objectName 後要重新套用樣式表才會生效
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


# --------------------------------------------------------------------------- #


class SegmentedControl(QFrame):
    """分段切換；選項會依模型能力動態增減。"""

    changed = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Segmented")
        self.setFixedHeight(32)
        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(3, 3, 3, 3)
        self._row.setSpacing(3)
        self._buttons: list[QPushButton] = []
        self._values: list = []

    def set_options(self, options: list[tuple[str, object]]) -> None:
        keep = self.value()
        for btn in self._buttons:
            self._row.removeWidget(btn)
            btn.setParent(None)
            btn.deleteLater()
        self._buttons.clear()
        self._values.clear()

        for text, value in options:
            btn = QPushButton(text)
            btn.setObjectName("SegmentButton")
            btn.setCheckable(True)
            btn.setAutoExclusive(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _checked=False, v=value: self._on_pick(v))
            self._row.addWidget(btn, 1)
            self._buttons.append(btn)
            self._values.append(value)

        if options:
            self.set_value(keep if keep in self._values else options[0][1],
                           notify=False)

    def _on_pick(self, value) -> None:
        self.changed.emit(value)

    def value(self):
        for btn, value in zip(self._buttons, self._values):
            if btn.isChecked():
                return value
        return None

    def set_value(self, value, notify: bool = True) -> None:
        for btn, own in zip(self._buttons, self._values):
            if own == value and type(own) is type(value):
                btn.setChecked(True)
                if notify:
                    self.changed.emit(value)
                return
        if self._buttons:
            self._buttons[0].setChecked(True)

    def setEnabled(self, enabled: bool) -> None:  # noqa: N802 (Qt 命名)
        super().setEnabled(enabled)
        for btn in self._buttons:
            btn.setEnabled(enabled)


# --------------------------------------------------------------------------- #


class ParamSlider(QWidget):
    """一列取樣參數：名稱 + 數值徽章 + 滑桿。

    QSlider 只吃整數，浮點參數內部乘上 scale 再除回來。
    """

    changed = Signal(float)

    def __init__(self, name: str, minimum: float, maximum: float,
                 value: float, decimals: int = 2, parent=None):
        super().__init__(parent)
        self._decimals = decimals
        self._scale = 10 ** decimals

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)
        self.name = QLabel(name)
        self.badge = QLabel()
        self.badge.setObjectName("ValueBadge")
        self.badge.setAlignment(Qt.AlignCenter)
        top.addWidget(self.name)
        top.addStretch(1)
        top.addWidget(self.badge)

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setMinimum(int(round(minimum * self._scale)))
        self.slider.setMaximum(int(round(maximum * self._scale)))
        self.slider.setValue(int(round(value * self._scale)))
        self.slider.valueChanged.connect(self._on_slide)

        outer.addLayout(top)
        outer.addWidget(self.slider)
        self._update_badge()

    def _on_slide(self, _raw: int) -> None:
        self._update_badge()
        self.changed.emit(self.value())

    def _update_badge(self) -> None:
        value = self.value()
        text = f"{value:.{self._decimals}f}" if self._decimals else f"{int(value)}"
        self.badge.setText(text)

    def value(self) -> float:
        return self.slider.value() / self._scale

    def set_value(self, value: float) -> None:
        self.slider.setValue(int(round(value * self._scale)))
        self._update_badge()


class Field(QWidget):
    """小標籤 + 單行輸入。"""

    def __init__(self, name: str, value: str = "", mono: bool = True, parent=None):
        super().__init__(parent)
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(5)
        self.label = QLabel(name)
        self.label.setObjectName("FieldLabel")
        self.edit = QLineEdit(value)
        if mono:
            self.edit.setObjectName("MonoEdit")
        self.edit.setFixedHeight(30)
        box.addWidget(self.label)
        box.addWidget(self.edit)

    def text(self) -> str:
        return self.edit.text()

    def set_text(self, value: str) -> None:
        self.edit.setText(value)


class Toggle(QCheckBox):
    def __init__(self, text: str, checked: bool = False, parent=None):
        super().__init__(text, parent)
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)
