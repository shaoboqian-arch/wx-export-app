# -*- coding: utf-8 -*-
"""MCP stdio 服务端（零第三方依赖，手写 JSON-RPC 2.0 子集）。

兼容性设计：
  · 帧格式自动识别 —— 换行分隔 JSON（MCP 现行规范）与 Content-Length 帧（LSP 风格）
  · 协议版本回显客户端请求值；capabilities 同时声明 tools / resources / prompts / logging
  · 对客户端握手期间的常见探测方法返回合法空结果，而不是 -32601
  · stdout 只输出协议帧，全部日志走 stderr；报文字节为纯 ASCII（中文用 \\u 转义）
  · 工具调用耗时写进结果，便于对照官方「单次请求建议 30 秒内」的建议

工具：wx_status / wx_preflight / wx_export / wx_retry。
"""
import json
import sys
import time
import traceback

import engine
from common import MCP_SERVER_NAME, VERSION

PROTOCOL_FALLBACK = "2024-11-05"
_FRAME_MODE = "line"          # "line" | "lsp"
_EXIT = False

INSTRUCTIONS = (
    "首次接入请先调用 wx_status 确认本机微信数据与密钥就绪。"
    "wx_export 需要 dest/from/to，日期 YYYY-MM-DD 且起止都含当天；"
    "省略 kinds 时与主界面相同：导出图片和视频文件，并把文本、链接卡片写入清单；"
    "kinds 只选择 resolve_item 类型（image,video,file,link,chat），"
    "其中 link 是聊天记录打包（type=14），不是公众号文章。"
    "区间较大时单次可能超过 30 秒，建议按月分批。"
    "结果里的 failed_items 是未取到原件的文件清单（含状态与原因）。"
    "未取到原件时可调用 wx_retry，对同一批次补导，不必重选日期。"
)

TOOLS = [
    {
        "name": "wx_status",
        "description": "检查本机微信收藏导出环境是否就绪（账号目录、数据库密钥、图片密钥）。接入后建议先调用本工具。",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "wx_preflight",
        "description": "只读预检：统计指定日期区间内的微信收藏条数与类型分布，不写任何文件。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "from": {"type": "string", "description": "起始日期 YYYY-MM-DD（含当天）"},
                "to": {"type": "string", "description": "结束日期 YYYY-MM-DD（含当天）"},
                "kinds": {"type": "string", "description": "可选。resolve_item 类型，逗号分隔：image,video,file,link,chat。省略时与主界面相同，预检会额外给出 text_count/link_count。link 是聊天记录打包（type=14），不是公众号文章。"},
                "account_root": {"type": "string", "description": "可选：微信账号目录绝对路径"},
            },
            "required": ["from", "to"],
        },
    },
    {
        "name": "wx_export",
        "description": "把指定日期区间内的微信收藏导出到目标文件夹（日期起止均含当天）。省略 kinds 时与主界面相同：导出图片和视频文件，并把文本、链接卡片写入批次清单（不另存单文件）。kinds 只选择 resolve_item 类型（image,video,file,link,chat）；link 是聊天记录打包（type=14），不是公众号文章。每次新建带时间戳的批次目录，绝不覆盖已有文件；区间 0 条时返回错误且不创建任何目录。返回分类计数与 failed_items（未取到原件的文件清单）。单次大区间可能超过 30 秒，建议按月分批。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "dest": {"type": "string", "description": "目标文件夹绝对路径"},
                "from": {"type": "string", "description": "起始日期 YYYY-MM-DD（含当天）"},
                "to": {"type": "string", "description": "结束日期 YYYY-MM-DD（含当天）"},
                "kinds": {"type": "string", "description": "可选。resolve_item 类型，逗号分隔：image,video,file,link,chat。省略时与主界面相同（图片/视频文件 + 文本和链接卡片写入清单）。指定后只导出这些类型的文件，不自动附带文本和链接。link 是聊天记录打包（type=14），不是公众号文章。"},
                "account_root": {"type": "string", "description": "可选：微信账号目录绝对路径"},
                "image_key": {"type": "string", "description": "可选：16 字节图片密钥（留空读本机配置）"},
            },
            "required": ["dest", "from", "to"],
        },
    },
    {
        "name": "wx_retry",
        "description": "对已有导出批次里未取到原件的图片/视频再次补导，写回同一批次目录，不必重选日期和文件夹。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "batch_path": {"type": "string", "description": "批次目录绝对路径"},
                "account_root": {"type": "string", "description": "可选：微信账号目录绝对路径"},
                "image_key": {"type": "string", "description": "可选：16 字节图片密钥（留空读本机配置）"},
            },
            "required": ["batch_path"],
        },
    },
]


def _log(msg):
    try:
        sys.stderr.write(f"[wx-fav-export] {msg}\n")
        sys.stderr.flush()
    except (OSError, ValueError):
        pass


def _send(obj):
    """按当前帧格式输出；报文纯 ASCII，不受任何控制台代码页影响。"""
    data = json.dumps(obj, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    out = sys.stdout.buffer
    if _FRAME_MODE == "lsp":
        out.write(b"Content-Length: " + str(len(data)).encode("ascii") + b"\r\n\r\n" + data)
    else:
        out.write(data + b"\n")
    out.flush()


def _ok(mid, result):
    _send({"jsonrpc": "2.0", "id": mid, "result": result})


def _err(mid, code, message):
    _send({"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}})


def _text(obj, is_error=False):
    if isinstance(obj, dict):
        obj = dict(obj)
    return {"content": [{"type": "text", "text": json.dumps(obj, ensure_ascii=False, indent=2)}],
            "isError": bool(is_error)}


def _call_tool(name, args):
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return _text({"ok": False,
                      "error": f"arguments 必须是对象，收到 {type(args).__name__}"}, True)
    if name == "wx_status":
        return _text(engine.safe_call(engine.status, args.get("account_root") or None))
    if name == "wx_preflight":
        r = engine.safe_call(engine.preflight, args.get("from"), args.get("to"),
                             args.get("kinds"), args.get("account_root") or None)
        return _text(r, not r.get("ok", False))
    if name == "wx_export":
        r = engine.safe_call(engine.export, args.get("from"), args.get("to"),
                             args.get("dest"), args.get("kinds"),
                             args.get("account_root") or None, args.get("image_key") or None)
        return _text(r, not r.get("ok", False))
    if name == "wx_retry":
        r = engine.safe_call(engine.retry_missing, args.get("batch_path"),
                             args.get("account_root") or None, args.get("image_key") or None)
        return _text(r, not r.get("ok", False))
    return _text({"ok": False, "error": f"未知工具：{name}"}, True)


def _handle(msg):
    global _EXIT
    mid = msg.get("id")
    method = msg.get("method")
    params = msg.get("params") or {}

    if method == "initialize":
        ver = params.get("protocolVersion") or PROTOCOL_FALLBACK
        return _ok(mid, {
            "protocolVersion": ver,
            "capabilities": {
                "tools": {"listChanged": False},
                "resources": {"subscribe": False, "listChanged": False},
                "prompts": {"listChanged": False},
                "logging": {},
            },
            "serverInfo": {"name": MCP_SERVER_NAME, "version": VERSION},
            "instructions": INSTRUCTIONS,
        })
    if str(method).startswith("notifications/"):
        return None
    if method == "ping":
        return _ok(mid, {})
    if method == "tools/list":
        return _ok(mid, {"tools": TOOLS})
    # 客户端握手阶段的常见探测；返回合法空结果比 -32601 更兼容
    empty_caps = {"resources/list": "resources",
                  "resources/templates/list": "resourceTemplates",
                  "prompts/list": "prompts"}
    if method in empty_caps:
        return _ok(mid, {empty_caps[method]: []})
    if method == "resources/read":
        return _ok(mid, {"contents": []})
    if method == "completion/complete":
        return _ok(mid, {"completion": {"values": [], "total": 0, "hasMore": False}})
    if method == "logging/setLevel":
        return _ok(mid, {})
    if method == "shutdown":
        _EXIT = True
        return _ok(mid, {})
    if method == "exit":
        _EXIT = True
        return None
    if method == "tools/call":
        t0 = time.perf_counter()
        try:
            res = _call_tool(params.get("name"), params.get("arguments"))
        except Exception as e:
            _log(traceback.format_exc(limit=5))
            res = _text({"ok": False, "error": str(e)}, True)
        ms = int((time.perf_counter() - t0) * 1000)
        try:
            body = json.loads(res["content"][0]["text"])
            if isinstance(body, dict):
                body["elapsed_ms"] = ms
                res["content"][0]["text"] = json.dumps(body, ensure_ascii=False, indent=2)
        except (ValueError, KeyError, TypeError):
            pass
        return _ok(mid, res)
    if mid is not None:
        return _err(mid, -32601, f"Method not found: {method}")
    return None


def _dispatch(text):
    try:
        msg = json.loads(text)
    except json.JSONDecodeError as e:
        _send({"jsonrpc": "2.0", "id": None,
               "error": {"code": -32700, "message": f"Parse error: {e}"}})
        return
    try:
        if isinstance(msg, list):
            for one in msg:
                _handle(one)
        elif isinstance(msg, dict):
            _handle(msg)
        else:
            _send({"jsonrpc": "2.0", "id": None,
                   "error": {"code": -32600, "message": "Invalid Request"}})
    except Exception as e:
        _log(traceback.format_exc(limit=5))
        if isinstance(msg, dict) and msg.get("id") is not None:
            _err(msg["id"], -32603, f"Internal error: {e}")


def serve():
    global _FRAME_MODE
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass
    _log(f"MCP stdio ready, version {VERSION}")
    inp = sys.stdin.buffer
    while not _EXIT:
        line = inp.readline()
        if not line:
            break
        head = line.strip()
        if not head:
            continue
        if head.lower().startswith(b"content-length:"):
            _FRAME_MODE = "lsp"
            try:
                length = int(head.split(b":", 1)[1].strip())
            except ValueError:
                _send({"jsonrpc": "2.0", "id": None,
                       "error": {"code": -32700, "message": "Invalid Content-Length"}})
                continue
            while True:                       # 读完剩余头部，直到空行
                h = inp.readline()
                if not h or h in (b"\r\n", b"\n"):
                    break
            body = inp.read(length) if length > 0 else b""
            _dispatch(body.decode("utf-8", "replace"))
        else:
            _FRAME_MODE = "line"
            _dispatch(line.decode("utf-8", "replace"))
    return 0
