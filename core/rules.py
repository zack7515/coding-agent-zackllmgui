# -*- coding: utf-8 -*-
"""允許規則：把「這一次要不要問我」寫下來一次，不用每天重新點。

規則只從確認卡上的「以後都放行」長出來，介面沒有手動新增的表單 ——
一條規則的存在一定對應到「使用者當時看著某一次呼叫按了下去」。
"""

import fnmatch
import json
from pathlib import Path

from core.workspace import HERE, cur

RULES_FILE = ".zackllmgui-rules.json"   # 兩份都讀，專案的優先

# ══════════════════════ 允許規則 ══════════════════════ #
# 人真正想要的不是全有全無的三段，而是「pytest 一律放行、git commit 要問我、
# secrets/ 永遠不准碰」。規則檔把這種判斷寫下來一次。
#
# 順序（第一個成立的說了算）：
#   deny 規則 > 擋掉的危險指令 > 風險指令一律問 > allow 規則 > 自動模式
# allow **不能**蓋過風險指令：那條保證寫在文件上，不能被一個設定檔拿掉。

def rules_files() -> list:
    """[(範圍, 路徑)]。兩份都讀，專案的排在前面（第一條命中的說了算）。

    **不能寫成二選一。** skills 那邊踩過同一個坑：只要專案有了自己的一份，
    全域那份就整個消失 —— 使用者加了一條專案規則，結果全域的 deny 全部失效。
    """
    out = []
    if cur().ws is not None:
        out.append(("專案", cur().ws / RULES_FILE))
    here = HERE / RULES_FILE
    if not out or out[0][1].resolve() != here.resolve():
        out.append(("全域", here))
    return out


def rules_path(write: bool = False) -> Path:
    """要寫到哪一份：有工作區就寫專案的，沒有就寫全域的。"""
    files = rules_files()
    return files[0][1] if (write and files) else (HERE / RULES_FILE)


def rules_read_one(f: Path, scope: str) -> list:
    if not f.is_file():
        return []
    try:
        data = json.loads(f.read_text("utf-8", errors="replace"))
    except ValueError:
        return []          # 壞掉就當成沒有：規則是為了少按幾次，不能擋住整個程式
    out = []
    for r in (data.get("rules") if isinstance(data, dict) else data) or []:
        if not isinstance(r, dict):
            continue
        act = str(r.get("action", "")).lower()
        if act not in ("allow", "ask", "deny"):
            continue
        out.append({"tool": str(r.get("tool", "*")) or "*",
                    "pattern": str(r.get("pattern", "*")) or "*",
                    "action": act,
                    "note": str(r.get("note", ""))[:200],
                    "scope": scope})
    return out


def rules_load() -> list:
    out = []
    for scope, f in rules_files():
        out += rules_read_one(f, scope)
    # deny 一律排到最前面：第一條命中的說了算，禁止的不該被任何 allow 蓋掉
    return ([r for r in out if r["action"] == "deny"]
            + [r for r in out if r["action"] != "deny"])


def rules_save(rules: list, scope: str = "") -> None:
    """把某一個範圍的規則寫回它自己那一份檔案。"""
    for sc, f in rules_files():
        if scope and sc != scope:
            continue
        keep = [{k: v for k, v in r.items() if k != "scope"}
                for r in rules if r.get("scope", sc) == sc]
        if not keep and not f.is_file():
            continue
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"rules": keep}, ensure_ascii=False, indent=2) + "\n",
                     encoding="utf-8")


def rule_subject(name: str, args: dict) -> str:
    """這一次呼叫要拿什麼去比對樣式。

    指令類比指令本身、檔案類比路徑、連網類比網址 —— 都是使用者心裡
    「我要放行的是什麼」的那個東西。
    """
    if not isinstance(args, dict):
        return ""
    for key in ("command", "path", "url", "query", "target", "name"):
        if args.get(key):
            return str(args[key])
    return ""


def rule_match(name: str, args: dict) -> dict:
    """回傳命中的規則，沒有就回 None。第一條命中的說了算。"""
    subject = rule_subject(name, args)
    for r in rules_load():
        if not fnmatch.fnmatch(name, r["tool"]):
            continue
        pat = r["pattern"]
        # 路徑樣式常寫成 secrets/**，fnmatch 不認得 ** 的遞迴語意，補一個前綴比對
        if (fnmatch.fnmatch(subject, pat)
                or (pat.endswith("/**") and subject.startswith(pat[:-2]))
                or (pat.endswith("*") and subject.startswith(pat[:-1]))):
            return r
    return None
