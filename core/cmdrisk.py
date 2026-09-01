# -*- coding: utf-8 -*-
"""一行 shell 指令有多危險。純函式，不需要知道工作區在哪。

對外只有 command_risk()；canon() 是它的前處理，攤開來測比較好抓。
"""

import os
import re
import shlex

# cmd.exe 專屬的規則另外一組，只在 Windows 上併進來 —— 混在一起會誤殺
# `python3 -c "del cache[k]"`。獨立成常數是為了測試能在 Linux 上假裝成 Windows。
WINDOWS = os.name == "nt"


# 一定要擋下來的：備份救不回來的那種。
# block 要精準、risky 可以寬鬆 —— risky 錯殺只是多問一次，block 錯殺沒有按鈕救得回來。
# 所以 rm 那兩條綁在 SEG（一段指令的開頭），`git rm -rf build` 不算：那是 git 在刪。
SEG = r"(?:^|[;&|(`]\s*)(?:sudo\s+)?"
BLOCKED_CMDS = [
    (SEG + r"rm\s+(-[a-zA-Z]*\s+)*(/|/\*|~|~/|\$HOME)(\s|$)", "rm 掉根目錄或家目錄"),
    (SEG + r"rm\s+-[a-zA-Z]*(r[a-zA-Z]*f|f[a-zA-Z]*r)", "rm -rf（工作區裡的東西請改用 rm -r <路徑>，不要加 -f）"),
    (r"\bmkfs(\.|\s)", "格式化磁碟"),
    (r"\bdd\s+[^|]*of=/dev/", "dd 寫進裝置"),
    (r">\s*/dev/(sd|nvme|hd)", "覆寫磁碟裝置"),
    (r":\(\)\s*\{\s*:\|:&\s*\}\s*;\s*:", "fork bomb"),
    (r"\b(shutdown|reboot|halt|poweroff)\b", "關機或重開機"),
    (r"\bchmod\s+-R\s+777\s+/(\s|$)", "把根目錄權限打開"),
    (r"\b(userdel|groupdel|passwd)\b", "動到系統帳號"),
    (r"\bgit\s+push\b[^|;]*--force(?!-with-lease)", "強制推送（會覆蓋遠端歷史）"),
    (r"\bcurl\b[^|]*\|\s*(sudo\s+)?(ba)?sh", "把網路上的東西直接餵給 shell"),
    (r"\bwget\b[^|]*\|\s*(sudo\s+)?(ba)?sh", "把網路上的東西直接餵給 shell"),
    # PowerShell Core 在 Linux／macOS 也跑得動，而 Remove-Item 不會跟任何
    # POSIX 指令或英文字撞名 —— 所以它不放 Windows 那一組，兩邊都要擋。
    (r"\bRemove-Item\b(?=[^;&|]*\s-(?:Recurse|r)\b)(?=[^;&|]*\s-(?:Force|fo)\b)",
     "Remove-Item -Recurse -Force（工作區裡請拿掉 -Force）"),
]

# Windows 專屬：沙盒沒開時 run_shell 走 cmd，上面那幾條一條都打不到。
# 尺度跟 rm 那條一樣 —— 遞迴不擋，遞迴又不問才擋。
# POSIX 那幾條在 Windows 上不關掉：git-bash 與 WSL 裡 rm -rf 照樣有效。
WIN_BLOCKED = [
    (r"\b(rmdir|rd)\s+(/[a-z]+\s+)*/(s\s+(/[a-z]+\s+)*/q|q\s+(/[a-z]+\s+)*/s)\b",
     "rmdir /s /q（工作區裡的東西請改用 rmdir /s <路徑>，不要加 /q）"),
    (r"\bdel\s+(/[a-z]+\s+)*/s\b", "del /s（遞迴刪除，沒有備份救得回來）"),
    (r"\bformat\s+[a-z]:", "格式化磁碟"),
    (r"\bdiskpart\b", "磁碟分割工具"),
]

# 會改動環境但救得回來的：不擋，但確認卡要標紅，自動模式一定要問人。
# 第三欄 True＝「動的是檔案」：路徑全部落在工作區裡的話，「工作區內全自動」
# 那一檔可以不問（見 ws_scoped）。沒有第三欄的動的不是檔案，永遠要問。
RISKY_CMDS = [
    (r"\bsudo\b", "用 sudo 提權"),
    (r"\brm\b", "刪除檔案", True),
    (r"\bpip\s+(install|uninstall)|\bnpm\s+(i|install|uninstall)\b|\bconda\s+(install|remove)",
     "安裝或移除套件"),
    (r"\bapt(-get)?\s+(install|remove|purge)|\byum\s+(install|remove)", "動到系統套件"),
    (r"\bgit\s+(push|reset\s+--hard|clean\s+-[a-zA-Z]*f|checkout\s+--\s)", "動到 git 歷史或工作區"),
    (r"\bmv\b|\bchmod\b|\bchown\b", "搬動檔案或改權限", True),
    (r">\s*/(etc|usr|bin|boot|lib)", "寫進系統目錄"),
    (r"\bkill(all)?\b|\bpkill\b", "終止程序"),
    (r"\bRemove-Item\b", "刪除檔案（PowerShell）", True),
]

# rd 太像一般英文字，只在後面接旗標時才算
WIN_RISKY = [
    (r"\b(del|erase|rmdir)\s+\S|\brd\s+/", "刪除檔案（Windows）", True),
]


# 長旗標 ↔ 短旗標：canon 兩種寫法都補上去，規則寫成哪一種都比對得到。
# 只放認得的那幾組 —— 亂猜的話 `--one-file-system` 裡的 f 跟 r 會湊成假的 -rf。
FLAG_PAIRS = [("--recursive", "r"), ("--force", "f")]
# 這些指令後面第一個字是子指令（git push、pip install），不是要操作的東西。
SUBCOMMAND = {"sudo", "env", "git", "npm", "pnpm", "yarn", "pip", "pip3", "apt",
              "apt-get", "yum", "dnf", "conda", "docker", "cargo", "go"}
SEPARATOR = {";", "|", "||", "&&", "&"}


def risky_rules() -> list:
    """這台 shell 認得的風險規則。ws_scoped 要跟 command_risk 讀同一份。"""
    return RISKY_CMDS + (WIN_RISKY if WINDOWS else [])


def canon(command: str) -> str:
    """把一行指令重寫成固定的樣子：`指令 子指令 -併好的旗標 參數 --長旗標`。

    `rm -rf x`、`rm -r -f x`、`rm x -rf`、`rm --recursive --force x` 是同一件事，
    正規表示式看到的卻是四個樣子（實測 19 種寫法有 12 種掉一級）。順便脫引號 ——
    `dd of="/dev/sda"` 以前就是靠那對引號躲過去的。長旗標排最後面是因為
    `chmod -R 777 /` 那條要的是 `-R` 緊接著 777。

    ponytail: 一個 token 算一個字，`git -C /repo push`、`a&&b` 會算歪。所以
    command_risk 原字串與 canon 兩種都比對 —— canon 只會多抓不會少抓。
    """
    try:
        toks = shlex.split(command)
    except ValueError:
        return command                   # 引號沒配對，交給原字串那一輪
    out, seg = [], []

    def flush():
        if not seg:
            return
        head, rest = [seg[0]], seg[1:]
        while head[-1].lower() in SUBCOMMAND:
            nxt = next((t for t in rest if not t.startswith("-")), None)
            if nxt is None:
                break
            rest.remove(nxt)
            head.append(nxt)
        short, longs, args = set(), [], []
        for t in rest:
            if re.fullmatch(r"-[a-zA-Z]+", t):
                short |= set(t[1:])
            elif t.startswith("--"):
                longs.append(t)
            else:
                args.append(t)
        for lg, sh in FLAG_PAIRS:
            if lg in longs and sh not in short:
                short.add(sh)
            elif sh in short and lg not in longs:
                longs.append(lg)
        out.extend(head)
        if short:
            out.append("-" + "".join(sorted(short)))
        out.extend(args + longs)
        seg.clear()

    for t in toks:
        if t in SEPARATOR:
            flush()
            out.append(t)
        else:
            seg.append(t)
    flush()
    return " ".join(out)


def command_risk(command: str) -> tuple:
    """判斷一行指令的風險。回傳 ("block"|"risky"|"ok", 原因)。

    **擋的是打錯字與粗心，不是對手。** 決心要繞過正規表示式的人有的是寫法
    （`$IFS`、變數展開、寫成腳本再跑），那一層靠沙盒不是靠這裡。

    只有這一份判斷，前端的確認卡直接顯示它的結論 —— 寫兩份總有一份會過期。
    原字串與 canon() 都比對，取比較嚴的那個。
    """
    cmd = " ".join(str(command or "").split())
    forms = (cmd, canon(cmd))
    for pattern, why in BLOCKED_CMDS + (WIN_BLOCKED if WINDOWS else []):
        if any(re.search(pattern, f, re.I) for f in forms):
            return ("block", why)
    for pattern, why, *_ in risky_rules():
        if any(re.search(pattern, f, re.I) for f in forms):
            return ("risky", why)
    return ("ok", "")
