# -*- coding: utf-8 -*-
"""serve.py 拆出來的功能模組。

拆的標準是耦合度不是行數。相依只有一個方向：

    workspace ← 所有人（Session、cur()、ws_path()，檔案工具的安全邊界）
    jobs      ← agents
    skills    ← agents（借 parse_skill 解 md frontmatter）

serve.py 留下的是黏著層：HTTP Handler、工具派送（run_tool／tool_defs）、
系統提示、沙盒包裝。那幾塊要跟十幾個模組打交道，拆出去只是把耦合搬家。
"""
