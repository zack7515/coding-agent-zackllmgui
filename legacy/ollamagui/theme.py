# -*- coding: utf-8 -*-
"""色票與 QSS —— 所有顏色集中在此，切換主題只換一份 dict 再重新套用。

數值直接取自設計規格畫板，請勿在其他模組硬寫顏色。
"""

from __future__ import annotations

UI_FAMILIES = ["Segoe UI Variable Text", "Segoe UI", "Microsoft JhengHei UI", "Noto Sans TC"]
MONO_FAMILIES = ["Cascadia Code", "Cascadia Mono", "Consolas", "Courier New"]

DARK = {
    "name": "dark",
    "ground": "#0d0f12",        # 視窗底 / 聊天區
    "surface": "#141619",       # 側欄 / 參數面板
    "raised": "#1b1e23",        # 控制項
    "hover": "#22262d",         # hover / 使用者氣泡
    "line": "#23272e",          # 細分隔線
    "line2": "#31363e",         # 控制項邊框
    "ink": "#e9ebee",           # 主文字
    "ink2": "#a3aab4",          # 次要文字
    "ink3": "#6d7580",          # 提示 / 停用
    "accent": "#5b95f7",
    "accent_ink": "#0d0f12",    # 藍底上的文字
    "think": "#a78bff",
    "think_bg": "#17151f",
    "think_line": "#2c2640",
    "think_ink": "#b9a6f5",
    "think_dim": "#8b8399",
    "think_sel": "#2f2a45",
    "think_sel_ink": "#c4b5fd",
    "ok": "#3ddc97",
    "warn": "#f5b544",
    "err": "#ff6b6b",
    "err_bg": "#1a1315",
    "err_line": "#3a2529",
    "err_ink": "#ffc4c4",
    "code_bg": "#0a0c0e",
    "code_head": "#111417",
    "code_ink": "#c8cdd4",
    "inline_bg": "#20242b",     # 行內程式碼：要比聊天底稍亮才看得出來
    "composer_bg": "#16191e",
    "composer_line": "#2c313a",
    "scroll": "#2a2f37",
    # 程式碼語法高亮
    "syn_kw": "#c792ea",
    "syn_str": "#9ece6a",
    "syn_fn": "#7aa2f7",
    "syn_com": "#565f6b",
    "syn_num": "#f5b544",
}

LIGHT = {
    "name": "light",
    "ground": "#ffffff",
    "surface": "#f7f8fa",
    "raised": "#eef0f3",
    "hover": "#e4e7ec",
    "line": "#dcdfe4",
    "line2": "#c9ced6",
    "ink": "#1a1d21",
    "ink2": "#4d545d",
    "ink3": "#7b828c",
    "accent": "#2f6fe0",
    "accent_ink": "#ffffff",
    "think": "#7c5cf0",
    "think_bg": "#f5f2fe",
    "think_line": "#ddd4fb",
    "think_ink": "#5b3fc4",
    "think_dim": "#6f6689",
    "think_sel": "#e5dcfd",
    "think_sel_ink": "#4c2fb8",
    "ok": "#12a05f",
    "warn": "#a8700a",
    "err": "#d13b3b",
    "err_bg": "#fdf3f3",
    "err_line": "#f0cfcf",
    "err_ink": "#8f2626",
    "code_bg": "#f5f6f8",
    "code_head": "#eceef2",
    "code_ink": "#2b2f36",
    "inline_bg": "#eceef2",
    "composer_bg": "#ffffff",
    "composer_line": "#c9ced6",
    "scroll": "#c9ced6",
    "syn_kw": "#8b34c4",
    "syn_str": "#1f7a3d",
    "syn_fn": "#2255c7",
    "syn_com": "#8b929b",
    "syn_num": "#a8700a",
}

THEMES = {"dark": DARK, "light": LIGHT}


# --------------------------------------------------------------------------- #
# QSS
# --------------------------------------------------------------------------- #
# 說明：一律用 #objectName 選取，避免樣式外溢到子控制項。
# 以 %(key)s 取代，因為 QSS 本身滿是大括號，不能用 str.format。

_QSS = """
QWidget {
    color: %(ink)s;
    font-size: 13px;
}
/* 標籤一律明確指定顏色與透明底：只靠 QWidget 繼承的話，換主題重新 polish
   之後會掉回系統 palette（Windows 深色模式下就變成白字配白底）。 */
QLabel { color: %(ink)s; background: transparent; }
QMainWindow, #Root { background: %(ground)s; }
QToolTip {
    background: %(raised)s;
    color: %(ink)s;
    border: 1px solid %(line2)s;
    border-radius: 6px;
    padding: 4px 8px;
}

/* ── 側欄 ── */
#Sidebar { background: %(surface)s; border-right: 1px solid %(line)s; }
#SidebarHeader { border-bottom: 1px solid %(line)s; }
#AppName { font-size: 14px; font-weight: 600; }
#SectionLabel {
    color: %(ink3)s;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 1px;
}
#SidebarFooter { border-top: 1px solid %(line)s; }
#Avatar {
    background: %(hover)s;
    border-radius: 13px;
    color: %(ink2)s;
    font-size: 11px;
    font-weight: 600;
}

#ChatList {
    background: transparent;
    border: none;
    outline: none;
}
#ChatList::item {
    color: %(ink2)s;
    border-radius: 8px;
    padding: 7px 10px;
    margin: 1px 0px;
    border-left: 3px solid transparent;
}
#ChatList::item:hover { background: %(hover)s; }
#ChatList::item:selected {
    background: %(hover)s;
    color: %(ink)s;
    border-left: 3px solid %(accent)s;
}

/* ── 按鈕 ── */
QPushButton {
    background: %(raised)s;
    color: %(ink)s;
    border: 1px solid %(line2)s;
    border-radius: 10px;
    padding: 7px 12px;
    text-align: left;
}
QPushButton:hover { background: %(hover)s; }
QPushButton:pressed { background: %(line)s; }
QPushButton:disabled { color: %(ink3)s; border-color: %(line)s; }

#GhostButton {
    background: transparent;
    border: 1px solid %(line)s;
    border-radius: 8px;
    padding: 5px 9px;
    color: %(ink2)s;
    font-size: 11px;
    text-align: center;
}
#GhostButton:hover { background: %(hover)s; }

#IconButton {
    background: transparent;
    border: 1px solid %(line)s;
    border-radius: 8px;
    padding: 0px;
}
#IconButton:hover { background: %(hover)s; }
#IconButton:disabled { border-color: %(line)s; }

#FlatIconButton {
    background: transparent;
    border: none;
    border-radius: 6px;
    padding: 0px;
}
#FlatIconButton:hover { background: %(hover)s; }

#SendButton {
    background: %(accent)s;
    border: none;
    border-radius: 17px;
    padding: 0px;
}
#SendButton:hover { background: %(accent)s; }
#SendButton:disabled { background: %(line)s; }

#StopButton {
    background: %(err)s;
    border: none;
    border-radius: 17px;
    padding: 0px;
}

#DangerButton {
    background: %(err)s;
    color: %(err_bg)s;
    border: none;
    border-radius: 8px;
    padding: 6px 13px;
    font-weight: 600;
    text-align: center;
}

/* ── 模型選擇 ── */
#ModelButton {
    background: %(raised)s;
    border: 1px solid %(line2)s;
    border-radius: 8px;
    padding: 5px 10px 5px 12px;
    font-weight: 600;
}
#ModelButton:hover { background: %(hover)s; }
#CapChip {
    border-radius: 6px;
    padding: 2px 7px;
    font-size: 11px;
    font-weight: 600;
}
#SizeChip {
    background: %(line2)s;
    color: %(ink2)s;
    border-radius: 5px;
    padding: 1px 6px;
    font-size: 10px;
    font-weight: 600;
}

/* ── 連線狀態 ── */
#StatusPill {
    background: %(surface)s;
    border: 1px solid %(line)s;
    border-radius: 15px;
}
#StatusPill:hover { border-color: %(line2)s; }
#StatusPillError {
    background: %(err_bg)s;
    border: 1px solid %(err_line)s;
    border-radius: 15px;
}
#StatusText { font-size: 12px; font-weight: 500; }
#StatusHost { color: %(ink3)s; font-size: 11px; }

/* ── 輸入區 ── */
#Composer {
    background: %(composer_bg)s;
    border: 1px solid %(composer_line)s;
    border-radius: 20px;
}
#ComposerInput {
    background: transparent;
    border: none;
    color: %(ink)s;
    font-size: 14px;
    selection-background-color: %(accent)s;
    selection-color: %(accent_ink)s;
}
#ThinkChip {
    background: %(think_bg)s;
    border: 1px solid %(think_line)s;
    border-radius: 8px;
    color: %(think_ink)s;
    font-size: 12px;
    font-weight: 600;
    padding: 5px 10px;
    text-align: center;
}
#ThinkChip:hover { background: %(think_sel)s; }
#ThinkChip:disabled { background: transparent; border-color: %(line)s; color: %(ink3)s; }
#Hint { color: %(ink3)s; font-size: 11px; }

/* ── 輸入欄位 ── */
QLineEdit, QPlainTextEdit, QTextEdit {
    background: %(ground)s;
    border: 1px solid %(line)s;
    border-radius: 8px;
    padding: 5px 9px;
    color: %(ink)s;
    selection-background-color: %(accent)s;
    selection-color: %(accent_ink)s;
}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus { border-color: %(accent)s; }
#MonoEdit { font-family: "%(mono)s"; font-size: 12px; }

/* ── 參數面板 ── */
#ParamPanel { background: %(surface)s; border-left: 1px solid %(line)s; }
#ParamContent, #ParamViewport { background: %(surface)s; }
#ChatViewport { background: %(ground)s; }
#ParamHeader { border-bottom: 1px solid %(line)s; }
#PanelTitle { font-size: 14px; font-weight: 600; }
#Divider { background: %(line)s; }
#ValueBadge {
    background: %(raised)s;
    border: 1px solid %(line2)s;
    border-radius: 5px;
    padding: 1px 7px;
    font-family: "%(mono)s";
    font-size: 11px;
}
#Muted { color: %(ink3)s; font-size: 11px; }
#FieldLabel { color: %(ink3)s; font-size: 11px; font-family: "%(mono)s"; }

/* ── 分段控制項 ── */
#Segmented {
    background: %(ground)s;
    border: 1px solid %(line)s;
    border-radius: 9px;
}
#SegmentButton {
    background: transparent;
    border: none;
    border-radius: 6px;
    color: %(ink2)s;
    font-size: 12px;
    padding: 4px 0px;
    text-align: center;
}
#SegmentButton:hover:!checked { background: %(hover)s; }
#SegmentButton:checked {
    background: %(think_sel)s;
    color: %(think_sel_ink)s;
    font-weight: 600;
}
#SegmentButton:disabled { color: %(ink3)s; }

/* ── 滑桿 ── */
QSlider::groove:horizontal {
    height: 4px;
    background: %(line)s;
    border-radius: 2px;
}
QSlider::sub-page:horizontal {
    background: %(accent)s;
    border-radius: 2px;
}
QSlider::handle:horizontal {
    width: 13px;
    height: 13px;
    margin: -5px 0px;
    border-radius: 7px;
    background: %(ink)s;
    border: 3px solid %(accent)s;
}
QSlider::handle:horizontal:disabled { border-color: %(line2)s; }

QCheckBox { spacing: 8px; font-size: 12px; color: %(ink2)s; }
QCheckBox::indicator {
    width: 15px; height: 15px;
    border-radius: 4px;
    border: 1px solid %(line2)s;
    background: %(ground)s;
}
QCheckBox::indicator:checked {
    background: %(think)s;
    border-color: %(think)s;
}

/* ── 訊息 ── */
#UserBubble {
    background: %(hover)s;
    border-radius: 18px;
    padding: 10px 15px;
    font-size: 14px;
}
#AssistantAvatar {
    background: %(raised)s;
    border: 1px solid %(line2)s;
    border-radius: 14px;
}
#MsgName { font-size: 12px; font-weight: 600; }
#MsgMeta { color: %(ink3)s; font-size: 11px; font-family: "%(mono)s"; }
#MsgBody { font-size: 14px; color: %(ink)s; }
#ErrorCard {
    background: %(err_bg)s;
    border: 1px solid %(err_line)s;
    border-radius: 12px;
}
#ErrorTitle { color: %(err_ink)s; font-size: 13px; font-weight: 600; }
#ErrorBody { color: %(ink2)s; font-size: 12px; }

/* ── 思考區塊 ── */
#ThinkBlock {
    background: %(think_bg)s;
    border: 1px solid %(think_line)s;
    border-radius: 12px;
}
#ThinkHeader { border: none; background: transparent; }
#ThinkTitle { color: %(think_ink)s; font-size: 11px; font-weight: 600; }
#ThinkTime { color: %(think_dim)s; font-size: 11px; font-family: "%(mono)s"; }
#ThinkBody { color: %(think_dim)s; font-size: 12px; }

/* ── 程式碼區塊 ── */
#CodeBlock {
    background: %(code_bg)s;
    border: 1px solid %(line)s;
    border-radius: 12px;
}
#CodeHeader { background: %(code_head)s; border-bottom: 1px solid %(line)s; }
#CodeLang { color: %(ink3)s; font-size: 11px; font-family: "%(mono)s"; }
#CodeText {
    color: %(code_ink)s;
    font-family: "%(mono)s";
    font-size: 12px;
    background: transparent;
}

/* ── 捲軸 ── */
QScrollArea { background: transparent; border: none; }
QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 0px;
}
QScrollBar::handle:vertical {
    background: %(scroll)s;
    border-radius: 5px;
    min-height: 28px;
}
QScrollBar::handle:vertical:hover { background: %(line2)s; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QScrollBar:horizontal { background: transparent; height: 10px; }
QScrollBar::handle:horizontal { background: %(scroll)s; border-radius: 5px; min-width: 28px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0px; }
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: transparent; }

/* ── 其他 ── */
QSplitter::handle { background: %(line)s; }
QSplitter::handle:horizontal { width: 1px; }
QMenu {
    background: %(surface)s;
    border: 1px solid %(line2)s;
    border-radius: 10px;
    padding: 5px;
}
QMenu::item { padding: 6px 24px 6px 12px; border-radius: 6px; }
QMenu::item:selected { background: %(hover)s; }
QMenu::separator { height: 1px; background: %(line)s; margin: 4px 8px; }
QDialog { background: %(surface)s; }
"""


def build_qss(colors: dict) -> str:
    """把色票套進 QSS 模板。"""
    values = dict(colors)
    values["mono"] = MONO_FAMILIES[0]
    return _QSS % values
