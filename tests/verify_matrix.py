# -*- coding: utf-8 -*-
"""复检矩阵：引擎边界 + MCP 协议边界 + 连续运行（源码态，可反复跑）。

用法:
    python tests/verify_matrix.py [轮次名]

每项打印 PASS/FAIL，并把全部结果写到 验证输出/复检矩阵-<轮次名>.json。
FAIL 不代表流程中断，正是要拿来当「问题清单」。
"""
import datetime as dt
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
SRC = os.path.join(APP, "src")
VENDOR = os.path.join(APP, "build", "vendor")
for p in (VENDOR, SRC):
    if p not in sys.path:
        sys.path.insert(0, p)

import engine  # noqa: E402
from verify_mcp import Client  # noqa: E402

OUT = os.path.join(APP, "验证输出")
TMP = os.path.join(OUT, "矩阵")
os.makedirs(TMP, exist_ok=True)
ROUND = sys.argv[1] if len(sys.argv) > 1 else "轮次1"
RESULTS = []


def check(group, name, cond, detail=""):
    RESULTS.append({"group": group, "name": name, "ok": bool(cond), "detail": str(detail)})
    print(("PASS " if cond else "FAIL ") + f"[{group}] {name}" + (f"  {detail}" if detail else ""),
          flush=True)


def engine_cases():
    g = "引擎边界"
    r = engine.safe_call(engine.preflight, "bad-date", "2026-11-15")
    check(g, "非法日期被拒", r.get("ok") is False and "YYYY-MM-DD" in r.get("error", ""),
          r.get("error", "")[:80])
    r = engine.safe_call(engine.preflight, "2026-11-15", "2026-11-01")
    check(g, "结束早于起始被拒", r.get("ok") is False, r.get("error", "")[:80])
    r = engine.safe_call(engine.preflight, "2026-11-01", "2026-11-15")
    check(g, "正常预检有数据", r.get("ok") is True and int(r.get("total") or 0) > 0,
          f"total={r.get('total')}")

    empty = "1999-01-01"
    r0 = engine.safe_call(engine.preflight, empty, empty)
    check(g, "空区间预检 total=0", r0.get("ok") is True and r0.get("total") == 0,
          f"total={r0.get('total')}")

    # 目标文件夹异常：不应创建任何目录
    r = engine.safe_call(engine.export, "2026-11-15", "2026-11-15", "")
    check(g, "空目标文件夹被拒", r.get("ok") is False, r.get("error", "")[:80])
    fp = os.path.join(TMP, "这是个文件.txt")
    with open(fp, "w", encoding="utf-8") as f:
        f.write("x")
    r = engine.safe_call(engine.export, "2026-11-15", "2026-11-15", fp)
    check(g, "目标是文件被拒", r.get("ok") is False, r.get("error", "")[:80])

    # 0 条：基线口径是「停下、不产空包」
    before = set(os.listdir(TMP))
    t0 = time.time()
    r = engine.safe_call(engine.export, empty, empty, TMP)
    dt_s = time.time() - t0
    after = set(os.listdir(TMP))
    new = sorted(after - before)
    check(g, "0 条区间不产空包", r.get("ok") is False and not new,
          f"ok={r.get('ok')} 新增={new} 耗时={dt_s:.1f}s err={r.get('error','')[:60]}")

    r = engine.safe_call(engine.status)
    check(g, "status 结构完整", all(k in r for k in
          ("version", "ready", "account_root", "db_keys", "image_key", "message")),
          r.get("message", ""))

    base = "batch-collision-test"
    p1 = engine._unique_batch(TMP, base)
    os.makedirs(p1, exist_ok=True)
    p2 = engine._unique_batch(TMP, base)
    check(g, "同秒批次不冲突", p1 != p2, f"{os.path.basename(p1)} -> {os.path.basename(p2)}")
    os.rmdir(p1)

    # 连续预检稳定性
    vals = [engine.safe_call(engine.preflight, "2026-11-01", "2026-11-15").get("total")
            for _ in range(3)]
    check(g, "连续 3 次预检一致", len(set(vals)) == 1 and vals[0] > 0, str(vals))

    import importlib.util
    engine._load()
    check(g, "源码态 Crypto 可用", importlib.util.find_spec("Crypto.Cipher.AES") is not None)

    # 写出中途失败：本次新建的批次目录必须被清掉，不留半成品
    fe, _ = engine._load()
    orig = fe.cmd_export

    def boom(_args):
        raise RuntimeError("模拟写出失败")

    fe.cmd_export = boom
    try:
        before = set(os.listdir(TMP))
        r = engine.safe_call(engine.export, "2026-11-15", "2026-11-15", TMP)
        new = sorted(set(os.listdir(TMP)) - before)
        check(g, "写出失败不留半成品", r.get("ok") is False and not new,
              f"err={r.get('error','')[:50]} 新增={new}")
    finally:
        fe.cmd_export = orig


def mcp_cases():
    g = "MCP协议"
    c = Client([sys.executable, os.path.join(SRC, "app.py"), "--mcp"])
    try:
        # 空行不应打断会话
        c.p.stdin.write("\n")
        c.p.stdin.flush()
        r = c.call(1, "ping")
        check(g, "空行后 ping 正常", r.get("result") == {}, str(r)[:80])

        c.p.stdin.write("{not json}\n")
        c.p.stdin.flush()
        r = c.recv()
        check(g, "非法 JSON 返回 -32700", r.get("error", {}).get("code") == -32700, str(r)[:80])

        # 批量请求
        c.p.stdin.write(json.dumps([
            {"jsonrpc": "2.0", "id": 21, "method": "ping"},
            {"jsonrpc": "2.0", "id": "abc", "method": "ping"},
        ]) + "\n")
        c.p.stdin.flush()
        a, b = c.recv(), c.recv()
        check(g, "批量请求与字符串 id", a.get("id") == 21 and b.get("id") == "abc",
              f"{a.get('id')} / {b.get('id')}")

        for m in ("resources/list", "prompts/list"):
            r = c.call(30, m)
            ok = "result" in r and r.get("error", {}).get("code") != -32601
            check(g, f"{m} 返回空结果而非报错", ok, str(r)[:90])

        r = c.call(31, "tools/call", {"name": "wx_preflight", "arguments": None})
        check(g, "arguments=null 不崩", r.get("result", {}).get("isError") is True or
              "result" in r, str(r)[:90])

        before = set(os.listdir(TMP))
        r = c.call(32, "tools/call", {"name": "wx_export",
                                      "arguments": {"dest": TMP, "from": "1999-01-01",
                                                    "to": "1999-01-01"}})
        body = json.loads(r["result"]["content"][0]["text"])
        err = body.get("error", "")
        new = sorted(set(os.listdir(TMP)) - before)
        # 必须是因为「0 条」而拒绝，不能是缺依赖之类的假阳性
        check(g, "MCP 空区间不产空包",
              r["result"].get("isError") is True and body.get("ok") is False
              and "0 条" in err and not new,
              f"err={err[:60]} 新增={new}")

        # stdout 纯净：会话期间每条响应都必须是合法 JSON（Client 已解析）
        check(g, "stdout 无协议外输出", True, "所有响应均可 json.loads")
    finally:
        c.close()


def main():
    print(f"=== 复检矩阵 {ROUND} · {dt.datetime.now():%Y-%m-%d %H:%M:%S} ===", flush=True)
    engine_cases()
    mcp_cases()
    path = os.path.join(OUT, f"复检矩阵-{ROUND}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"round": ROUND, "time": dt.datetime.now().isoformat(),
                   "items": RESULTS}, f, ensure_ascii=False, indent=2)
    fails = [x for x in RESULTS if not x["ok"]]
    print(f"\n合计 {len(RESULTS)} 项 · FAIL {len(fails)}", flush=True)
    for x in fails:
        print(f"  - [{x['group']}] {x['name']}: {x['detail'][:120]}", flush=True)
    print("结果文件:", path, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
