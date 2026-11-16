# -*- coding: utf-8 -*-
"""MCP 协议端到端验证：对源码或冻结 exe 发起真实 JSON-RPC 会话。

用法:
    python tests/verify_mcp.py                       # 测源码 app.py
    python tests/verify_mcp.py "<exe 绝对路径>"      # 测打包后的 exe（--mcp）

逐项打印 PASS/FAIL，任一失败退出码为 1。
"""
import json
import os
import queue
import subprocess
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
SRC = os.path.join(APP, "src")
VENDOR = os.path.join(APP, "build", "vendor")
if VENDOR not in sys.path:
    sys.path.insert(0, VENDOR)
if SRC not in sys.path:
    sys.path.insert(0, SRC)
from common import VERSION  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (f"  {detail}" if detail else ""), flush=True)


class Client:
    def __init__(self, cmd):
        self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                  errors="replace", bufsize=1, cwd=SRC)
        self.q = queue.Queue()

        def reader():
            for line in self.p.stdout:
                self.q.put(line)
            self.q.put(None)
        threading.Thread(target=reader, daemon=True).start()

    def send(self, obj):
        self.p.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self.p.stdin.flush()

    def recv(self, timeout=180):
        try:
            line = self.q.get(timeout=timeout)
        except queue.Empty:
            raise RuntimeError("等待 MCP 响应超时")
        if line is None:
            raise RuntimeError("MCP 服务端提前退出")
        return json.loads(line)

    def call(self, mid, method, params=None):
        self.send({"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}})
        return self.recv()

    def close(self):
        try:
            self.p.stdin.close()
            self.p.wait(timeout=10)
        except Exception:
            self.p.kill()


def main():
    exe = sys.argv[1] if len(sys.argv) > 1 else None
    cmd = [exe, "--mcp"] if exe else [sys.executable, os.path.join(SRC, "app.py"), "--mcp"]
    print("入口:", " ".join(cmd), flush=True)
    c = Client(cmd)
    try:
        r = c.call(1, "initialize", {"protocolVersion": "2024-11-05",
                                     "capabilities": {}, "clientInfo": {"name": "verify", "version": "1"}})
        check("initialize 返回 result", "result" in r, str(r.get("error", "")))
        res = r.get("result", {})
        check("protocolVersion 回显", res.get("protocolVersion") == "2024-11-05")
        check("serverInfo", res.get("serverInfo", {}).get("version") == VERSION,
              str(res.get("serverInfo")))
        check("capabilities.tools", "tools" in res.get("capabilities", {}))

        c.send({"jsonrpc": "2.0", "method": "notifications/initialized"})  # 不应有响应
        r = c.call(2, "ping")
        check("ping", r.get("result") == {}, str(r))

        r = c.call(3, "tools/list")
        names = [t["name"] for t in r.get("result", {}).get("tools", [])]
        check("tools/list 四个工具", set(names) == {"wx_status", "wx_preflight", "wx_export", "wx_retry"}, str(names))
        for t in r.get("result", {}).get("tools", []):
            check(f"  {t['name']} 有 inputSchema", t.get("inputSchema", {}).get("type") == "object")

        r = c.call(4, "tools/call", {"name": "wx_status", "arguments": {}})
        text = r["result"]["content"][0]["text"]
        st = json.loads(text)
        check("wx_status 可读", st.get("version") == VERSION, st.get("message", ""))
        check("wx_status ready", st.get("ready") is True, st.get("message", ""))

        r = c.call(5, "tools/call", {"name": "wx_preflight",
                                     "arguments": {"from": "2026-11-01", "to": "2026-11-15"}})
        pf = json.loads(r["result"]["content"][0]["text"])
        check("wx_preflight 真实条数", pf.get("ok") and pf.get("total", 0) > 0,
              f"total={pf.get('total')} by_kind={pf.get('by_kind')}")
        check("wx_preflight 含当天", "2026-11-15" in (pf.get("latest") or ""), pf.get("latest", ""))

        r = c.call(6, "tools/call", {"name": "wx_preflight",
                                     "arguments": {"from": "bad-date", "to": "2026-11-15"}})
        bad = json.loads(r["result"]["content"][0]["text"])
        check("非法日期返回 isError", r["result"].get("isError") is True and bad.get("ok") is False,
              bad.get("error", ""))

        r = c.call(7, "tools/call", {"name": "no_such_tool", "arguments": {}})
        check("未知工具返回 isError", r["result"].get("isError") is True)

        # 真实导出：MCP 接入后能不能真的干活（写入 验证输出/MCP导出）
        dest = os.path.join(APP, "验证输出", "MCP导出")
        r = c.call(8, "tools/call", {"name": "wx_export",
                                     "arguments": {"dest": dest,
                                                   "from": "2026-11-15", "to": "2026-11-15"}})
        res = r.get("result", {})
        ex = json.loads(res["content"][0]["text"]) if res.get("content") else {}
        check("wx_export 真实导出 ok", ex.get("ok") is True and res.get("isError") is False,
              f"total={ex.get('total')} written={ex.get('written')} {ex.get('error','')}")
        check("wx_export 批次目录落盘", os.path.isdir(ex.get("batch_path", "")),
              ex.get("batch", ""))
        check("wx_export 报告落盘", os.path.isfile(ex.get("report", "")))
    finally:
        c.close()
        err = c.p.stderr.read() if c.p.stderr else ""
        if err:
            print("[server stderr]", err[-1200:], flush=True)

    failed = [n for n, ok, _ in results if not ok]
    print(f"\n合计 {len(results)} 项 · 失败 {len(failed)}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
