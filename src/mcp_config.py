# -*- coding: utf-8 -*-
"""MCP 接入配置：生成、检查、合并写入、连通性自检。

配置格式依据 WorkBuddy 开放平台连接器文档（stdio 类型）：
    {"mcpServers": {"<名称>": {"type":"stdio","command":...,"args":[...],"description":...}}}
写入时**只增改 wx-fav-export 一个键**，其余连接器原样保留；
写前备份、临时文件原子替换；解析失败一律不写。

本模块不依赖 tkinter，可单独测试；真实写入只在用户点击「写入配置」时发生。
"""
import datetime as dt
import json
import os
import subprocess
import sys
import time

from common import MCP_SERVER_NAME, VERSION, is_frozen, src_dir

DESCRIPTION = "微信收藏按日期范围导出（读取本机微信 4.x 本地数据）"


def mcp_json_path():
    return os.path.join(os.path.expanduser("~"), ".workbuddy", "mcp.json")


def current_command():
    """返回 (command, args)；冻结态指向自身 exe，源码态指向 python + app.py。"""
    if is_frozen():
        return sys.executable, ["--mcp"]
    return sys.executable, [os.path.join(src_dir(), "app.py"), "--mcp"]


def server_entry():
    command, args = current_command()
    return {
        "type": "stdio",
        "command": command,
        "args": args,
        "description": DESCRIPTION,
    }


def config_fragment():
    return {"mcpServers": {MCP_SERVER_NAME: server_entry()}}


def fragment_text():
    return json.dumps(config_fragment(), ensure_ascii=False, indent=2)


def read_config(path=None):
    """只读。返回 (data, error)；文件不存在时返回空配置。"""
    path = path or mcp_json_path()
    if not os.path.isfile(path):
        return {"mcpServers": {}}, ""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        return None, f"读取/解析失败：{e}"
    if not isinstance(data, dict):
        return None, "顶层不是 JSON 对象"
    servers = data.get("mcpServers")
    if servers is None:
        data["mcpServers"] = {}
        servers = data["mcpServers"]
    if not isinstance(servers, dict):
        return None, "mcpServers 不是对象"
    return data, ""


def inspect_config(path=None):
    """设置页展示用的状态快照，不做任何写入。"""
    path = path or mcp_json_path()
    data, err = read_config(path)
    if err:
        return {"ok": False, "path": path, "error": err}
    servers = data.get("mcpServers", {})
    entry = servers.get(MCP_SERVER_NAME)
    command, args = current_command()
    return {
        "ok": True,
        "path": path,
        "exists": os.path.isfile(path),
        "servers": sorted(servers.keys()),
        "has_wx": entry is not None,
        "command_matches": bool(entry and entry.get("command") == command),
        "current_command": command,
        "current_args": args,
        "other_count": len([k for k in servers if k != MCP_SERVER_NAME]),
    }


def merge_into(path=None, backup=True):
    """把本服务条目并入 mcp.json。返回结构化报告；失败时文件不会被破坏。"""
    path = path or mcp_json_path()
    data, err = read_config(path)
    if err:
        return {"ok": False, "error": err, "path": path}

    servers = data["mcpServers"]
    preserved = sorted(k for k in servers if k != MCP_SERVER_NAME)
    existed = MCP_SERVER_NAME in servers
    old_command = servers.get(MCP_SERVER_NAME, {}).get("command", "") if existed else ""
    servers[MCP_SERVER_NAME] = server_entry()
    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"

    backup_path = ""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if backup and os.path.isfile(path):
            stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            backup_path = f"{path}.bak-{stamp}"
            n = 1
            while os.path.exists(backup_path):
                backup_path = f"{path}.bak-{stamp}-{n}"
                n += 1
            with open(path, "rb") as src, open(backup_path, "wb") as dst:
                dst.write(src.read())
        tmp = path + ".tmp-wxfav"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp, path)
    except OSError as e:
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except (OSError, UnboundLocalError):
            pass
        return {"ok": False, "error": f"写入失败：{e}", "path": path}

    return {
        "ok": True,
        "path": path,
        "backup": backup_path,
        "action": "更新" if existed else "新增",
        "old_command": old_command,
        "command": server_entry()["command"],
        "preserved": preserved,
        "preserved_count": len(preserved),
    }


def test_connection(timeout=30):
    """模拟严格 MCP 客户端完整握手（含能力探测），验证本服务可被接入。"""
    command, args = current_command()
    argv = [command] + list(args)   # 源码态是 python + app.py + --mcp，冻结态是 exe + --mcp
    # 子进程是 --console 构建的 exe，不给它会闪出一个多余的控制台窗口。
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, encoding="utf-8",
                             errors="replace", bufsize=1, creationflags=flags)
    except OSError as e:
        return {"ok": False, "error": f"无法启动：{e}"}

    def send(obj):
        p.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
        p.stdin.flush()

    def recv():
        line = p.stdout.readline()
        return json.loads(line) if line.strip() else None

    result = {"ok": False}
    t0 = time.perf_counter()
    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                         "clientInfo": {"name": "wx-fav-export-settings", "version": VERSION}}})
        r = recv() or {}
        init = r.get("result", {})
        result["protocol"] = init.get("protocolVersion", "")
        result["instructions"] = bool(init.get("instructions"))
        result["capabilities"] = sorted(init.get("capabilities", {}).keys())

        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send({"jsonrpc": "2.0", "id": 2, "method": "resources/list"})
        send({"jsonrpc": "2.0", "id": 3, "method": "prompts/list"})
        send({"jsonrpc": "2.0", "id": 4, "method": "tools/list"})
        send({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
              "params": {"name": "wx_status", "arguments": {}}})
        r2, r3, r4, r5 = recv() or {}, recv() or {}, recv() or {}, recv() or {}
        tools = [t["name"] for t in r4.get("result", {}).get("tools", [])]
        body = {}
        try:
            body = json.loads(r5["result"]["content"][0]["text"])
        except (ValueError, KeyError, TypeError, IndexError):
            pass
        result["tools"] = tools
        result["resources_ok"] = "resources" in r2.get("result", {})
        result["prompts_ok"] = "prompts" in r3.get("result", {})
        result["ready"] = bool(body.get("ready"))
        result["message"] = body.get("message", "")
        core_tools = {"wx_status", "wx_preflight", "wx_export"}
        result["ok"] = bool(result["protocol"] and "tools" in result["capabilities"]
                            and core_tools.issubset(set(tools))
                            and result["resources_ok"]
                            and result["prompts_ok"])
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
    finally:
        try:
            p.stdin.close()
            p.wait(timeout=5)
        except Exception:
            p.kill()
    result["elapsed_ms"] = int((time.perf_counter() - t0) * 1000)
    return result
