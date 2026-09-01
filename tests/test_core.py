# -*- coding: utf-8 -*-
"""core/ 各模組的介面測試。

跟 test_serve.py 的分工：那一份走 HTTP，驗的是「整條路通不通」；
這一份直接呼叫，驗的是**拆出去時新長出來的那些參數**。
repo_map(files, rel)、skills_usable(have)、skill_live(body, run, allowed, build)
這幾個介面是為了不讓 core 反過來 import serve 才有的，只有 serve.py 的
薄殼在用 —— 沒有人直接驗過它們，換句話說那幾個參數傳錯了也不會有測試變紅。

    python -m pytest tests/test_core.py -q
"""

import os
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import cmdrisk, repomap, skills, sysinfo, workspace   # noqa: E402
from sandbox import container   # noqa: E402


# ── repo_map：拆出去之後改成收 (files, rel) ────────────────────────── #

def test_repo_map_takes_files_and_rel_from_outside():
    """地圖不自己走檔案系統，走訪與轉相對路徑都由呼叫端給。

    這是拆出去時改的介面：原本它直接用 ws_walk()／ws_rel()，
    那樣 repomap 就要認識工作區。現在傳什麼就畫什麼。
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "calc.py").write_text(
            "def add(a, b):\n    return a + b\n\n\nclass Sum:\n    pass\n",
            encoding="utf-8")
        (root / "app.js").write_text(
            "function boot() {}\nconst run = () => {};\nconst PORT = 8080;\n",
            encoding="utf-8")
        (root / "notes.txt").write_text("沒有符號可抓\n", encoding="utf-8")

        files = sorted(root.iterdir())
        out = repomap.repo_map(files, lambda p: str(p.relative_to(root)))

        assert "calc.py：add, Sum" in out, out
        # 抓的是「可以呼叫的東西」：箭頭函式算，純數值不算
        assert "app.js：boot, run" in out, out
        assert "PORT" not in out, "常數值不該進地圖，那是雜訊"
        assert "notes.txt" in out and "notes.txt：" not in out, "抓不到符號就只列檔名"
        assert repomap.repo_map([], lambda p: "") == "", "沒有檔案就不要生一份空標題出來"


def test_repo_map_leaves_out_build_artifacts():
    """編譯產物不該佔專案地圖的位置 —— 那是每一輪都要重送的固定成本。

    DENY_DIRS 只擋得掉 build/，Makefile 專案的產出落在原地；沒有副檔名的
    執行檔只能靠 mode 認，而那個 stat 在 repo_map 裡本來就要做。
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        rel = lambda p: str(p.relative_to(root))       # noqa: E731
        (root / "calc.c").write_text("int add(int a, int b){ return a + b; }\n",
                                     encoding="utf-8")
        (root / "calc.o").write_bytes(b"\x7fELF fake")
        binary = root / "calc_test"
        binary.write_bytes(b"\x7fELF fake")
        binary.chmod(0o755)
        (root / "run.sh").write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
        (root / "run.sh").chmod(0o755)

        # ws_walk 那一層先擋掉有副檔名的產物
        assert ".o" in workspace.DENY_EXT and ".so" in workspace.DENY_EXT
        workspace.SESSIONS[""].ws = root
        walked = {rel(f) for f in workspace.ws_walk()}
        assert "calc.o" not in walked, walked
        assert "calc.c" in walked and "run.sh" in walked

        # 沒有副檔名的執行檔由 repo_map 這一層擋
        out = repomap.repo_map(sorted(root.iterdir()), rel)
        assert "calc_test" not in out, "make 產出的執行檔進了地圖：" + out
        assert "calc.c：add" in out, out
        assert "run.sh" in out, "有副檔名的腳本是原始碼，不能一起掃掉"


def test_repo_map_cache_key_includes_size():
    """同一格 mtime 內改兩次也要重算。

    ext4 上連著兩次寫入連 st_mtime_ns 都可能一樣，只有大小不同。
    快取鍵少了大小，模型連下兩次 edit_file 之後拿到的是舊地圖。
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        f = root / "m.py"
        rel = lambda p: str(p.relative_to(root))       # noqa: E731

        f.write_text("def one():\n    pass\n", encoding="utf-8")
        first = repomap.repo_map([f], rel)
        assert "one" in first

        st = f.stat()
        f.write_text("def one():\n    pass\n\n\ndef two():\n    pass\n", encoding="utf-8")
        os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns))   # 時間戳硬設成一樣
        assert f.stat().st_mtime_ns == st.st_mtime_ns, "前提沒成立，這個測試就沒在驗東西"

        again = repomap.repo_map([f], rel)
        assert "two" in again, "mtime 沒變就不重算 —— 大小沒進快取鍵"


def test_repo_map_reads_c_and_cpp_symbols():
    """C/C++ 靠「頂層的東西寫在第一欄」抓符號。

    抓不到幾個沒關係，**抓錯很有關係** —— 地圖上多一個不存在的名字，
    模型會拿它去 search_files 然後空手而回。所以這裡驗的重點是誤報：
    #define、迴圈、第一欄的 return、變數宣告都不能被讀成符號。
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "geo.cpp").write_text(
            "#include <vector>\n"
            "#define SQUARE(x) ((x) * (x))\n"
            "namespace geo {\n"
            "struct Point { double x, y; };\n"
            "enum class Shape { Circle };\n"
            "class Solver {\n"
            " public:\n"
            "  int solve(int n) const;\n"      # 縮排＝class 成員，不是頂層
            "};\n"
            "}\n"
            "static int helper(int a);\n"
            "int geo::Solver::solve(int n) const {\n"
            "  for (int i = 0; i < n; i++) {}\n"
            "return helper(n);\n"              # 第一欄的 return，最容易誤報的一行
            "}\n"
            "template <typename T>\n"
            "T clamp_to(T v) { return v; }\n"
            "const char* kName = \"geo\";\n",
            encoding="utf-8")
        (root / "calc.h").write_text("int add(int a, int b);\nvoid run(void);\n",
                                     encoding="utf-8")

        out = repomap.repo_map(sorted(root.iterdir()), lambda p: p.name)
        assert "calc.h：add, run" in out, out
        got = [ln for ln in out.splitlines() if ln.startswith("geo.cpp：")][0]
        assert got == ("geo.cpp：geo, Point, Shape, Solver, helper, "
                       "geo::Solver::solve, clamp_to"), got
        for bad in ("SQUARE", "kName", " i,", "vector"):
            assert bad not in got, f"{bad} 不該出現在地圖上：{got}"


def test_repo_map_reads_csharp():
    """C# 不能靠第一欄 —— class 縮在 namespace 裡，method 再縮一層。

    改認存取修飾詞：區域變數與區域函式不會寫 public/internal，那個關鍵字就是
    天然的過濾器。驗的重點一樣是**誤報**：地圖上多一個不存在的名字，
    模型會拿它去 search_files 然後空手而回。
    """
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "Shape.cs"
        f.write_text("""using System;

namespace Geo.Shapes
{
    public enum Kind { Circle, Rect }

    public interface IArea { double Area(); }

    [Serializable]
    public abstract class Shape : IArea
    {
        private readonly Kind _kind;
        public Shape(Kind kind) => _kind = kind;
        public Kind Kind => _kind;
        public abstract double Area();
        public override string ToString()
        {
            int Helper(int x) => x * 2;
            return Helper(1).ToString();
        }
        public static implicit operator double(Shape s) => s.Area();
        internal async Task<int> CountAsync(List<int> xs) => xs.Count;
    }

    public sealed record Point(double X, double Y);

    public static class Util
    {
        public static double Total(IEnumerable<IArea> items) => 0;
        static double Hidden() => 0;
    }
}
""", encoding="utf-8")
        got = repomap.file_symbols(f).split(", ")
        for want in ["Geo.Shapes", "Kind", "IArea", "Shape", "Area", "ToString",
                     "CountAsync", "Point", "Util", "Total"]:
            assert want in got, (want, got)
        # 這幾個都不該出現：區域函式、運算子、欄位、屬性、建構式、沒有修飾詞的
        for no in ["Helper", "operator", "_kind", "Hidden", "Serializable"]:
            assert no not in got, (no, got)

        # C# 10 的檔案範圍命名空間少一層縮排，一樣要抓得到
        g = Path(tmp) / "Program.cs"
        g.write_text("namespace App;\n\npublic class Program\n{\n"
                     "    public static void Main(string[] args) { }\n}\n", encoding="utf-8")
        assert repomap.file_symbols(g) == "App, Program, Main"


def test_missing_toolchain_is_reported_not_installed():
    """工作區用得到但這台沒裝的工具鏈要回報 —— 但這裡只回報，絕不代裝。

    裝 SDK 是使用者的決定（而且沙盒裡沒有網路）。介面拿這份跳確認、
    agent_rules 拿它叫模型不要自己去下載。
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "Program.cs").write_text("class P { }\n", encoding="utf-8")
        workspace.SESSIONS[""].ws = root

        real = workspace.shutil.which
        try:
            workspace.shutil.which = lambda name: None
            miss = workspace.ws_missing_tools()
            assert [m["lang"] for m in miss] == ["csharp"], miss
            assert miss[0]["tool"] == "dotnet" and miss[0]["what"] and miss[0]["how"]

            workspace.shutil.which = lambda name: "/usr/bin/" + name
            assert workspace.ws_missing_tools() == [], "裝好之後就不該再回報"
        finally:
            workspace.shutil.which = real

        # 只列真的可能沒有的：python 跑得起來 serve.py 就有，不必問
        assert "python" not in workspace.LANG_TOOL


def test_ws_langs_decides_which_tools_to_send():
    """工作區是哪種語言。C++ 專案不該看到 run_tests 與 setup_env。"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        workspace.SESSIONS[""].ws = root
        try:
            assert workspace.ws_langs() == set(), "空資料夾不該猜出語言"
            (root / "main.cpp").write_text("int main(){return 0;}\n", encoding="utf-8")
            assert workspace.ws_langs() == {"c"}
            (root / "tool.py").write_text("x = 1\n", encoding="utf-8")
            assert workspace.ws_langs() == {"c", "python"}, "混合專案兩邊都要算"
            (root / "notes.md").write_text("hi\n", encoding="utf-8")
            assert "markdown" not in workspace.ws_langs()
        finally:
            workspace.SESSIONS[""].ws = None


# ── skills：借工具清單、借指令執行 ──────────────────────────────── #

def test_skills_usable_filters_by_the_tool_names_it_is_given():
    """用得動的那幾份是拿「現在有哪些工具」篩出來的，不是 skills 自己去問。

    篩掉的條件只看**認得的**工具名：MCP 那些不在 TOOL_SCHEMAS 裡，
    不能因為它們沒開就把一份 skill 判死。
    """
    every = skills.skills_list()
    assert every, "專案裡的 skills 一份都沒讀到"

    every_name = {s["name"] for s in skills.skills_usable({t for s in every for t in s["tools"]})}
    assert every_name == {s["name"] for s in every}, "工具全開時應該一份都不篩掉"

    # 一支工具都沒有：只留下沒有要求工具的那幾份
    none = {s["name"] for s in skills.skills_usable(set())}
    assert none == {s["name"] for s in every if not s["tools"]}, none

    # 認不得的工具名不算數
    fake = skills.skills_usable({"mcp__whatever"})
    assert {s["name"] for s in fake} == none, "沒開的 MCP 工具不該影響篩選"


def test_skill_live_runs_commands_through_the_callbacks_it_is_handed():
    """!`指令` 的放行與執行都是外面給的，skills 自己不碰 shell。

    這兩個 callback 就是拆出去時長出來的介面：allowed 決定准不准，
    build 決定怎麼跑。skills 只負責找出要跑什麼、把輸出貼回去。
    """
    body = "先看狀態：\n\n!`git status`\n\n再看分支：\n\n!`git branch`\n"
    asked, ran = [], []

    def allowed(cmd):
        asked.append(cmd)
        return "" if cmd == "git status" else "這條不在白名單裡"

    def build(name, args):
        ran.append(args["command"])
        return ([sys.executable, "-c", "print('MARKER-OUT')"], None, False, "")

    with tempfile.TemporaryDirectory() as tmp:
        workspace.SESSIONS[""].ws = Path(tmp)
        try:
            out = skills.skill_live(body, True, allowed, build)
            assert asked == ["git status", "git branch"], asked
            assert ran == ["git status"], "被擋下來的指令不能跑到 build"
            assert "MARKER-OUT" in out, out
            assert "這條不在白名單裡" in out, "擋下來的理由要寫進去，不能安靜跳過"

            # run=False：一條都不跑，而且要講明為什麼
            asked.clear()
            ran.clear()
            out = skills.skill_live(body, False, allowed, build)
            assert ran == [] and asked == [], "run=False 還在跑指令"
            assert "不代跑指令" in out, out
        finally:
            workspace.SESSIONS[""].ws = None


# ── cmdrisk：純函式，沒有任何外部相依 ─────────────────────────────── #

def test_command_risk_is_pure():
    """指令風險判斷不碰檔案系統也不碰工作區 —— 拆出去的第一個理由。"""
    assert cmdrisk.command_risk("ls -la")[0] == "ok"
    assert cmdrisk.command_risk("rm -rf /")[0] == "block"
    assert cmdrisk.command_risk("ls && rm -rf ~")[0] == "block", "分隔符後面也要看"
    assert cmdrisk.canon("  git   status  ") == "git status"


def test_local_system_usage_has_real_host_values():
    """Windows 不可退回 /proc 的空資料；第一次 CPU 沒基準，第二次必須有值。"""
    sysinfo.CPU_LAST.clear()
    sysinfo.cpu_percent()
    time.sleep(0.05)
    cpu = sysinfo.cpu_percent()
    if os.name == "nt" or sys.platform.startswith("linux"):
        assert 0 <= cpu <= 100, cpu
        ram = sysinfo.ram_info()
        assert 0 < ram["used"] <= ram["total"], ram


def test_container_needs_a_live_engine_not_only_a_cli():
    """Docker 命令存在但 Desktop 沒啟動時，不可把沙盒誤報成可用。"""
    failed = type("Result", (), {"returncode": 1})()
    with mock.patch.object(container, "runtime", return_value="docker"), \
            mock.patch.object(container.shutil, "which", return_value="docker"), \
            mock.patch.object(container.subprocess, "run", return_value=failed):
        container._HEALTH.update(at=0.0, runtime="", ok=False)
        assert container.available() == ""
        assert "已安裝" in container.why()


# ── workspace：安全邊界 ─────────────────────────────────────────── #

def test_ws_path_is_the_only_boundary():
    """路徑逃逸、機密檔案、封鎖目錄都在這一支擋掉。"""
    import pytest
    with tempfile.TemporaryDirectory() as tmp:
        workspace.SESSIONS[""].ws = Path(tmp)
        try:
            (Path(tmp) / "ok.py").write_text("x = 1\n", encoding="utf-8")
            assert workspace.ws_path("ok.py").name == "ok.py"
            for bad in ("../outside.py", "~/secret", ".env", ".git/config",
                        ".zackllmgui-rules.json"):
                with pytest.raises((PermissionError, FileNotFoundError)):
                    workspace.ws_path(bad, must_exist=False)
        finally:
            workspace.SESSIONS[""].ws = None
