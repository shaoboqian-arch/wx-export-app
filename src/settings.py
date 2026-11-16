# -*- coding: utf-8 -*-
"""设置页面：MCP 接入配置 + 本机环境状态。

写入 mcp.json 属于对用户环境的修改，只由用户点击「写入配置」触发；
采用合并写入 + 写前备份 + 原子替换，绝不覆盖已有连接器。
"""
import queue
import threading
import tkinter as tk
from tkinter import ttk

import engine
import mcp_config
from common import APP_NAME, VERSION
from ui_kit import BG, Tooltip, apply_icon, apply_island_style, center, fonts

W, H = 620, 560
GREY = "#666666"
GREEN = "#1a7f37"
RED = "#b42318"


class SettingsUI:
    def __init__(self, win, font_main, font_small):
        self.win = win
        self.q = queue.Queue()
        self.full_state = ""
        win.title(f"设置 · {APP_NAME} V{VERSION}")
        win.resizable(False, False)
        apply_icon(win)
        apply_island_style(win)
        try:
            win.configure(bg=BG)
        except Exception:
            pass
        center(win, W, H)
        if isinstance(win, tk.Toplevel):
            win.transient(win.master)

        f = ttk.Frame(win, padding=(20, 16))
        f.pack(fill="both", expand=True)
        f.columnconfigure(0, weight=1)

        # ---------------- MCP 接入 ----------------
        mcp = ttk.LabelFrame(f, text="MCP 接入", padding=(12, 10))
        mcp.grid(row=0, column=0, sticky="ew")
        mcp.columnconfigure(1, weight=1)

        ttk.Label(mcp, text="程序路径", font=font_small).grid(
            row=0, column=0, sticky="w", pady=(0, 6))
        cmd, _args = mcp_config.current_command()
        e = ttk.Entry(mcp, font=font_small)
        e.insert(0, cmd)
        e.configure(state="readonly")
        e.grid(row=0, column=1, columnspan=2, sticky="ew", pady=(0, 6))

        ttk.Label(mcp, text="配置片段", font=font_small).grid(
            row=1, column=0, sticky="nw", pady=(4, 0))
        box = ttk.Frame(mcp)
        box.grid(row=1, column=1, columnspan=2, sticky="ew", pady=(4, 8))
        self.preview = tk.Text(box, height=8, font=("Consolas", 9),
                               relief="solid", borderwidth=1, wrap="none")
        psb = ttk.Scrollbar(box, orient="vertical", command=self.preview.yview)
        self.preview.configure(yscrollcommand=psb.set)
        self.preview.pack(side="left", fill="both", expand=True)
        psb.pack(side="right", fill="y")
        self.preview.insert("1.0", mcp_config.fragment_text())
        self.preview.configure(state="disabled")

        btns = ttk.Frame(mcp)
        btns.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(2, 6))
        ttk.Button(btns, text="复制配置", width=12, command=self.copy_config).pack(side="left")
        ttk.Button(btns, text="写入配置", width=12, command=self.write_config).pack(
            side="left", padx=(8, 0))
        ttk.Button(btns, text="测试连接", width=12, command=self.test_conn).pack(
            side="left", padx=(8, 0))
        self.cfg_state = ttk.Label(mcp, text="", font=font_small, foreground=GREY,
                                   wraplength=540, anchor="w")
        self.cfg_state.grid(row=3, column=0, columnspan=3, sticky="w")
        ttk.Label(mcp,
                  text="写入只增改本服务条目，不覆盖已有连接器；保存后需在连接器管理页点「信任」",
                  font=font_small, foreground=GREY, wraplength=540).grid(
            row=4, column=0, columnspan=3, sticky="w", pady=(4, 0))

        # ---------------- 环境 ----------------
        env = ttk.LabelFrame(f, text="环境", padding=(12, 10))
        env.grid(row=1, column=0, sticky="ew", pady=(14, 0))
        env.columnconfigure(1, weight=1)
        self.env_labels = {}
        for i, (key, name) in enumerate((
                ("account_root", "账号目录"), ("db_keys", "数据库密钥"),
                ("image_key", "图片密钥"), ("ready", "状态"))):
            ttk.Label(env, text=name, font=font_small, width=12).grid(
                row=i, column=0, sticky="w", pady=2)
            v = ttk.Label(env, text="检查中…", font=font_small,
                          foreground=GREY, anchor="w", wraplength=440)
            v.grid(row=i, column=1, sticky="w", pady=2)
            self.env_labels[key] = v
        r = ttk.Frame(env)
        r.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.env_state = ttk.Label(r, text="", font=font_small, foreground=GREY,
                                   wraplength=430, anchor="w")
        self.env_state.pack(side="left", fill="x", expand=True)
        ttk.Button(r, text="刷新", width=8, command=self.refresh_env).pack(side="right")

        # ---------------- 底部 ----------------
        bottom = ttk.Frame(f)
        bottom.grid(row=2, column=0, sticky="ew", pady=(16, 0))
        bottom.columnconfigure(0, weight=1)
        self.state = ttk.Label(bottom, text="", font=font_small, foreground=GREY,
                               anchor="w", wraplength=470)
        self.state.grid(row=0, column=0, sticky="w")
        self.close_btn = ttk.Button(bottom, text="关闭", width=8, command=win.destroy)
        self.close_btn.grid(row=0, column=1)
        Tooltip(self.state, lambda: self.full_state, font=font_small)

        self._after_id = win.after(80, self.poll)
        self.q.put(("inspect", None))
        self.refresh_env()

    def set_state(self, text, color=GREY):
        self.full_state = text
        self.state.configure(text=text if len(text) <= 70 else text[:67] + "…",
                             foreground=color)

    def copy_config(self):
        try:
            self.win.clipboard_clear()
            self.win.clipboard_append(mcp_config.fragment_text())
            self.set_state("已复制配置片段", GREEN)
        except tk.TclError as e:
            self.set_state(f"复制失败：{e}", RED)

    def _bg(self, fn, tag):
        def work():
            try:
                self.q.put((tag, fn()))
            except Exception as e:
                self.q.put((tag, {"ok": False, "error": f"{type(e).__name__}: {e}"}))
        threading.Thread(target=work, daemon=True).start()

    def write_config(self):
        self.set_state("正在写入配置…")
        self._bg(mcp_config.merge_into, "write")

    def test_conn(self):
        self.set_state("正在测试连接…")
        self._bg(mcp_config.test_connection, "conn")

    def refresh_env(self):
        self.env_state.configure(text="检查中…")
        self._bg(lambda: engine.safe_call(engine.status), "env")

    def _inspect(self):
        s = mcp_config.inspect_config()
        if not s.get("ok"):
            return f"配置文件读取失败：{s.get('error')}", RED
        state = "已配置" if s["has_wx"] else "未配置"
        if s["has_wx"] and not s["command_matches"]:
            state = "已配置（路径与当前程序不一致）"
        return (f"{state} · 另有 {s['other_count']} 个连接器 · "
                f"{s['path']}"), GREY

    def poll(self):
        try:
            while True:
                tag, data = self.q.get_nowait()
                if tag == "inspect":
                    text, color = self._inspect()
                    self.cfg_state.configure(text=text, foreground=color)
                elif tag == "write":
                    if data.get("ok"):
                        msg = (f"{data['action']}成功 · 保留 {data['preserved_count']} 个已有连接器"
                               + (f" · 已备份 {data['backup']}" if data.get("backup") else ""))
                        self.set_state(msg, GREEN)
                        text, color = self._inspect()
                        self.cfg_state.configure(text=text, foreground=color)
                    else:
                        self.set_state(f"写入失败：{data.get('error')}", RED)
                elif tag == "conn":
                    if data.get("ok"):
                        self.set_state(
                            f"连接正常 · {data.get('protocol')} · {len(data.get('tools', []))} 个工具 · "
                            f"环境{'就绪' if data.get('ready') else data.get('message', '未就绪')} · "
                            f"{data.get('elapsed_ms', 0)} ms", GREEN)
                    else:
                        self.set_state(f"连接失败：{data.get('error') or data.get('message')}", RED)
                elif tag == "env":
                    if data.get("ok", True) is False:
                        self.env_state.configure(text=data.get("error", "检查失败"), foreground=RED)
                    else:
                        vals = {
                            "account_root": data.get("account_root") or "未找到",
                            "db_keys": "已就绪" if data.get("db_keys") else "缺失 all_keys.json",
                            "image_key": "已就绪" if data.get("image_key") else "缺失 image_aes_key",
                            "ready": "就绪" if data.get("ready") else data.get("message", "未就绪"),
                        }
                        for k, v in vals.items():
                            self.env_labels[k].configure(
                                text=v,
                                foreground=GREEN if v in ("已就绪", "就绪") else
                                (RED if "缺失" in v or "未找到" in v else GREY))
                        self.env_state.configure(
                            text="环境检查完成", foreground=GREEN if data.get("ready") else GREY)
        except queue.Empty:
            pass
        try:
            if not self.win.winfo_exists():
                return
            self._after_id = self.win.after(100, self.poll)
        except tk.TclError:
            return


def open(parent=None):
    """parent 为 None 时创建独立根窗口（供 --settings 直接使用）。"""
    if parent is None:
        win = tk.Tk()
        try:
            ttk.Style().theme_use("vista")
        except tk.TclError:
            pass
    else:
        win = tk.Toplevel(parent)
    fm, fs, _family = fonts(win)
    SettingsUI(win, fm, fs)
    if parent is None:
        win.mainloop()
    return 0
