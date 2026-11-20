# -*- coding: utf-8 -*-
"""轮次 3：连续运行压测（CLI 多次导出 + 同一 MCP 会话内多次导出 + 大区间预检）。

用法:
    python tests/verify_stress.py "<exe 绝对路径>" [轮次名]
    python tests/verify_stress.py                    # 源码态

检查批次唯一、逐条 md5、每次耗时、大区间不崩、MCP 会话内状态不串。
"""
import csv
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
SRC = os.path.join(APP, "src")
VENDOR = os.path.join(APP, "build", "vendor")
for p in (VENDOR, SRC):
    if p not in sys.path:
        sys.path.insert(0, p)
from verify_mcp import Client  # noqa: E402

DEST = os.path.join(APP, "验证输出", "压测")
FRM, TO = "2026-11-15", "2026-11-15"
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append({"name": name, "ok": bool(cond), "detail": str(detail)})
    print(("PASS " if cond else "FAIL ") + name + (f"  {detail}" if detail else ""), flush=True)


def md5(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def verify_batch(r):
    if not r.get("ok"):
        return False, r.get("error", "")[:80]
    batch = r.get("batch_path", "")
    rp = r.get("report", "")
    if not os.path.isfile(rp):
        return False, "报告缺失"
    if not batch or os.path.exists(os.path.join(batch, "_导出报告.csv")) or os.path.exists(os.path.join(batch, "_导出清单.csv")):
        return False, "存在冗余 CSV"
    import engine
    rows = engine.read_batch_rows(batch)
    full = [x for x in rows if x.get("状态") == "完整"]
    bad = [x for x in full
           if not os.path.isfile(x.get("输出路径", ""))
           or md5(x["输出路径"]).lower() != (x.get("fullmd5") or "").lower()]
    return not bad and len(full) > 0, f"{len(rows)} 行 / 完整 {len(full)} / 不符 {len(bad)}"


def cli_export(exe, dest):
    t0 = time.time()
    if exe:
        r = subprocess.run([exe, "--cli", "--export", "--dest", dest,
                            "--from", FRM, "--to", TO],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=600)
        s = (r.stdout or "").strip()
        try:
            out = json.loads(s[s.index("{"):])
        except (ValueError, json.JSONDecodeError):
            out = {"ok": False, "error": s[-300:] + (r.stderr or "")[-300:]}
    else:
        import engine
        out = engine.safe_call(engine.export, FRM, TO, dest)
    return out, time.time() - t0


def main():
    exe = sys.argv[1] if len(sys.argv) > 1 else None
    if exe and not os.path.isabs(exe):
        exe = os.path.abspath(exe)
    rnd = sys.argv[2] if len(sys.argv) > 2 else "轮次3"
    os.makedirs(DEST, exist_ok=True)
    print(f"=== 连续运行压测 {rnd} · {dt.datetime.now():%Y-%m-%d %H:%M:%S} ===", flush=True)
    print("入口:", exe or "源码 engine", flush=True)

    batches, times = [], []
    for i in range(1, 4):
        r, sec = cli_export(exe, DEST)
        ok, detail = verify_batch(r)
        check(f"第 {i} 次 CLI 导出", ok and r.get("ok") is True, f"{detail} · {sec:.1f}s")
        batches.append(r.get("batch", ""))
        times.append(sec)
    check("3 次批次名互不相同", len(set(batches)) == 3, str(batches))
    check("无耗时异常增长", max(times) < min(times) * 3 + 5,
          f"{[round(t, 1) for t in times]}")

    # 同一 MCP 会话内连做 2 次导出：验证中文路径、状态不串、编码不退化
    cmd = [exe, "--mcp"] if exe else [sys.executable, os.path.join(SRC, "app.py"), "--mcp"]
    c = Client(cmd)
    try:
        r = c.call(1, "initialize", {"protocolVersion": "2024-11-05"})
        check("MCP 会话建立", "result" in r)
        mcp_batches = []
        for i in range(2):
            r = c.call(10 + i, "tools/call", {"name": "wx_export",
                                              "arguments": {"dest": DEST, "from": FRM, "to": TO}})
            body = json.loads(r["result"]["content"][0]["text"])
            ok, detail = verify_batch(body)
            check(f"MCP 会话内第 {i+1} 次导出", ok and body.get("ok") is True, detail)
            mcp_batches.append(body.get("batch", ""))
        check("MCP 两次批次互不相同", len(set(mcp_batches)) == 2, str(mcp_batches))
        check("与 CLI 批次也不冲突", not (set(mcp_batches) & set(batches)))
    finally:
        c.close()

    # 超大区间只读预检：不能崩、不能写文件
    before = set(os.listdir(DEST))
    t0 = time.time()
    if exe:
        r = subprocess.run([exe, "--cli", "--preflight", "--from", "2000-01-01",
                            "--to", "2099-12-31"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=600)
        s = (r.stdout or "").strip()
        out = json.loads(s[s.index("{"):]) if "{" in s else {"ok": False}
    else:
        import engine
        out = engine.safe_call(engine.preflight, "2000-01-01", "2099-12-31")
    check("超大区间预检不崩", out.get("ok") is True and out.get("total", 0) > 0,
          f"total={out.get('total')} · {time.time()-t0:.1f}s")
    check("预检不写任何文件", set(os.listdir(DEST)) == before)

    path = os.path.join(APP, "验证输出", f"压测-{rnd}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"round": rnd, "time": dt.datetime.now().isoformat(),
                   "exe": exe or "source", "items": RESULTS}, f,
                  ensure_ascii=False, indent=2)
    fails = [x for x in RESULTS if not x["ok"]]
    print(f"\n合计 {len(RESULTS)} 项 · FAIL {len(fails)}", flush=True)
    print("结果文件:", path, flush=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
