# README 用的截圖

都是**真的跑起來截的**，沒有合成：`serve.py` 端在 8899、工作區放一份 wafer 標注資料，
用 geckodriver 的 HTTP 介面（不裝 selenium，只用 `urllib`）驅動 headless Firefox。

| 檔案 | 內容 | 視窗 |
|---|---|---|
| `main.png` | 主畫面：模型能力膠囊、思考模式五段、取樣與進階參數 | 1600×1000 |
| `agent-run.png` | 一輪全自動跑完：12 次工具、`5 passed`、答案 380 片 | 1600×1340 |
| `files.png` | 檔案分頁：工作區的檔案樹，點開直接看內容 | 1600×1000 |
| `tools.png` | 功能與工具：本機工具、連網、沙盒（bwrap）、計畫模式、自動模式、驗證指令、子代理模型 | 1600×1000 |

`main.png` 與 `tools.png` 是 2026-08-28 重截的 —— 功能選單從六列變七列（多了驗證指令、
子代理模型，允許規則改成有規則才出現），頂上也多了 VRAM 用量。
`agent-run.png` 與 `files.png` 還是第一次那一輪的，要重截得先讓模型真的再跑一次，見下面。


## 路徑一律換成假的

截圖會進 GitHub，所以**不留真實的家目錄與檔名**。做法不是事後修圖，是**截圖前先改 DOM**：
走一遍所有文字節點與 `title`／`placeholder`／`value`，把真實路徑換成 `/home/dev/…`，
再按快門。改的是畫面不是資料，`serve.py` 那邊完全沒動。

示範資料本身是**產生出來的**：`wafer_001.json` … `wafer_012.json`，12 個 labelme 檔、
合計 380 個 `shapes`。第一版用的是實際產線的檔案，重截時沒有留著，就照同樣的格式與總數重做一份 ——
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

### 模型要挑塞得進 VRAM 的

`agent-run.png` 跟 `files.png` 得等模型真的跑完一輪才有東西可截，所以**先確認選的模型
整包進得了顯示卡**。溢出到 CPU 的話速度會掉到 1 tok/s 上下，一輪跑不完。

還有一件事：Ollama 預設只有一個槽位，而**網頁按停止不會讓那一端跟著停** —— 前端 abort
了、`serve.py` 整支砍掉、上游 socket 斷了，`llama-server` 照樣生到 EOS 為止，
期間所有請求都排在後面。要立刻拿回槽位只能 `sudo systemctl restart ollama`。
