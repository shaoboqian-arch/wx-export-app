# -*- coding: utf-8 -*-
"""海岛风格主界面：选目标文件夹 + 日期范围 → 导出；完成后可补导未取到原件的条目。

常驻可见元素只有四个：目标文件夹、日期范围、导出按钮、状态行。
导出或完成后按需展开进度表、未取到清单、解决提示与补导按钮。
"""
import datetime as dt
import json
import os
import queue
import threading
import traceback
import tkinter as tk
from tkinter import filedialog, ttk

import engine
import settings
import export_list as el
from common import APP_NAME, VERSION, prefs_path
from ui_kit import (
    BG, TEXT, MUTED, ERR, MINT, SAND, BORDER, ERR_BG,
    Tooltip, apply_icon, apply_island_style, center, fonts,
    CapsuleButton, IslandEntry, IslandBar, PhaseBadge, DatePicker,
)

W = 600
BASE_H = 268
PANEL_H = 360
HINT_H = 96
PROG_H = 22
GROUPS = (("image", "图片"), ("video", "视频"), ("other", "其他资料"))
PHASES = (("index", "读取"), ("export", "导出"), ("report", "清单"), ("done", "完成"))

RETRY_HINT = (
    "未取到原件时，请按下面做完再点「补导」（只补这些条目，不必重选日期和文件夹）：\n"
    "· 图片：请在微信中点开该图大图并等加载完成\n"
    "· 视频：请在微信中真正播放该视频\n"
    "· 头部缺失：补齐图片密钥后再补导"
)


def _load_prefs():
    try:
        with open(prefs_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_prefs(dest, frm, to, last_batch=""):
    try:
        p = prefs_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        cur = _load_prefs()
        cur.update(dest=dest, frm=frm, to=to)
        if last_batch:
            cur["last_batch"] = last_batch
        with open(p, "w", encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False)
    except OSError:
        pass


def _default_range():
    today = dt.date.today()
    return today.replace(day=1).isoformat(), today.isoformat()


def _blank_stats():
    return {g: {"total": 0, "ok": 0, "failed": 0} for g, _ in GROUPS}


class App:
    def __init__(self, root, font_main, font_small, family):
        self.root = root
        self.q = queue.Queue()
        self.busy = False
        self.dirty = False
        self.panel_visible = False
        self.hint_visible = False
        self.full_state = ""
        self.env_message = ""
        self.env_ready = False
        self.last_batch = ""
        self.stats = _blank_stats()
        self.failed = []
        self.pack_stat = {"ok": 0, "failed": 0}
        self.pack_failed = []
        self.font_main = font_main
        self.font_small = font_small
        self._retrying = False

        root.title(f"{APP_NAME}  {VERSION}")
        root.resizable(True, True)
        root.minsize(W, BASE_H)
        root.configure(bg=BG)
        apply_icon(root)
        apply_island_style(root)
        center(root, W, BASE_H)

        f = tk.Frame(root, bg=BG, padx=20, pady=16)
        f.pack(fill="both", expand=True)
        f.columnconfigure(1, weight=1)
        f.rowconfigure(5, weight=1)

        tk.Label(f, text="目标文件夹", font=font_main, bg=BG, fg=MUTED).grid(
            row=0, column=0, sticky="w", pady=(2, 12))
        self.dest = IslandEntry(f, font=font_main)
        self.dest.grid(row=0, column=1, sticky="ew", pady=(2, 12), padx=(12, 8))
        self.browse_btn = CapsuleButton(
            f, text="浏览…", command=self.pick, width=84, height=36,
            font=font_small, primary=False)
        self.browse_btn.grid(row=0, column=2, pady=(2, 12))

        tk.Label(f, text="日期范围", font=font_main, bg=BG, fg=MUTED).grid(
            row=1, column=0, sticky="w", pady=(0, 18))
        self.date_row = rng = tk.Frame(f, bg=BG)
        rng.grid(row=1, column=1, columnspan=2, sticky="ew", padx=(12, 0), pady=(0, 18))
        rng.columnconfigure(0, weight=1)
        rng.columnconfigure(3, weight=1)
        self.frm = IslandEntry(rng, font=font_main, width=10, pxwidth=160)
        self.to = IslandEntry(rng, font=font_main, width=10, pxwidth=160)
        self.frm.grid(row=0, column=0, sticky="ew")
        self._cal_from = DatePicker(self.frm, font=font_small)
        CapsuleButton(rng, text="▾", command=self._cal_from.toggle, width=28, height=36,
                      font=font_small).grid(row=0, column=1, padx=(4, 0))
        tk.Label(rng, text="  —  ", font=font_main, bg=BG, fg=MUTED).grid(row=0, column=2)
        self.to.grid(row=0, column=3, sticky="ew")
        self._cal_to = DatePicker(self.to, font=font_small)
        CapsuleButton(rng, text="▾", command=self._cal_to.toggle, width=28, height=36,
                      font=font_small).grid(row=0, column=4, padx=(4, 0))

        self.btn = CapsuleButton(f, text="导出", command=self.start, primary=True,
                                 height=42, font=(family, 11, "bold"))
        self.btn.grid(row=2, column=0, columnspan=2, sticky="ew", padx=(0, 8))
        self.settings_btn = CapsuleButton(
            f, text="设置", command=self.open_settings,
            width=84, height=42, font=font_small)
        self.settings_btn.grid(row=2, column=2)

        self.prog = IslandBar(f, height=10)
        self.prog.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(12, 0))
        self.prog.grid_remove()

        sf = tk.Frame(f, bg=BG)
        sf.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(12, 0))
        sf.columnconfigure(0, weight=1)
        self.state = tk.Label(sf, text="", font=font_small, fg=MUTED, bg=BG,
                              anchor="w", wraplength=300, justify="left",
                              takefocus=1)
        self.state.grid(row=0, column=0, sticky="w")
        self.info = tk.Label(sf, text="ⓘ", font=font_small, fg="#b45309", bg=BG,
                             cursor="question_arrow")
        self.info.grid(row=0, column=1, padx=(6, 0))
        self.info.grid_remove()
        self.retry_btn = CapsuleButton(sf, text="补导", command=self.start_retry,
                                       width=64, height=28, font=font_small)
        self.retry_btn.grid(row=0, column=2, padx=(6, 0))
        self.retry_btn.grid_remove()
        self.open_btn = CapsuleButton(sf, text="打开", command=self.open_result,
                                      width=64, height=28, font=font_small)
        self.open_btn.grid(row=0, column=3, padx=(6, 0))
        self.open_btn.grid_remove()
        Tooltip(self.state, lambda: self.full_state, font=font_small)
        Tooltip(self.info, lambda: self.env_message, font=font_small)
        # 悬停之外再给一条通路：点一下/键盘回车能把完整文本弹出来，
        # 否则键盘用户和读屏只能拿到截断后的那半句。
        self.info.configure(takefocus=1)
        for w, getter in ((self.state, lambda: self.full_state),
                          (self.info, lambda: self.env_message)):
            w.bind("<Button-1>", lambda _e, g=getter: self.show_full(g()))
            w.bind("<Return>", lambda _e, g=getter: self.show_full(g()))
            w.bind("<space>", lambda _e, g=getter: self.show_full(g()))

        self.panel = self._build_panel(f)
        self.panel.grid(row=5, column=0, columnspan=3, sticky="nsew", pady=(12, 0))
        self.panel.grid_remove()

        prefs = _load_prefs()
        self.dest.insert(0, prefs.get("dest") or os.path.join(
            os.path.expanduser("~"), "Desktop", "微信收藏导出"))
        a, b = _default_range()
        self.frm.insert(0, prefs.get("frm") or a)
        self.to.insert(0, prefs.get("to") or b)

        root.bind("<Return>", lambda _e: self.primary_action())
        root.bind("<Escape>", lambda _e: self.close())
        root.bind("<Configure>", self._on_configure)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self._after_id = root.after(60, self.poll)
        threading.Thread(target=self.env_check, daemon=True).start()
        self.root.after(120, lambda: self._restore_retry(prefs))

    # ---------------------------------------------------------------- 进度表
    def _build_panel(self, parent):
        p = tk.Frame(parent, bg=BG, highlightbackground=BORDER,
                     highlightthickness=1, padx=12, pady=10)
        p.columnconfigure(0, weight=1)
        p.rowconfigure(3, weight=1)

        hdr = tk.Frame(p, bg=BG)
        hdr.grid(row=0, column=0, columnspan=2, sticky="ew")
        hdr.columnconfigure(4, weight=1)
        self.phase_labels = {}
        for i, (key, label) in enumerate(PHASES):
            badge = PhaseBadge(hdr, text=label, font=self.font_small, width=70, height=24)
            badge.grid(row=0, column=i, padx=(0, 8))
            self.phase_labels[key] = badge
        CapsuleButton(hdr, text="收起", command=self.hide_panel,
                      width=64, height=26, font=self.font_small).grid(
            row=0, column=5, sticky="e")

        tbl = tk.Frame(p, bg=BG)
        tbl.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(12, 8))
        for j, c in enumerate(("类别", "总数", "完整", "未取到原件")):
            tk.Label(tbl, text=c, font=self.font_small, fg=MUTED, bg=BG,
                     width=12 if j else 14,
                     anchor="w" if j == 0 else "e").grid(row=0, column=j, padx=(0, 6))
        self.cell = {}
        for i, (g, name) in enumerate(GROUPS, 1):
            tk.Label(tbl, text=name, font=self.font_small, fg=TEXT, bg=BG,
                     width=14, anchor="w").grid(row=i, column=0, sticky="w")
            for j, key in enumerate(("total", "ok", "failed"), 1):
                v = tk.Label(tbl, text="0", font=self.font_small, fg=TEXT, bg=BG,
                             width=12, anchor="e")
                v.grid(row=i, column=j, padx=(0, 6))
                self.cell[(g, key)] = v

        self.fail_title = tk.Label(p, text="未取到原件 0",
                                   font=self.font_small, fg=MUTED, bg=BG, anchor="w")
        self.fail_title.grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 4))
        self.tree = ttk.Treeview(p, columns=("name", "reason"),
                                 show="headings", height=5)
        self.tree.heading("name", text="文件名")
        self.tree.heading("reason", text="原因")
        self.tree.column("name", width=310, anchor="w")
        self.tree.column("reason", width=210, anchor="w")
        sb = ttk.Scrollbar(p, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.grid(row=3, column=0, sticky="nsew")
        sb.grid(row=3, column=1, sticky="ns")

        self.hint = tk.Frame(p, bg=ERR_BG, padx=10, pady=8)
        self.hint.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.hint_label = tk.Label(
            self.hint, text=RETRY_HINT, font=self.font_small, fg=TEXT, bg=ERR_BG,
            justify="left", anchor="w", wraplength=520)
        self.hint_label.pack(fill="x")
        self.hint.grid_remove()
        return p

    def resize(self):
        h = BASE_H
        if self.busy or self.prog.winfo_manager():
            h += PROG_H
        if self.panel_visible:
            h += PANEL_H
            if self.hint_visible:
                h += HINT_H
        self.root.minsize(W, h)
        self.root.update_idletasks()
        w = max(W, int(self.root.winfo_width() or W))
        cur_h = int(self.root.winfo_height() or 0)
        if cur_h < h or not self.panel_visible:
            self.root.geometry(f"{w}x{h}")

    def _on_configure(self, event):
        if event.widget is not self.root:
            return
        state_w = max(160, int(event.width) - 280)
        hint_w = max(200, int(event.width) - 80)
        if int(self.state.cget("wraplength") or 0) != state_w:
            self.state.configure(wraplength=state_w)
        if int(self.hint_label.cget("wraplength") or 0) != hint_w:
            self.hint_label.configure(wraplength=hint_w)

    def show_panel(self):
        self.panel_visible = True
        self.panel.grid()
        self.resize()

    def hide_panel(self):
        self.panel_visible = False
        self.panel.grid_remove()
        self.resize()

    def show_retry_ui(self, show=True):
        self.hint_visible = bool(show)
        if show:
            self.hint.grid()
            self.retry_btn.grid()
        else:
            self.hint.grid_remove()
            self.retry_btn.grid_remove()
        self.resize()

    def set_phase(self, name):
        order = [k for k, _ in PHASES]
        idx = order.index(name) if name in order else -1
        for i, (k, _) in enumerate(PHASES):
            if name == "done" or i < idx:
                mode = "done"
            elif i == idx:
                mode = "now"
            else:
                mode = "pending"
            badge = self.phase_labels[k]
            if hasattr(badge, "set_mode"):
                badge.set_mode(mode)
            else:
                badge.configure(foreground={"done": "#1a7f37", "now": "#b45309",
                                            "pending": "#666666"}[mode])

    def refresh_table(self):
        for (g, key), lbl in self.cell.items():
            lbl.configure(text=str(self.stats[g][key]))
        bad = len(self.failed)
        title = f"未取到原件 {bad}"
        if self.pack_failed:
            title += f" · 打包失败 {len(self.pack_failed)}"
        self.fail_title.configure(
            text=title, fg=ERR if (bad or self.pack_failed) else MUTED)
        self.tree.delete(*self.tree.get_children())
        for it in self.failed:
            label = it.get("display") or it.get("name") or el.display_name(it)
            if not str(label).strip() or str(label).strip() == "（无文件）":
                label = el.display_name(it)
            self.tree.insert("", "end", values=(label, it.get("reason") or ""))
        for p in self.pack_failed:
            self.tree.insert("", "end", values=(
                f'包：{p.get("title") or "（无标题）"}',
                p.get("reason") or "HTML 生成失败"))
        self.dirty = False

    def handle_event(self, e):
        typ = e.get("type")
        if typ == "phase":
            self.set_phase(e.get("name", ""))
        elif typ == "pack":
            p = e.get("pack") or {}
            if p.get("ok"):
                self.pack_stat["ok"] += 1
            else:
                self.pack_stat["failed"] += 1
                self.pack_failed.append(p)
            self.dirty = True
        elif typ == "item":
            total = e.get("total")
            index = e.get("index")
            if total:
                self.prog.set_progress(index or 0, total)
            if self._retrying:
                return
            it = e["item"]
            g = it["group"]
            self.stats[g]["total"] += 1
            self.stats[g]["ok" if it["ok"] else "failed"] += 1
            if not it["ok"]:
                self.failed.append(it)
            self.dirty = True

    # ---------------------------------------------------------------- 其余
    def env_check(self):
        self.q.put(("env", engine.safe_call(engine.status)))

    def refresh_env(self):
        threading.Thread(target=self.env_check, daemon=True).start()

    def primary_action(self):
        if self.env_ready:
            self.start()
        else:
            self.open_setup_env()

    def _sync_primary(self):
        if self.env_ready:
            self.btn.configure(text="导出", command=self.start)
        else:
            self.btn.configure(text="初始化本机环境", command=self.open_setup_env)

    def apply_env(self, payload):
        self.env_ready = bool(payload.get("ready"))
        missing = []
        if not payload.get("account_root"):
            missing.append("微信账号目录")
        if not payload.get("db_keys"):
            missing.append("数据库密钥")
        if not payload.get("image_key"):
            missing.append("图片密钥")
        if self.env_ready:
            self.show_env("")
            if not self.busy:
                self.set_state("", MUTED)
        else:
            self.show_env(("点「初始化本机环境」补齐：" + "、".join(missing)) if missing
                          else (payload.get("message") or "环境未就绪"))
            if not self.busy:
                self.set_state(("缺少" + "、".join(missing)) if missing
                               else payload.get("message", "环境未就绪"), ERR)
        self._sync_primary()

    def open_setup_env(self):
        import setup_env
        setup_env.open(self.root, on_done=self.refresh_env)

    def pick(self):
        d = filedialog.askdirectory(title="选择目标文件夹")
        if d:
            self.dest.delete(0, "end")
            self.dest.insert(0, os.path.normpath(d))

    def set_state(self, text, color=MUTED):
        self.full_state = text
        self.state.configure(text=text if len(text) <= 50 else text[:47] + "…",
                             fg=color)

    def show_env(self, message):
        self.env_message = message
        if message:
            self.info.grid()
        else:
            self.info.grid_remove()

    def show_full(self, text):
        """把截断的状态/环境说明完整弹出来（点击或回车触发）。"""
        text = (text or "").strip()
        if not text:
            return "break"
        try:
            from tkinter import messagebox
            messagebox.showinfo("详情", text, parent=self.root)
        except tk.TclError:
            pass
        return "break"

    def open_result(self):
        if self.last_batch and os.path.isdir(self.last_batch):
            try:
                os.startfile(self.last_batch)
            except OSError:
                pass

    def open_settings(self):
        settings.open(self.root)

    def close(self):
        if self.busy:
            self.set_state("导出中，请稍候", "#b45309")
            return
        try:
            for aid in self.root.tk.call("after", "info"):
                self.root.after_cancel(aid)
        except (tk.TclError, TypeError):
            pass
        self.root.destroy()

    def _restore_retry(self, prefs):
        batch = (prefs or {}).get("last_batch") or ""
        if not batch or not os.path.isdir(batch):
            return
        st = engine.read_retry_state(batch)
        if not st.get("pending_lids"):
            return
        self.last_batch = batch
        try:
            import sender_archive as sa
            rows = sa.read_report(os.path.join(batch, "_导出报告.csv"))
            _items, failed, by_kind = engine._items_from_report(rows)
            self.stats = by_kind
            self.failed = failed
            self.dirty = True
            self.set_phase("done")
            self.show_panel()
            self.show_retry_ui(True)
            self.open_btn.grid()
            self.set_state("上次导出还有未取到的原件，处理完后可直接补导", TEXT)
            self.refresh_table()
        except Exception:
            self.last_batch = batch
            self.open_btn.grid()
            self.show_retry_ui(True)
            self.set_state("发现可补导的上次批次，点「补导」继续", TEXT)

    def start(self):
        if self.busy:
            return
        if not self.env_ready:
            self.open_setup_env()
            return
        dest = self.dest.get().strip()
        frm, to = self.frm.get().strip(), self.to.get().strip()
        if not dest:
            self.set_state("请选择目标文件夹", ERR)
            return
        for label, value in (("起始日期", frm), ("结束日期", to)):
            try:
                dt.datetime.strptime(value, "%Y-%m-%d")
            except ValueError:
                self.set_state(f"{label}格式：YYYY-MM-DD", ERR)
                return
        try:
            if dt.date.fromisoformat(to) < dt.date.fromisoformat(frm):
                self.set_state("结束日期早于起始日期", ERR)
                return
        except ValueError:
            self.set_state("日期格式：YYYY-MM-DD", ERR)
            return

        self.busy = True
        self._retrying = False
        self.last_batch = ""
        self.stats = _blank_stats()
        self.failed = []
        self.pack_stat = {"ok": 0, "failed": 0}
        self.pack_failed = []
        self.dirty = True
        self.set_phase("index")
        self.open_btn.grid_remove()
        self.show_retry_ui(False)
        self.btn.configure(state="disabled", text="导出中…")
        self.prog.grid()
        self.prog.set_progress(0, 1)
        self.show_panel()
        self.set_state("正在读取收藏库…")
        _save_prefs(dest, frm, to)

        def work():
            r = engine.safe_call(
                engine.export, frm, to, dest,
                on_progress=lambda m: self.q.put(("progress", m)),
                on_event=lambda e: self.q.put(("event", e)))
            self.q.put(("done", r))

        threading.Thread(target=work, daemon=True).start()

    def start_retry(self):
        if self.busy or not self.last_batch:
            return
        self.busy = True
        self._retrying = True
        self.retry_btn.configure(state="disabled", text="补导中…")
        self.btn.configure(state="disabled")
        self.prog.grid()
        self.prog.set_progress(0, 1)
        self.show_panel()
        self.set_phase("index")
        self.set_state("正在补导未取到的原件…")
        self.resize()

        batch = self.last_batch

        def work():
            r = engine.safe_call(
                engine.retry_missing, batch,
                on_progress=lambda m: self.q.put(("progress", m)),
                on_event=lambda e: self.q.put(("event", e)))
            self.q.put(("retry_done", r))

        threading.Thread(target=work, daemon=True).start()

    def _finish_export(self, payload, retried=False):
        self.busy = False
        self._retrying = False
        self.prog.stop()
        self.prog.grid_remove()
        self._sync_primary()
        self.btn.configure(state="normal")
        self.retry_btn.configure(state="normal", text="补导")
        if payload.get("ok"):
            self.last_batch = payload.get("batch_path") or self.last_batch
            dest, frm, to = self.dest.get().strip(), self.frm.get().strip(), self.to.get().strip()
            _save_prefs(dest, frm, to, self.last_batch)
            if self.last_batch:
                self.open_btn.grid()
            self.stats = payload.get("by_kind", self.stats)
            self.failed = payload.get("failed_items", self.failed)
            self.set_phase("done")
            self.dirty = True
            self.show_env(payload.get("warning", ""))
            failed = int(payload.get("failed_count") or 0)
            if retried:
                upgraded = int(payload.get("upgraded") or 0)
                if failed == 0:
                    self.set_state("补导完成，本批原件已全部取到", "#1a7f37")
                    self.show_retry_ui(False)
                else:
                    self.set_state(
                        f"补导完成，升格 {upgraded} 条，仍有 {failed} 条未取到原件", TEXT)
                    self.show_retry_ui(True)
            else:
                self.set_state(el.format_done_status(payload), "#1a7f37")
                self.show_retry_ui(failed > 0)
        else:
            self.set_state(payload.get("error", "导出失败"), ERR)
            self.show_env("")
            if retried and self.failed:
                self.show_retry_ui(True)
        self.resize()

    def poll(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "progress":
                    self.set_state(payload)
                elif kind == "event":
                    self.handle_event(payload)
                elif kind == "env":
                    self.apply_env(payload)
                elif kind == "retry_done":
                    self._finish_export(payload, retried=True)
                else:
                    self._finish_export(payload, retried=False)
        except queue.Empty:
            pass
        if self.dirty:
            self.refresh_table()
        try:
            if not self.root.winfo_exists():
                return
            self._after_id = self.root.after(60 if self.busy else 140, self.poll)
        except tk.TclError:
            return


def run():
    try:
        root = tk.Tk()
        apply_island_style(root)
        fm, fs, family = fonts(root)
        App(root, fm, fs, family)
        root.mainloop()
        return 0
    except Exception as e:
        try:
            from tkinter import messagebox
            from common import data_dir
            log = os.path.join(data_dir(), "startup-error.log")
            os.makedirs(os.path.dirname(log), exist_ok=True)
            with open(log, "w", encoding="utf-8") as f:
                f.write(traceback.format_exc())
            messagebox.showerror(APP_NAME, f"启动失败：{type(e).__name__}: {e}\n详情已写入：{log}")
        except Exception:
            pass
        raise
