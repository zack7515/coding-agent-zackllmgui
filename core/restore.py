# -*- coding: utf-8 -*-
"""還原點：改檔案前備份，每則提示前照一張整個工作區的相。

兩層是刻意的。單筆（M／A）只有三支檔案工具會記，退得掉一個檔案；
檢查點（C）是 git 的 shadow commit，退得掉整輪 —— 包含 run_shell 改的東西。
"""

import contextlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from core import workspace
from core.workspace import BACKUP_DIR, WORKTREE_DIR, cur, ws_path, ws_rel, ws_root

JOURNAL = "journal.jsonl"          # 放在 BACKUP_DIR 底下

def backup_file(p: Path) -> str:
    """改檔案之前先留一份，介面上才有「還原」可以按。

    時間戳只到秒，同一秒內改同一個檔案兩次就會蓋掉前一份備份 ——
    模型連續改同一個檔案時這是常態，不是邊角情況。撞到就在後面加序號，
    第一份（也就是最原始的那一份）永遠留得住。
    """
    root = ws_root().resolve()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    rel = p.relative_to(root)
    for n in range(1, 1000):
        dst = root / BACKUP_DIR / stamp / rel
        if not dst.exists():
            break
        dst = root / BACKUP_DIR / f"{stamp}-{n}" / rel
        if not dst.exists():
            break
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(p, dst)
    return dst.relative_to(root).as_posix()


def journal_path() -> Path:
    return ws_root().resolve() / BACKUP_DIR / JOURNAL


def journal_add(tool: str, rel: str, backup: str, created: bool, **extra) -> str:
    """記一筆改檔案的操作。回傳這一筆的 id。

    寫失敗不能讓工具跟著失敗 —— 紀錄是為了方便，不是為了正確性。
    """
    entry = {
        "id": f"{time.time():.6f}",
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "tool": tool, "path": rel, "backup": backup, "created": created,
        "chat": workspace.cur_chat(), **extra,
    }
    try:
        f = journal_path()
        f.parent.mkdir(parents=True, exist_ok=True)
        with f.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return entry["id"]


def journal_read() -> list:
    f = journal_path()
    if not f.is_file():
        return []
    out = []
    for line in f.read_text("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def journal_retitle(entry_id: str, note: str, msg: int, chat: str) -> bool:
    """把某一列的標題換成新的提示。跳過檢查點時用。

    整份重寫。journal 是一則對話幾十列的東西，而這件事一輪最多發生一次。
    """
    rows = journal_read()
    hit = False
    for e in rows:
        if e.get("id") == entry_id:
            e["path"], e["msg"], e["chat"] = note, msg, chat
            hit = True
    if not hit:
        return False
    try:
        journal_path().write_text(
            "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in rows), "utf-8")
    except OSError:
        return False
    return True


def journal_for(chat: str) -> list:
    """這則對話的還原點。有檢查點就一輪一列，沒有就退回「一次改檔案一列」。

    undo_count／other_chats 是給確認框用的：還原一定是照時間倒著做，退回某一點
    會連別則對話後來改的一起退掉，那要講出來。
    """
    entries = journal_read()
    rows = [e for e in entries if e.get("tree")] or entries
    out = []
    for i, e in enumerate(rows):
        if chat and e.get("chat") and e["chat"] != chat:
            continue
        extra = {}
        if e.get("tree"):
            # 下一個檢查點是**全部裡面**的下一個，不是這則對話的下一個：
            # 兩個檢查點之間改的就是那一輪改的，跟誰問的無關
            nxt = rows[i + 1]["tree"] if i + 1 < len(rows) else ""
            try:
                extra["files"] = ckpt_files(e["tree"], nxt)
            except Exception:
                extra["files"] = []
        rest = rows[i:]
        out.append(dict(e, **extra, undo_count=len(rest),
                        other_chats=sum(1 for x in rest
                                        if x.get("chat") and x.get("chat") != e.get("chat"))))
    return out


# ── 每則提示一個檢查點 ──────────────────────────────────────────── #
# 上面那套只有三支檔案工具會記，run_shell 改的一個字都沒進去。這裡一輪照一張相。
# 做法是 git 的 shadow commit：臨時 index 寫成 tree、commit-tree 成孤兒 commit、
# 用 ref 釘住。HEAD、分支、使用者的暫存區都沒動 —— 用 stash 或 checkout 會蓋掉。
# ponytail: 不是 git repo 就不照相，.gitignore 忽略的也不在快照裡。
CKPT_REF = "refs/zackllmgui/ckpt"
CKPT_SKIP = ("--", ".", ":!" + BACKUP_DIR, ":!" + WORKTREE_DIR)


def ws_is_git() -> bool:
    return cur().ws is not None and (cur().ws / ".git").exists()


@contextlib.contextmanager
def tmp_index():
    """在一份用完就丟的 index 上跑 git，使用者的 .git/index 不會被碰到。"""
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(tmp) / "index"))

        def run(*a, timeout: int = 120) -> str:
            proc = subprocess.run(["git"] + list(a), cwd=str(ws_root()), env=env,
                                  capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=timeout)
            if proc.returncode != 0:
                raise RuntimeError((proc.stderr or proc.stdout).strip()[:400] or "git 失敗")
            return proc.stdout.strip()

        yield run


def ckpt_msg(note: str, files: list) -> str:
    """不把提示、對話或本機絕對路徑寫進 git 物件。"""
    return f"工作區檢查點（{len(files)} 個檔案變更）" if files else "工作區檢查點"


def ckpt_files(tree: str, nxt: str = "") -> list:
    """這個檢查點之後改了哪些檔案。nxt 留空就比到現在的工作區。"""
    with tmp_index() as git:
        if not nxt:
            git("add", "-A", *CKPT_SKIP)
            nxt = git("write-tree")
        rows = git("diff", "--name-status", tree, nxt).splitlines()
    return [{"st": r.split("\t")[0][:1], "path": r.split("\t")[-1]}
            for r in rows if "\t" in r]


def checkpoint(note: str = "", msg: int = -1) -> dict:
    """照一張相。回傳 {"id":…} 或 {"skipped": 原因}。

    拍不到不能擋住使用者送出訊息，所以每條出路都是「跳過並說原因」，不丟例外。
    msg 是這一相對應到對話裡的第幾則訊息 —— 介面靠它把還原點指回那句話。
    """
    if cur().ws is None:
        return {"skipped": "還沒設定工作區"}
    if not ws_is_git():
        return {"skipped": "工作區不是 git repo，這則提示沒有檢查點"}
    try:
        with tmp_index() as git:
            git("add", "-A", *CKPT_SKIP)
            tree = git("write-tree")
            prev = next((e for e in reversed(journal_read()) if e.get("tree")), None)
            if prev and prev["tree"] == tree:
                # 樹一樣就不再多一個 git 物件，但標題要換成**這一則**提示 ——
                # 上一則既然沒改到任何東西，退回這裡等於退掉這一則，列上就該寫這一則。
                journal_retitle(prev["id"], " ".join(str(note or "").split())[:80],
                                msg, workspace.cur_chat())
                return {"skipped": "跟上一個檢查點一模一樣", "tree": tree,
                        "id": prev["id"], "retitled": True}
            # 訊息裡帶上一輪改了什麼：這一相拍的就是那一輪的結果
            done = ckpt_files(prev["tree"], tree) if prev else []
            sha = git("commit-tree", tree, "-m", ckpt_msg(note, done))
            git("update-ref", f"{CKPT_REF}/{sha[:12]}", sha)   # 沒 ref 釘著會被 gc 掃掉
    except Exception as e:
        return {"skipped": f"{type(e).__name__}: {e}"}
    # 提示留在 journal 裡（那份在 .zackllmgui-backup/，gitignore 掉了），
    # 但不進 commit message —— 介面靠它顯示標題並確認序號沒指錯人。
    return {"id": journal_add("checkpoint", " ".join(str(note or "").split())[:80],
                              "", False, tree=tree, commit=sha, msg=msg),
            "commit": sha, "tree": tree}


def restore_tree(tree: str) -> list:
    """把工作區變回這棵 tree 的樣子，多出來的檔案一起刪掉。回傳變動的檔名。"""
    with tmp_index() as git:
        git("add", "-A", *CKPT_SKIP)
        now = git("write-tree")
        names = [ln.split("\t", 1)[-1]
                 for ln in git("diff", "--name-status", now, tree).splitlines() if ln.strip()]
        # 兩棵樹的 read-tree -m -u 會連多出來的檔案一起刪掉，
        # 那是 `git checkout <tree> -- .` 做不到的那一半
        git("read-tree", "-m", "-u", now, tree)
    # ponytail: 只動工作區不動 index，所以這輪 git add 過的新檔會變成
    #           「暫存區有、磁碟沒有」。要收拾得改寫人家的暫存區，那更糟。
    return names


def rewind_to(entry_id: str) -> dict:
    """把工作區退回「某一筆操作發生之前」的樣子。

    做法是把那一筆之後（含那一筆）的操作**反著做回去**：
    有備份就複製回來，是新建的檔案就刪掉。順序不能反 ——
    同一個檔案被改過三次時，只有從最新往回走才會停在正確的版本。
    """
    entries = journal_read()
    idx = next((i for i, e in enumerate(entries) if e.get("id") == entry_id), -1)
    if idx < 0:
        raise ValueError("找不到這個還原點")

    undone, failed = [], []
    if entries[idx].get("tree"):
        # 檢查點：整棵樹換回去。只有這一條退得掉 run_shell 改的東西
        try:
            names = restore_tree(entries[idx]["tree"])
            undone = [f"還原到檢查點（{len(names)} 個檔案）"] + names[:50]
        except Exception as ex:
            failed.append(f"檢查點還原失敗：{type(ex).__name__}: {ex}")
        keep = entries[:idx]
        try:
            journal_path().write_text(
                "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in keep),
                encoding="utf-8")
        except OSError:
            pass
        return {"undone": undone, "failed": failed, "entries": keep}

    for e in reversed(entries[idx:]):
        rel = e.get("path", "")
        try:
            target = ws_path(rel)
            if e.get("created"):
                if target.exists():
                    target.unlink()
                undone.append(f"刪除 {rel}（原本不存在）")
            elif e.get("backup"):
                restore_backup(e["backup"])
                undone.append(f"還原 {rel}")
            else:
                failed.append(f"{rel}：沒有備份可以還原")
        except Exception as ex:
            failed.append(f"{rel}：{type(ex).__name__}: {ex}")

    # 還原本身也是一次操作，記下來（但不記成可以再被 rewind 的項目）
    keep = entries[:idx]
    try:
        f = journal_path()
        f.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in keep),
                     encoding="utf-8")
    except OSError:
        pass
    return {"undone": undone, "failed": failed, "entries": keep}


def restore_backup(rel: str) -> str:
    root = ws_root().resolve()
    src = (root / rel).resolve()
    if root / BACKUP_DIR not in src.parents and (root / BACKUP_DIR) != src.parent:
        raise PermissionError("只能還原備份資料夾裡的檔案")
    if not src.exists():
        raise FileNotFoundError(f"找不到備份 {rel}")
    # .zackllmgui-backup/<時間戳>/<原本的相對路徑>
    parts = Path(rel).parts
    dest = ws_path(str(Path(*parts[2:])))
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return ws_rel(dest)
