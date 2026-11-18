# -*- coding: utf-8 -*-
"""环境初始化窗口：微信账号目录、数据库密钥、图片密钥。

只在环境未就绪时出现（主界面主按钮会变成「初始化本机环境」）。
三个动作：
  · 选账号目录 —— 支持多账号与非默认存储路径
  · 提取密钥   —— 从微信进程内存取数据库密钥，普通权限即可
  · 检测图片密钥 —— 需用户先在微信里点开一张收藏图片；失败可手动填写
"""
import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, ttk

import engine
from common import APP_NAME, VERSION
from ui_kit import (
    BG, TEXT, MUTED, ERR, BORDER, CARD, CARD_LINE, OK, NEUTRAL, WARN,
    Tooltip, apply_icon, apply_island_style,
    center, fonts, CapsuleButton, IslandEntry,
)

W, H = 640, 350


def _card(parent, title, font, row, pady=(0, 0)):
    outer = tk.Frame(parent, bg=CARD, highlightbackground=CARD_LINE, highlightthickness=1)
    outer.grid(row=row, column=0, sticky="ew", pady=pady)
    outer.columnconfigure(0, weight=1)
    inner = tk.Frame(outer, bg=CARD, padx=16, pady=12)
    inner.grid(row=0, column=0, sticky="ew")
    inner.columnconfigure(0, weight=1)
    tk.Label(inner, text=title, font=(font[0], 10, "bold"), bg=CARD, fg=TEXT, anchor="w").pack(
        anchor="w", pady=(0, 6))
    tk.Frame(inner, bg=CARD_LINE, height=1).pack(fill="x", pady=(0, 8))
    body = tk.Frame(inner, bg=CARD)
    body.pack(fill="both", expand=True)
    return body


class SetupEnvUI:
    def __init__(self, win, font_main, font_small, on_done=None):
        self.win = win
        self.on_done = on_done
        self.q = queue.Queue()
        self.roots = []
        self.busy_flag = False
        self.stop_flag = False
        self.full_state = ""
        self.manual_open = False

        win.title(f"环境初始化 · {APP_NAME} {VERSION}")
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

        # ---------------- 账号目录 ----------------
        acc = _card(f, "微信账号", font_main, row=0)
        acc.columnconfigure(0, weight=1)

        self.combo = ttk.Combobox(acc, state="readonly", font=font_small)
        self.combo.grid(row=0, column=0, sticky="ew")
        self.combo.bind("<<ComboboxSelected>>", lambda _e: self.refresh_status())
        CapsuleButton(acc, text="浏览…", width=84, height=34, font=font_small,
                      bg=CARD, command=self.pick).grid(row=0, column=1, padx=(8, 0))
        self.root_state = tk.Label(acc, text="", font=font_small, bg=CARD, fg=NEUTRAL,
                                   wraplength=540, anchor="w")
        self.root_state.grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))

        # ---------------- 密钥 ----------------
        keys = _card(f, "密钥", font_main, row=1, pady=(10, 0))
        keys.columnconfigure(1, weight=1)

        tk.Label(keys, text="数据库密钥", font=font_small, bg=CARD, fg=MUTED, width=11,
                 anchor="w").grid(row=0, column=0, sticky="w", pady=2)
        self.db_state = tk.Label(keys, text="检查中…", font=font_small, bg=CARD,
                                 fg=NEUTRAL, anchor="w")
        self.db_state.grid(row=0, column=1, sticky="w", pady=2)
        self.db_btn = CapsuleButton(keys, text="提取密钥", command=self.do_extract,
                                    width=100, height=32, font=font_small, bg=CARD)
        self.db_btn.grid(row=0, column=2, sticky="e", pady=2)

        tk.Label(keys, text="图片密钥", font=font_small, bg=CARD, fg=MUTED, width=11,
                 anchor="w").grid(row=1, column=0, sticky="w", pady=(6, 2))
        self.img_state = tk.Label(keys, text="检查中…", font=font_small, bg=CARD,
                                  fg=NEUTRAL, anchor="w")
        self.img_state.grid(row=1, column=1, sticky="w", pady=(6, 2))
        ib = tk.Frame(keys, bg=CARD)
        ib.grid(row=1, column=2, sticky="e", pady=(6, 2))
        self.img_btn = CapsuleButton(ib, text="开始检测", command=self.do_capture,
                                     width=92, height=32, font=font_small, bg=CARD)
        self.img_btn.pack(side="left")
        CapsuleButton(ib, text="手动填写", command=self.toggle_manual,
                      width=90, height=32, font=font_small, bg=CARD).pack(side="left", padx=(6, 0))

        self.manual = tk.Frame(keys, bg=CARD)
        self.manual.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        self.manual.columnconfigure(0, weight=1)
        # 图片密钥是 16/32 位十六进制，明文回显等于肩窥即泄露；默认遮蔽，按需显形
        self.manual_entry = IslandEntry(self.manual, font=font_small, show="*", panel_bg=CARD)
        self.manual_entry.grid(row=0, column=0, sticky="ew")
        self.reveal_btn = CapsuleButton(self.manual, text="显示", command=self.toggle_reveal,
                                        width=64, height=32, font=font_small, bg=CARD)
        self.reveal_btn.grid(row=0, column=1, padx=(6, 0))
        CapsuleButton(self.manual, text="保存", command=self.save_manual,
                      width=64, height=32, font=font_small, bg=CARD).grid(row=0, column=2, padx=(6, 0))
        self.manual.grid_remove()

        # ---------------- 底部 ----------------
        bottom = tk.Frame(f, bg=BG)
        bottom.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        bottom.columnconfigure(0, weight=1)
        self.state = tk.Label(bottom, text="", font=font_small, bg=BG, fg=NEUTRAL,
                              anchor="w", wraplength=470, justify="left")
        self.state.grid(row=0, column=0, sticky="w")
        self.close_btn = CapsuleButton(bottom, text="完成", command=self.close,
                                       width=84, height=34, primary=True, font=font_small, bg=BG)
        self.close_btn.grid(row=0, column=1)
        Tooltip(self.state, lambda: self.full_state, font=font_small)

        self._after_id = win.after(80, self.poll)
        self.reload_roots()

    # ---------------------------------------------------------------- 状态
    def set_state(self, text, color=NEUTRAL):
        self.full_state = text
        self.state.configure(text=text if len(text) <= 64 else text[:61] + "…",
                             foreground=color)

    @staticmethod
    def _label(x):
        p = x.get("path") or ""
        tail = p if len(p) <= 46 else "…" + p[-45:]
        return f"{x.get('name', '')}   ·   {tail}"

    def current_root(self):
        i = self.combo.current()
        if 0 <= i < len(self.roots):
            return self.roots[i]["path"]
        return ""

    def reload_roots(self):
        r = engine.candidate_roots()
        self.roots = r.get("roots") or []
        if self.roots:
            self.combo["values"] = [self._label(x) for x in self.roots]
            self.combo.current(0)
        else:
            self.combo["values"] = ["未找到微信账号目录"]
            self.combo.current(0)
        self.refresh_status()

    def refresh_status(self):
        root = self.current_root()
        p = engine.probe_env(root or None)
        if root:
            has_fav = os.path.isfile(os.path.join(root, "db_storage", "favorite", "favorite.db"))
            self.root_state.configure(
                text="收藏库已找到" if has_fav else "该目录下没有收藏数据库",
                foreground=OK if has_fav else ERR)
        else:
            self.root_state.configure(text="未找到，请点「浏览…」指定 xwechat_files 下的账号目录",
                                      foreground=ERR)
        if not p.get("ok", True):
            self.set_state(p.get("error", "环境检查失败"), ERR)
            return
        db_ok, img_ok = bool(p.get("db_keys")), bool(p.get("image_key"))
        self.db_state.configure(text="已就绪" if db_ok else "未就绪",
                                foreground=OK if db_ok else ERR)
        self.img_state.configure(text="已就绪" if img_ok else "未就绪",
                                 foreground=OK if img_ok else ERR)
        self.db_btn.configure(text="重新提取" if db_ok else "提取密钥")
        self.img_btn.configure(text="重新检测" if img_ok else "开始检测")
        if p.get("ready"):
            self.set_state("环境就绪，可以开始导出", OK)
        elif not db_ok:
            self.set_state("先提取数据库密钥", NEUTRAL)
        elif not img_ok:
            self.set_state("在微信里打开一张收藏图片，再点「开始检测」", NEUTRAL)

    def pick(self):
        d = filedialog.askdirectory(title="选择微信账号目录（包含 db_storage 的那一级）")
        if not d:
            return
        d = os.path.normpath(d)
        if os.path.basename(d).lower() == "db_storage":
            d = os.path.dirname(d)
        self.roots.insert(0, {"path": d, "name": os.path.basename(d)})
        self.combo["values"] = [self._label(x) for x in self.roots]
        self.combo.current(0)
        self.refresh_status()

    # ---------------------------------------------------------------- 后台
    def _bg(self, fn, tag):
        def work():
            try:
                self.q.put((tag, fn()))
            except Exception as e:
                self.q.put((tag, {"ok": False, "error": f"{type(e).__name__}: {e}"}))
        threading.Thread(target=work, daemon=True).start()

    def _lock(self, on):
        self.busy_flag = on
        self.db_btn.configure(state="disabled" if on else "normal")
        self.img_btn.configure(state="disabled" if on else "normal")

    def do_extract(self):
        if self.busy_flag:
            return
        root = self.current_root()
        if not root:
            self.set_state("先选择微信账号目录", ERR)
            return
        db = os.path.join(root, "db_storage")
        self._lock(True)
        self.set_state("正在提取数据库密钥…")
        self._bg(lambda: engine.setup_db_keys(
            db, on_step=lambda m: self.q.put(("step", m))), "db")

    def do_capture(self):
        if self.busy_flag:
            return
        root = self.current_root()
        if not root:
            self.set_state("先选择微信账号目录", ERR)
            return
        n = engine.image_sample_count(root)
        if not n.get("count"):
            self.set_state("本机还没有图片缓存：先在微信里打开一张收藏图片", WARN)
            return
        self.stop_flag = False
        self._lock(True)
        self.set_state(f"正在扫描（{n['count']} 个校验样本）…")
        self._bg(lambda: engine.capture_image_key(
            root, on_tick=lambda a, b, c: self.q.put(("tick", (a, b, c))),
            should_stop=lambda: self.stop_flag), "img")

    def toggle_reveal(self):
        """在明文/掩码之间切换；只影响本机输入框，不落盘。"""
        shown = self.manual_entry.entry.cget("show") == ""
        self.manual_entry.entry.configure(show="" if not shown else "*")
        self.reveal_btn.configure(text="隐藏" if not shown else "显示")

    def toggle_manual(self):
        self.manual_open = not self.manual_open
        if self.manual_open:
            self.manual.grid()
            self.win.geometry(f"{W}x{H + 46}")
            self.set_state("粘贴或输入 16 位图片密钥，点「保存」")
        else:
            self.manual.grid_remove()
            self.win.geometry(f"{W}x{H}")

    def save_manual(self):
        key = self.manual_entry.get().strip()
        if not key:
            self.set_state("先填写密钥", ERR)
            return
        r = engine.set_image_key_manual(key)
        if r.get("ok"):
            self.set_state(r.get("message", "已写入图片密钥"), OK)
            self.refresh_status()
        else:
            self.set_state(r.get("error", "写入失败"), ERR)

    # ---------------------------------------------------------------- 轮询
    def poll(self):
        try:
            while True:
                tag, data = self.q.get_nowait()
                if tag == "step":
                    self.set_state(data)
                elif tag == "tick":
                    a, b, phase = data
                    if phase == "scan":
                        self.set_state(f"第 {a}/{b} 轮扫描中…")
                    elif phase == "miss":
                        self.set_state(f"第 {a} 轮未命中，继续扫描…", WARN)
                    else:
                        self.set_state(f"第 {a} 轮 · 已扫 {phase} 个内存区")
                elif tag == "db":
                    self._lock(False)
                    if data.get("ok"):
                        self.set_state(data.get("message", "密钥已写入"), OK)
                    else:
                        self.set_state(data.get("error", "提取失败"), ERR)
                    self.refresh_status()
                elif tag == "img":
                    self._lock(False)
                    if data.get("ok"):
                        self.set_state(data.get("message", "已捕获图片密钥"), OK)
                    else:
                        self.set_state(data.get("error", "未捕获到"), ERR)
                    self.refresh_status()
        except queue.Empty:
            pass
        try:
            if not self.win.winfo_exists():
                return
            self._after_id = self.win.after(100, self.poll)
        except tk.TclError:
            return

    def close(self):
        self.stop_flag = True
        if self.busy_flag:
            self.set_state("正在收尾，稍候再关…", WARN)
            self.win.after(600, self.close)
            return
        root = self.current_root()
        if root:
            engine.set_account_root(root)
        if self.on_done:
            try:
                self.on_done()
            except Exception:
                pass
        try:
            self.win.destroy()
        except tk.TclError:
            pass


def open(parent=None, on_done=None):
    if parent is None:
        win = tk.Tk()
        try:
            ttk.Style().theme_use("vista")
        except tk.TclError:
            pass
    else:
        win = tk.Toplevel(parent)
    fm, fs, _family = fonts(win)
    SetupEnvUI(win, fm, fs, on_done=on_done)
    if parent is None:
        win.mainloop()
    return 0
