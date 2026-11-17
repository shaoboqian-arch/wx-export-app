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
import time
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
# 状态色（跨视图共用）。**不要在 settings/installer/setup_env 里重复定义十六进制**：
# 之前 GREEN 在三处各写一遍、AMBER 三处，改一处漏两处就是这么来的。
OK = "#1a7f37"        # 成功 / 完成
WARN = "#b45309"      # 进行中 / 需要注意
NEUTRAL = "#666666"   # 未开始 / 设置页正文灰
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



def _hex_rgb(color):
    c = color.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def _lerp_color(a, b, t):
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    ra, ga, ba = _hex_rgb(a)
    rb, gb, bb = _hex_rgb(b)
    return "#{:02x}{:02x}{:02x}".format(
        int(ra + (rb - ra) * t),
        int(ga + (gb - ga) * t),
        int(ba + (bb - ba) * t),
    )


def _bezier_xy(s, p1, p2):
    u = 1.0 - s
    x = 3 * u * u * s * p1[0] + 3 * u * s * s * p2[0] + s * s * s
    y = 3 * u * u * s * p1[1] + 3 * u * s * s * p2[1] + s * s * s
    return x, y


def ease_out(t):
    """cubic-bezier(0.23, 1, 0.32, 1)，用于 UI 出现与位移。"""
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    lo, hi = 0.0, 1.0
    p1, p2 = (0.23, 1.0), (0.32, 1.0)
    for _ in range(14):
        mid = (lo + hi) / 2.0
        x, _y = _bezier_xy(mid, p1, p2)
        if x < t:
            lo = mid
        else:
            hi = mid
    _x, y = _bezier_xy((lo + hi) / 2.0, p1, p2)
    return y


def prefers_reduced_motion():
    """跟随系统「窗口内动画」开关；关了就只留颜色，不做位移。"""
    if os.name != "nt":
        return False
    try:
        val = ctypes.c_int(1)
        # SPI_GETCLIENTAREAANIMATION = 0x1042
        ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(val), 0)
        return val.value == 0
    except Exception:
        return False


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
    """胶囊按钮。主界面、设置页、安装向导共用同一套悬停/按下力学：

    悬停抬起并拉出阴影，按下缩小并收回阴影；颜色只是伴随变化，不是唯一反馈。
    """

    _HOVER_MS = 140
    _PRESS_MS = 100

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
        self._lift = 0.0
        self._press = 0.0
        self._lift_from = 0.0
        self._press_from = 0.0
        self._lift_to = 0.0
        self._press_to = 0.0
        self._anim_id = None
        self._anim_t0 = 0.0
        self._anim_dur = self._HOVER_MS / 1000.0
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<FocusIn>", self._on_focus_in)
        self.bind("<FocusOut>", self._on_focus_out)
        self.bind("<space>", self._on_key)
        self.bind("<Return>", self._on_key)
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Destroy>", self._cancel_anim, add="+")
        self._draw()

    def configure(self, **kw):
        if "text" in kw:
            self._text = kw.pop("text")
        if "command" in kw:
            self._command = kw.pop("command")
        if "state" in kw:
            self._state = kw.pop("state")
            try:
                super().configure(cursor="arrow" if self._state == "disabled" else "hand2")
            except tk.TclError:
                pass
            if self._state == "disabled":
                self._hover = False
                self._lift = self._press = 0.0
                self._lift_to = self._press_to = 0.0
                self._cancel_anim()
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

    def _cancel_anim(self, _e=None):
        if self._anim_id is not None:
            try:
                self.after_cancel(self._anim_id)
            except (tk.TclError, ValueError):
                pass
            self._anim_id = None

    def _retarget(self, duration_ms):
        self._lift_from, self._press_from = self._lift, self._press
        if prefers_reduced_motion():
            self._lift, self._press = self._lift_to, self._press_to
            self._cancel_anim()
            self._draw()
            return
        if abs(self._lift - self._lift_to) < 0.01 and abs(self._press - self._press_to) < 0.01:
            self._lift, self._press = self._lift_to, self._press_to
            self._cancel_anim()
            self._draw()
            return
        self._anim_t0 = time.perf_counter()
        self._anim_dur = max(0.06, duration_ms / 1000.0)
        self._cancel_anim()
        self._tick()

    def _tick(self):
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return
        t = (time.perf_counter() - self._anim_t0) / self._anim_dur
        p = ease_out(t)
        if t >= 1:
            self._lift, self._press = self._lift_to, self._press_to
            self._anim_id = None
            self._draw()
            return
        self._lift = self._lift_from + (self._lift_to - self._lift_from) * p
        self._press = self._press_from + (self._press_to - self._press_from) * p
        self._draw()
        try:
            self._anim_id = self.after(8, self._tick)
        except tk.TclError:
            self._anim_id = None

    def _draw(self):
        self.delete("all")
        # 未布局的画布 winfo_width() 返回 1，而 1 是真值 —— `or self._box_w` 永远轮不到，
        # 于是早退分支把整只按钮画成空白（焦点环也一起没了）。这里显式判 <=1 再回退。
        w = int(self.winfo_width())
        h = int(self.winfo_height())
        if w <= 1:
            w = int(self._box_w)
        if h <= 1:
            h = int(self._box_h)
        if w < 8 or h < 8:
            return
        disabled = self._state == "disabled"
        lift = 0.0 if disabled else self._lift
        press = 0.0 if disabled else self._press
        if disabled:
            fill, fg, border = SAND, MUTED, BORDER
            shadow = None
        elif self._primary:
            fill = _lerp_color(MINT, MINT_HI, lift)
            if press:
                fill = _lerp_color(fill, "#14b3a5", press)
            fg, border = ON_MINT, fill
            # 中性暖灰影，避免薄荷影和浅填充叠在一起看不清抬起。
            shadow = _lerp_color("#c9bfb0", "#9e9282", lift)
        else:
            fill = _lerp_color(SAND, "#f7f1e6", lift)
            if press:
                fill = _lerp_color(fill, "#e8dfcf", press)
            fg, border = TEXT, BORDER
            shadow = "#b8aea0"

        # 预留上下空位：悬停抬起约 4px，阴影落到按钮下面。
        pad_top, pad_bot = 5.0, 7.0
        shift_y = -4.0 * lift + 2.0 * press
        scale = 1.0 - 0.05 * press
        x1, y1, x2, y2 = 1.0, pad_top + shift_y, w - 1.0, h - pad_bot + shift_y
        if scale < 0.999:
            cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
            bw, bh = (x2 - x1) * scale, (y2 - y1) * scale
            x1, x2 = cx - bw / 2.0, cx + bw / 2.0
            y1, y2 = cy - bh / 2.0, cy + bh / 2.0
        r = max(2.0, (y2 - y1) / 2.0)
        shadow_dy = (3.0 + 4.0 * lift) * (1.0 - press) if self._primary else (5.0 * lift * (1.0 - press))
        if shadow and shadow_dy > 0.4 and not disabled:
            _round_rect(self, x1, y1 + shadow_dy, x2, y2 + shadow_dy, r,
                        fill=shadow, outline="")
            _round_rect(self, x1, y1, x2, y2, r, fill=fill, outline="")
        else:
            _round_rect(self, x1, y1, x2, y2, r, fill=fill, outline=border)
        self.create_text((x1 + x2) / 2.0, (y1 + y2) / 2.0, text=self._text,
                         fill=fg, font=self._font)
        # 焦点环只给键盘，避免鼠标按下时被深色描边抢走「按下收回」的观感。
        if self._focus and not disabled and press < 0.2:
            _round_rect(self, x1 + 1, y1 + 1, x2 - 1, y2 - 1, max(1.0, r - 1),
                        fill="", outline=TEXT, width=2)

    def _on_enter(self, _e=None):
        if self._state == "disabled":
            return
        self._hover = True
        self._lift_to = 1.0
        if self._press_to < 0.5:
            self._press_to = 0.0
        self._retarget(self._HOVER_MS)

    def _on_leave(self, _e=None):
        self._hover = False
        self._lift_to = 0.0
        self._press_to = 0.0
        self._retarget(self._HOVER_MS)

    def _on_focus_in(self, _e=None):
        self._focus = True
        self._draw()

    def _on_focus_out(self, _e=None):
        self._focus = False
        self._draw()

    def _on_key(self, _e=None):
        if self._state == "disabled":
            return "break"
        self._press_to = 1.0
        self._lift_to = 0.0
        self._retarget(self._PRESS_MS)

        def _bounce():
            self._press_to = 0.0
            self._lift_to = 1.0 if self._hover else 0.0
            self._retarget(self._PRESS_MS)

        try:
            self.after(self._PRESS_MS, _bounce)
        except tk.TclError:
            pass
        if self._command:
            self._command()
        return "break"

    def _on_press(self, _e=None):
        if self._state == "disabled":
            return
        self._press_to = 1.0
        self._lift_to = 0.0
        self._retarget(self._PRESS_MS)

    def _on_release(self, _e=None):
        if self._state == "disabled":
            return
        was_pressed = self._press_to > 0.5 or self._press > 0.5
        self._press_to = 0.0
        self._lift_to = 1.0 if self._hover else 0.0
        self._retarget(self._PRESS_MS)
        if was_pressed and self._hover and self._command:
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
        self._hover = False
        for w in (self, self._canvas, self.entry):
            w.bind("<Enter>", self._on_enter, add="+")
            w.bind("<Leave>", self._on_leave, add="+")
        self.bind("<Configure>", self._on_resize)

    def _on_resize(self, _event=None):
        w = max(self._min_w, int(self.winfo_width() or self._min_w))
        self.entry.place_configure(x=14, y=5, width=max(20, w - 28), height=26)
        self._draw()

    def _on_enter(self, _e=None):
        self._hover = True
        self._draw()

    def _on_leave(self, _e=None):
        # 移到内部 Entry 时 Frame 会先 Leave，这里看指针还在不在整块里。
        try:
            x, y = self.winfo_pointerxy()
            if (self.winfo_rootx() <= x <= self.winfo_rootx() + self.winfo_width()
                    and self.winfo_rooty() <= y <= self.winfo_rooty() + self.winfo_height()):
                return
        except tk.TclError:
            pass
        self._hover = False
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
        if self._focus:
            outline, width = MINT, 2
        elif self._hover:
            outline, width = "#c8bfb0", 1
        else:
            outline, width = BORDER, 1
        _round_rect(c, 1, 1, w - 1, h - 1, h / 2, fill=WHITE, outline=outline, width=width)

    def get(self):
        return self.entry.get()

    def insert(self, index, text):
        return self.entry.insert(index, text)

    def delete(self, first, last=None):
        return self.entry.delete(first, last)

    def bind(self, seq, func, add=None):
        return self.entry.bind(seq, func, add)


class PhaseBadge(tk.Canvas):
    """阶段胶囊微标：pending / now / done。状态只由 set_mode 驱动。"""

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
        # foreground 只为兼容老调用保留，不再映射状态：阶段只由 set_mode 决定。
        # 旧实现里 "#19c8b9" 在 if/elif 各出现一次、else 永远不可达，等于假装支持。
        kw.pop("foreground", None)
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
        w = int(self.winfo_width())
        h = int(self.winfo_height())
        if w <= 1:
            w = int(self._box_w)
        if h <= 1:
            h = int(self._box_h)
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
    """简单日历弹层，选中后把 YYYY-MM-DD 写回输入框。

    翻月是同方向滑入滑出（约 220ms，可打断），星期行和左右键不动。
    """

    _SLIDE_MS = 220

    def __init__(self, entry, font=None):
        self.entry = entry
        self.font = font or ("Microsoft YaHei UI", 9)
        self.win = None
        self._slide_job = None
        self._pages = []

    def toggle(self, _event=None):
        if self.win is not None:
            self.close()
            return
        self.open()

    def close(self):
        self._cancel_slide()
        if self.win is not None:
            try:
                self.win.destroy()
            except tk.TclError:
                pass
            self.win = None
        self._pages = []

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
        self._build_chrome()
        self.win.update_idletasks()
        self._show_month(self._year, self._month, animate=False)
        self.win.bind("<FocusOut>", lambda _e: self.close())
        self.win.bind("<Escape>", self._on_escape)
        self.win.focus_set()

    def _on_escape(self, _e=None):
        self.close()
        return "break"

    def _cell_size(self):
        try:
            fnt = tkfont.Font(font=self.font)
            cw = max(28, fnt.measure("00") + 12)
            ch = max(22, fnt.metrics("linespace") + 8)
        except (tk.TclError, TypeError, ValueError):
            cw, ch = 30, 24
        return cw, ch

    def _build_chrome(self):
        cw, ch = self._cell_size()
        self._stage_w = cw * 7
        # 日期格有 pady=1、ipady=1，6 行要加上这些间距，避免最后一周被裁切。
        self._stage_h = ch * 6 + 4 * 6
        head = tk.Frame(self._body, bg=BG)
        head.pack(fill="x")
        CapsuleButton(head, text="‹", width=28, height=28, font=self.font,
                      command=lambda: self._shift(-1)).pack(side="left")
        self._title_stage = tk.Frame(head, bg=BG, width=self._stage_w - 64, height=28)
        self._title_stage.pack(side="left", expand=True, fill="both")
        self._title_stage.pack_propagate(False)
        CapsuleButton(head, text="›", width=28, height=28, font=self.font,
                      command=lambda: self._shift(1)).pack(side="right")
        days = tk.Frame(self._body, bg=BG)
        days.pack(pady=(6, 0))
        for i, name in enumerate("一二三四五六日"):
            tk.Label(days, text=name, bg=BG, fg=MUTED, font=self.font,
                     width=3).grid(row=0, column=i)
        self._stage = tk.Frame(self._body, bg=BG, width=self._stage_w,
                               height=self._stage_h)
        self._stage.pack()
        self._stage.pack_propagate(False)

    def _make_grid(self, year, month):
        cw, ch = self._cell_size()
        grid = tk.Frame(self._stage, bg=BG, width=self._stage_w, height=self._stage_h)
        selected = self._current()
        today = dt.date.today()
        weeks = calendar.Calendar(0).monthdayscalendar(year, month)
        while len(weeks) < 6:
            weeks.append([0] * 7)
        for r, week in enumerate(weeks[:6]):
            for c, day in enumerate(week):
                cell = tk.Label(grid, text="" if day == 0 else str(day),
                                bg=BG, fg=TEXT, font=self.font, width=3)
                cell.grid(row=r, column=c, padx=1, pady=1, ipady=1)
                if day == 0:
                    continue
                d = dt.date(year, month, day)
                if d == selected:
                    cell.configure(bg=MINT, fg=ON_MINT, cursor="hand2")
                elif d == today:
                    cell.configure(bg=MINT_BG, fg=TEXT, cursor="hand2")
                else:
                    cell.configure(cursor="hand2")
                cell._rest_bg = cell.cget("bg")
                cell._rest_fg = cell.cget("fg")
                if d != selected:
                    def _enter(e, w=cell):
                        if w.cget("bg") != MINT:
                            w.configure(bg=SAND)
                    def _leave(e, w=cell):
                        w.configure(bg=w._rest_bg, fg=w._rest_fg)
                    cell.bind("<Enter>", _enter)
                    cell.bind("<Leave>", _leave)
                cell.bind("<Button-1>", lambda _e, dd=d: self._pick(dd))
        return grid

    def _make_title(self, year, month):
        return tk.Label(self._title_stage, text=f"{year}-{month:02d}",
                        bg=BG, fg=TEXT, font=self.font)

    def _cancel_slide(self):
        if self._slide_job is not None:
            try:
                if self.win is not None:
                    self.win.after_cancel(self._slide_job)
            except (tk.TclError, ValueError):
                pass
            self._slide_job = None

    def _clear_pages(self, keep=None):
        kept = []
        for item in self._pages:
            if keep is not None and item is keep:
                kept.append(item)
                continue
            for part in (item.get("grid"), item.get("title")):
                try:
                    part.destroy()
                except (tk.TclError, AttributeError):
                    pass
        self._pages = kept

    def _place_page(self, page, x):
        page["grid"].place(x=int(x), y=0, width=self._stage_w, height=self._stage_h)
        tw = int(self._title_stage.winfo_width())
        if tw <= 1:
            tw = self._stage_w - 64
        page["title"].place(x=int(x * tw / max(1, self._stage_w)), y=0,
                            width=tw, height=28)
        page["x"] = x

    def _show_month(self, year, month, animate=False, direction=1):
        if self.win is None:
            return
        incoming = {
            "grid": self._make_grid(year, month),
            "title": self._make_title(year, month),
            "x": 0,
        }
        if not animate or prefers_reduced_motion() or not self._pages:
            self._cancel_slide()
            self._clear_pages()
            self._pages = [incoming]
            self._title_stage.update_idletasks()
            self._place_page(incoming, 0)
            return
        outgoing = self._pages[-1]
        self._cancel_slide()
        # 打断：丢掉更早的页，从当前偏移继续滑向新的一个月。
        self._clear_pages(keep=outgoing)
        start = outgoing.get("x", 0)
        incoming_start = start + direction * self._stage_w
        self._place_page(incoming, incoming_start)
        self._pages = [outgoing, incoming]
        t0 = time.perf_counter()
        dur = self._SLIDE_MS / 1000.0

        def tick():
            if self.win is None:
                return
            t = (time.perf_counter() - t0) / dur
            p = ease_out(t)
            if t >= 1:
                self._place_page(incoming, 0)
                self._clear_pages(keep=incoming)
                self._pages = [incoming]
                self._slide_job = None
                return
            out_x = start + (-direction * self._stage_w - start) * p
            in_x = incoming_start + (0 - incoming_start) * p
            self._place_page(outgoing, out_x)
            self._place_page(incoming, in_x)
            try:
                self._slide_job = self.win.after(8, tick)
            except tk.TclError:
                self._slide_job = None

        tick()

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
        self._show_month(y, m, animate=True, direction=1 if delta > 0 else -1)

    def _pick(self, day):
        try:
            self.entry.delete(0, "end")
            self.entry.insert(0, day.isoformat())
        except tk.TclError:
            pass
        self.close()
