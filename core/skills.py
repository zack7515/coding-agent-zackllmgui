# -*- coding: utf-8 -*-
"""skills：一個資料夾一份 SKILL.md，格式與範本見 skills/README.md。

正文按需載入 —— 描述常駐在系統提示裡只要 240 token，正文全塞進去是幾千。
"""

import re
import subprocess
from pathlib import Path

from core.jobs import decode_output, kill_tree, process_group_kwargs
from core.workspace import HERE, cur
from tools.schemas import TOOL_SCHEMAS


SKILLS_DIR = "skills"    # serve.py 旁邊；工作區有自己的就用工作區的
SKILL_LIST_MAX = 30      # 系統提示裡最多列幾個 skill
SKILL_DESC_MAX = 120     # 每一則描述最多幾個字
SKILL_BODY_LIMIT = 8000

SKILL_CMD = re.compile(r"!`([^`\n]{1,200})`")
SKILL_CMD_MAX = 5          # 一份 skill 最多跑幾行
SKILL_CMD_OUT = 1500       # 每一行的輸出最多留幾個字


def skill_commands(body: str) -> list:
    """skill 正文裡寫成 !`指令` 的那幾行。"""
    return SKILL_CMD.findall(body)[:SKILL_CMD_MAX]


def skill_live(body: str, run, allowed, build) -> str:
    """把 !`指令` 換成它現在的輸出。

    SKILL.md 是靜態文字，但流程需要現場狀態（`git status`、有沒有 `.venv`）。
    這是一個新的執行入口，所以每一道關卡都走既有的：`auto_cmd_block()` 判能不能跑、
    `build_command()` 做風險檢查與沙盒包裝。run=False 見 `_tool_load_skill`。
    """
    if not run:
        return SKILL_CMD.sub(
            lambda m: f"`{m.group(1)}`（模型改得到這份 skill，不代跑指令）", body)
    cmds = skill_commands(body)
    if not cmds or cur().ws is None:
        return SKILL_CMD.sub(lambda m: f"`{m.group(1)}`（沒有工作區，沒有執行）", body)
    done = {}
    for cmd in cmds:
        if cmd in done:
            continue
        no = allowed(cmd)
        if no:
            done[cmd] = ("", no)
            continue
        try:
            argv, cwd, use_shell, _ = build("run_shell", {"command": cmd})
            # 用 Popen 而不是 subprocess.run：逾時的時候 run 只殺得到最上面那個 sh，
            # 真正在跑的孫子會活下來，而且沒有任何一支看得到它（見 kill_tree）。
            proc = subprocess.Popen(argv, shell=use_shell, cwd=cwd,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    **process_group_kwargs())
            try:
                out = proc.communicate(timeout=15)[0]
            except subprocess.TimeoutExpired:
                kill_tree(proc)
                # 說「沒有執行」是騙人的：它跑了十五秒，副作用早就發生了
                done[cmd] = ("", "跑超過 15 秒已中止，可能已經有副作用")
                continue
            # 只去頭尾的換行：porcelain 那種輸出前兩欄是空白，strip() 會把它吃掉
            done[cmd] = (decode_output(out, use_shell).strip("\r\n")[:SKILL_CMD_OUT], "")
        except Exception as e:
            done[cmd] = ("", f"{type(e).__name__}: {e}")

    def fill(m):
        cmd = m.group(1)
        out, why = done.get(cmd, ("", "超過一份 skill 能跑的行數"))
        if why:
            return f"`{cmd}`（沒有執行：{why}）"
        return f"`{cmd}` 的輸出：\n```\n{out}\n```"

    return SKILL_CMD.sub(fill, body)


# 一個資料夾一份 SKILL.md，格式與範本見 skills/README.md。
# 目前只給人用（在對話框打 / 叫出來），模型端的 load_skill 還沒接。

def skills_roots() -> list:
    """要掃的 skills 資料夾，順序是 [內建, 工作區]。

    兩邊都讀，同名時工作區的贏。**不能寫成二選一** ——
    那樣的話模型照 make-skill 在專案裡寫下第一份 skill 的瞬間，
    內建那六份會全部從清單上消失（踩過）。
    """
    roots = [HERE / SKILLS_DIR]
    if cur().ws is not None:
        ws = cur().ws / SKILLS_DIR
        if ws.is_dir() and ws.resolve() != (HERE / SKILLS_DIR).resolve():
            roots.append(ws)
    return [r for r in roots if r.is_dir()]


def parse_skill(md: str) -> tuple:
    """回傳 (中繼資料, 正文)。格式壞掉就丟 ValueError。"""
    if not md.startswith("---"):
        raise ValueError("開頭要有 --- 夾起來的中繼資料")
    end = md.find("\n---", 3)
    if end < 0:
        raise ValueError("中繼資料沒有結尾的 ---")
    meta = {}
    for line in md[3:end].strip().splitlines():
        if ":" in line and not line.lstrip().startswith("#"):
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, md[end + 4:].strip()


def skills_list() -> list:
    """所有可用的 skill，只讀中繼資料（正文要另外拿）。同名時工作區的蓋掉內建的。"""
    found = {}
    for root in skills_roots():
        scope = "專案" if root != HERE / SKILLS_DIR else "內建"
        for folder in sorted(root.iterdir()):
            if not folder.is_dir() or folder.name.startswith("_"):
                continue
            f = folder / "SKILL.md"
            if not f.is_file():
                continue
            try:
                meta, _ = parse_skill(f.read_text("utf-8", errors="replace"))
            except ValueError:
                continue
            if meta.get("name") and meta.get("description"):
                found[meta["name"]] = {
                    "name": meta["name"], "description": meta["description"],
                    "scope": scope,
                    "tools": [t.strip() for t in meta.get("tools", "").split(",") if t.strip()]}
    return [found[k] for k in sorted(found)]


def skills_usable(have) -> list:
    """現在這個狀態下真的用得動的 skill。

    `tools:` 用來**篩清單**不是限制工具：skill 是流程說明不是沙盒，做到一半被擋住
    比多給幾支工具糟。工作區唯讀時列一份要 write_file 的 skill 只會帶進死路。
    只管清單，`load_skill` 指名還是叫得到；認不得的工具名（MCP 的）不算數。
    """
    known = {t["name"] for t in TOOL_SCHEMAS}
    return [s for s in skills_list()
            if all(t in have for t in s["tools"] if t in known)]


def skill_find(name: str) -> tuple:
    """回傳 (資料夾, 正文)。**資料夾決定它有沒有資格執行指令**，見 skill_trusted。"""
    clean = str(name or "").strip()
    # 反著找：工作區的優先，跟 skills_list() 的覆蓋規則一致
    for root in reversed(skills_roots()):
        folder = root / clean
        # 名稱只當資料夾名用，不接受路徑；底線開頭的是範本，不給讀。
        # 比對 Path(clean).name 而不是 folder.parent —— 後者是**字面**比對，
        # (root/"..").parent 就等於 root，`..` 一路讀到 skills/ 的上一層去。
        if (not clean or clean != Path(clean).name or clean.startswith("_")
                or not (folder / "SKILL.md").is_file()):
            continue
        _, body = parse_skill((folder / "SKILL.md").read_text("utf-8", errors="replace"))
        return folder, body[:SKILL_BODY_LIMIT]
    raise ValueError(f"沒有這個 skill：{name}")


def skill_body(name: str) -> str:
    return skill_find(name)[1]


def skill_trusted(folder: Path) -> bool:
    """這份 skill 的檔案，模型自己改得到嗎？改得到就不准它跑指令。

    問的是「在不在工作區裡」，不是「在不在內建資料夾裡」—— 預設工作區就是
    `os.getcwd()`，那兩個會是同一個資料夾，用後者等於這道關卡沒開。
    代價是工作區設在 checkout 上時內建那幾份也不跑，那是對的：模型確實改得動。
    """
    try:
        folder = folder.resolve()
        ws = cur().ws
        if ws is None:
            return True
        folder.relative_to(ws.resolve())    # 沒丟例外＝在工作區裡面
        return False
    except ValueError:
        return True
    except OSError:
        return False
