#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工具的 schema —— 送給模型的那份工具定義。

**這裡只有資料，沒有邏輯。** 實作在 serve.py 的 `_tool_*`，
開放與否由 `tool_defs()` 依 `needs` 決定：

    needs        條件
    ''           工具開著就有
    'ws'         還要有工作區
    'write'      還要按下「修改檔案」
    'plan'       只在計畫模式出現
    'browser'    還要開「連網瀏覽」
    'tools'      工具開著就有（跟 '' 一樣，語意上是「不需要工作區」）
    'client'     由網頁處理，不在伺服器執行

拆出來的理由：這是整個專案裡最常改、也最需要逐字推敲的一份東西
（描述寫得好不好，直接決定小模型會不會用錯），跟 HTTP 路由與檔案操作
混在同一個檔案裡很難專心看。

描述的寫法參考 xai-org/grok-build 的 search_replace / read_file：
講清楚唯一性要求、講清楚行號前綴不是內容的一部分。這兩點沒寫，小模型就會
把「  12→」也一起貼進 old 裡，然後永遠對不上。
"""

TOOL_SCHEMAS = [
    {"name": "fetch_url", "needs": "", "description": "抓取一個網頁並轉成純文字",
     "properties": {"url": {"type": "string", "description": "http/https 網址"}},
     "required": ["url"]},
    {"name": "list_dir", "needs": "ws", "description": "列出工作區裡某個資料夾的內容",
     "properties": {"path": {"type": "string", "description": "相對於工作區的資料夾，預設為根目錄"}},
     "required": []},
    {"name": "search_files", "needs": "ws",
     "description": "用正規表示式在工作區裡搜尋，只回傳命中的那幾行。要找東西一律先用這個，不要整個檔案讀進來",
     "properties": {"pattern": {"type": "string", "description": "正規表示式"},
                    "glob": {"type": "string",
                             "description": "限定範圍，檔名或相對路徑都可以："
                                            "*.py、pkg/*.py、pkg/calc.py"}},
     "required": ["pattern"]},
    {"name": "read_file", "needs": "ws",
     "description": "讀取工作區裡的檔案。每一行前面會加上「行號→」，那個前綴不是檔案內容",
     "properties": {"path": {"type": "string", "description": "相對於工作區的檔案路徑"},
                    "start": {"type": "integer", "description": "起始行，從 1 開始；檔案很大時才需要指定"},
                    "end": {"type": "integer", "description": "結束行"}},
     "required": ["path"]},
    {"name": "run_shell", "needs": "ws",
     "description": ("在工作區目錄下執行一行指令並取得輸出。"
                     "**預設只等 30 秒，超過就會被中止** —— 安裝套件、建置、編譯、"
                     "資料庫遷移這種要跑幾分鐘的，一律加 background=true 丟背景，"
                     "拿到 id 之後先去做別的，再用 check_job 收"),
     "properties": {"command": {"type": "string", "description": "要執行的指令"},
                    "background": {"type": "boolean",
                                   "description": "丟到背景跑，立刻回傳一個 job id 而不是等它跑完。"
                                                  "預期超過 30 秒的指令都要開這個"}},
     "required": ["command"]},
    {"name": "check_job", "needs": "ws",
     "description": ("收背景指令的結果。還沒跑完的話它會先幫你等一段時間再回話，"
                     "所以不必自己重複輪詢。id 留空就列出全部"),
     "properties": {"id": {"type": "string", "description": "run_shell 丟背景時回傳的 id"},
                    "kill": {"type": "boolean", "description": "true＝終止這條指令"},
                    "wait": {"type": "integer",
                             "description": "還沒跑完時最多等幾秒再回話，預設 20。"
                                            "有別的事要做就填 0，馬上拿現在的進度"}},
     "required": []},
    {"name": "run_tests", "needs": "ws", "description": "用專案偵測到的 python 跑 pytest",
     "properties": {"target": {"type": "string", "description": "要跑的檔案或目錄，留空為全部"},
                    "k": {"type": "string", "description": "只跑名稱符合的測試（pytest -k）"}},
     "required": []},
    {"name": "run_browser", "needs": "browser",
     "description": ("上網查東西。不知道網址就先 action=\"search\" 搜尋，"
                     "拿到網址再 action=\"open\" 開來讀。open 會一併回傳那一頁上的連結，"
                     "所以可以順著連結一路查下去 —— 需要多找幾層就多呼叫幾次"),
     "properties": {
         "action": {"type": "string", "enum": ["search", "open"],
                    "description": "search＝用關鍵字搜尋，open＝打開一個網址"},
         "url": {"type": "string", "description": "action=open 時的網址（http/https）"},
         "query": {"type": "string", "description": "action=search 時的關鍵字"},
         "limit": {"type": "integer", "description": "搜尋結果或連結的筆數上限，預設 10"}},
     "required": ["action"]},
    {"name": "setup_env", "needs": "ws",
     "description": ("在工作區裡建立 .venv 並安裝套件，之後 run_tests 會自動用它。"
                     "需要套件（例如 pytest）時用這支，不要自己用 run_shell 下 pip install ——"
                     "那會裝進系統環境"),
     "properties": {"packages": {"type": "array", "description": "套件名稱，例如 [\"pytest\"]",
                                 "items": {"type": "string"}},
                    "requirements": {"type": "string",
                                     "description": "requirements.txt 的相對路徑（可省略）"}},
     "required": []},
    {"name": "todo_write", "needs": "tools",
     "description": ("維護這次工作的待辦清單。開始一件多步驟的工作時先列出來，"
                     "每完成一項就整份重送一次並把它標成完成。清單會顯示給使用者看"),
     "properties": {"items": {"type": "array", "description": "完整的待辦清單，不是只送新增的那幾項",
                              "items": {"type": "object", "properties": {
                                  "text": {"type": "string", "description": "一句話描述這一步"},
                                  "done": {"type": "boolean", "description": "是否已完成"},
                                  "blocked_by": {
                                      "type": "array", "items": {"type": "integer"},
                                      "description": "要等哪幾項先做完才能開始，填項次編號"
                                                     "（第一項是 1）。只能指向排在前面的項目"}},
                                  "required": ["text"]}}},
     "required": ["items"]},
    {"name": "ask_user_question", "needs": "client",
     "description": ("需要使用者決定時用這個問，不要自己猜。"
                     "例如：不確定要改哪一個檔案、有兩種做法要選、缺少必要資訊"),
     "properties": {"question": {"type": "string", "description": "要問的問題，一次只問一件事"},
                    "options": {"type": "array", "description": "可選的答案（沒有就讓使用者自己打）",
                                "items": {"type": "string"}}},
     "required": ["question"]},
    {"name": "submit_plan", "needs": "plan",
     "description": ("先把要做的事寫成計畫送出，使用者核准之後才會開放修改檔案的工具。"
                     "計畫要列出：要改哪些檔案、每個檔案改什麼、怎麼驗證"),
     "properties": {"plan": {"type": "string", "description": "計畫內容，條列式"}},
     "required": ["plan"]},
    {"name": "task", "needs": "ws",
     "description": ("把一件需要翻很多檔案的事交給子代理去做，只有結論會回到這裡。"
                     "適合「掃過整個專案找出所有用到 X 的地方」這種問題 —— "
                     "自己做的話幾十個檔案的內容會塞滿對話。子代理有同樣的工具，"
                     "但不能再開子代理，也問不到使用者，所以任務要一次講清楚"),
     "properties": {"prompt": {"type": "string",
                               "description": "要子代理做的事，講清楚範圍與你要什麼結論"}},
     "required": ["prompt"]},
    {"name": "load_skill", "needs": "skills",
     "description": ("把一份 skill 的完整步驟讀進來。系統提示裡只有名字與一行描述，"
                     "看到有現成做法適用於眼前的工作，就先載入再動手，不要自己重新發明流程"),
     "properties": {"name": {"type": "string", "description": "skill 的名字，見系統提示裡的清單"}},
     "required": ["name"]},
    {"name": "write_file", "needs": "write",
     "description": "在工作區建立新檔案。既有且非空的檔案請改用 edit_file，這裡不會覆寫",
     "properties": {"path": {"type": "string", "description": "相對於工作區的檔案路徑"},
                    "content": {"type": "string", "description": "完整內容"}},
     "required": ["path", "content"]},
    {"name": "delete_file", "needs": "write",
     "description": ("刪掉工作區裡的一個檔案。會先備份，所以還原得回來 —— "
                     "**要刪檔案一律用這支，不要用 run_shell 下 rm**（那條沒有備份）。"
                     "只刪單一檔案，不刪資料夾"),
     "properties": {"path": {"type": "string", "description": "相對於工作區的檔案路徑"}},
     "required": ["path"]},
    {"name": "edit_file", "needs": "write",
     "description": ("把檔案裡的一段文字換成另一段。old 必須與檔案內容完全一致（含縮排）"
                     "且在檔案裡只出現一次；出現多次就多帶幾行前後文，或設 replace_all。"
                     "read_file 每行開頭的「行號→」不是檔案內容，old 只寫 → 後面的部分。"
                     "**同一個檔案要改好幾個地方時，用 edits 一次送完**，不要一輪改一處"),
     "properties": {"path": {"type": "string", "description": "相對於工作區的檔案路徑"},
                    "old": {"type": "string", "description": "要被取代的原文，含縮排"},
                    "new": {"type": "string", "description": "取代後的內容"},
                    "replace_all": {"type": "boolean", "description": "取代全部出現的位置，預設 false"},
                    "edits": {"type": "array",
                              "description": "一次改多處：依序套用，有任何一組對不上就整個不寫。"
                                             "用了 edits 就不要再給 old / new",
                              "items": {"type": "object",
                                        "properties": {
                                            "old": {"type": "string", "description": "要被取代的原文，含縮排"},
                                            "new": {"type": "string", "description": "取代後的內容"},
                                            "replace_all": {"type": "boolean",
                                                            "description": "取代全部出現的位置，預設 false"}},
                                        "required": ["old", "new"]}}},
     "required": ["path"]},
]
