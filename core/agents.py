# -*- coding: utf-8 -*-
"""子代理：一種一個 agents/*.md，加一種不必改程式。

工具白名單由 agent_guard() 在伺服器擋 —— 網頁那層只是「不要讓它看到」。
深度上限是機制不是提示詞：這裡的前提是放著跑三十分鐘沒人看。
"""

import contextlib
import os
import shutil
import subprocess
import time
from pathlib import Path

from core.jobs import JOBS, JOBS_LOCK, kill_tree
from core.skills import parse_skill
from core.workspace import (HERE, WORKTREE_DIR, WORKTREE_LINK, WORKTREE_MAX, WORKTREE_SKIP,
                            _CUR, Session, cur, ws_root)


AGENTS_DIR = "agents"              # 子代理型別，一種一個 .md，規則同上
AGENT_BODY_LIMIT = 4000


# 一種子代理是一個檔案，不是一段程式碼：加一種寫份 md 丟進 agents/ 就好。
# 每一種自己宣告拿得到哪些工具 —— 唯讀靠工具清單擋，不是靠提示詞求它別寫。
# 跟那些商用 agent 不一樣的一點：這裡**一定要有深度上限**。它們的煞車是提示詞，
# 因為有人在看著帳單；這裡的前提是放著跑三十分鐘沒人看，所以要機制。


def agents_roots() -> list:
    """要掃的 agents 資料夾，順序是 [內建, 工作區]。同名時工作區的贏，規則同 skills。"""
    roots = [HERE / AGENTS_DIR]
    if cur().ws is not None:
        ws = cur().ws / AGENTS_DIR
        if ws.is_dir() and ws.resolve() != (HERE / AGENTS_DIR).resolve():
            roots.append(ws)
    return [r for r in roots if r.is_dir()]


def agent_types() -> list:
    """可用的子代理型別。tools 是 ["*"] 代表「除了永遠不給的以外都給」。"""
    found = {}
    for root in agents_roots():
        scope = "專案" if root.resolve() != (HERE / AGENTS_DIR).resolve() else "內建"
        for f in sorted(root.glob("*.md")):
            if f.name.startswith("_"):
                continue
            try:
                meta, body = parse_skill(f.read_text("utf-8", errors="replace"))
            except (ValueError, OSError):
                continue
            if not meta.get("description"):
                continue
            name = (meta.get("name") or f.stem).strip()
            tools = [t.strip() for t in meta.get("tools", "").split(",") if t.strip()]
            found[name] = {
                "name": name, "description": meta["description"], "scope": scope,
                "tools": tools or ["*"],
                "isolation": meta.get("isolation", "").strip(),
                "model": meta.get("model", "").strip(),
                "prompt": body[:AGENT_BODY_LIMIT],
            }
    return [found[k] for k in sorted(found)]


def git_at(root: Path, *args) -> str:
    """在指定的資料夾跑 git。跟 git_run() 不同：那一支固定跑在工作區根目錄，
    這一支要能指到 worktree 或主 repo 兩邊。"""
    # quotepath=false：不然中文檔名會變成 "\345\255..." 一路送到畫面上
    p = subprocess.run(["git", "-c", "core.quotepath=false", "-C", str(root)] + list(args),
                       capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout).strip()[:400] or "git 失敗")
    return p.stdout


AGENT_NEVER = ("ask_user_question", "todo_write")
# 前者問了也沒人看得懂上下文；後者是主代理那一條線的待辦，子代理跟它同一個 Session，
# 寫進去會真的把清單蓋掉。型別檔寫進 tools 也沒用 —— 這一條在伺服器擋。
SUB_DEPTH_MAX = 2     # 子代理再開子代理的層數上限。網頁那一層也擋，但真正算數的是這裡


def agent_open(type_name: str = "", parent: str = "", chat: str = "",
               task: str = "") -> dict:
    """登記一個子代理。**每一種都要登記，不只隔離型的。**

    為什麼不只在需要 worktree 時才登記：工具白名單如果只靠網頁「不送那幾支定義」，
    模型幻覺出一個工具名就繞過去了 —— 送到 /tool 的是名字，伺服器不知道這是誰在叫。
    登記之後 agent_guard() 才擋得住，那才是規則；網頁那一層只是「不要讓它看到」。
    """
    types = {t["name"]: t for t in agent_types()}
    t = types.get(str(type_name or "")) or (agent_types()[0] if types else None)
    if t is None:
        raise ValueError("agents/ 裡沒有任何子代理型別")
    s = cur()
    up = s.agents.get(str(parent)) if parent else None
    if parent and up is None:
        raise ValueError(f"沒有這個上層子代理：{parent}")
    if up and up.get("stopped"):
        raise PermissionError(f"上層子代理 {parent} 已經被中斷，不能再開下一層")
    depth = (up["depth"] + 1) if up else 1
    if depth > SUB_DEPTH_MAX:
        raise PermissionError(f"子代理最多 {SUB_DEPTH_MAX} 層，這是第 {depth} 層")
    if len(s.agents) >= WORKTREE_MAX:
        raise RuntimeError(f"同時最多 {WORKTREE_MAX} 個子代理，先收掉沒在用的")

    aid = f"a{int(time.time() * 1000) % 100000000:08d}{len(s.agents)}"
    ws = up["ws"] if up else ws_root().resolve()
    rec = {"id": aid, "type": t["name"], "tools": list(t["tools"]),
           "isolation": "", "ws": ws, "branch": "", "root": None, "linked": [],
           "parent": str(parent or ""), "depth": depth, "chat": str(chat or "")[:64],
           "started": time.time(), "calls": 0, "last": None,
           "stopped": False, "why": "", "task": str(task or "")[:200]}
    # 下一層跑在上一層的 worktree 裡：它是同一件工作的細分，而各開一份的話
    # 下一層是從 HEAD 開出來的，看不到上一層還沒提交的修改。
    if t["isolation"] == "worktree" and not (up and up["isolation"]):
        info = worktree_add()
        rec.update(isolation="worktree", ws=info["ws"], branch=info["branch"],
                   root=info["root"], linked=info["linked"])
    elif up and up["isolation"]:
        rec.update(isolation="inherited", branch=up["branch"], root=up["root"])
    s.agents[aid] = rec
    return agent_view(rec)


def worktree_add() -> dict:
    """給子代理一份自己的 git worktree。

    兩個會改檔案的子代理平行跑時，原本只有「不要平行」一條路；各給一份 checkout
    之後衝突變成 merge 問題，而 merge 有現成工具。邊界照舊由 ws_path() 一支擋
    （root 換成 worktree），不必再寫第二個路徑檢查。
    """
    root = ws_root().resolve()
    if not (root / ".git").exists():
        raise RuntimeError("這個工作區不是 git 儲存庫，給不了獨立的 worktree")
    tag = f"{int(time.time() * 1000) % 100000000:08d}"
    dst = root / WORKTREE_DIR / tag
    branch = f"zackllmgui/{tag}"
    # 主 worktree 不該把這個資料夾看成未追蹤的檔案。寫 .git/info/exclude 而不是
    # .gitignore：那是使用者的檔案，我們不動它。
    try:
        ex = Path(git_at(root, "rev-parse", "--git-common-dir").strip())
        if not ex.is_absolute():
            ex = root / ex
        ex = ex / "info" / "exclude"
        ex.parent.mkdir(parents=True, exist_ok=True)
        line = WORKTREE_DIR + "/\n"
        had = ex.read_text("utf-8", errors="replace") if ex.is_file() else ""
        if line not in had:
            with ex.open("a", encoding="utf-8") as fh:
                fh.write(line)
    except Exception:
        pass          # 沒寫成功只是主目錄會多一筆未追蹤，不影響隔離本身
    git_at(root, "worktree", "add", "-b", branch, str(dst), "HEAD")
    # 沒進版控的東西不會跟過來，而 node_modules 重建一次要幾分鐘、還多佔一份磁碟。
    # **連過去等於共用**：子代理在裡面 npm install 會動到主專案那一份，
    # 兩個子代理同時裝也會互相蓋。agents/work.md 有告訴它不要自己裝。
    linked = []
    for name in WORKTREE_LINK:
        src, at = root / name, dst / name
        if not src.is_dir() or at.exists() or at.is_symlink():
            continue          # 有進版控的話 checkout 裡已經有了，不能蓋掉真的那一份
        try:
            os.symlink(src, at, target_is_directory=True)
            linked.append(name)
        except Exception:
            pass              # Windows 沒權限就算了：子代理自己會發現裝不起來
    return {"ws": dst.resolve(), "branch": branch, "root": root, "linked": linked}


def branch_unique(root: Path, branch: str) -> int:
    """這個分支上有幾個主 HEAD 沒有的 commit。**刪分支前要問的唯一問題。**

    「工作目錄乾淨」不等於「分支上沒東西」—— 子代理自己 commit 過、或是上一次收的
    時候幫它 commit 過，工作目錄都會是乾淨的。只看乾不乾淨就刪分支會把成果刪掉。
    """
    try:
        return len([ln for ln in git_at(root, "rev-list", branch, "^HEAD").splitlines()
                    if ln.strip()])
    except Exception:
        return 1                 # 問不出來就當它有東西：不刪比刪錯好


def agent_commit_msg(rec: dict) -> str:
    return f"子代理 {rec['id']}（{rec['type']}）：{rec.get('task') or '沒有說明'}"


def worktree_orphans() -> list:
    """磁碟上有、但這個分頁的登記裡沒有的 worktree。

    不必另外存狀態，分支名 `zackllmgui/<tag>` 就是登記。Session.agents 活在行程裡，
    serve.py 重啟之後那幾份就沒人認得、也就收不掉，這一支把它們找回來。
    只列不刪：「沒有人認得」不等於「可以刪」。
    """
    root = ws_root().resolve()
    if not (root / ".git").exists():
        return []
    live = {r["branch"] for r in cur().agents.values() if r["branch"]}
    try:
        blocks = git_at(root, "worktree", "list", "--porcelain").split("\n\n")
    except Exception:
        return []
    out = []
    for block in blocks:
        info = dict(ln.split(" ", 1) for ln in block.splitlines() if " " in ln)
        branch = info.get("branch", "").replace("refs/heads/", "", 1)
        path = info.get("worktree", "")
        if not branch.startswith("zackllmgui/") or branch in live:
            continue
        rec = {"id": "w" + branch.split("/", 1)[1], "branch": branch,
               "path": path, "changes": 0, "gone": not Path(path).is_dir(),
               "msg": "", "commits": branch_unique(root, branch), "secs": 0}
        try:
            # 分支上有自己的 commit 才拿它的訊息 —— 沒有的話那是開分支時的
            # 那個 base commit，講的是別人的事
            if rec["commits"]:
                rec["msg"] = git_at(root, "log", "-1", "--format=%s", branch).strip()[:200]
            rec["secs"] = int(time.time() - Path(path).stat().st_mtime)
        except Exception:
            pass
        try:
            rec["changes"] = len([
                ln for ln in git_at(Path(path), "status", "--porcelain").splitlines()
                if ln.strip()
                and not ln[3:].strip('"').startswith(WORKTREE_SKIP)])
        except Exception:
            pass
        out.append(rec)
    return out


def orphan_rec(aid: str) -> dict:
    """把一筆孤兒 worktree 補成 agent_close() 看得懂的樣子。

    它是死的（沒有 tools、沒有 chat），只夠拿來收 —— 收掉正是唯一還能對它做的事。
    """
    root = ws_root().resolve()
    for o in worktree_orphans():
        if o["id"] == str(aid):
            return {"id": o["id"], "type": "orphan", "tools": [], "isolation": "worktree",
                    "ws": Path(o["path"]), "branch": o["branch"], "root": root,
                    "parent": "", "depth": 1, "chat": "", "started": time.time(),
                    "calls": 0, "last": None, "stopped": True,
                    "why": "沒人認得的 worktree", "task": o["msg"] or "serve.py 重啟前留下的"}
    return None


def agent_view(rec: dict) -> dict:
    """給網頁看的樣子。Path 不能直接進 JSON，而且要看得出它現在在幹嘛。"""
    return {"id": rec["id"], "type": rec["type"], "tools": rec["tools"],
            "isolation": rec["isolation"], "path": str(rec["ws"]),
            "branch": rec["branch"], "linked": rec.get("linked", []),
            "parent": rec["parent"], "depth": rec["depth"],
            "chat": rec["chat"], "secs": int(time.time() - rec["started"]),
            "calls": rec["calls"], "last": rec["last"],
            "stopped": rec["stopped"], "why": rec["why"],
            "jobs": [j["id"] for j in jobs_of(rec["id"])]}


def agent_kin(aid: str) -> list:
    """這個子代理與它底下的所有後代。中斷要連根拔，不是只停自己。"""
    s = cur()
    out = []
    todo = [str(aid)]
    while todo:
        cur_id = todo.pop()
        rec = s.agents.get(cur_id)
        if rec is None or rec in out:
            continue
        out.append(rec)
        todo += [k for k, v in s.agents.items() if v["parent"] == cur_id]
    return out


def agent_chain(aid: str) -> list:
    """從這個子代理往上走到根。**追溯根源用的就是這一支。**"""
    s = cur()
    out = []
    seen = set()
    node = s.agents.get(str(aid))
    while node is not None and node["id"] not in seen:
        seen.add(node["id"])
        out.append(agent_view(node))
        node = s.agents.get(node["parent"]) if node["parent"] else None
    return out


def jobs_of(aid: str) -> list:
    with JOBS_LOCK:
        return [j for j in JOBS.values() if j.get("agent") == str(aid)]


def agent_stop(aid: str, why: str = "") -> dict:
    """依 id 中斷：自己、所有後代，以及它們丟到背景的指令。

    **這一支是規則不是提示。** 標記之後，任何綁在這些 id 上的呼叫都會被
    agent_guard() 直接拒絕 —— 就算網頁那一端沒收到、或根本不理，也叫不動工具了。
    背景指令活在這個行程裡，所以連它們一起殺，不然「中斷」只中斷了一半。
    """
    kin = agent_kin(aid)
    if not kin:
        raise ValueError(f"沒有這個子代理：{aid}")
    killed = []
    for rec in kin:
        rec["stopped"] = True
        rec["why"] = str(why or "使用者中斷")[:200]
        for job in jobs_of(rec["id"]):
            if job["code"] is None and job.get("proc") is not None:
                kill_tree(job["proc"])
                killed.append(job["id"])
    return {"stopped": [r["id"] for r in kin], "jobs": killed,
            "why": kin[0]["why"]}


def agent_trace(aid: str) -> dict:
    """給一個 id，說清楚它是什麼、誰開的、現在在跑什麼、丟了哪些背景指令。"""
    chain = agent_chain(aid)
    if not chain:
        raise ValueError(f"沒有這個子代理：{aid}")
    return {"agent": chain[0], "chain": chain,
            "children": [agent_view(r) for r in agent_kin(aid) if r["id"] != str(aid)],
            "jobs": [{"id": j["id"], "cmd": j["cmd"], "code": j["code"],
                      "secs": int((j["ended"] or time.time()) - j["started"])}
                     for j in jobs_of(aid)]}


def agent_close(aid: str, force: bool = False) -> dict:
    """收掉一個子代理（連同它底下沒收的後代）。

    **有改動就先 commit 到自己的分支，再收掉目錄。** 不 commit 的話那些改動只是
    未追蹤檔案：分支是空的、`git merge` 沒東西可合，目錄一旦沒人認得就只能留著。
    落到分支上之後，「收掉目錄」與「留住成果」不再是二選一。
    """
    s = cur()
    rec = s.agents.get(str(aid)) or orphan_rec(str(aid))
    if rec is None:
        raise ValueError(f"沒有這個子代理：{aid}")
    for kid in [r for r in agent_kin(aid) if r["id"] != str(aid)]:
        s.agents.pop(kid["id"], None)
    out = {"id": str(aid), "branch": rec["branch"], "path": str(rec["ws"]),
           "kept": False, "changes": 0, "stat": "", "committed": False,
           "commits": 0, "merge": ""}
    if rec["isolation"] != "worktree":
        s.agents.pop(str(aid), None)
        return out
    try:
        # 自己的備份目錄與巢狀 worktree 不算「子代理做的事」——
        # 算進去的話每一份 worktree 都會回報有改動，那個訊號就沒有意義了
        # 連過去的 node_modules 是**符號連結**不是資料夾，所以 .gitignore 裡的
        # `node_modules/`（尾巴有斜線＝只比對資料夾）比對不到它，git 會回報 ?? node_modules。
        # 不擋掉的話每一份 worktree 都會回報「有改動」，還會把一條斷掉的連結 commit 進分支。
        lines = [ln for ln in git_at(rec["ws"], "status", "--porcelain").splitlines()
                 if ln.strip()
                 and not ln[3:].strip('"').startswith(WORKTREE_SKIP)]
    except Exception:
        lines = []
    if lines:
        out["changes"] = len(lines)
        out["stat"] = "\n".join(lines)[:2000]
        try:
            # 只收子代理做的事：自己的備份目錄與巢狀 worktree 不算，
            # 掃進去的話合併過來會把我們的內部檔案倒進使用者的專案
            git_at(rec["ws"], "add", "-A", "--", ".",
                   *[f":(exclude){d}" for d in WORKTREE_SKIP])
            git_at(rec["ws"], "commit", "-q", "-m", agent_commit_msg(rec))
            out["committed"] = True
            out["merge"] = f"git merge {rec['branch']}"
        except Exception as e:
            # commit 不進去（例如這台 git 連身分都沒設）就退回舊行為：整份留著。
            # 寧可讓資料夾積在專案裡，也不能把改動丟掉 —— 除非呼叫的人指名要丟。
            if not force:
                out["kept"] = True
                out["error"] = f"改動 commit 不進去，先留著：{e}"
                s.agents.pop(str(aid), None)
                return out
    out["commits"] = branch_unique(rec["root"], rec["branch"])
    if out["commits"]:
        # 主代理只拿到一個分支名的話，要收不收沒有依據。commit 之後 diff 才算得出來
        try:
            out["diff"] = git_at(rec["root"], "diff", "--stat",
                                 f"HEAD...{rec['branch']}").strip()[:2000]
        except Exception:
            pass
    try:
        if Path(rec["ws"]).is_dir():
            git_at(rec["root"], "worktree", "remove", "--force", str(rec["ws"]))
        else:
            git_at(rec["root"], "worktree", "prune")     # 資料夾被手動刪掉的情況
        if not out["commits"]:
            git_at(rec["root"], "branch", "-D", rec["branch"])
        elif not out["merge"]:
            out["merge"] = f"git merge {rec['branch']}"
    except Exception as e:
        out["kept"] = True
        out["error"] = str(e)
    s.agents.pop(str(aid), None)
    return out


@contextlib.contextmanager
def as_agent(aid: str):
    """只在**跑工具的那一段**切到子代理的身分。

    回應裡的 todos／plan／tool_defs 仍然要是分頁自己的 —— 子代理的 Session 是新的，
    待辦是空的，切過去不切回來會讓網頁上的待辦清單整個消失。
    """
    was_s = getattr(_CUR, "s", None)
    was_a = getattr(_CUR, "agent", None)
    try:
        bind_agent(aid)
        yield
    finally:
        _CUR.s, _CUR.agent = was_s, was_a


def bind_agent(aid: str) -> None:
    """把這個請求切到某個子代理的身分（工作區 + 工具白名單）。

    只認 Session 自己開過的 id —— 路徑是伺服器產生的，不是請求帶進來的，
    所以網頁那端沒辦法靠這條路指到任意資料夾。
    """
    if not aid:
        _CUR.agent = None
        return
    s = cur()
    rec = s.agents.get(str(aid))
    if rec is None:
        raise ValueError(f"沒有這個子代理：{aid}（可能已經收掉了）")
    sub = Session(s)                 # 繼承 write
    sub.ws = rec["ws"]
    sub.auto = s.auto
    sub.agents = s.agents            # 讓下一層還找得到
    _CUR.s = sub
    _CUR.agent = rec


def agent_guard(name: str) -> None:
    """綁在子代理身上的呼叫，工具白名單由這裡擋。

    **兩層是刻意的，不是重複**：網頁那一層決定「不要讓模型看到它不該用的工具」，
    這一層決定「就算它硬叫也叫不動」。只有前者的話，模型幻覺出一個工具名就過去了——
    送到 /tool 的只是一個字串，伺服器原本無從知道是誰在叫。
    """
    rec = getattr(_CUR, "agent", None)
    if not rec:
        return
    if rec["stopped"]:
        raise PermissionError(f"子代理 {rec['id']} 已經被中斷（{rec['why']}），不再執行任何工具")
    if name in AGENT_NEVER:
        raise PermissionError(f"子代理不能用 {name}")
    tools = rec["tools"] or ["*"]
    if "*" not in tools and name not in tools:
        raise PermissionError(
            f"子代理型別「{rec['type']}」拿不到 {name}（它的工具是：{'、'.join(tools)}）")
    rec["calls"] += 1
    rec["last"] = {"tool": name, "at": time.time()}
