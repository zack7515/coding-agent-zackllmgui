# README 用的截圖

都是**真的跑起來截的**，沒有合成：`serve.py` 端在 8899、工作區放一份 wafer 標注資料，
用 geckodriver 的 HTTP 介面（不裝 selenium，只用 `urllib`）驅動 headless Firefox。

| 檔案 | 內容 | 視窗 |
|---|---|---|
| `main.png` | 主畫面：模型能力膠囊、思考模式五段、取樣與進階參數 | 1600×1000 |
| `agent-run.png` | 一輪全自動跑完：`write_file` → `run_shell`（`exit 0`）、答案 380 片，底下是「這則對話累計」 | 1600×1340 |
| `files.png` | 檔案分頁：工作區、「＋資料夾」，點開的檔案直接顯示內容 | 1600×1000 |
| `tools.png` | 功能與工具：本機工具、連網、沙盒（bwrap）、計畫模式、自動模式、驗證指令、子代理模型 | 1600×1000 |

**2026-09-01 四張全部重截。** 這一輪改到畫面的有兩處：檔案分頁多了「＋資料夾」，
輸入框上方那一條在沒東西跑的時候改顯示「這則對話累計 N 秒 · N 輪 · N 次工具」。
驅動腳本這次踩到三個坑，都寫在下面的「怎麼重截」裡。


## 路徑一律換成假的

截圖會進 GitHub，所以**不留真實的家目錄與檔名**。做法不是事後修圖，是**截圖前先改 DOM**：
走一遍所有文字節點與 `title`／`placeholder`／`value`，把真實路徑換成 `/home/dev/…`，
再按快門。改的是畫面不是資料，`serve.py` 那邊完全沒動。

示範資料本身是**產生出來的**：`wafer_001.json` … `wafer_012.json`，12 個 labelme 檔、
合計 380 個 `shapes`（固定亂數種子，重跑生得出同一份）。第一版用的是實際產線的檔案，重截時沒有留著，就照同樣的格式與總數重做一份 ——
模型看到的東西一模一樣，而檔名不再帶任何產線資訊。

截完記得確認一次：`document.body.textContent.indexOf('<你的使用者名稱>')` 要回 `-1`。

## 怎麼重截

```bash
python serve.py --no-browser --port 8899 --workspace <工作區> --sandbox
```

然後用 geckodriver 開 session、導到 `http://127.0.0.1:8899/`，**等 10 秒**讓 `/api/tags`
與 `/api/show` 回來（等不夠久就會截到「尚未連線」，`firefox --screenshot` 就是死在這一點：
它在 load 事件當下就按快門，等不到非同步的請求）。

`agent-run.png` 那張：視窗拉到 1340 高（不然聊天區只剩 574px），把工具卡片全部收折、
只留 `run_tests` 攤開，再把 `#scroll` 捲到底 —— 一張圖要同時看得到「跑了什麼」跟「結果」。

### 注入的 JS 看不到頁面的 `const`

Firefox 的 WebDriver 用 Xray wrapper 執行 `execute/sync`，那層只看得到 window 上的
**標準屬性**。頁面頂層的 `const S`／`function showTab` 住在 global lexical scope，
`window.__app` 是 expando —— 三者在注入的腳本裡全都是 `undefined`。
症狀是 `ReferenceError: S is not defined`，很容易誤判成「頁面沒載好」。

所以驅動一律走 DOM：`document.getElementById('tabFile').click()`、
選單項目用 `[...document.querySelectorAll('.menu button')].find(b => …)` 撈。

**別想抄捷徑寫 `localStorage` 再 reload。** `localStorage` 本身寫得進去（WebIDL
屬性，不受 Xray 影響），但頁面掛了 `beforeunload → saveConfig()` ——
reload 的時候舊頁面**先**把記憶體裡的設定存一次，剛寫進去的模型名就被蓋回去了。
症狀是狀態列顯示的仍然是原本那個模型，而且完全沒有錯誤訊息。
模型與自動模式照樣點選單：`#modelBtn` 一層，`#featBtn` →「自動模式」→ 檔位兩層。
自動模式選到「改檔案自動」以上時，選單那支 handler 會順手把寫入權一起打開。

### 「跑完了沒」要問 Ollama，不要問瀏覽器

第一版是每 10 秒 `execute/sync` 讀一次執行列的文字，看它從「第 N 輪」變成
「這則對話累計…」。結果整支卡死在那個請求上 —— 頁面在串流、markdown 一直重解，
`execute/sync` 就一直等不到空檔。**卡住跟「還在跑」在外面看起來一模一樣**，
只能靠 GPU 使用率 0% 才分得出來。

改成問 Ollama：`/api/ps` 的 `expires_at` 是 `keep_alive` 推出來的，每呼叫一次
就往後移一次。那個時間戳連續一分鐘沒動，就是這一輪結束了。整段等待完全不碰
瀏覽器，最後才進去按快門。

### snap 版的 geckodriver 殺不掉就換 port

`/snap/bin/geckodriver` 跑起來的行程，同一個使用者也可能 `kill` 到
`Permission denied`（snap 的 confinement）。上一輪留下來的會一直佔著 4444，
下一次開 session 收到的是 `session not created: Session is already started`。
不必跟它纏鬥 —— `geckodriver --port 4456` 換一個就好。

### 模型要挑塞得進 VRAM 的

`agent-run.png` 跟 `files.png` 得等模型真的跑完一輪才有東西可截，所以**先確認選的模型
整包進得了顯示卡**。溢出到 CPU 的話速度會掉到 1 tok/s 上下，一輪跑不完。

還有一件事：Ollama 預設只有一個槽位，而**網頁按停止不會讓那一端跟著停** —— 前端 abort
了、`serve.py` 整支砍掉、上游 socket 斷了，`llama-server` 照樣生到 EOS 為止，
期間所有請求都排在後面。要立刻拿回槽位只能 `sudo systemctl restart ollama`。
