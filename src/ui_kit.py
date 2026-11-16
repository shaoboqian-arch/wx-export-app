# -*- coding: utf-8 -*-
"""海岛风格自绘控件：胶囊按钮、圆角输入、阶段微标、进度条，以及 DPI / 居中 / 字体 / 图标 / Tooltip。

配色对照 animal-island 方案：羊皮纸底 #f8f8f0，字 #794f27，薄荷主色 #19c8b9。
不改系统窗口圆角框，只把内部画成海岛风格。字体仍用 Microsoft YaHei UI 回退。
"""
import calendar
import datetime as dt
import ctypes
import os
import sys
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

# 海岛配色
BG = "#f8f8f0"
TEXT = "#794f27"
# MUTED 原为 #9f927d：配 BG 只有 2.86:1、配 SAND 2.51:1，达不到 AA。
# 加深到 #6f6455 → BG 5.42:1、SAND 4.75:1，仍属同一支灰棕。
MUTED = "#6f6455"
MINT = "#19c8b9"
MINT_HI = "#50b9ab"
MINT_BG = "#e6f9f6"
# 薄荷主按钮上的字：米白 #f8f8f0 只有 1.97:1（悬停 2.22:1），改用深棕 6.45:1（悬停 5.72:1）。
ON_MINT = "#3f2a12"
SAND = "#f0e8d8"
BORDER = "#dcd8d1"
# 错误红原为 #e05a5a（3.40:1），加深到 6.15:1；ERR_BG 只是底色，不动。
ERR = "#b02a1f"
ERR_BG = "#fdeeee"
WHITE = "#ffffff"
PAPER = "#fffef8"


def enable_dpi():
    """进程级 DPI 感知，必须在创建任何 Tk 根窗口之前调用。"""
    if os.name != "nt":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def center(root, width, height):
    """窗口居中（略偏上，视觉重心更稳）。"""
    root.update_idletasks()
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    x = max(0, (sw - width) // 2)
    y = max(0, (sh - height) // 3)
    root.geometry(f"{width}x{height}+{x}+{y}")


def fonts(root):
    """按本机实际可用字体回退，避免硬编码字体缺失导致字形发虚。"""
    try:
        fams = set(tkfont.families(root))
    except Exception:
        fams = set()
    fam = next((n for n in ("Microsoft YaHei UI", "Microsoft YaHei", "微软雅黑", "Segoe UI")
                if n in fams), "TkDefaultFont")
    return (fam, 10), (fam, 9), fam


def icon_path():
    m = getattr(sys, "_MEIPASS", None)
    p = os.path.join(m, "app.ico") if m else os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.ico")
    return p if os.path.isfile(p) else ""


def apply_icon(root):
    p = icon_path()
    if not p:
        return
    try:
        root.iconbitmap(p)
    except (tk.TclError, OSError):
        pass


def apply_island_style(root):
    """给根窗口和残留的 ttk 控件上浅色海岛底，不重做功能。"""
    try:
        root.configure(bg=BG)
    except tk.TclError:
        pass
    style = ttk.Style(root)
    try:
        style.theme_use("vista")
    except tk.TclError:
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
    for name in ("TFrame", "TLabel", "TLabelframe", "TLabelframe.Label"):
        try:
            style.configure(name, background=BG, foreground=TEXT)
        except tk.TclError:
            pass
    try:
        style.configure("TButton", foreground=TEXT)
        style.configure("TEntry", fieldbackground=WHITE, foreground=TEXT)
        style.configure("Treeview", background=WHITE, fieldbackground=WHITE,
                        foreground=TEXT, bordercolor=BORDER)
        style.configure("Treeview.Heading", background=SAND, foreground=MUTED)
        style.configure("TScrollbar", background=SAND)
        style.configure("TProgressbar", troughcolor=SAND, background=MINT)
    except tk.TclError:
        pass


def _round_rect(canvas, x1, y1, x2, y2, r, **kw):
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    pts = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return canvas.create_polygon(pts, smooth=True, splinesteps=12, **kw)


class Tooltip:
    """仅鼠标悬停时出现的完整文本，用来替代常驻的解释性文字。"""

    def __init__(self, widget, text_func, font=("Microsoft YaHei UI", 9)):
        self.widget = widget
        self.text_func = text_func
        self.font = font
        self.tip = None
        widget.bind("<Enter>", self.show)
        widget.bind("<Leave>", self.hide)
        widget.bind("<ButtonPress>", self.hide)

    def show(self, _event=None):
        try:
            text = self.text_func()
        except Exception:
            return
        if not text or self.tip:
            return
        try:
            x = self.widget.winfo_rootx() + 12
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
            self.tip = tk.Toplevel(self.widget)
            self.tip.wm_overrideredirect(True)
            self.tip.wm_geometry(f"+{x}+{y}")
            tk.Label(self.tip, text=text, justify="left", wraplength=430,
                     background=SAND, foreground=TEXT,
                     relief="solid", borderwidth=1, font=self.font,
                     highlightbackground=BORDER, padx=8, pady=6).pack()
        except (tk.TclError, OSError):
            self.tip = None

    def hide(self, _event=None):
        if self.tip is not None:
            try:
                self.tip.destroy()
            except (tk.TclError, OSError):
                pass
            self.tip = None


class CapsuleButton(tk.Canvas):
    """胶囊按钮。primary=薄荷主按钮，否则砂色次按钮。"""

    def __init__(self, master, text="", command=None, primary=False,
                 width=80, height=36, font=None, **kw):
        self._bg = kw.pop("bg", None) or getattr(master, "cget", lambda _k: BG)("bg") or BG
        super().__init__(master, width=width, height=height, highlightthickness=0,
                         bd=0, bg=self._bg, cursor="hand2", takefocus=1, **kw)
        self._text = text
        self._command = command
        self._primary = primary
        self._state = "normal"
        self._hover = False
        self._focus = False
        self._font = font or ("Microsoft YaHei UI", 10, "bold" if primary else "normal")
        self._box_w, self._box_h = width, height
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        # 自绘控件拿不到 ttk 的默认键盘行为，这里显式补上 Tab 焦点与空格/回车。
        self.bind("<FocusIn>", self._on_focus_in)
        self.bind("<FocusOut>", self._on_focus_out)
        self.bind("<space>", self._on_key)
        self.bind("<Return>", self._on_key)
        self.bind("<Configure>", lambda _e: self._draw())
        self._draw()

    def configure(self, **kw):
        if "text" in kw:
            self._text = kw.pop("text")
        if "command" in kw:
            self._command = kw.pop("command")
        if "state" in kw:
            self._state = kw.pop("state")
            self.configure(cursor="" if self._state == "disabled" else "hand2") if False else None
            try:
                super().configure(cursor="arrow" if self._state == "disabled" else "hand2")
            except tk.TclError:
                pass
        if kw:
            super().configure(**kw)
        self._draw()

    config = configure

    def cget(self, key):
        if key == "text":
            return self._text
        if key == "state":
            return self._state
        return super().cget(key)

    def _palette(self):
        if self._state == "disabled":
            return SAND, MUTED, BORDER, None
        if self._primary:
            fill = MINT_HI if self._hover else MINT
            return fill, ON_MINT, fill, MINT_HI
        fill = "#f7f1e6" if self._hover else SAND
        return fill, TEXT, BORDER, None

    def _draw(self):
        self.delete("all")
        w = int(self.winfo_width() or self._box_w)
        h = int(self.winfo_height() or self._box_h)
        fill, fg, border, shadow = self._palette()
        r = h / 2
        if shadow and self._state != "disabled":
            _round_rect(self, 1, 3, w - 1, h - 1, r, fill=shadow, outline="")
            _round_rect(self, 1, 1, w - 1, h - 3, r, fill=fill, outline="")
        else:
            _round_rect(self, 1, 1, w - 1, h - 1, r, fill=fill, outline=border)
        self.create_text(w / 2, h / 2 - (1 if shadow else 0), text=self._text,
                         fill=fg, font=self._font)
        if self._focus and self._state != "disabled":
            # 键盘焦点环：自绘按钮没有系统焦点框，必须自己画一个可见的
            _round_rect(self, 2, 2, w - 2, h - 2, r - 1, fill="", outline=TEXT, width=2)

    def _on_enter(self, _e=None):
        self._hover = True
        self._draw()

    def _on_leave(self, _e=None):
        self._hover = False
        self._draw()

    def _on_focus_in(self, _e=None):
        self._focus = True
        self._draw()

    def _on_focus_out(self, _e=None):
        self._focus = False
        self._draw()

    def _on_key(self, _e=None):
        if self._state != "disabled" and self._command:
            self._command()
        return "break"

    def _on_press(self, _e=None):
        self.focus_set()
        if self._state != "disabled":
            self._hover = False
            self._draw()

    def _on_release(self, _e=None):
        self._hover = True
        self._draw()
        if self._state != "disabled" and self._command:
            self._command()


class IslandEntry(tk.Frame):
    """圆角胶囊输入框，对外代理 get / insert / delete。

    输入框用 place 叠在画布上，并给出明确的最小宽度，避免作为
    canvas window 时撑乱并排布局、把日期文字挤出可视区域。
    """

    def __init__(self, master, font=None, width=20, pxwidth=None, **kw):
        super().__init__(master, bg=BG, highlightthickness=0)
        self._height = 36
        if pxwidth:
            self._min_w = int(pxwidth)
        else:
            try:
                fnt = tkfont.Font(font=font) if font else tkfont.nametofont("TkDefaultFont")
                self._min_w = max(120, fnt.measure("0") * int(width) + 36)
            except (tk.TclError, TypeError, ValueError):
                self._min_w = max(120, int(width or 20) * 9 + 36)
        self.configure(width=self._min_w, height=self._height)
        self.pack_propagate(False)
        self.grid_propagate(False)
        self._canvas = tk.Canvas(self, highlightthickness=0, bd=0, bg=BG)
        self._canvas.place(x=0, y=0, relwidth=1, relheight=1)
        self.entry = tk.Entry(
            self, font=font, width=width, bd=0, highlightthickness=0,
            relief="flat", bg=WHITE, fg=TEXT, insertbackground=TEXT, **kw)
        self.entry.place(x=14, y=5, width=self._min_w - 28, height=26)
        self._canvas.bind("<Configure>", self._on_resize)
        self.entry.bind("<FocusIn>", lambda _e: self._draw(focus=True))
        self.entry.bind("<FocusOut>", lambda _e: self._draw(focus=False))
        self._focus = False
        self.bind("<Configure>", self._on_resize)

    def _on_resize(self, _event=None):
        w = max(self._min_w, int(self.winfo_width() or self._min_w))
        self.entry.place_configure(x=14, y=5, width=max(20, w - 28), height=26)
        self._draw()

    def _draw(self, event=None, focus=None):
        if focus is not None:
            self._focus = focus
        c = self._canvas
        c.delete("all")
        w = int(c.winfo_width() or self.winfo_width() or self._min_w)
        h = int(c.winfo_height() or self._height)
        if w < 4 or h < 4:
            return
        outline = MINT if self._focus else BORDER
        _round_rect(c, 1, 1, w - 1, h - 1, h / 2, fill=WHITE, outline=outline)

    def get(self):
        return self.entry.get()

    def insert(self, index, text):
        return self.entry.insert(index, text)

    def delete(self, first, last=None):
        return self.entry.delete(first, last)

    def bind(self, seq, func, add=None):
        return self.entry.bind(seq, func, add)


class PhaseBadge(tk.Canvas):
    """阶段胶囊微标：pending / now / done。兼容 configure(foreground=...)。"""

    def __init__(self, master, text="", font=None, width=72, height=26, **kw):
        super().__init__(master, width=width, height=height, highlightthickness=0,
                         bd=0, bg=BG, **kw)
        self._text = text
        self._font = font or ("Microsoft YaHei UI", 9)
        self._mode = "pending"
        self._box_w, self._box_h = width, height
        self.bind("<Configure>", lambda _e: self._draw())
        self._draw()

    def set_mode(self, mode):
        self._mode = mode if mode in ("pending", "now", "done") else "pending"
        self._draw()

    def configure(self, **kw):
        if "text" in kw:
            self._text = kw.pop("text")
        if "foreground" in kw:
            # 兼容旧 set_phase：绿=完成，琥珀=进行中，灰=未到
            color = (kw.pop("foreground") or "").lower()
            if color in ("#1a7f37", "#19c8b9"):
                self._mode = "done"
            elif color in ("#b45309", "#19c8b9"):
                self._mode = "now"
            elif color in ("#666666", "#9f927d"):
                self._mode = "pending"
            else:
                # 由调用顺序保证；未知颜色时按绿/琥珀/灰近似
                if color in ("#1a7f37",):
                    self._mode = "done"
        if kw:
            super().configure(**kw)
        self._draw()

    config = configure

    def cget(self, key):
        if key == "text":
            return self._text
        if key == "foreground":
            return {"done": TEXT, "now": ON_MINT, "pending": MUTED}[self._mode]
        return super().cget(key)

    def _draw(self):
        self.delete("all")
        w = int(self.winfo_width() or self._box_w)
        h = int(self.winfo_height() or self._box_h)
        if self._mode == "now":
            fill, fg = MINT, ON_MINT
        elif self._mode == "done":
            fill, fg = MINT_BG, TEXT
        else:
            fill, fg = SAND, MUTED
        _round_rect(self, 1, 1, w - 1, h - 1, h / 2, fill=fill, outline="")
        self.create_text(w / 2, h / 2, text=self._text, fill=fg, font=self._font)


class IslandBar(tk.Canvas):
    """薄荷进度条。默认确定进度；start/stop 兼容旧的不确定进度用法。"""

    def __init__(self, master, height=10, **kw):
        super().__init__(master, height=height + 4, highlightthickness=0, bd=0, bg=BG, **kw)
        self._h = height
        self._value = 0
        self._maximum = 100
        self._mode = "determinate"
        self._job = None
        self._pulse = 0
        self.bind("<Configure>", lambda _e: self._draw())
        self._draw()

    def start(self, interval=14):
        self._mode = "indeterminate"
        if self._job:
            try:
                self.after_cancel(self._job)
            except (tk.TclError, ValueError):
                pass
        self._pulse = 0
        self._tick(max(int(interval), 8))

    def stop(self):
        self._mode = "determinate"
        if self._job:
            try:
                self.after_cancel(self._job)
            except (tk.TclError, ValueError):
                pass
            self._job = None
        self._draw()

    def set_progress(self, index, total):
        self._mode = "determinate"
        try:
            self._value = max(0, int(index))
            self._maximum = max(1, int(total) if total else 1)
        except (TypeError, ValueError):
            return
        if self._job:
            try:
                self.after_cancel(self._job)
            except (tk.TclError, ValueError):
                pass
            self._job = None
        self._draw()

    def _tick(self, interval):
        self._pulse = (self._pulse + 3) % 120
        self._draw()
        try:
            self._job = self.after(interval, lambda: self._tick(interval))
        except tk.TclError:
            self._job = None

    def _draw(self):
        self.delete("all")
        w = int(self.winfo_width() or 100)
        h = int(self.winfo_height() or (self._h + 4))
        y1, y2 = 2, h - 2
        _round_rect(self, 0, y1, w, y2, (y2 - y1) / 2, fill="#e8e2d6", outline="")
        if self._mode == "indeterminate":
            span = max(24, int(w * 0.28))
            travel = max(1, w - span)
            x = int((self._pulse / 120.0) * travel)
            _round_rect(self, x, y1, x + span, y2, (y2 - y1) / 2, fill=MINT, outline="")
        else:
            ratio = min(1.0, self._value / float(self._maximum or 1))
            if ratio > 0:
                _round_rect(self, 0, y1, max(8, int(w * ratio)), y2,
                            (y2 - y1) / 2, fill=MINT, outline="")


class DatePicker:
    """简单日历弹层，选中后把 YYYY-MM-DD 写回输入框。"""

    def __init__(self, entry, font=None):
        self.entry = entry
        self.font = font or ("Microsoft YaHei UI", 9)
        self.win = None

    def toggle(self, _event=None):
        if self.win is not None:
            self.close()
            return
        self.open()

    def close(self):
        if self.win is not None:
            try:
                self.win.destroy()
            except tk.TclError:
                pass
            self.win = None

    def _current(self):
        raw = ""
        try:
            raw = self.entry.get().strip()
        except Exception:
            pass
        try:
            return dt.datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            return dt.date.today()

    def open(self):
        self.close()
        widget = self.entry.winfo_toplevel() if hasattr(self.entry, "winfo_toplevel") else self.entry
        host = self.entry
        try:
            x = host.winfo_rootx()
            y = host.winfo_rooty() + host.winfo_height() + 4
        except tk.TclError:
            return
        cur = self._current()
        self._year, self._month = cur.year, cur.month
        self.win = tk.Toplevel(widget)
        self.win.wm_overrideredirect(True)
        self.win.configure(bg=BORDER)
        self.win.wm_geometry(f"+{x}+{y}")
        self._body = tk.Frame(self.win, bg=BG, padx=8, pady=8)
        self._body.pack(padx=1, pady=1)
        self._render(cur)
        self.win.bind("<FocusOut>", lambda _e: self.close())
        # 日历自己吃掉 Esc：否则按键会冒到根窗口，直接把整个程序关掉。
        self.win.bind("<Escape>", self._on_escape)
        self.win.focus_set()

    def _on_escape(self, _e=None):
        self.close()
        return "break"

    def _render(self, selected):
        for w in self._body.winfo_children():
            w.destroy()
        head = tk.Frame(self._body, bg=BG)
        head.pack(fill="x")
        tk.Button(head, text="‹", bd=0, bg=BG, fg=TEXT, font=self.font,
                  command=lambda: self._shift(-1), cursor="hand2").pack(side="left")
        tk.Label(head, text=f"{self._year}-{self._month:02d}", bg=BG, fg=TEXT,
                 font=self.font).pack(side="left", expand=True)
        tk.Button(head, text="›", bd=0, bg=BG, fg=TEXT, font=self.font,
                  command=lambda: self._shift(1), cursor="hand2").pack(side="right")
        grid = tk.Frame(self._body, bg=BG)
        grid.pack(pady=(6, 0))
        for i, name in enumerate("一二三四五六日"):
            tk.Label(grid, text=name, bg=BG, fg=MUTED, font=self.font, width=3).grid(row=0, column=i)
        weeks = calendar.Calendar(0).monthdayscalendar(self._year, self._month)
        today = dt.date.today()
        for r, week in enumerate(weeks, 1):
            for c, day in enumerate(week):
                if day == 0:
                    tk.Label(grid, text="", bg=BG, width=3).grid(row=r, column=c)
                    continue
                d = dt.date(self._year, self._month, day)
                bg, fg = BG, TEXT
                if d == selected:
                    # 选中日原来也是羊皮纸字压薄荷（1.97:1），跟主按钮一处毛病
                    bg, fg = MINT, ON_MINT
                elif d == today:
                    bg = MINT_BG
                btn = tk.Label(grid, text=str(day), bg=bg, fg=fg, font=self.font,
                               width=3, cursor="hand2")
                btn.grid(row=r, column=c, padx=1, pady=1)
                btn.bind("<Button-1>", lambda _e, dd=d: self._pick(dd))

    def _shift(self, delta):
        m = self._month + delta
        y = self._year
        while m < 1:
            m += 12
            y -= 1
        while m > 12:
            m -= 12
            y += 1
        self._year, self._month = y, m
        self._render(self._current())

    def _pick(self, day):
        try:
            self.entry.delete(0, "end")
            self.entry.insert(0, day.isoformat())
        except tk.TclError:
            pass
        self.close()
