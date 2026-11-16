# -*- coding: utf-8 -*-
"""设置页复检：配置片段格式、合并写入（临时文件，绝不碰真实 mcp.json）、
真实连接握手、设置窗口布局与截图。

用法: python tests/verify_settings.py [轮次名]
"""
import datetime as dt
import json
import os
import sys
import time
import tkinter as tk
from tkinter import ttk

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
SRC = os.path.join(APP, "src")
VENDOR = os.path.join(APP, "build", "vendor")
for p in (VENDOR, SRC):
    if p not in sys.path:
        sys.path.insert(0, p)
import mcp_config  # noqa: E402
import settings  # noqa: E402
from ui_kit import fonts  # noqa: E402
from verify_gui import all_windows  # noqa: E402

OUT = os.path.join(APP, "验证输出")
TMP = os.path.join(OUT, "设置验证")
os.makedirs(TMP, exist_ok=True)
ROUND = sys.argv[1] if len(sys.argv) > 1 else "新功能"
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append({"name": name, "ok": bool(cond), "detail": str(detail)})
    print(("PASS " if cond else "FAIL ") + name + (f"  {detail}" if detail else ""), flush=True)


def config_cases():
    frag = json.loads(mcp_config.fragment_text())
    entry = frag["mcpServers"]["wx-fav-export"]
    check("配置含 type=stdio", entry.get("type") == "stdio", str(entry.get("type")))
    check("配置含 args --mcp", "--mcp" in entry.get("args", []), str(entry.get("args")))
    check("配置含 description", bool(entry.get("description")))
    check("command 是绝对路径", os.path.isabs(entry.get("command", "")))

    path = os.path.join(TMP, "mcp.json")
    existing = {"mcpServers": {
        "alpha": {"command": "alpha-cmd", "args": ["--a"]},
        "beta": {"url": "https://example.com/mcp"}}}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)

    r = mcp_config.merge_into(path)
    check("合并写入成功", r.get("ok") is True, r.get("error", ""))
    check("已有连接器全部保留", r.get("preserved") == ["alpha", "beta"], str(r.get("preserved")))
    check("写前生成备份", bool(r.get("backup")) and os.path.isfile(r["backup"]),
          os.path.basename(r.get("backup", "")))
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    servers = data["mcpServers"]
    check("新增本服务条目", "wx-fav-export" in servers)
    check("已有条目内容未变", servers["alpha"] == existing["mcpServers"]["alpha"]
          and servers["beta"] == existing["mcpServers"]["beta"])

    r2 = mcp_config.merge_into(path)
    check("重复写入为更新且幂等", r2.get("ok") and r2.get("action") == "更新",
          str(r2.get("action")))
    with open(path, encoding="utf-8") as f:
        data2 = json.load(f)
    check("幂等后仍是 3 个条目", len(data2["mcpServers"]) == 3)

    bad = os.path.join(TMP, "broken.json")
    raw = "{ this is not json "
    with open(bad, "w", encoding="utf-8") as f:
        f.write(raw)
    r3 = mcp_config.merge_into(bad)
    with open(bad, encoding="utf-8") as f:
        after = f.read()
    check("损坏配置拒绝写入且不破坏原文件", r3.get("ok") is False and after == raw,
          r3.get("error", "")[:60])

    s = mcp_config.inspect_config(path)
    check("状态检查识别已配置", s.get("has_wx") is True and s.get("other_count") == 2,
          str({k: s.get(k) for k in ("has_wx", "other_count")}))


def connection_case():
    r = mcp_config.test_connection()
    check("真实 MCP 握手成功", r.get("ok") is True, r.get("error") or r.get("message", ""))
    check("协议版本回显", r.get("protocol") == "2025-06-18", str(r.get("protocol")))
    check("返回核心工具", {"wx_status", "wx_preflight", "wx_export"}.issubset(set(r.get("tools", []))), str(r.get("tools")))
    check("能力探测正常", r.get("resources_ok") and r.get("prompts_ok"))
    check("环境就绪", r.get("ready") is True, r.get("message", ""))
    check("握手耗时可见", isinstance(r.get("elapsed_ms"), int),
          f"{r.get('elapsed_ms')} ms")


def window_case():
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    fm, fs, _ = fonts(root)
    ui = settings.SettingsUI(root, fm, fs)
    root.update_idletasks()
    root.update()
    check("设置窗口标题", "设置" in root.title() and "微信收藏导出" in root.title(), root.title())
    check("设置窗口尺寸", root.winfo_width() == settings.W and root.winfo_height() == settings.H,
          f"{root.winfo_width()}x{root.winfo_height()}")
    check("配置预览非空", bool(ui.preview.get("1.0", "end").strip()))
    check("环境标签齐全", set(ui.env_labels) ==
          {"account_root", "db_keys", "image_key", "ready"})
    bottom = ui.close_btn.winfo_rooty() + ui.close_btn.winfo_height()
    win_bottom = root.winfo_rooty() + root.winfo_height()
    check("关闭按钮在窗口内", bool(ui.close_btn.winfo_ismapped()) and bottom <= win_bottom + 2,
          f"按钮底 {bottom} / 窗口底 {win_bottom}")
    for _ in range(20):
        time.sleep(0.1)
        root.update()
        if ui.cfg_state.cget("text") and ui.env_state.cget("text") not in ("", "检查中…"):
            break
    check("配置状态已刷新", bool(ui.cfg_state.cget("text")), ui.cfg_state.cget("text"))
    check("环境状态已刷新", ui.env_state.cget("text") not in ("", "检查中…"),
          ui.env_state.cget("text"))
    time.sleep(0.45)
    hits = [w for w in all_windows() if "设置" in w[1] and "微信收藏导出" in w[1]]
    if hits:
        from PIL import ImageGrab
        img = ImageGrab.grab(bbox=hits[0][2], all_screens=True)
        p = os.path.join(OUT, f"设置页-{ROUND}.png")
        img.save(p)
        check("设置页截图", True, f"{img.width}x{img.height} {os.path.basename(p)}")
    else:
        check("设置页截图找到窗口", False)
    root.destroy()


def main():
    print(f"=== 设置页复检 {ROUND} · {dt.datetime.now():%Y-%m-%d %H:%M:%S} ===", flush=True)
    config_cases()
    connection_case()
    window_case()
    path = os.path.join(OUT, f"设置页复检-{ROUND}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"round": ROUND, "time": dt.datetime.now().isoformat(),
                   "items": RESULTS}, f, ensure_ascii=False, indent=2)
    fails = [x for x in RESULTS if not x["ok"]]
    print(f"\n合计 {len(RESULTS)} 项 · FAIL {len(fails)}", flush=True)
    for x in fails:
        print(f"  - {x['name']}: {x['detail']}", flush=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
