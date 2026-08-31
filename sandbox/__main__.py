# -*- coding: utf-8 -*-
"""python -m sandbox —— 這台機器能用哪一種沙盒，擋不擋得住，多花多少時間。"""

from __future__ import annotations

import json
import sys

from . import detect, pick
from .probe import bench, probe


def main() -> int:
    as_json = "--json" in sys.argv
    want = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--backend=")), "")
    # 裸的後端名字也接受：`python -m sandbox container` 看起來就該能用，
    # 以前它會安靜地忽略掉那個字然後去測 bwrap —— 測錯後端還說「全部通過」。
    want = want or next((a for a in sys.argv[1:] if not a.startswith("-")), "")
    image = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--image=")), "")
    opts = {"image": image} if image else {}
    info = detect()

    if as_json and not info["ok"]:
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return 1
    h = info["host"]
    print(f"這台機器    {h['system']} {h['release']}（{h['machine']}）")
    for b in info["backends"]:
        print(("  ✅ " if b["ok"] else "  ❌ ") + b["name"].ljust(11) + b["kind"].ljust(18)
              + (b["path"] or b["why"]))
    if not info["ok"]:
        print("\n" + info["why"])
        return 1

    mod = pick(want)
    print(f"\n會用        {mod.NAME}" + (f"（映像檔 {image}）" if image else ""))
    for note in mod.describe()["notes"]:
        print(f"  · {note}")

    rows = [] if "--bench" in sys.argv else probe(mod.NAME, **opts)
    if rows:
        print()
        for name, ok, text, why in rows:
            print(("✅ " if ok else "❌ ") + name.ljust(22) + why)
            if text:
                print("      " + text.replace("\n", "\n      "))

    print("\n開銷（每次呼叫，平均）")
    b = bench(mod.NAME, 3 if rows else 5, **opts)
    for k, v in b.items():
        print(f"  {k:22} {v * 1000:8.1f} ms")
    for k, v in b.items():
        if "沙盒" in k:
            direct = b.get(k.replace("沙盒", "直接"))
            if direct:
                print(f"  → {k.split('／')[0]} 多花 {(v - direct) * 1000:.0f} ms")

    if as_json:
        print(json.dumps({"detect": info, "backend": mod.NAME,
                          "checks": [{"name": n, "ok": o, "output": t} for n, o, t, _ in rows],
                          "bench": b}, ensure_ascii=False, indent=2))

    bad = [n for n, ok, _, _ in rows if not ok]
    print("\n" + ("全部通過。" if not bad else "沒過：" + "、".join(bad)))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
