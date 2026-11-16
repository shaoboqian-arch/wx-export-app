# -*- coding: utf-8 -*-
"""MCP 兼容性复检：Content-Length 帧 + 新版协议握手 + 常见探测方法。

用法:
    python tests/verify_mcp_compat.py "<exe 绝对路径>"   # 冻结 exe
    python tests/verify_mcp_compat.py                    # 源码 app.py --mcp
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
SRC = os.path.join(APP, "src")
VENDOR = os.path.join(APP, "build", "vendor")
for p in (VENDOR, SRC):
    if p not in sys.path:
        sys.path.insert(0, p)
from common import VERSION  # noqa: E402
from verify_mcp import Client  # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append({"name": name, "ok": bool(cond), "detail": str(detail)})
    print(("PASS " if cond else "FAIL ") + name + (f"  {detail}" if detail else ""), flush=True)


class LspClient:
    """LSP 风格 Content-Length 帧客户端（二进制 stdio）。"""

    def __init__(self, cmd):
        self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, bufsize=0, cwd=SRC)

    def send(self, obj):
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        head = (b"Content-Length: " + str(len(b)).encode("ascii") +
                b"\r\nContent-Type: application/json\r\n\r\n")
        self.p.stdin.write(head + b)
        self.p.stdin.flush()

    def notify(self, method, params=None):
        self.send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def call(self, mid, method, params=None):
        self.send({"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}})
        return self.recv()

    def recv(self):
        length = None
        while True:
            line = self.p.stdout.readline()
            if not line:
                return None
            if line in (b"\r\n", b"\n"):
                break
            if line.lower().startswith(b"content-length:"):
                length = int(line.split(b":", 1)[1].strip())
        if length is None:
            return None
        body = self.p.stdout.read(length)
        return json.loads(body.decode("utf-8"))

    def close(self):
        try:
            self.p.stdin.close()
            self.p.wait(timeout=8)
        except Exception:
            self.p.kill()


def lsp_cases(cmd):
    c = LspClient(cmd)
    try:
        r = c.call(1, "initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {"roots": {"listChanged": True}},
            "clientInfo": {"name": "compat-probe", "version": "1"}})
        init = (r or {}).get("result", {})
        check("LSP 帧 initialize", init.get("protocolVersion") == "2025-06-18",
              str(init.get("protocolVersion")))
        caps = init.get("capabilities", {})
        check("capabilities 声明四项", {"tools", "resources", "prompts", "logging"} <= set(caps),
              str(sorted(caps)))
        check("instructions 非空", bool(init.get("instructions")),
              (init.get("instructions") or "")[:36])
        check("serverInfo 版本", init.get("serverInfo", {}).get("version") == VERSION)

        c.notify("notifications/initialized")
        c.notify("notifications/cancelled", {"requestId": 99})
        r = c.call(2, "resources/list")
        check("resources/list 空结果", r.get("result", {}).get("resources") == [], str(r)[:80])
        r = c.call(3, "resources/templates/list")
        check("resources/templates/list 空结果",
              r.get("result", {}).get("resourceTemplates") == [], str(r)[:80])
        r = c.call(4, "prompts/list")
        check("prompts/list 空结果", r.get("result", {}).get("prompts") == [], str(r)[:80])
        r = c.call(5, "resources/read", {"uri": "file:///x"})
        check("resources/read 不报错", "contents" in r.get("result", {}), str(r)[:80])
        r = c.call(6, "completion/complete", {"ref": {"type": "ref/prompt", "name": "x"}})
        check("completion/complete 不报错", "completion" in r.get("result", {}), str(r)[:80])
        r = c.call(7, "logging/setLevel", {"level": "info"})
        check("logging/setLevel 不报错", r.get("result") == {}, str(r)[:80])
        r = c.call(8, "tools/list")
        names = [t["name"] for t in r.get("result", {}).get("tools", [])]
        check("tools/list 四个工具", set(names) == {"wx_status", "wx_preflight", "wx_export", "wx_retry"},
              str(names))
        r = c.call(9, "tools/call", {"name": "wx_status", "arguments": {}})
        body = json.loads(r["result"]["content"][0]["text"])
        check("工具结果含 elapsed_ms", isinstance(body.get("elapsed_ms"), int),
              str(body.get("elapsed_ms")))
        check("wx_status ready", body.get("ready") is True, body.get("message", ""))
        r = c.call(10, "ping")
        check("ping", r.get("result") == {})
        r = c.call(11, "shutdown")
        check("shutdown", r.get("result") == {})
    finally:
        c.close()


def line_cases(cmd):
    """换行帧下也要支持这批新方法（两种帧格式行为一致）。"""
    c = Client(cmd)
    try:
        r = c.call(1, "initialize", {"protocolVersion": "2024-11-05"})
        check("换行帧 initialize", "result" in r)
        for mid, method, key in ((2, "resources/list", "resources"),
                                 (3, "prompts/list", "prompts"),
                                 (4, "resources/templates/list", "resourceTemplates")):
            r = c.call(mid, method)
            check(f"换行帧 {method}", key in r.get("result", {}), str(r)[:70])
        r = c.call(5, "tools/call", {"name": "wx_status", "arguments": {}})
        body = json.loads(r["result"]["content"][0]["text"])
        check("换行帧工具结果含 elapsed_ms", isinstance(body.get("elapsed_ms"), int))
    finally:
        c.close()


def main():
    exe = sys.argv[1] if len(sys.argv) > 1 else None
    cmd = [exe, "--mcp"] if exe else [sys.executable, os.path.join(SRC, "app.py"), "--mcp"]
    print("入口:", " ".join(cmd), flush=True)
    lsp_cases(cmd)
    line_cases(cmd)
    fails = [x for x in RESULTS if not x["ok"]]
    print(f"\n合计 {len(RESULTS)} 项 · FAIL {len(fails)}", flush=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
