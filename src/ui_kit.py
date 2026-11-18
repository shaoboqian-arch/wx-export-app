# -*- coding: utf-8 -*-
"""现代浅色自绘控件：圆角按钮、圆角输入、阶段微标、进度条，以及 DPI / 居中 / 字体 / 图标 / Tooltip。

配色基调：瓷白底 #f6f7f9，纯白卡片 #ffffff，石墨字 #182026，深翠青主色 #0d6e66。
保留海岛风格对外符号与 WCAG AA 对比度。
"""
import calendar
import ctypes
import datetime as dt
import math
import os
import sys
import time
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

# 现代浅色配色（WCAG AA 全过）
BG = "#F6F7F9"
TEXT = "#182026"
MUTED = "#525D6B"
MINT = "#0D6E66"
MINT_HI = "#095952"
MINT_BG = "#E6F4F2"
MINT_DEEP = "#0b6f66"
ON_MINT = "#FFFFFF"
SAND = "#EEF0F4"
BORDER = "#D8DDE6"
ERR = "#B42318"
# 状态色（跨视图共用）。不要在 settings/installer/setup_env 里重复定义十六进制。
OK = "#157347"        # 成功 / 完成
WARN = "#9A4D00"      # 进行中 / 需要注意
NEUTRAL = "#525D6B"   # 未开始 / 设置页正文灰
ERR_BG = "#FEF3F2"
WHITE = "#FFFFFF"
PAPER = "#FFFFFF"
TRACK = "#E2E6ED"

# 兼容别名
GREEN = OK
CYAN = OK
DONE_BG = MINT_BG
PANEL = PAPER
SURFACE = SAND
CARD = PAPER
CARD_LINE = "#E3E7EE"
ACCENT = MINT
ACCENT_HI = MINT_HI
ON_ACCENT = ON_MINT
FONT_FAMILY = "Microsoft YaHei UI"


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
    global FONT_FAMILY
    try:
        fams = set(tkfont.families(root))
    except Exception:
        fams = set()
    fam = next((n for n in ("Microsoft YaHei UI", "Microsoft YaHei", "微软雅黑", "Segoe UI")
                if n in fams), "TkDefaultFont")
    FONT_FAMILY = fam
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
    """浅色极简底 + ttk 控件跟着走（优先 clam 以支持扁平表头与滚动条）。"""
    try:
        root.configure(bg=BG)
    except tk.TclError:
        pass
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
    for name in ("TFrame", "TLabel", "TLabelframe", "TLabelframe.Label"):
        try:
            style.configure(name, background=BG, foreground=TEXT)
        except tk.TclError:
            pass
    try:
        style.configure("TButton", background=SAND, foreground=TEXT,
                        bordercolor=BORDER, focusthickness=1)
        style.map("TButton", background=[("active", TRACK)])
        style.configure("TEntry", fieldbackground=WHITE, foreground=TEXT,
                        insertcolor=TEXT, bordercolor=BORDER)
        style.configure("TCombobox", fieldbackground=WHITE, background=SAND,
                        foreground=TEXT, bordercolor=BORDER, lightcolor=BORDER,
                        darkcolor=BORDER, arrowcolor=MUTED, padding=(8, 4))
        style.map("TCombobox", fieldbackground=[("readonly", WHITE)],
                  selectbackground=[("readonly", MINT_BG)],
                  selectforeground=[("readonly", TEXT)])
        style.configure("Treeview", background=WHITE, fieldbackground=WHITE,
                        foreground=TEXT, bordercolor=BORDER, lightcolor=BORDER,
                        darkcolor=BORDER, borderwidth=1, relief="solid", rowheight=28)
        style.map("Treeview", background=[("selected", MINT_BG)],
                  foreground=[("selected", TEXT)])
        style.configure("Treeview.Heading", background=SAND, foreground=MUTED,
                        bordercolor=BORDER, lightcolor=SAND, darkcolor=SAND,
                        relief="flat", padding=(10, 6), font=(FONT_FAMILY, 9, "bold"))
        style.map("Treeview.Heading",
                  background=[("active", TRACK)],
                  foreground=[("active", TEXT)])
        for name in ("TScrollbar", "Vertical.TScrollbar", "Horizontal.TScrollbar"):
            style.configure(name, background="#CFD6E0", troughcolor=BG, bordercolor=BG,
                            lightcolor="#CFD6E0", darkcolor="#CFD6E0",
                            arrowcolor=MUTED, relief="flat", width=10, gripcount=0)
            style.map(name, background=[("active", "#B5BFCE")],
                      arrowcolor=[("active", TEXT)])
        style.configure("TProgressbar", troughcolor=TRACK, background=MINT)
    except tk.TclError:
        pass


apply_theme = apply_island_style


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


def contrast(fg, bg):
    """WCAG 对比度。"""
    def lum(hex_color):
        c = hex_color.lstrip("#")
        vals = [int(c[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        lin = [(v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4) for v in vals]
        return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
    a, b = lum(fg), lum(bg)
    hi, lo = max(a, b), min(a, b)
    return round((hi + 0.05) / (lo + 0.05), 2)


def _pointer_inside(x, y, width, height):
    try:
        x, y, width, height = float(x), float(y), float(width), float(height)
    except (TypeError, ValueError):
        return False
    return 0 <= x <= width and 0 <= y <= height


def _round_rect(canvas, x1, y1, x2, y2, r, **kw):
    """真圆弧圆角矩形，避开 Tk smooth=True 的八边形样条畸变。"""
    r = max(0.0, min(float(r), (x2 - x1) / 2.0, (y2 - y1) / 2.0))
    if r <= 0.5:
        return canvas.create_rectangle(x1, y1, x2, y2, **kw)
    pts = []
    steps = 12
    corners = (
        (x2 - r, y1 + r, -math.pi / 2.0, 0.0),
        (x2 - r, y2 - r, 0.0, math.pi / 2.0),
        (x1 + r, y2 - r, math.pi / 2.0, math.pi),
        (x1 + r, y1 + r, math.pi, 1.5 * math.pi),
    )
    for cx, cy, a0, a1 in corners:
        for i in range(steps + 1):
            ang = a0 + (a1 - a0) * (i / steps)
            pts.append(cx + r * math.cos(ang))
            pts.append(cy + r * math.sin(ang))
    return canvas.create_polygon(pts, smooth=False, **kw)


class Tooltip:
    """仅鼠标悬停时出现的完整文本，用来替代常驻的解释性文字。"""

    def __init__(self, widget, text_func, font=None):
        self.widget = widget
        self.text_func = text_func
        self.font = font or (FONT_FAMILY, 9)
        self.tip = None
        widget.bind("<Enter>", self.show, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")

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
            self.tip.configure(bg=BORDER)
            tk.Label(self.tip, text=text, justify="left", wraplength=430,
                     background=PAPER, foreground=TEXT,
                     relief="flat", borderwidth=0, font=self.font,
                     padx=10, pady=8).pack(padx=1, pady=1)
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
    """圆角按钮。主界面、设置页、初始化、安装向导共用同一套悬停/按下力学：

    松开时若指针已离开矩形则取消点击；长按空格/回车去重防连发。
    """

    _HOVER_MS = 140
    _PRESS_MS = 100

    def __init__(self, master, text="", command=None, primary=False,
                 width=80, height=36, font=None, subtle=False, **kw):
        self._bg = kw.pop("bg", None) or getattr(master, "cget", lambda _k: BG)("bg") or BG
        super().__init__(master, width=width, height=height, highlightthickness=0,
                         bd=0, bg=self._bg, cursor="hand2", takefocus=1, **kw)
        self._text = text
        self._command = command
        self._primary = primary
        self._subtle = subtle
        self._state = "normal"
        self._hover = False
        self._focus = False
        self._key_held = False
        self._font = font or (FONT_FAMILY, 10, "bold" if primary else "normal")
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
        self.bind("<KeyRelease-space>", self._on_key_release)
        self.bind("<KeyRelease-Return>", self._on_key_release)
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
        # 未布局的画布 winfo_width() 返回 1，必须显式判 <=1 再回退。
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
            fill, fg, border, bw = SAND, MUTED, BORDER, 1
        elif self._primary:
            fill = _lerp_color(MINT, MINT_HI, lift)
            if press:
                fill = _lerp_color(fill, "#074741", press)
            fg, border, bw = ON_MINT, fill, 1
        elif self._subtle:
            fill = _lerp_color(WHITE, SAND, lift)
            if press:
                fill = _lerp_color(fill, TRACK, press)
            fg = _lerp_color(MUTED, TEXT, lift)
            border, bw = "", 0
        else:
            fill = _lerp_color("#F8F9FB", SAND, lift)
            if press:
                fill = _lerp_color(fill, TRACK, press)
            fg = TEXT
            border = _lerp_color("#CFD6E0", "#B5BFCE", lift)
            bw = 1

        # 与输入框(y=2..h-2)严格等高对齐，悬停轻抬 1px。
        pad_top, pad_bot = 2.0, 2.0
        shift_y = -1.0 * lift + 1.0 * press
        scale = 1.0 - 0.02 * press
        x1, y1, x2, y2 = 1.0, pad_top + shift_y, w - 1.0, h - pad_bot + shift_y
        if scale < 0.999:
            cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
            bw_s, bh_s = (x2 - x1) * scale, (y2 - y1) * scale
            x1, x2 = cx - bw_s / 2.0, cx + bw_s / 2.0
            y1, y2 = cy - bh_s / 2.0, cy + bh_s / 2.0
        r = min(8.0, max(2.0, (y2 - y1) / 2.0))
        if border:
            _round_rect(self, x1, y1, x2, y2, r, fill=fill, outline=border, width=bw)
        else:
            _round_rect(self, x1, y1, x2, y2, r, fill=fill, outline="")
        self.create_text((x1 + x2) / 2.0, (y1 + y2) / 2.0, text=self._text,
                         fill=fg, font=self._font)
        if self._focus and not disabled and press < 0.2:
            ring = ON_MINT if self._primary else MINT
            _round_rect(self, x1 + 2, y1 + 2, x2 - 2, y2 - 2, max(1.0, r - 2),
                        fill="", outline=ring, width=1.5)

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
        self._key_held = False
        self._draw()

    def _on_key(self, _e=None):
        if self._state == "disabled":
            return "break"
        if self._key_held:
            return "break"
        self._key_held = True
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

    def _on_key_release(self, _e=None):
        self._key_held = False
        return "break"

    def _on_press(self, _e=None):
        if self._state == "disabled":
            return
        self._press_to = 1.0
        self._lift_to = 0.0
        self._retarget(self._PRESS_MS)

    def _on_release(self, event=None):
        if self._state == "disabled":
            return
        was_pressed = self._press_to > 0.5 or self._press > 0.5
        w = int(self.winfo_width() or self._box_w)
        h = int(self.winfo_height() or self._box_h)
        inside = bool(event is not None and _pointer_inside(event.x, event.y, w, h)) if event is not None else self._hover
        self._hover = inside
        self._press_to = 0.0
        self._lift_to = 1.0 if inside else 0.0
        self._retarget(self._PRESS_MS)
        if was_pressed and inside and self._command:
            self._command()


class RoundedEntry(tk.Frame):
    """圆角输入框，对外代理 get / insert / delete / bind。

    path=True 时：失焦把视野停在路径尾部，并用 tooltip 给出完整路径。
    回车默认截在输入框内，不冒泡到窗口级绑定。
    """

    def __init__(self, master, font=None, width=20, pxwidth=None, placeholder="",
                 path=False, path_font=None, panel_bg=None, **kw):
        self._placeholder = placeholder
        self._path_mode = False
        self._right_inset = 12
        self._panel_bg = panel_bg or getattr(master, "cget", lambda _k: BG)("bg") or BG
        super().__init__(master, bg=self._panel_bg, highlightthickness=0)
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
        self._canvas = tk.Canvas(self, highlightthickness=0, bd=0, bg=self._panel_bg)
        self._canvas.place(x=0, y=0, relwidth=1, relheight=1)
        kw.setdefault("readonlybackground", WHITE)
        kw.setdefault("disabledbackground", WHITE)
        kw.setdefault("disabledforeground", MUTED)
        kw.setdefault("selectbackground", MINT_BG)
        kw.setdefault("selectforeground", TEXT)
        self.entry = tk.Entry(
            self, font=font, width=width, bd=0, highlightthickness=0,
            relief="flat", bg=WHITE, fg=TEXT, insertbackground=TEXT, **kw)
        self.entry.place(x=12, y=6, width=self._min_w - 24, height=24)
        self._ph = tk.Label(self, text=placeholder, bg=WHITE, fg=MUTED, font=font,
                            cursor="xterm")
        self._ph.bind("<Button-1>", lambda _e: self.entry.focus_set())
        self._canvas.bind("<Configure>", self._on_resize)
        self.entry.bind("<FocusIn>", lambda _e: self._on_focus(True))
        self.entry.bind("<FocusOut>", lambda _e: self._on_focus(False))
        self.entry.bind("<KeyRelease>", lambda _e: self._sync_placeholder())
        self.entry.bind("<Return>", lambda _e: "break")
        self.entry.bind("<KP_Enter>", lambda _e: "break")
        self._focus = False
        self._hover = False
        for w in (self, self._canvas, self.entry, self._ph):
            w.bind("<Enter>", self._on_enter, add="+")
            w.bind("<Leave>", self._on_leave, add="+")
        tk.Frame.bind(self, "<Configure>", self._on_resize, add="+")
        if path:
            self.enable_path_view(path_font)

    def set_right_inset(self, px):
        try:
            self._right_inset = max(12, int(px))
        except (TypeError, ValueError):
            self._right_inset = 12
        self._on_resize()

    def enable_path_view(self, font=None):
        if self._path_mode:
            return
        self._path_mode = True
        Tooltip(self.entry, self.get, font=font or (FONT_FAMILY, 9))
        self.entry.bind("<FocusOut>", lambda _e: self.reveal_end(), add="+")
        try:
            self.after_idle(self.reveal_end)
        except tk.TclError:
            pass

    def reveal_end(self, _event=None):
        try:
            self.entry.xview_moveto(1)
        except tk.TclError:
            pass

    def _on_resize(self, _event=None):
        w = max(self._min_w, int(self.winfo_width() or self._min_w))
        inner = max(20, w - 12 - self._right_inset)
        self.entry.place_configure(x=12, y=6, width=inner, height=24)
        self._sync_placeholder()
        self._draw()
        if self._path_mode:
            try:
                if self.entry.focus_get() is not self.entry:
                    self.reveal_end()
            except tk.TclError:
                self.reveal_end()

    def _on_enter(self, _e=None):
        self._hover = True
        self._draw()

    def _on_leave(self, _e=None):
        try:
            x, y = self.winfo_pointerxy()
            if (self.winfo_rootx() <= x <= self.winfo_rootx() + self.winfo_width()
                    and self.winfo_rooty() <= y <= self.winfo_rooty() + self.winfo_height()):
                return
        except tk.TclError:
            pass
        self._hover = False
        self._draw()

    def _on_focus(self, focus):
        self._draw(focus=focus)
        self._sync_placeholder()
        if not focus and self._path_mode:
            self.reveal_end()

    def _sync_placeholder(self):
        if not self._placeholder:
            return
        try:
            show = (not self.entry.get()) and self.entry.focus_get() is not self.entry
            if show:
                self._ph.place(x=12, y=6)
            else:
                self._ph.place_forget()
        except (tk.TclError, KeyError):
            pass

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
            outline, width = "#B5BFCE", 1
        else:
            outline, width = BORDER, 1
        _round_rect(c, 1, 2, w - 1, h - 2, 8.0, fill=WHITE, outline=outline, width=width)

    def get(self):
        return self.entry.get()

    def insert(self, index, text):
        out = self.entry.insert(index, text)
        self._sync_placeholder()
        if self._path_mode:
            try:
                if self.entry.focus_get() is not self.entry:
                    self.reveal_end()
            except tk.TclError:
                self.reveal_end()
        return out

    def delete(self, first, last=None):
        out = self.entry.delete(first, last)
        self._sync_placeholder()
        return out

    def set(self, text):
        state = str(self.entry.cget("state") or "normal")
        if state != "normal":
            self.entry.configure(state="normal")
        self.delete(0, "end")
        self.insert(0, text or "")
        if state != "normal":
            self.entry.configure(state=state)

    def set_readonly(self, readonly=True):
        self.entry.configure(state="readonly" if readonly else "normal")

    def bind(self, seq, func, add=None):
        return self.entry.bind(seq, func, add)


IslandEntry = RoundedEntry


class PhaseBadge(tk.Canvas):
    """阶段胶囊微标：pending / now / done / failed。状态只由 set_mode 驱动。"""

    def __init__(self, master, text="", font=None, width=72, height=26, **kw):
        bg = kw.pop("bg", None) or getattr(master, "cget", lambda _k: BG)("bg") or BG
        super().__init__(master, width=width, height=height, highlightthickness=0,
                         bd=0, bg=bg, **kw)
        self._text = text
        self._font = font or (FONT_FAMILY, 9)
        self._mode = "pending"
        self._box_w, self._box_h = width, height
        self.bind("<Configure>", lambda _e: self._draw())
        self._draw()

    def set_mode(self, mode):
        self._mode = mode if mode in ("pending", "now", "done", "failed") else "pending"
        self._draw()

    def configure(self, **kw):
        if "text" in kw:
            self._text = kw.pop("text")
        kw.pop("foreground", None)
        if kw:
            super().configure(**kw)
        self._draw()

    config = configure

    def cget(self, key):
        if key == "text":
            return self._text
        if key == "foreground":
            return {"done": MINT, "now": ON_MINT, "failed": ERR, "pending": MUTED}[self._mode]
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
            fill, fg = MINT_BG, MINT
        elif self._mode == "failed":
            fill, fg = ERR_BG, ERR
        else:
            fill, fg = SAND, MUTED
        _round_rect(self, 1, 1, w - 1, h - 1, 6.0, fill=fill, outline="")
        self.create_text(w / 2, h / 2, text=self._text, fill=fg, font=self._font)


class IslandBar(tk.Canvas):
    """进度条。默认确定进度；start/stop 兼容不确定进度用法。"""

    def __init__(self, master, height=8, **kw):
        bg = kw.pop("bg", None) or getattr(master, "cget", lambda _k: BG)("bg") or BG
        super().__init__(master, height=height + 4, highlightthickness=0, bd=0, bg=bg, **kw)
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
        _round_rect(self, 0, y1, w, y2, (y2 - y1) / 2, fill=TRACK, outline="")
        if self._mode == "indeterminate":
            span = max(24, int(w * 0.28))
            travel = max(1, w - span)
            x = int((self._pulse / 120.0) * travel)
            _round_rect(self, x, y1, x + span, y2, (y2 - y1) / 2,
                        fill=MINT_DEEP, outline="")
        else:
            ratio = min(1.0, self._value / float(self._maximum or 1))
            if ratio > 0:
                _round_rect(self, 0, y1, max(8, int(w * ratio)), y2,
                            (y2 - y1) / 2, fill=MINT_DEEP, outline="")


ProgressBar = IslandBar


class DatePicker:
    """日历弹层，选中后把 YYYY-MM-DD 写回输入框；点弹层外或 Esc 关闭。

    embedded=True 时把「▾」收进圆角输入框尾部，不再单独占一列。
    翻月是同方向滑入滑出（约 220ms，可打断），星期行和左右键不动。
    """

    _SLIDE_MS = 220

    def __init__(self, entry, font=None, embedded=False, on_pick=None):
        self.entry = entry
        self.font = font or (FONT_FAMILY, 9)
        self.on_pick = on_pick
        self.win = None
        self._trigger = None
        self._slide_job = None
        self._root = None
        self._root_click_id = None
        self._pages = []
        if embedded:
            self._embed()

    def _embed(self):
        host = self.entry
        if not isinstance(host, RoundedEntry):
            return
        host.set_right_inset(34)
        self._trigger = CapsuleButton(
            host, text="▾", command=self.toggle, width=26, height=26,
            font=self.font, bg=WHITE, subtle=True)
        self._trigger.place(relx=1, x=-30, y=5, width=26, height=26)

    def toggle(self, _event=None):
        if self.win is not None:
            self.close()
            return
        self.open()

    def close(self):
        self._cancel_slide()
        try:
            if self._root is not None and self._root_click_id:
                self._root.unbind("<Button-1>", self._root_click_id)
        except (tk.TclError, KeyError, ValueError):
            pass
        self._root_click_id = None
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
        self._body = tk.Frame(self.win, bg=PAPER, padx=12, pady=10)
        self._body.pack(padx=1, pady=1)
        self._build_chrome()
        self.win.update_idletasks()
        self._show_month(self._year, self._month, animate=False)
        self.win.bind("<Tab>", self._on_tab)
        self.win.bind("<Escape>", self._on_escape)
        self._root = widget
        try:
            self._root_click_id = self._root.bind("<Button-1>", self._on_click, add="+")
        except (tk.TclError, AttributeError):
            self._root_click_id = None
        self.win.focus_set()

    def _on_click(self, event=None):
        if self.win is None or event is None:
            return
        try:
            x, y = event.x_root, event.y_root
            wx, wy = self.win.winfo_rootx(), self.win.winfo_rooty()
            ww, wh = self.win.winfo_width(), self.win.winfo_height()
            ex, ey = self.entry.winfo_rootx(), self.entry.winfo_rooty()
            ew, eh = self.entry.winfo_width(), self.entry.winfo_height()
        except (tk.TclError, AttributeError):
            return
        if ex <= x < ex + ew and ey <= y < ey + eh:
            return
        if not (wx <= x < wx + ww and wy <= y < wy + wh):
            self.close()

    def _on_tab(self, _e=None):
        if self.win is None:
            return "break"
        try:
            self._today_btn.focus_set()
        except (tk.TclError, AttributeError):
            pass
        return "break"

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
        self._stage_h = ch * 6 + 4 * 6
        head = tk.Frame(self._body, bg=PAPER)
        head.pack(fill="x")
        self._arrow(head, "‹", -1).pack(side="left")
        self._title_stage = tk.Frame(head, bg=PAPER, width=self._stage_w - 64, height=28)
        self._title_stage.pack(side="left", expand=True, fill="both")
        self._title_stage.pack_propagate(False)
        self._arrow(head, "›", 1).pack(side="right")
        days = tk.Frame(self._body, bg=PAPER)
        days.pack(pady=(6, 0))
        for i, name in enumerate("一二三四五六日"):
            tk.Label(days, text=name, bg=PAPER, fg=MUTED, font=self.font,
                     width=3).grid(row=0, column=i)
        self._stage = tk.Frame(self._body, bg=PAPER, width=self._stage_w,
                               height=self._stage_h)
        self._stage.pack()
        self._stage.pack_propagate(False)
        foot = tk.Frame(self._body, bg=PAPER)
        foot.pack(fill="x", pady=(8, 0))
        self._today_btn = CapsuleButton(
            foot, text="今天", command=lambda: self._pick(dt.date.today()),
            width=120, height=30, font=self.font, bg=PAPER)
        self._today_btn.pack()

    def _make_grid(self, year, month):
        grid = tk.Frame(self._stage, bg=PAPER, width=self._stage_w, height=self._stage_h)
        selected = self._current()
        today = dt.date.today()
        weeks = calendar.Calendar(0).monthdayscalendar(year, month)
        while len(weeks) < 6:
            weeks.append([0] * 7)
        for r, week in enumerate(weeks[:6]):
            for c, day in enumerate(week):
                cell = tk.Label(grid, text="" if day == 0 else str(day),
                                bg=PAPER, fg=TEXT, font=self.font, width=3)
                cell.grid(row=r, column=c, padx=1, pady=1, ipady=1)
                if day == 0:
                    continue
                d = dt.date(year, month, day)
                if d == selected:
                    cell.configure(bg=MINT, fg=ON_MINT, cursor="hand2")
                elif d == today:
                    cell.configure(bg=MINT_BG, fg=MINT, cursor="hand2")
                else:
                    cell.configure(cursor="hand2")
                cell._rest_bg = cell.cget("bg")
                cell._rest_fg = cell.cget("fg")
                if d != selected:
                    def _enter(_e, w=cell):
                        if w.cget("bg") != MINT:
                            w.configure(bg=SAND)

                    def _leave(_e, w=cell):
                        w.configure(bg=w._rest_bg, fg=w._rest_fg)

                    cell.bind("<Enter>", _enter)
                    cell.bind("<Leave>", _leave)
                cell.bind("<Button-1>", lambda _e, dd=d: self._pick(dd))
        return grid

    def _make_title(self, year, month):
        return tk.Label(self._title_stage, text=f"{year}-{month:02d}",
                        bg=PAPER, fg=TEXT, font=(self.font[0], self.font[1], "bold"))

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

    def _arrow(self, parent, text, delta):
        lb = tk.Label(parent, text=text, bg=PAPER, fg=TEXT, font=(self.font[0], 12),
                      width=2, cursor="hand2", takefocus=0)
        lb.bind("<Button-1>", lambda _e: self._shift(delta))
        lb.bind("<Enter>", lambda _e: lb.configure(bg=SAND, fg=MINT_DEEP))
        lb.bind("<Leave>", lambda _e: lb.configure(bg=PAPER, fg=TEXT))
        return lb

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
        if self.on_pick:
            try:
                self.on_pick()
            except Exception:
                pass
