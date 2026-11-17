# -*- coding: utf-8 -*-
"""V0.4.5 整改项的回归钉：F-1 / F-3 / F-4 / F-5 / F-7。

F-2 不是代码缺陷，是流程缺陷：单测已并入 tests/run_all.py 的第一步。
F-6 靠 settings.py 的 _busy() 门禁，属于交互态，这里用源码断言兜住调用点。
"""
import io
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
SRC = os.path.join(APP, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import tkinter as tk  # noqa: E402
import ui_kit  # noqa: E402
from ui_kit import CapsuleButton, IslandEntry, PhaseBadge, apply_island_style, fonts  # noqa: E402


def read_src(name):
    with io.open(os.path.join(SRC, name), encoding="utf-8") as f:
        return f.read()


class V045Tests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        apply_island_style(self.root)

    def tearDown(self):
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    # ---- F-1：未布局的画布也必须画出来（winfo_width()=1 曾让早退吞掉整只按钮）----
    def test_draw_works_before_layout(self):
        btn = CapsuleButton(self.root, text="导出", width=90, height=36)
        # 关键：不 pack、不 update，模拟「还没被几何管理器布局」
        self.assertEqual(int(btn.winfo_width()), 1)
        btn._draw()
        self.assertGreater(len(btn.find_all()), 0, "未布局态必须仍画出按钮")

    def test_focus_ring_drawn_before_layout(self):
        btn = CapsuleButton(self.root, text="导出", width=90, height=36)
        btn._draw()
        before = len(btn.find_all())
        btn._on_focus_in()
        self.assertGreater(len(btn.find_all()), before, "focus 时应多画一个焦点环")

    # ---- F-3：状态色只准在 ui_kit 定义一次 ----
    def test_no_duplicated_status_colors(self):
        banned = ("#1a7f37", "#b45309", "#666666")
        offenders = []
        for fn in os.listdir(SRC):
            if not fn.endswith(".py") or fn in ("ui_kit.py", "report_html.py"):
                continue          # report_html 是 HTML 的 CSS 变量，不是 Tk 令牌
            text = read_src(fn)
            for color in banned:
                if color in text.lower():
                    offenders.append(f"{fn}:{color}")
        self.assertEqual(offenders, [], "状态色应引用 ui_kit.OK/WARN/NEUTRAL")

    # ---- F-4：foreground 兼容分支已删，不再假装能改状态 ----
    def test_phase_badge_foreground_no_longer_maps_mode(self):
        fm, fs, _ = fonts(self.root)
        badge = PhaseBadge(self.root, text="读取", font=fs)
        badge.configure(foreground="#1a7f37")
        self.assertEqual(badge._mode, "pending", "foreground 不应再改变阶段")
        badge.set_mode("now")
        self.assertEqual(badge._mode, "now")

    def test_gui_has_no_dead_hasattr_branch(self):
        text = read_src("gui.py")
        self.assertNotIn('hasattr(badge, "set_mode")', text)

    # ---- F-5：密钥输入默认遮蔽 ----
    def test_key_entry_show_is_masked(self):
        entry = IslandEntry(self.root, font=None, show="*")
        self.assertEqual(entry.entry.cget("show"), "*", "IslandEntry 应把 show 透传给内部 Entry")
        self.assertIn('show="*"', read_src("setup_env.py"), "手动密钥框必须默认遮蔽")

    # ---- F-6：动作入口有 busy 门禁 ----
    def test_settings_has_busy_guard(self):
        text = read_src("settings.py")
        for needle in ('def _busy(', 'self._busy("write", True)',
                       'self._busy("conn", True)', 'self._busy("env", True)'):
            self.assertIn(needle, text)

    # ---- F-7：设置窗 Esc 可关 ----
    def test_settings_binds_escape(self):
        self.assertIn('bind("<Escape>"', read_src("settings.py"))


if __name__ == "__main__":
    unittest.main()
