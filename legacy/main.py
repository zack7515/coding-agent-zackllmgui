#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ollama GUI 進入點。

用法：
    python main.py

需要 PySide6：
    pip install -r requirements.txt
"""

import sys


def _die(message: str) -> None:
    print(message, file=sys.stderr)
    sys.exit(1)


try:
    from ollamagui.app import main
except ImportError as exc:
    if "PySide6" in str(exc):
        _die("找不到 PySide6，請先安裝：\n    pip install PySide6\n\n"
             "若是 conda 環境出現 DLL 載入錯誤，請改用乾淨的 venv：\n"
             "    python -m venv .venv\n"
             "    .venv\\Scripts\\activate\n"
             "    pip install PySide6\n\n"
             "（conda 的 Library\\bin 內有版本不同的 Qt6 DLL 會搶先被載入）")
    raise

if __name__ == "__main__":
    sys.exit(main())
