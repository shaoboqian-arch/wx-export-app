# -*- coding: utf-8 -*-
"""一键全量复检（串行，避免 GUI 截图互相遮挡 / 并发抢同一份微信数据）。

用法:
    python tests/run_all.py [轮次名]

产出: 验证输出/全量复检-<轮次名>.json（每步退出码、耗时、末 12 行输出）
"""
import datetime as dt
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
PY = sys.executable
sys.path.insert(0, os.path.join(APP, "src"))
from common import VERSION  # noqa: E402  —— 升版后测试自动跟随

EXE = os.path.join(APP, "build", "pyi-dist", "wxexport.exe")
SETUP = os.path.join(APP, "dist", f"微信收藏导出-Setup-V{VERSION}.exe")
INSTALLED = os.path.join(APP, "验证安装", "微信收藏导出.exe")
OUT = os.path.join(APP, "验证输出")
ROUND = sys.argv[1] if len(sys.argv) > 1 else "最终"


def steps():
    return [
        ("复检矩阵", [PY, os.path.join(HERE, "verify_matrix.py"), ROUND]),
        ("界面布局", [PY, os.path.join(HERE, "verify_ui.py"), ROUND]),
        ("MCP 兼容", [PY, os.path.join(HERE, "verify_mcp_compat.py"), EXE]),
        ("进度与失败清单", [PY, os.path.join(HERE, "verify_progress.py"), EXE]),
        ("设置页", [PY, os.path.join(HERE, "verify_settings.py"), ROUND]),
        ("MCP 全链路", [PY, os.path.join(HERE, "verify_mcp.py"), EXE]),
        ("导出回归", [PY, os.path.join(HERE, "verify_export.py"), EXE]),
        # 冻结态截图单独命名，避免与 verify_ui.py 的源码态截图互相覆盖
        ("GUI 窗口", [PY, os.path.join(HERE, "verify_gui.py"), EXE,
                    f"冻结-GUI-{ROUND}.png"]),
        ("安装向导", [PY, os.path.join(HERE, "verify_gui.py"), SETUP,
                    f"冻结-安装界面-{ROUND}.png"]),
        ("连续压测", [PY, os.path.join(HERE, "verify_stress.py"), EXE, ROUND]),
    ]


def main():
    missing = [p for p in (EXE, SETUP) if not os.path.isfile(p)]
    if missing:
        print("缺少文件:", missing)
        return 2
    results = []
    print(f"=== 全量复检 {ROUND} · {dt.datetime.now():%Y-%m-%d %H:%M:%S} ===", flush=True)
    for name, cmd in steps():
        t0 = time.time()
        print(f"\n--- {name} ---", flush=True)
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=900, cwd=APP)
            out = r.stdout or ""
            sec = time.time() - t0
            for line in out.strip().splitlines()[-12:]:
                print("   " + line, flush=True)
            if r.returncode:
                print("   [stderr]", (r.stderr or "")[-500:], flush=True)
            results.append({"step": name, "rc": r.returncode, "sec": round(sec, 1),
                            "tail": out.strip().splitlines()[-12:]})
            print(f"   => {'PASS' if r.returncode == 0 else 'FAIL'} · {sec:.1f}s", flush=True)
        except subprocess.TimeoutExpired:
            results.append({"step": name, "rc": -1, "sec": 900, "tail": ["超时"]})
            print("   => FAIL · 超时", flush=True)
    path = os.path.join(OUT, f"全量复检-{ROUND}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"round": ROUND, "time": dt.datetime.now().isoformat(),
                   "results": results}, f, ensure_ascii=False, indent=2)
    fails = [x for x in results if x["rc"] != 0]
    print(f"\n=== 汇总 {len(results)} 步 · 失败 {len(fails)} ===", flush=True)
    for x in results:
        print(f"  {'PASS' if x['rc'] == 0 else 'FAIL'}  {x['step']:<12} {x['sec']:>6.1f}s", flush=True)
    print("汇总文件:", path, flush=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
