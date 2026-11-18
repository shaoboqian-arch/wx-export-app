# -*- coding: utf-8 -*-
"""设置页面：MCP 接入配置 + 本机环境状态。

写入 mcp.json 属于对用户环境的修改，只由用户点击「写入配置」触发；
采用合并写入 + 写前备份 + 原子替换，绝不覆盖已有连接器。
"""
import queue
import threading
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

import engine
import mcp_config
from common import APP_NAME, VERSION
from ui_kit import (
    BG, BORDER, CARD, CARD_LINE, ERR, MUTED, PAPER, SAND, SURFACE, TEXT, WHITE, OK, NEUTRAL, WARN,
    CapsuleButton, IslandEntry, Tooltip, _round_rect,
    apply_icon, apply_island_style, center, fonts,
)

W, H = 620, 660


def _mono(root):
    try:
        fams = set(tkfont.families(root))
    except Exception:
        fams = set()
    fam = next((n for n in ("Consolas", "Cascadia Mono", "Sarasa Mono SC",
                             "Noto Sans Mono", "Courier New")
                if n in fams), "TkFixedFont")
    return (fam, 9)


def _card(parent, title, font, row, pady=(0, 0)):
    outer = tk.Frame(parent, bg=CARD, highlightbackground=CARD_LINE, highlightthickness=1)
    outer.grid(row=row, column=0, sticky="ew", pady=pady)
    outer.columnconfigure(0, weight=1)
    inner = tk.Frame(outer, bg=CARD, padx=18, pady=12)
    inner.grid(row=0, column=0, sticky="ew")
    inner.columnconfigure(0, weight=1)
    tk.Label(inner, text=title, font=(font[0], 11, "bold"), bg=CARD, fg=TEXT, anchor="w").pack(
        anchor="w", pady=(0, 6))
    tk.Frame(inner, bg=CARD_LINE, height=1).pack(fill="x", pady=(0, 10))
    body = tk.Frame(inner, bg=CARD)
    body.pack(fill="both", expand=True)
    return body


class IslandCodeWell(tk.Frame):
    """圆角代码井：SURFACE 底、浅边，给只读配置片段用。"""

    def __init__(self, master, height=7, font=None, bg=CARD):
        super().__init__(master, bg=bg, highlightthickness=0)
        try:
            line = tkfont.Font(font=font).metrics("linespace") if font else 16
        except (tk.TclError, TypeError, ValueError):
            line = 16
        self.configure(width=400, height=line * height + 20)
        self.pack_propagate(False)
        self.grid_propagate(False)
        self._canvas = tk.Canvas(self, highlightthickness=0, bd=0, bg=bg)
        self._canvas.place(x=0, y=0, relwidth=1, relheight=1)
        inner = tk.Frame(self, bg=SURFACE)
        inner.place(x=10, y=8, relwidth=1, relheight=1, width=-22, height=-16)
        inner.columnconfigure(0, weight=1)
        inner.rowconfigure(0, weight=1)
        self.text = tk.Text(
            inner, height=height, font=font or ("Consolas", 9),
            relief="flat", borderwidth=0, highlightthickness=0, wrap="none",
            bg=SURFACE, fg=TEXT, insertbackground=TEXT,
            selectbackground=SAND, selectforeground=TEXT, padx=2, pady=2)
        self.text.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(inner, orient="vertical", command=self.text.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.text.configure(yscrollcommand=sb.set)
        self._canvas.bind("<Configure>", self._draw)
        self.bind("<Configure>", self._draw)
        self._draw()

    def _draw(self, _event=None):
        c = self._canvas
        c.delete("all")
        w, h = int(c.winfo_width()), int(c.winfo_height())
        if w < 8 or h < 8:
            return
        _round_rect(c, 1, 1, w - 1, h - 1, 8.0, fill=SURFACE, outline=BORDER)


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

        f = tk.Frame(win, bg=BG, padx=20, pady=14)
        f.pack(fill="both", expand=True)
        f.columnconfigure(0, weight=1)

        # ---------------- MCP 接入 ----------------
        mcp = _card(f, "MCP 接入", font_main, row=0)
        mcp.columnconfigure(1, weight=1)

        tk.Label(mcp, text="程序路径", font=font_small, bg=CARD, fg=MUTED).grid(
            row=0, column=0, sticky="w", pady=(0, 8), padx=(0, 12))
        cmd, _args = mcp_config.current_command()
        path = IslandEntry(mcp, font=font_small, path=True, path_font=font_small,
                           panel_bg=CARD, readonlybackground=WHITE)
        path.insert(0, cmd)
        path.entry.configure(state="readonly")
        path.grid(row=0, column=1, columnspan=2, sticky="ew", pady=(0, 8))

        tk.Label(mcp, text="配置片段", font=font_small, bg=CARD, fg=MUTED).grid(
            row=1, column=0, sticky="nw", pady=(6, 0), padx=(0, 12))
        well = IslandCodeWell(mcp, height=7, font=_mono(win), bg=CARD)
        well.grid(row=1, column=1, columnspan=2, sticky="ew", pady=(2, 10))
        self.preview = well.text
        self.preview.insert("1.0", mcp_config.fragment_text())
        self.preview.configure(state="disabled")

        btns = tk.Frame(mcp, bg=CARD)
        btns.grid(row=2, column=0, columnspan=3, sticky="w", pady=(0, 6))
        self.btn_copy = CapsuleButton(btns, text="复制配置", width=100, height=34,
                                      font=font_small, bg=CARD, command=self.copy_config)
        self.btn_copy.pack(side="left")
        self.btn_write = CapsuleButton(btns, text="写入配置", width=100, height=34,
                                       font=font_small, primary=True, bg=CARD,
                                       command=self.write_config)
        self.btn_write.pack(side="left", padx=(8, 0))
        self.btn_conn = CapsuleButton(btns, text="测试连接", width=100, height=34,
                                      font=font_small, bg=CARD, command=self.test_conn)
        self.btn_conn.pack(side="left", padx=(8, 0))
        self.cfg_state = tk.Label(mcp, text="", font=font_small, fg=MUTED, bg=CARD,
                                  wraplength=520, anchor="w", justify="left")
        self.cfg_state.grid(row=3, column=0, columnspan=3, sticky="w")
        tk.Label(mcp,
                 text="写入只增改本服务条目，不覆盖已有连接器；保存后需在连接器管理页点「信任」",
                 font=font_small, fg=MUTED, bg=CARD, wraplength=520,
                 anchor="w", justify="left").grid(
            row=4, column=0, columnspan=3, sticky="w", pady=(3, 0))

        # ---------------- 环境 ----------------
        env = _card(f, "环境", font_main, row=1, pady=(12, 0))
        env.columnconfigure(1, weight=1)
        self.env_labels = {}
        for i, (key, name) in enumerate((
                ("account_root", "账号目录"), ("db_keys", "数据库密钥"),
                ("image_key", "图片密钥"), ("ready", "状态"))):
            tk.Label(env, text=name, font=font_small, bg=CARD, fg=MUTED, width=12,
                     anchor="w").grid(row=i, column=0, sticky="w", pady=2)
            v = tk.Label(env, text="检查中…", font=font_small, fg=MUTED, bg=CARD,
                         anchor="w", wraplength=410, justify="left")
            v.grid(row=i, column=1, sticky="w", pady=2)
            self.env_labels[key] = v
        r = tk.Frame(env, bg=CARD)
        r.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.env_state = tk.Label(r, text="", font=font_small, fg=MUTED, bg=CARD,
                                  wraplength=400, anchor="w", justify="left")
        self.env_state.pack(side="left", fill="x", expand=True)
        self.btn_refresh = CapsuleButton(r, text="刷新", width=84, height=34,
                                         font=font_small, bg=CARD, command=self.refresh_env)
        self.btn_refresh.pack(side="right")

        # ---------------- 底部 ----------------
        bottom = tk.Frame(f, bg=BG)
        bottom.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        bottom.columnconfigure(0, weight=1)
        self.state = tk.Label(bottom, text="", font=font_small, fg=MUTED, bg=BG,
                              anchor="w", wraplength=470, justify="left")
        self.state.grid(row=0, column=0, sticky="w")
        self.close_btn = CapsuleButton(bottom, text="关闭", width=84, height=36,
                                       font=font_small, command=win.destroy)
        self.close_btn.grid(row=0, column=1)
        Tooltip(self.state, lambda: self.full_state, font=font_small)
        win.after_idle(path.reveal_end)

        self._after_id = win.after(80, self.poll)
        win.bind("<Destroy>", self._on_destroy, add="+")
        # 主窗 Esc=关闭，设置窗之前没绑，按了没反应
        win.bind("<Escape>", lambda _e: (win.destroy(), "break")[1])
        self.q.put(("inspect", None))
        self.refresh_env()

    def _on_destroy(self, event=None):
        if event and event.widget is not self.win:
            return
        aid = getattr(self, "_after_id", None)
        if aid is not None:
            try:
                self.win.after_cancel(aid)
            except Exception:
                pass
            self._after_id = None

    def set_state(self, text, color=MUTED):
        self.full_state = text
        self.state.configure(text=text if len(text) <= 70 else text[:67] + "…",
                             foreground=color)

    def copy_config(self):
        try:
            self.win.clipboard_clear()
            self.win.clipboard_append(mcp_config.fragment_text())
            self.set_state("已复制配置片段", OK)
        except tk.TclError as e:
            self.set_state(f"复制失败：{e}", ERR)

    def _bg(self, fn, tag):
        def work():
            try:
                self.q.put((tag, fn()))
            except Exception as e:
                self.q.put((tag, {"ok": False, "error": f"{type(e).__name__}: {e}"}))
        threading.Thread(target=work, daemon=True).start()

    def _busy(self, tag, on):
        """进行中禁按钮：连点会并发跑两次 merge/握手，备份文件越堆越多。"""
        btn = {"write": "btn_write", "conn": "btn_conn", "env": "btn_refresh"}.get(tag)
        w = getattr(self, btn, None) if btn else None
        if w is not None:
            w.configure(state="disabled" if on else "normal")

    def write_config(self):
        if self.btn_write.cget("state") == "disabled":
            return
        self._busy("write", True)
        self.set_state("正在写入配置…")
        self._bg(mcp_config.merge_into, "write")

    def test_conn(self):
        if self.btn_conn.cget("state") == "disabled":
            return
        self._busy("conn", True)
        self.set_state("正在测试连接…")
        self._bg(mcp_config.test_connection, "conn")

    def refresh_env(self):
        if self.btn_refresh.cget("state") == "disabled":
            return
        self._busy("env", True)
        self.env_state.configure(text="检查中…")
        self._bg(lambda: engine.safe_call(engine.status), "env")

    def _inspect(self):
        s = mcp_config.inspect_config()
        if not s.get("ok"):
            return f"配置文件读取失败：{s.get('error')}", ERR
        state = "已配置" if s["has_wx"] else "未配置"
        if s["has_wx"] and not s["command_matches"]:
            state = "已配置（路径与当前程序不一致）"
        return (f"{state} · 另有 {s['other_count']} 个连接器 · "
                f"{s['path']}"), MUTED

    def poll(self):
        try:
            while True:
                tag, data = self.q.get_nowait()
                if tag in ("write", "conn", "env"):
                    self._busy(tag, False)
                if tag == "inspect":
                    text, color = self._inspect()
                    self.cfg_state.configure(text=text, foreground=color)
                elif tag == "write":
                    if data.get("ok"):
                        msg = (f"{data['action']}成功 · 保留 {data['preserved_count']} 个已有连接器"
                               + (f" · 已备份 {data['backup']}" if data.get("backup") else ""))
                        self.set_state(msg, OK)
                        text, color = self._inspect()
                        self.cfg_state.configure(text=text, foreground=color)
                    else:
                        self.set_state(f"写入失败：{data.get('error')}", ERR)
                elif tag == "conn":
                    if data.get("ok"):
                        self.set_state(
                            f"连接正常 · {data.get('protocol')} · {len(data.get('tools', []))} 个工具 · "
                            f"环境{'就绪' if data.get('ready') else data.get('message', '未就绪')} · "
                            f"{data.get('elapsed_ms', 0)} ms", OK)
                    else:
                        self.set_state(f"连接失败：{data.get('error') or data.get('message')}", ERR)
                elif tag == "env":
                    if data.get("ok", True) is False:
                        self.env_state.configure(text=data.get("error", "检查失败"), foreground=ERR)
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
                                foreground=OK if v in ("已就绪", "就绪") else
                                (ERR if "缺失" in v or "未找到" in v else MUTED))
                        self.env_state.configure(
                            text="环境检查完成", foreground=OK if data.get("ready") else MUTED)
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
