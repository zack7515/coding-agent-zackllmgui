# sandbox — 把 `run_shell` 關進一個跑不出工作區的地方

`run_shell` 是唯一跑得出工作區的工具。檔案工具有 `ws_path()` 擋著，它沒有 ——
`cat ~/.ssh/id_rsa`、`curl` 把東西送出去，不關起來就都做得到。

**預設是關的。** 介面上是「功能與工具 → 沙盒執行」，或啟動時加 `--sandbox`。

```bash
python -m sandbox                    # 這台能用哪一種、擋不擋得住、多花多少時間
python -m sandbox --backend=container   # 或裸寫：python -m sandbox container
python -m sandbox --backend=container --image=gcc:14   # 換映像檔再驗一次
python -m sandbox --json
```

## 一個作業系統一種做法

能用的東西本來就不一樣，所以不是「一套包到底」，是一個平台一個後端：

| 平台 | 後端 | 要裝什麼 | 實測 |
|---|---|---|---|
| Linux | `bwrap`（bubblewrap） | `sudo apt install bubblewrap` | ✅ 這台全部通過 |
| macOS | `seatbelt`（內建 `sandbox-exec`） | 不用裝 | ⚠️ 沒有機器，未實測 |
| Windows | `container`（Docker Desktop） | Docker Desktop | ✅ Windows 11 / Docker 28.1.1 實測 |
| 跨平台 | `container`（docker / podman） | docker 或 podman | ✅ 這台全部通過 |

挑選順序寫在 `__init__.py` 的 `BACKENDS`：**核心層優先、容器墊底**。

Windows 11 的實測環境是 Python 3.12.7、Docker 28.1.1、NVIDIA RTX 3080。
`python -m sandbox` 的 8 項探測全部通過：容器啟動、工作區讀寫、一次性 rootfs、
憑證隔離、斷網、C/C++ 工具鏈紀錄與 GPU 可見性；三種短指令量到約 0.5–0.6 秒容器開銷。
預設 `python:3.13-slim` 沒有 C/C++ 工具鏈，所以該項通過代表「能正確回報沒有」，
不是映像檔內含編譯器。

## 為什麼核心層排在容器前面

差別是**有沒有換掉檔案系統**。

容器帶自己的 rootfs，所以裡面是映像檔的內容，不是你的機器 ——
`python:3.13-slim` 裡沒有 pytest、沒有 node、沒有 gcc、沒有 CUDA。
接上去的第一次實測就撞到 `No module named pytest`。

bwrap 與 seatbelt 是核心層的限制：程序還是跑在你的機器上，只是「不能寫工作區以外」
「看不到家目錄」「沒有網路」。工具鏈本來就在原地，GPU 驅動也在。

這台量到的差距：

| | bwrap | docker |
|---|---|---|
| 冷啟動 | **7 ms** | Linux 約 176 ms；Windows 約 600 ms |
| pytest / node / gcc | 宿主機有就有 | 映像檔沒有就沒有（預設 `python:3.13-slim` **三個都沒有**）|
| GPU | **自動接進去**，不用開關（見下） | 加 `--sandbox-gpu`，並準備 NVIDIA Container Toolkit + 工作負載需要的 CUDA 映像檔 |
| 記憶體／CPU 上限 | ❌ 沒有（要 cgroup） | ✅ 4g／4 cpu／512 pids |

所以容器不是「比較差」，是**換到的東西不同**：它多給資源上限與乾淨的環境，
代價是工具鏈要自己準備、慢一個數量級。

## 每個後端保證什麼

`describe()["isolation"]` 是機器可讀的版本，介面直接顯示它。

| | bwrap | seatbelt | container |
|---|---|---|---|
| 工作區可寫 | ✅ | ✅ | ✅ |
| 工作區以外寫不動 | ✅ 整台唯讀 | ✅ `deny file-write*` | ✅ 只掛工作區 |
| 讀不到家目錄 | ✅ tmpfs 蓋掉 | ❌ **讀得到** | ✅ |
| 沒有網路 | ✅ `--unshare-net` | ✅ `deny network*` | ✅ `--network none` |
| 資源上限 | ❌ | ❌ | ✅ |

**seatbelt 讀得到家目錄**這件事要講清楚：Seatbelt 擋的是寫入與網路，不是讀取。
要更嚴得寫 `(deny file-read* ...)` 的白名單，那會讓一堆正常的指令壞掉。
macOS 上在意讀取的話，用 `container`。

## 加一個後端

一個檔案，四個函式：

```python
NAME = "myjail"
OS = ("linux",)
KIND = "核心層（...）"

def available() -> str:      # 可用就回執行檔路徑，不可用回 ""
def why() -> str:            # 為什麼不可用（這句話會直接顯示在介面上）
def wrap(command, workspace, net=False, gpu=False, **_) -> list
def describe() -> dict       # name / kind / isolation / notes
```

然後加進 `__init__.py` 的 `BACKENDS`，位置就是偏好順序。
`python -m sandbox --backend=myjail` 會用同一組檢查驗它 ——
**跑得起來不等於擋得住**，那些檢查是真的執行指令去看結果，不是讀設定猜的。

## 已知擋不住什麼

- **資源耗盡**：bwrap 與 seatbelt 都沒有記憶體與 CPU 上限。`while true` 還是能把機器跑滿。
- **核心漏洞**：核心層沙盒與容器都共用同一個 kernel。
- **`--dev-bind /dev /dev`（gpu=True）**：GPU 看得到的同時，其他裝置節點也看得到。
  只在真的需要 GPU 時開。
- **工作區裡的東西**：沙盒擋的是「跑出去」，不是「在裡面亂搞」。
  工作區本來就是給它改的 —— 那一層靠備份與還原點（見 README 的「紀錄」分頁）。

整體的安全邊界（不只沙盒）寫在 [`../safety/README.md`](../safety/README.md)。


## GPU：換到別台機器還能用嗎

`/dev` 在沙盒裡一律是乾淨的假的，只有顯示卡的**字元裝置**單獨接回來
（`sandbox/bwrap.py` 的 `GPU_NODES`）：

| 節點 | 給誰用 | 驗過嗎 |
|---|---|---|
| `/dev/nvidia*` | NVIDIA CUDA | ✅ RTX 4070 SUPER、torch 2.13+cu130，沙盒內 `is_available()` 回 `True`、矩陣乘法跑得動 |
| `/dev/nvidia-caps/*` | NVIDIA MIG 切分 | ❌ 沒有 MIG 的卡可以驗 |
| `/dev/dri/*` | AMD／Intel 的 `card*`、`renderD*` | ⚠ 節點有接進去，但沒有 AMD／Intel 的卡可以跑實際運算 |
| `/dev/kfd` | AMD ROCm 的核心介面 | ❌ 沒有 AMD 卡可以驗。少了它 ROCm 一定不能用 |
| `/dev/dxg` | WSL2 接 Windows 的顯示卡 | ❌ 沒有 WSL 環境可以驗 |

**沒有卡的機器上這一步什麼都不做**（glob 是空的），不會報錯也不會變慢。

**換平台的話：**

- **Linux + NVIDIA** —— 直接可用，這就是驗過的組合。多張卡、不同型號都一樣，
  靠的是 glob 不是寫死的節點名。
- **Linux + AMD／Intel** —— 節點都列進去了，理論上可用。真的不行的話，
  先在沙盒裡跑 `ls /dev` 看少了什麼，再往 `GPU_NODES` 補一條。
- **WSL2** —— 同上，`/dev/dxg` 已經列進去；WSL 的驅動函式庫在 `/usr/lib/wsl`，
  而 bwrap 是把整個 `/` 唯讀掛進去的，所以那部分本來就看得到。
- **macOS** —— 走 `sandbox-exec`，那是**完全不同的機制**（沒有 `/dev` bind 這回事），
  Metal／MPS 目前沒有處理。
- **容器後端（Windows 或手動指定）** —— `--gpus all` 需要 NVIDIA Container Toolkit，
  沒裝的話 docker 會直接失敗，所以不無條件加；啟動時用 `--sandbox-gpu` 明確開啟。

**要自己補一條的話**：在 `GPU_NODES` 加 glob 就好，其他都不用動 ——
非字元裝置（例如 `/dev/dri/by-path` 那種符號連結目錄）會自動跳過。
