# -*- coding: utf-8 -*-
"""線條圖示 —— 24 格線、圓端點，顏色在產生時燒進 SVG。

不使用 emoji：桌面環境的 emoji 字型差異太大，且無法跟著主題換色。
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

# name -> SVG 內容（只放路徑，stroke 由外層 <g> 統一設定）
_PATHS = {
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>',
    "menu": '<path d="M4 6h16M4 12h16M4 18h16"/>',
    "more": '<circle cx="12" cy="5" r="1.3"/><circle cx="12" cy="12" r="1.3"/>'
            '<circle cx="12" cy="19" r="1.3"/>',
    "sparkle": '<path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/>',
    "chevron-down": '<path d="M6 9l6 6 6-6"/>',
    "chevron-right": '<path d="M9 6l6 6-6 6"/>',
    "copy": '<rect x="9" y="9" width="11" height="11" rx="2.5"/>'
            '<path d="M5 15V6a2 2 0 012-2h9"/>',
    "refresh": '<path d="M20 11a8 8 0 10-2.3 6.1"/><path d="M20 5v6h-6"/>',
    "retry": '<path d="M4 11a8 8 0 0113.7-5.6L20 8"/><path d="M20 4v4h-4"/>',
    "paperclip": '<path d="M20 11.5l-8 8a5 5 0 01-7-7l8.5-8.5a3.4 3.4 0 014.8 4.8'
                 'L9.9 17.2a1.8 1.8 0 01-2.5-2.5l7.8-7.8"/>',
    "send": '<path d="M12 19V5M6 11l6-6 6 6"/>',
    "stop": '<rect x="7" y="7" width="10" height="10" rx="2"/>',
    "moon": '<path d="M20.5 14.3A8.5 8.5 0 019.7 3.5a8.5 8.5 0 1010.8 10.8z"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2'
           'M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M19.1 4.9l-1.4 1.4M6.3 17.7l-1.4 1.4"/>',
    "sliders": '<path d="M4 6h10M18 6h2M4 12h2M10 12h10M4 18h7M15 18h5"/>'
               '<circle cx="16" cy="6" r="2"/><circle cx="8" cy="12" r="2"/>'
               '<circle cx="13" cy="18" r="2"/>',
    "check": '<path d="M20 6L9 17l-5-5"/>',
    "alert": '<circle cx="12" cy="12" r="9"/><path d="M12 8v5"/>'
             '<path d="M12 16.6v.4"/>',
    "trash": '<path d="M4 7h16M10 11v6M14 11v6"/>'
             '<path d="M6 7l1 12a2 2 0 002 2h6a2 2 0 002-2l1-12"/>'
             '<path d="M9 7V5a1 1 0 011-1h4a1 1 0 011 1v2"/>',
    "node": '<path d="M12 3l7.5 4.5v9L12 21l-7.5-4.5v-9z"/><circle cx="12" cy="12" r="2.6"/>',
    "plug": '<path d="M9 3v6M15 3v6"/><path d="M6 9h12v3a6 6 0 01-12 0z"/><path d="M12 18v3"/>',
    "download": '<path d="M12 4v11M7 11l5 5 5-5"/><path d="M5 20h14"/>',
}

_TEMPLATE = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="{s}" height="{s}">'
    '<g fill="none" stroke="{color}" stroke-width="{w}" '
    'stroke-linecap="round" stroke-linejoin="round">{body}</g></svg>'
)

_cache: dict[tuple, QPixmap] = {}


def pixmap(name: str, color: str, size: int = 16, width: float = 2.0,
           dpr: float = 1.0) -> QPixmap:
    """產生一張圖示 QPixmap；已考慮高 DPI。"""
    key = (name, color, size, width, round(dpr, 2))
    hit = _cache.get(key)
    if hit is not None:
        return hit

    body = _PATHS.get(name)
    if body is None:
        raise KeyError(f"未定義的圖示：{name}")

    svg = _TEMPLATE.format(s=size, color=color, w=width, body=body)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))

    px = QPixmap(int(size * dpr), int(size * dpr))
    px.setDevicePixelRatio(dpr)
    px.fill(Qt.transparent)
    painter = QPainter(px)
    painter.setRenderHint(QPainter.Antialiasing, True)
    renderer.render(painter)
    painter.end()

    _cache[key] = px
    return px


def icon(name: str, color: str, size: int = 16, width: float = 2.0,
         dpr: float = 1.0) -> QIcon:
    return QIcon(pixmap(name, color, size, width, dpr))


def clear_cache() -> None:
    """切換主題後呼叫，讓圖示以新顏色重新產生。"""
    _cache.clear()
